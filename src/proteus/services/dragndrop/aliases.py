"""
Question-identifier aliases at board binding (P4 RREF repair;
``cc-task-question-id-alias``, owner decisions 2026-10-04).

One ULA item was renamed in source on 2025-07-05 while Runestone kept serving
the old identifier through assignments, so both identifiers carry attempts in
five of six offerings and the collection-era source has a board only under
the new one. Every attempt logged under the old identifier therefore read
``missing_source_map`` and was excluded — the entire exclusion count of the
corpus (the 2026-09-09 item inventory, §5). The P4 decision
(``paper1-decisions.md`` 2026-09-27; the 2026-09-16 record) repairs this by
**aliasing the retired identifier to its successor at board-binding time**:
raw rows, ``event_id`` and ``problem_name`` are never rewritten
(``dev/decisions/event-identity-contract.md``); the alias is applied where a
board is looked up and where items are grouped, nowhere earlier.

The mechanism:

* ``dnd_question_aliases`` (migration dnd/0014) — one row per retired
  identifier, ``alias_question_id → canonical_question_id``, with a reason,
  the rename commit and the provenance of the import. Resolution is one
  step: a self-alias, an alias whose canonical is itself an alias, and an
  alias of an identifier that already serves as a canonical are refused; an
  alias identifier that has its own board is refused unless that board is
  explicitly retired (``replace_board``), which the row records.
* :func:`resolve_question_id` — the one resolver. ``board.load_board`` calls
  it, so every board lookup in the service (validate, reconstruct, score,
  analyze, the cohort and trajectory readers, the exports) binds an aliased
  identifier to the canonical board without keeping its own join.
* ``dnd_attempts.canonical_question_id`` (dnd/0014) — written by reconstruct,
  NULL unless the attempt's question was bound through an alias: the
  per-attempt provenance marker. A column, not a QC flag (owner decision
  2026-10-04): the flag vocabulary and the rulebook stay at eleven names and
  readiness is untouched — the inventory established structural identity,
  so an aliased attempt is as ready as any other.
* Labels are always parsed with the **logged** identifier: the positional
  grammar anchors on it (``decode.parse_label``). The resolver is applied to
  the board lookup only, never before parsing.
* Item-level grouping is by canonical identifier — :func:`item_ids` lists
  the logged identifiers an item aggregates; attempts stay per logged
  identifier (a student who worked both pages has two attempt sequences on
  one item); ``dnd_check_scores.question_id`` keeps the logged identifier so
  the per-question clear-and-rebuild still finds its rows.
* Provenance: :func:`alias_set_sha256` hashes the mapping; reconstruct,
  score and analyze record it in their F22 run configuration (the
  sensitivity harness checks it against the database it clones), and the
  run manifest carries the rows and the hash through the service descriptor
  (``manifest_section``).

The committed alias file ``aliases/ula-proteus.csv`` is loaded by
``proteus dnd alias import`` (idempotent; the frozen-run procedure's Stage
2.3 names the command). With an empty alias table every stage behaves
exactly as before.
"""

from __future__ import annotations

import csv
import hashlib
import logging
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from proteus.core.db import transaction

log = logging.getLogger(__name__)

__all__ = [
    "FILE_COLUMNS",
    "TABLE",
    "Alias",
    "AliasError",
    "ImportResult",
    "add_alias",
    "alias_set_sha256",
    "aliases_of",
    "file_sha256",
    "import_aliases",
    "is_alias",
    "item_ids",
    "list_aliases",
    "load_alias_map",
    "manifest_section",
    "read_alias_file",
    "remove_alias",
    "resolve_question_id",
]

TABLE = "dnd_question_aliases"

# The alias file's columns (a header row; '#' comment lines are skipped).
FILE_COLUMNS: tuple[str, ...] = (
    "alias_question_id",
    "canonical_question_id",
    "reason",
    "source_commit",
    "recorded_on",
)


class AliasError(ValueError):
    """A refused alias: self-alias, chain, ambiguous board, unknown id."""


@dataclass(frozen=True)
class Alias:
    """One ``dnd_question_aliases`` row."""

    alias_question_id: str
    canonical_question_id: str
    reason: str | None = None
    source_commit: str | None = None
    recorded_on: str | None = None
    imported_from: str | None = None  # the alias file's name, never a path
    imported_sha256: str | None = None
    replaced_board_source_hash: str | None = None


@dataclass(frozen=True)
class ImportResult:
    added: int
    unchanged: int
    file_sha256: str
    set_sha256: str | None
    rows: tuple[Alias, ...]  # the whole table after the import


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------
def _table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (TABLE,),
    ).fetchone()
    return row is not None


def load_alias_map(conn: sqlite3.Connection) -> dict[str, str]:
    """``alias_question_id → canonical_question_id`` for every row (empty on
    a database that predates dnd/0014)."""
    if not _table_exists(conn):
        return {}
    return {
        r["alias_question_id"]: r["canonical_question_id"]
        for r in conn.execute(
            f"SELECT alias_question_id, canonical_question_id FROM {TABLE}"
        )
    }


def resolve_question_id(conn: sqlite3.Connection, question_id: str) -> str:
    """The canonical identifier of ``question_id`` when it is a registered
    alias, else ``question_id`` itself. One step by construction: the table
    cannot hold a chain."""
    if not _table_exists(conn):
        return question_id
    row = conn.execute(
        f"SELECT canonical_question_id FROM {TABLE} WHERE alias_question_id = ?",
        (question_id,),
    ).fetchone()
    return row["canonical_question_id"] if row is not None else question_id


def is_alias(conn: sqlite3.Connection, question_id: str) -> bool:
    return resolve_question_id(conn, question_id) != question_id


def aliases_of(conn: sqlite3.Connection, canonical: str) -> tuple[str, ...]:
    """The alias identifiers retired to ``canonical``, sorted."""
    if not _table_exists(conn):
        return ()
    return tuple(
        r["alias_question_id"]
        for r in conn.execute(
            f"SELECT alias_question_id FROM {TABLE} "
            "WHERE canonical_question_id = ? ORDER BY alias_question_id",
            (canonical,),
        )
    )


def item_ids(conn: sqlite3.Connection, question_id: str) -> tuple[str, ...]:
    """
    The logged identifiers whose rows the item named by ``question_id``
    aggregates: the identifier itself first, then its aliases in sorted
    order. An alias identifier names only itself — a view of one logged
    identifier — so an item-level reader given the canonical identifier sees
    every attempt of the item, and one given the alias sees that
    identifier's own attempts (against the canonical board).
    """
    return (question_id, *aliases_of(conn, question_id))


def alias_set_sha256(conn: sqlite3.Connection) -> str | None:
    """SHA-256 over the sorted ``alias → canonical`` pairs (tab-separated,
    newline-joined); None when the table is empty. The content hash the
    stages record in their run configuration and the manifest carries."""
    pairs = sorted(load_alias_map(conn).items())
    if not pairs:
        return None
    text = "\n".join(f"{alias}\t{canonical}" for alias, canonical in pairs)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _row_to_alias(r: sqlite3.Row) -> Alias:
    return Alias(
        alias_question_id=r["alias_question_id"],
        canonical_question_id=r["canonical_question_id"],
        reason=r["reason"],
        source_commit=r["source_commit"],
        recorded_on=r["recorded_on"],
        imported_from=r["imported_from"],
        imported_sha256=r["imported_sha256"],
        replaced_board_source_hash=r["replaced_board_source_hash"],
    )


def list_aliases(conn: sqlite3.Connection) -> list[Alias]:
    if not _table_exists(conn):
        return []
    return [
        _row_to_alias(r)
        for r in conn.execute(f"SELECT * FROM {TABLE} ORDER BY alias_question_id")
    ]


# ---------------------------------------------------------------------------
# Writing: validate first, then apply — a refused alias writes nothing —
# under one write lock, so the validation and the write see one mapping
# ---------------------------------------------------------------------------
@contextmanager
def _write_locked(conn: sqlite3.Connection) -> Iterator[None]:
    """Run the validating reads and the writes under one SQLite write
    lock. ``BEGIN IMMEDIATE`` takes the reserved lock *before* the mapping
    is read, so two writers cannot both validate against the same mapping
    and then commit contradictory rows (A → B and B → A); the commit or the
    rollback releases it. A caller already inside a transaction keeps its
    own — nothing is begun or committed for it."""
    if conn.in_transaction:
        yield
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.rollback()
        raise
    else:
        conn.commit()


def _has_events(conn: sqlite3.Connection, question_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM events WHERE problem_name = ? LIMIT 1", (question_id,)
    ).fetchone()
    return row is not None


def _has_catalog_row(conn: sqlite3.Connection, question_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM questions WHERE question_id = ? LIMIT 1", (question_id,)
    ).fetchone()
    return row is not None


def _board_hash(conn: sqlite3.Connection, question_id: str) -> str | None:
    row = conn.execute(
        "SELECT source_hash FROM dnd_boards WHERE question_id = ?", (question_id,)
    ).fetchone()
    return row["source_hash"] if row is not None else None


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


@dataclass(frozen=True)
class _Plan:
    alias: Alias
    action: str  # "added" | "unchanged"


def _plan(
    conn: sqlite3.Connection,
    alias_id: str,
    canonical_id: str,
    *,
    reason: str | None,
    source_commit: str | None,
    recorded_on: str | None,
    replace_board: bool,
    imported_from: str | None,
    imported_sha256: str | None,
    pending: Mapping[str, str],
) -> _Plan:
    """
    Validate one alias against the table as it stands plus the rows already
    accepted earlier in the same import (``pending``). Raises
    :class:`AliasError` for a self-alias, a chain in either direction, an
    alias identifier that has its own board (unless ``replace_board``), an
    identifier the database has never seen, or an alias already pointing
    elsewhere. Returns the row to write and whether it is new.
    """
    alias_id = (alias_id or "").strip()
    canonical_id = (canonical_id or "").strip()
    if not alias_id or not canonical_id:
        raise AliasError("an alias needs both identifiers")
    if alias_id == canonical_id:
        raise AliasError(f"{alias_id!r} cannot alias itself")

    current = load_alias_map(conn)
    current.update(pending)
    existing = current.get(alias_id)
    if existing is not None:
        if existing == canonical_id:
            return _Plan(Alias(alias_id, canonical_id), "unchanged")
        raise AliasError(
            f"{alias_id!r} is already an alias of {existing!r}; remove it "
            "before pointing it elsewhere"
        )
    if canonical_id in current:
        raise AliasError(
            f"{canonical_id!r} is itself an alias of {current[canonical_id]!r}; "
            "aliases resolve in one step (no chains)"
        )
    if alias_id in current.values():
        raise AliasError(
            f"{alias_id!r} is the canonical identifier of another alias; "
            "aliases resolve in one step (no chains)"
        )

    board_hash = _board_hash(conn, alias_id)
    if board_hash is not None and not replace_board:
        raise AliasError(
            f"{alias_id!r} has its own board (source hash {board_hash[:12]}…); "
            "aliasing it is ambiguous — pass replace_board / --replace-board "
            "to retire that board and its results"
        )
    if not _has_events(conn, alias_id) and not _has_catalog_row(conn, alias_id):
        raise AliasError(
            f"{alias_id!r} is unknown here: no event and no catalog row carries "
            "it (a typo in the identifier?)"
        )
    if _board_hash(conn, canonical_id) is None:
        log.warning(
            "%s: canonical identifier %s has no board yet; `dnd validate` "
            "reports no_board for both until the source is synced",
            alias_id, canonical_id,
        )
    return _Plan(
        Alias(
            alias_question_id=alias_id,
            canonical_question_id=canonical_id,
            reason=reason or None,
            source_commit=source_commit or None,
            recorded_on=recorded_on or _today(),
            imported_from=imported_from,
            imported_sha256=imported_sha256,
            replaced_board_source_hash=board_hash,
        ),
        "added",
    )


def _apply(conn: sqlite3.Connection, plan: _Plan) -> None:
    """Write one planned alias (inside the caller's transaction)."""
    if plan.action != "added":
        return
    alias = plan.alias
    if alias.replaced_board_source_hash is not None:
        # Lazy import: reconstruct imports board, board imports this module.
        from proteus.services.dragndrop.reconstruct import clear_question_results

        cleared = clear_question_results(conn, alias.alias_question_id)
        for table in ("dnd_board_cards", "dnd_board_zones", "dnd_boards"):
            conn.execute(
                f"DELETE FROM {table} WHERE question_id = ?",
                (alias.alias_question_id,),
            )
        log.info(
            "%s: retired its own board (source hash %s) and cleared %d result "
            "row(s); it now resolves to %s",
            alias.alias_question_id, alias.replaced_board_source_hash[:12],
            cleared, alias.canonical_question_id,
        )
    conn.execute(
        f"INSERT INTO {TABLE} (alias_question_id, canonical_question_id, reason, "
        "source_commit, recorded_on, imported_from, imported_sha256, "
        "replaced_board_source_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            alias.alias_question_id,
            alias.canonical_question_id,
            alias.reason,
            alias.source_commit,
            alias.recorded_on,
            alias.imported_from,
            alias.imported_sha256,
            alias.replaced_board_source_hash,
        ),
    )
    log.info(
        "Alias registered: %s -> %s", alias.alias_question_id,
        alias.canonical_question_id,
    )


def add_alias(
    conn: sqlite3.Connection,
    alias_id: str,
    canonical_id: str,
    *,
    reason: str | None = None,
    source_commit: str | None = None,
    recorded_on: str | None = None,
    replace_board: bool = False,
) -> str:
    """Register one alias; returns ``"added"`` or ``"unchanged"`` (the same
    pair already registered). Refusals raise :class:`AliasError` and write
    nothing. Validation and the write run under one write lock."""
    with _write_locked(conn):
        plan = _plan(
            conn, alias_id, canonical_id,
            reason=reason, source_commit=source_commit, recorded_on=recorded_on,
            replace_board=replace_board, imported_from=None, imported_sha256=None,
            pending={},
        )
        _apply(conn, plan)
    return plan.action


def remove_alias(conn: sqlite3.Connection, alias_id: str) -> bool:
    """Remove one alias; True when a row was deleted. Results already
    computed under the alias are left as they are — re-run the stages."""
    with transaction(conn):
        cur = conn.execute(
            f"DELETE FROM {TABLE} WHERE alias_question_id = ?", (alias_id,)
        )
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# The alias file
# ---------------------------------------------------------------------------
def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_alias_file(path: Path) -> list[dict[str, str | None]]:
    """The rows of an alias file: a CSV with the :data:`FILE_COLUMNS` header
    (the two identifier columns required, the rest optional); lines starting
    with ``#`` and blank lines are skipped. Empty cells read as None."""
    lines = [
        line
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not lines:
        raise AliasError(f"{Path(path).name}: no header row")
    reader = csv.DictReader(lines)
    fields = tuple(reader.fieldnames or ())
    missing = [c for c in FILE_COLUMNS[:2] if c not in fields]
    if missing:
        raise AliasError(
            f"{Path(path).name}: header lacks {missing}; expected the columns "
            f"{list(FILE_COLUMNS)}"
        )
    rows: list[dict[str, str | None]] = []
    for raw in reader:
        rows.append(
            {c: ((raw.get(c) or "").strip() or None) for c in FILE_COLUMNS}
        )
    return rows


def import_aliases(
    conn: sqlite3.Connection, path: Path, *, replace_board: bool = False
) -> ImportResult:
    """
    Load an alias file. Every row is validated before anything is written,
    and validation and writing share one write lock, so a refused row
    leaves the table untouched and no concurrent writer can slip a row in
    between; a second import of the same file adds nothing and reports the
    same set hash (idempotent). The file's name and SHA-256 are recorded on
    each row it adds.
    """
    path = Path(path)
    digest = file_sha256(path)
    rows = read_alias_file(path)
    pending: dict[str, str] = {}
    plans: list[_Plan] = []
    with _write_locked(conn):
        for row in rows:
            plan = _plan(
                conn, row["alias_question_id"] or "", row["canonical_question_id"] or "",
                reason=row["reason"], source_commit=row["source_commit"],
                recorded_on=row["recorded_on"], replace_board=replace_board,
                imported_from=path.name, imported_sha256=digest, pending=pending,
            )
            if plan.action == "added":
                pending[plan.alias.alias_question_id] = plan.alias.canonical_question_id
            plans.append(plan)
        for plan in plans:
            _apply(conn, plan)
    added = sum(1 for p in plans if p.action == "added")
    log.info(
        "Alias import %s: %d added, %d unchanged (file sha256 %s)",
        path.name, added, len(plans) - added, digest[:12],
    )
    return ImportResult(
        added=added,
        unchanged=len(plans) - added,
        file_sha256=digest,
        set_sha256=alias_set_sha256(conn),
        rows=tuple(list_aliases(conn)),
    )


# ---------------------------------------------------------------------------
# The manifest contribution (E-03)
# ---------------------------------------------------------------------------
def manifest_section(conn: sqlite3.Connection) -> dict[str, object]:
    """The DnD contribution to the run manifest: the identifier-alias set by
    content hash, with its rows — provenance the database holds that the
    stages' run configurations only hash. No path is recorded
    (``imported_from`` is a file name)."""
    rows = [asdict(a) for a in list_aliases(conn)]
    return {
        "question_aliases": {
            "count": len(rows),
            "set_sha256": alias_set_sha256(conn),
            "rows": rows,
        }
    }
