"""
Citable dataset fingerprint and run manifest (expansion memo E-03;
``cc-task-frozen-run-tooling``, bundled by owner decision 2026-10-04).

One read-only command serializes what a frozen run was computed from, so a
manuscript sentence can cite one fingerprint and Stage 1.3 of the frozen-run
procedure is written by the tool rather than by hand:

* the package version and the schema state, the core ``schema_version`` and
  the ``applied_migrations`` ledger, every source;
* every ``raw_imports`` row, its SHA-256, format and row count, named by a
  caller-supplied pseudonym (``--pseudonyms``, a CSV with ``sha256`` and
  ``pseudonym`` columns, the export-inventory map's shape) and otherwise by
  the hash alone; **a file path is never written**;
* the registered textbooks, book id and git ref; a ``file://`` repository
  URL is redacted to its scheme, a remote URL is kept;
* the synced questions, count, count with source, and one SHA-256 over the
  sorted set of their source hashes (the source pins);
* every ``pipeline_runs`` row, service, stage, status, the F22
  configuration; a row without a stage or configuration (a pre-F22 pass, or
  an LLM stage recorded by prompt and model) is marked ``provenance:
  incomplete`` rather than hidden;
* each available service's own contribution, through its descriptor's
  ``manifest`` hook (registry-driven, so core names no service table):
  for DnD the question-identifier alias set and its content hash;
* per-table row counts for every table in the database;
* the database file's SHA-256 when the connection is a file.

The *fingerprint* is the SHA-256 of the canonical JSON of everything above
except the generation time, so two manifests of the same state hash alike.
What the manifest does **not** capture is stated in it: the Python
environment, the platform, and anything outside the database.

This module is core: it names no service and reads only core tables and
``sqlite_master``; service facts arrive through the registry's descriptors.
The manifest is written as YAML (the procedure's
manifest is YAML-shaped) through a small emitter, and read back for
``diff`` by a parser for exactly the subset the emitter writes, no third-
party dependency. ``diff`` reports every leaf that differs, by path.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import sqlite3
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

__all__ = [
    "MANIFEST_VERSION",
    "build_manifest",
    "fingerprint",
    "load_pseudonyms",
    "write_manifest",
    "read_manifest",
    "diff_manifests",
    "dump_yaml",
    "parse_yaml",
]

# 2: the per-service sections (`services`, through the descriptors' manifest
# hook), first the DnD question-identifier alias set (dnd/0014).
MANIFEST_VERSION = 2
_NOT_CAPTURED = (
    "the Python environment and installed dependency versions",
    "the operating system and hardware",
    "files outside the database (exports, archives, source checkouts)",
    "LLM endpoints, prompts and model identities beyond pipeline_runs' columns",
)


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------
def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone() is not None


def _package_version() -> str:
    try:
        from importlib.metadata import version

        return version("proteus-rs-pipeline")
    except Exception:  # pragma: no cover - outside an installed environment
        import proteus

        return getattr(proteus, "__version__", "unknown")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_pseudonyms(path: Path | None) -> dict[str, str]:
    """``sha256 → pseudonym`` from a CSV with those two columns (comment
    lines starting with ``#`` skipped); empty without a path."""
    if path is None:
        return {}
    out: dict[str, str] = {}
    with Path(path).open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(line for line in f if not line.startswith("#")):
            if row.get("sha256") and row.get("pseudonym"):
                out[row["sha256"].strip()] = row["pseudonym"].strip()
    return out


def _redact_url(url: str | None) -> str | None:
    if url is None:
        return None
    if url.startswith("file:"):
        return "file://<redacted local path>"
    return url


def _imports(conn: sqlite3.Connection, pseudonyms: Mapping[str, str]) -> list[dict]:
    out = []
    for r in conn.execute(
        "SELECT import_id, file_hash, file_format, row_count, imported_at "
        "FROM raw_imports ORDER BY import_id"
    ):
        out.append({
            "import_id": r["import_id"],
            "pseudonym": pseudonyms.get(r["file_hash"], f"unmapped-{r['file_hash'][:12]}"),
            "sha256": r["file_hash"],
            "format": r["file_format"],
            "row_count": r["row_count"],
            "imported_at": r["imported_at"],
        })
    return out


def _textbooks(conn: sqlite3.Connection) -> list[dict]:
    if not _table_exists(conn, "textbooks"):
        return []
    return [
        {
            "textbook_id": r["textbook_id"],
            "book_xml_id": r["book_xml_id"],
            "git_repo_url": _redact_url(r["git_repo_url"]),
            "git_ref": r["git_ref"],
        }
        for r in conn.execute(
            "SELECT textbook_id, book_xml_id, git_repo_url, git_ref FROM textbooks "
            "ORDER BY textbook_id"
        )
    ]


def _questions(conn: sqlite3.Connection) -> dict:
    if not _table_exists(conn, "questions"):
        return {"count": 0, "with_source": 0, "source_hash_set_sha256": None}
    hashes = sorted(
        r["source_hash"]
        for r in conn.execute(
            "SELECT source_hash FROM questions WHERE source_hash IS NOT NULL"
        )
    )
    total = conn.execute("SELECT count(*) AS n FROM questions").fetchone()["n"]
    digest = hashlib.sha256("\n".join(hashes).encode("utf-8")).hexdigest() if hashes else None
    by_type: dict[str, int] = {}
    try:
        for r in conn.execute(
            "SELECT question_type, count(*) AS n FROM questions GROUP BY question_type"
        ):
            by_type[str(r["question_type"])] = r["n"]
    except sqlite3.OperationalError:
        pass
    return {
        "count": total,
        "with_source": len(hashes),
        "source_hash_set_sha256": digest,
        "by_type": by_type,
    }


def _runs(conn: sqlite3.Connection) -> list[dict]:
    if not _table_exists(conn, "pipeline_runs"):
        return []
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(pipeline_runs)")}
    has_stage = "stage" in columns and "config_json" in columns
    has_service = "service" in columns
    out = []
    for r in conn.execute("SELECT * FROM pipeline_runs ORDER BY run_id"):
        stage = r["stage"] if has_stage else None
        config_json = r["config_json"] if has_stage else None
        config = None
        if config_json:
            try:
                config = json.loads(config_json)
            except ValueError:
                config = None
        entry = {
            "run_id": r["run_id"],
            "service": r["service"] if has_service else None,
            "stage": stage,
            "status": r["status"],
            "started_at": r["started_at"],
            "completed_at": r["completed_at"],
            "config": config,
            "prompt_version": r["prompt_version"],
            "model_id": r["model_id"],
            "vocab_version": r["vocab_version"],
            "codebook_version": r["codebook_version"],
        }
        if stage is None or config is None:
            entry["provenance"] = "incomplete"
        out.append(entry)
    return out


def _schema(conn: sqlite3.Connection) -> dict:
    core_version = None
    if _table_exists(conn, "schema_version"):
        row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        core_version = row["v"]
    ledger = []
    if _table_exists(conn, "applied_migrations"):
        ledger = [
            {"source": r["source"], "version": r["version"], "name": r["name"]}
            for r in conn.execute(
                "SELECT source, version, name FROM applied_migrations "
                "ORDER BY source, version"
            )
        ]
    return {"core_version": core_version, "applied_migrations": ledger}


def _table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ):
        name = r["name"]
        counts[name] = conn.execute(f'SELECT count(*) AS n FROM "{name}"').fetchone()["n"]
    return counts


def _services(conn: sqlite3.Connection) -> dict[str, object]:
    """Each available service's manifest contribution, keyed by service
    name, reached through the registry (core never imports a service
    directly); a contribution that fails is recorded as an error, never
    raised, so one broken service cannot sink the manifest."""
    try:
        from proteus.core import registry

        services = registry.available_services()
    except Exception as exc:  # noqa: BLE001 - isolation boundary
        return {"error": f"{type(exc).__name__}: {exc}"}
    out: dict[str, object] = {}
    for svc in services:
        if svc.manifest is None:
            continue
        try:
            out[svc.name] = svc.manifest(conn)
        except Exception as exc:  # noqa: BLE001 - isolation boundary
            out[svc.name] = {"error": f"{type(exc).__name__}: {exc}"}
    return out


def fingerprint(body: Mapping[str, object]) -> str:
    """SHA-256 of the canonical JSON of ``body``."""
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_manifest(
    conn: sqlite3.Connection,
    *,
    pseudonyms: Mapping[str, str] | None = None,
    db_path: Path | None = None,
) -> dict:
    """The manifest as a plain dict: a hashed ``body`` plus the fingerprint
    and the generation time (outside the hash)."""
    body = {
        "manifest_version": MANIFEST_VERSION,
        "package_version": _package_version(),
        "schema": _schema(conn),
        "imports": _imports(conn, pseudonyms or {}),
        "textbooks": _textbooks(conn),
        "questions": _questions(conn),
        "pipeline_runs": _runs(conn),
        "services": _services(conn),
        "table_counts": _table_counts(conn),
        "database_sha256": (
            _sha256_file(db_path) if db_path is not None and Path(db_path).is_file() else None
        ),
        "not_captured": list(_NOT_CAPTURED),
    }
    return {
        "fingerprint": fingerprint(body),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "body": body,
    }


# ---------------------------------------------------------------------------
# YAML (the subset the emitter writes: maps, lists, scalars)
# ---------------------------------------------------------------------------
def _scalar(value: object) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return json.dumps(str(value), ensure_ascii=True)  # always double-quoted


def dump_yaml(value: object, indent: int = 0) -> str:
    """A deterministic block-style YAML rendering of nested dicts, lists and
    scalars (strings always double-quoted, keys sorted)."""
    pad = "  " * indent
    if isinstance(value, Mapping):
        if not value:
            return pad + "{}\n"
        out = []
        for key in sorted(value):
            v = value[key]
            k = _scalar(str(key))
            if isinstance(v, (Mapping, list)) and v:
                out.append(f"{pad}{k}:\n{dump_yaml(v, indent + 1)}")
            else:
                out.append(f"{pad}{k}: {dump_yaml(v, 0).strip()}\n")
        return "".join(out)
    if isinstance(value, list):
        if not value:
            return pad + "[]\n"
        out = []
        for item in value:
            if isinstance(item, (Mapping, list)) and item:
                nested = dump_yaml(item, indent + 1)
                first, _, rest = nested.partition("\n")
                out.append(f"{pad}- {first.lstrip()}\n{rest}" if rest else f"{pad}- {first.lstrip()}\n")
            else:
                out.append(f"{pad}- {dump_yaml(item, 0).strip()}\n")
        return "".join(out)
    return _scalar(value) + "\n"


def _parse_scalar(text: str) -> object:
    text = text.strip()
    if text == "null":
        return None
    if text == "true":
        return True
    if text == "false":
        return False
    if text == "{}":
        return {}
    if text == "[]":
        return []
    if text.startswith('"'):
        return json.loads(text)
    try:
        return int(text)
    except ValueError:
        return float(text)


def parse_yaml(text: str) -> object:
    """Parse the subset :func:`dump_yaml` emits."""
    lines = [
        (len(line) - len(line.lstrip(" ")), line.strip())
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    pos = 0

    def parse_block(indent: int) -> object:
        nonlocal pos
        if pos >= len(lines):
            return None
        _, first = lines[pos]
        if first.startswith("- "):
            return parse_list(indent)
        return parse_map(indent)

    def parse_list(indent: int) -> list:
        nonlocal pos
        items: list = []
        while pos < len(lines) and lines[pos][0] == indent and lines[pos][1].startswith("- "):
            content = lines[pos][1][2:]
            if _is_mapping_line(content):
                # A map whose first key sits on the dash line; the remaining
                # keys are indented two deeper.
                lines[pos] = (indent + 2, content)
                items.append(parse_map(indent + 2))
            elif content.startswith("- "):
                # A list whose first item sits on the dash line; the rest
                # follows two deeper.
                lines[pos] = (indent + 2, content)
                items.append(parse_list(indent + 2))
            else:
                pos += 1
                items.append(_parse_scalar(content))
        return items

    def parse_map(indent: int) -> dict:
        nonlocal pos
        out: dict = {}
        while pos < len(lines) and lines[pos][0] == indent and not lines[pos][1].startswith("- "):
            key, value = _split_key(lines[pos][1])
            pos += 1
            if value == "":
                out[key] = parse_block(lines[pos][0]) if pos < len(lines) and lines[pos][0] > indent else {}
            else:
                out[key] = _parse_scalar(value)
        return out

    return parse_block(lines[0][0] if lines else 0)


def _is_mapping_line(content: str) -> bool:
    return content.startswith('"') and '": ' in content or content.endswith('":')


def _split_key(line: str) -> tuple[str, str]:
    # Keys are always double-quoted JSON strings.
    key_json, _, rest = line.partition('": ')
    if not rest and line.endswith('":'):
        return json.loads(line[:-1]), ""
    return json.loads(key_json + '"'), rest


def write_manifest(manifest: Mapping[str, object], path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        f"# PROTEUS run manifest v{MANIFEST_VERSION}; fingerprint = sha256 of the "
        "canonical JSON of `body`. No file path is recorded.\n"
    )
    path.write_text(header + dump_yaml(dict(manifest)), encoding="utf-8")
    return path


def read_manifest(path: Path) -> dict:
    data = parse_yaml(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not hold a manifest mapping")
    return data


def _flatten(value: object, prefix: str = "") -> dict[str, object]:
    if isinstance(value, Mapping):
        out: dict[str, object] = {}
        for k in sorted(value):
            out.update(_flatten(value[k], f"{prefix}.{k}" if prefix else str(k)))
        return out or {prefix: {}}
    if isinstance(value, list):
        out = {}
        for i, item in enumerate(value):
            out.update(_flatten(item, f"{prefix}[{i}]"))
        return out or {prefix: []}
    return {prefix: value}


def diff_manifests(a: Mapping[str, object], b: Mapping[str, object]) -> list[str]:
    """Every leaf under ``body`` that differs, as ``path: a -> b`` lines
    (``generated_at`` is outside the comparison)."""
    fa = _flatten(a.get("body", a))
    fb = _flatten(b.get("body", b))
    lines = []
    for key in sorted(set(fa) | set(fb)):
        if key not in fa:
            lines.append(f"+ {key}: {fb[key]!r}")
        elif key not in fb:
            lines.append(f"- {key}: {fa[key]!r}")
        elif fa[key] != fb[key]:
            lines.append(f"~ {key}: {fa[key]!r} -> {fb[key]!r}")
    return lines
