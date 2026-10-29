"""
Logging regimes: the calendar date from which an export's DnD component
logged moves (P1 amended 2026-10-05, option (ii′) "split by date, as a
cause"; ``cc-task-resumed-anchors``).

The replay-mismatch diagnosis (``paper1-replay-mismatch-diagnosis-2026-10-02
.md``, M-R 1) found that the three Fall 2025 ULA offerings logged no
``dragNdrop-drop`` or ``dragNdrop-reset`` row before the component release
of 2025-10-10: every check logged before each offering's first move row was
compared against a replay of ``s_0`` by construction. The decision records
the boundary as a **cause**, not a read-time filter, so the exclusion is
auditable from the archive: a check whose calendar date precedes its
offering's ``move_logging_from`` is stored not comparable with cause
``no_move_logging`` (``qc.CAUSE_NO_MOVE_LOGGING``), the first name in the
precedence order, and never compared.

The mechanism mirrors the identifier alias (dnd/0014, :mod:`aliases`):

* ``dnd_logging_regimes`` (migration dnd/0015) — one row per raw export,
  keyed by the export's sha256 (``raw_imports.file_hash``, the hash the run
  manifest freezes), with the ISO calendar date, a reason, the committed memo
  the date was read from and the provenance of the import. A logical
  reference: the importer refuses a sha256 that no import in the database
  carries (reporting only the count of known imports, never a hash or a
  path), so a row can never bind to the wrong file. Keys are hashes and
  dates — never a path, a pseudonym or a surname.
* :func:`regime_by_import` — the one reader reconstruct uses: ``import_id →
  move_logging_from`` for every import that has a row.
* :func:`assert_data_consistent` — the tripwire reconstruct runs first: for
  an import with a row, any move, takeback or reset row dated before
  ``move_logging_from`` contradicts the row (the memo's finding was "none"),
  and the pass refuses with the count of such rows — no timestamp printed.
  A regime row the data contradicts is a wrong freeze, not a soft
  condition. An import without a row is untouched.
* Provenance: :func:`regime_set_sha256` hashes the (sha256, date) pairs;
  reconstruct records it in its F22 run configuration beside the alias-set
  hash (the sensitivity harness checks it against the database it clones)
  and the run manifest carries the rows and the hash through the service
  descriptor (``manifest_section``). Score and analyze do not read the
  table and record nothing (ruling 2026-10-06).

The committed regime file ``logging_regimes/ula-proteus.csv`` is loaded by
``proteus dnd regime import`` (idempotent; the frozen-run procedure's Stage
2.3 names the command). With an empty regime table every stage behaves
exactly as before. Deleting an import leaves its row in place, as the alias
table is left: the row then binds to nothing until the export is re-imported.
"""

from __future__ import annotations

import csv
import hashlib
import logging
import re
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date as _date
from datetime import datetime, timezone
from pathlib import Path

from proteus.core.db import transaction

log = logging.getLogger(__name__)

__all__ = [
    "FILE_COLUMNS",
    "TABLE",
    "TRIPWIRE_KINDS",
    "Regime",
    "RegimeError",
    "ImportResult",
    "add_regime",
    "assert_data_consistent",
    "file_sha256",
    "import_regimes",
    "list_regimes",
    "load_regime_map",
    "manifest_section",
    "read_regime_file",
    "regime_by_import",
    "regime_set_sha256",
    "remove_regime",
]

TABLE = "dnd_logging_regimes"

# The regime file's columns (a header row; '#' comment lines are skipped).
# `pseudonym` is a comment column for the reader of the file: the importer
# ignores it — the table keys on the sha256 alone.
FILE_COLUMNS: tuple[str, ...] = (
    "file_sha256",
    "pseudonym",
    "move_logging_from",
    "reason",
    "source_memo",
    "recorded_on",
)

# The derived event kinds the tripwire tests against the date: a drop row
# derives to `move` or `takeback`, a reset row to `reset` (dragndrop/derive).
TRIPWIRE_KINDS: tuple[str, ...] = ("move", "takeback", "reset")

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class RegimeError(ValueError):
    """A refused regime row: a malformed key or date, an unknown export, a
    row already pointing elsewhere — or data that contradicts a row."""


@dataclass(frozen=True)
class Regime:
    """One ``dnd_logging_regimes`` row."""

    file_sha256: str
    move_logging_from: str
    reason: str | None = None
    source_memo: str | None = None
    recorded_on: str | None = None
    imported_from: str | None = None  # the regime file's name, never a path
    imported_sha256: str | None = None


@dataclass(frozen=True)
class ImportResult:
    added: int
    unchanged: int
    file_sha256: str
    set_sha256: str | None
    rows: tuple[Regime, ...]  # the whole table after the import


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
def _table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (TABLE,),
    ).fetchone()
    return row is not None


def load_regime_map(conn: sqlite3.Connection) -> dict[str, str]:
    """``file_sha256 → move_logging_from`` for every row (empty on a database
    that predates dnd/0015)."""
    if not _table_exists(conn):
        return {}
    return {
        r["file_sha256"]: r["move_logging_from"]
        for r in conn.execute(f"SELECT file_sha256, move_logging_from FROM {TABLE}")
    }


def regime_by_import(conn: sqlite3.Connection) -> dict[int, str]:
    """``import_id → move_logging_from`` for every import whose export has a
    regime row. The one reader reconstruct uses."""
    if not _table_exists(conn):
        return {}
    return {
        r["import_id"]: r["move_logging_from"]
        for r in conn.execute(
            f"SELECT i.import_id, g.move_logging_from FROM {TABLE} g "
            "JOIN raw_imports i ON i.file_hash = g.file_sha256"
        )
    }


def regime_set_sha256(conn: sqlite3.Connection) -> str | None:
    """SHA-256 over the sorted ``sha256 → date`` pairs (tab-separated,
    newline-joined); None when the table is empty. The content hash
    reconstruct records in its run configuration and the manifest carries."""
    pairs = sorted(load_regime_map(conn).items())
    if not pairs:
        return None
    text = "\n".join(f"{digest}\t{date}" for digest, date in pairs)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _row_to_regime(r: sqlite3.Row) -> Regime:
    return Regime(
        file_sha256=r["file_sha256"],
        move_logging_from=r["move_logging_from"],
        reason=r["reason"],
        source_memo=r["source_memo"],
        recorded_on=r["recorded_on"],
        imported_from=r["imported_from"],
        imported_sha256=r["imported_sha256"],
    )


def list_regimes(conn: sqlite3.Connection) -> list[Regime]:
    if not _table_exists(conn):
        return []
    return [
        _row_to_regime(r)
        for r in conn.execute(f"SELECT * FROM {TABLE} ORDER BY file_sha256")
    ]


# ---------------------------------------------------------------------------
# The tripwire
# ---------------------------------------------------------------------------
def assert_data_consistent(conn: sqlite3.Connection) -> dict[int, str]:
    """
    Refuse when the data contradicts a regime row: for every import with a
    row, count the derived move, takeback and reset rows whose calendar date
    (the first ten characters of the opaque timestamp, the memo's own cut)
    precedes ``move_logging_from``. Any such row means the export logged
    moves before the date the row freezes — a wrong row, not a soft
    condition — and :class:`RegimeError` names the count of rows and of
    imports, never a timestamp or a hash. Returns the ``import_id → date``
    map the caller then applies. An import without a row is untouched.
    """
    regimes = regime_by_import(conn)
    offending_rows = 0
    offending_imports = 0
    placeholders = ", ".join("?" * len(TRIPWIRE_KINDS))
    for import_id, date in sorted(regimes.items()):
        n = conn.execute(
            "SELECT count(*) AS n FROM dnd_events d JOIN events e USING (event_id) "
            f"WHERE e.import_id = ? AND d.kind IN ({placeholders}) "
            "AND e.timestamp IS NOT NULL AND substr(e.timestamp, 1, 10) < ?",
            (import_id, *TRIPWIRE_KINDS, date),
        ).fetchone()["n"]
        if n:
            offending_rows += n
            offending_imports += 1
    if offending_rows:
        raise RegimeError(
            f"logging regime contradicted by the data: {offending_rows} move/"
            f"takeback/reset row(s) in {offending_imports} import(s) precede "
            "the import's move_logging_from date; the regime row is wrong, "
            "not the data — `dnd regime remove` it or correct the committed "
            "file (no timestamp or hash is printed)"
        )
    return regimes


# ---------------------------------------------------------------------------
# Writing: validate first, then apply, under one write lock
# ---------------------------------------------------------------------------
@contextmanager
def _write_locked(conn: sqlite3.Connection) -> Iterator[None]:
    """``BEGIN IMMEDIATE`` before the validating reads, so two importers
    cannot both validate against the same table and commit contradictory
    rows; a caller already inside a transaction keeps its own."""
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


def _known_imports(conn: sqlite3.Connection) -> set[str]:
    return {r["file_hash"] for r in conn.execute("SELECT file_hash FROM raw_imports")}


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


@dataclass(frozen=True)
class _Plan:
    regime: Regime
    action: str  # "added" | "unchanged"


def _plan(
    conn: sqlite3.Connection,
    digest: str,
    date: str,
    *,
    reason: str | None,
    source_memo: str | None,
    recorded_on: str | None,
    imported_from: str | None,
    imported_sha256: str | None,
    pending: Mapping[str, str],
    known: set[str],
) -> _Plan:
    """
    Validate one row against the table as it stands plus the rows already
    accepted earlier in the same import (``pending``). Raises
    :class:`RegimeError` for a key that is not a 64-digit lowercase hex
    sha256, a date that is not ``YYYY-MM-DD``, a sha256 no import carries
    (the message gives the count of known imports, never a hash), or a row
    already recorded with another date. Returns the row to write and whether
    it is new.
    """
    digest = (digest or "").strip().lower()
    date = (date or "").strip()
    if not _SHA256_RE.match(digest):
        raise RegimeError("a regime row needs a 64-digit hex sha256 as its key")
    if not _DATE_RE.match(date):
        raise RegimeError(
            f"move_logging_from must be an ISO calendar date (YYYY-MM-DD), got {date!r}"
        )
    try:
        _date.fromisoformat(date)  # the shape is right; is the day real?
    except ValueError:
        raise RegimeError(
            f"move_logging_from must be a real calendar date, got {date!r}"
        ) from None
    current = load_regime_map(conn)
    current.update(pending)
    existing = current.get(digest)
    if existing is not None:
        if existing == date:
            return _Plan(Regime(digest, date), "unchanged")
        raise RegimeError(
            f"the export is already recorded with move_logging_from {existing}; "
            "remove that row before recording another date"
        )
    if digest not in known:
        raise RegimeError(
            "the sha256 matches none of the imported exports "
            f"({len(known)} known import(s)); import the export first, or "
            "check the key against the committed export inventory"
        )
    return _Plan(
        Regime(
            file_sha256=digest,
            move_logging_from=date,
            reason=reason or None,
            source_memo=source_memo or None,
            recorded_on=recorded_on or _today(),
            imported_from=imported_from,
            imported_sha256=imported_sha256,
        ),
        "added",
    )


def _apply(conn: sqlite3.Connection, plan: _Plan) -> None:
    if plan.action != "added":
        return
    g = plan.regime
    conn.execute(
        f"INSERT INTO {TABLE} (file_sha256, move_logging_from, reason, source_memo, "
        "recorded_on, imported_from, imported_sha256) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            g.file_sha256, g.move_logging_from, g.reason, g.source_memo,
            g.recorded_on, g.imported_from, g.imported_sha256,
        ),
    )
    log.info(
        "Logging regime recorded: export %s… logs moves from %s",
        g.file_sha256[:12], g.move_logging_from,
    )


def add_regime(
    conn: sqlite3.Connection,
    file_sha256: str,
    move_logging_from: str,
    *,
    reason: str | None = None,
    source_memo: str | None = None,
    recorded_on: str | None = None,
) -> str:
    """Record one regime row; returns ``"added"`` or ``"unchanged"`` (the same
    pair already recorded). Refusals raise :class:`RegimeError` and write
    nothing. Validation and the write run under one write lock."""
    with _write_locked(conn):
        plan = _plan(
            conn, file_sha256, move_logging_from,
            reason=reason, source_memo=source_memo, recorded_on=recorded_on,
            imported_from=None, imported_sha256=None, pending={},
            known=_known_imports(conn),
        )
        _apply(conn, plan)
    return plan.action


def remove_regime(conn: sqlite3.Connection, file_sha256: str) -> bool:
    """Remove one row; True when a row was deleted. Results already computed
    under it are left as they are — re-run reconstruct."""
    with transaction(conn):
        cur = conn.execute(
            f"DELETE FROM {TABLE} WHERE file_sha256 = ?",
            ((file_sha256 or "").strip().lower(),),
        )
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# The regime file
# ---------------------------------------------------------------------------
def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_regime_file(path: Path) -> list[dict[str, str | None]]:
    """The rows of a regime file: a CSV with the :data:`FILE_COLUMNS` header
    (``file_sha256`` and ``move_logging_from`` required, the rest optional;
    ``pseudonym`` is read and ignored); lines starting with ``#`` and blank
    lines are skipped. Empty cells read as None."""
    lines = [
        line
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not lines:
        raise RegimeError(f"{Path(path).name}: no header row")
    reader = csv.DictReader(lines)
    fields = tuple(reader.fieldnames or ())
    missing = [c for c in ("file_sha256", "move_logging_from") if c not in fields]
    if missing:
        raise RegimeError(
            f"{Path(path).name}: header lacks {missing}; expected the columns "
            f"{list(FILE_COLUMNS)}"
        )
    return [
        {c: ((raw.get(c) or "").strip() or None) for c in FILE_COLUMNS}
        for raw in reader
    ]


def import_regimes(conn: sqlite3.Connection, path: Path) -> ImportResult:
    """
    Load a regime file. Every row is validated before anything is written,
    and validation and writing share one write lock, so a refused row leaves
    the table untouched; a second import of the same file adds nothing and
    reports the same set hash (idempotent). The file's name and SHA-256 are
    recorded on each row it adds.
    """
    path = Path(path)
    digest = file_sha256(path)
    rows = read_regime_file(path)
    pending: dict[str, str] = {}
    plans: list[_Plan] = []
    with _write_locked(conn):
        known = _known_imports(conn)
        for row in rows:
            plan = _plan(
                conn, row["file_sha256"] or "", row["move_logging_from"] or "",
                reason=row["reason"], source_memo=row["source_memo"],
                recorded_on=row["recorded_on"],
                imported_from=path.name, imported_sha256=digest,
                pending=pending, known=known,
            )
            if plan.action == "added":
                pending[plan.regime.file_sha256] = plan.regime.move_logging_from
            plans.append(plan)
        for plan in plans:
            _apply(conn, plan)
    added = sum(1 for p in plans if p.action == "added")
    log.info(
        "Regime import %s: %d added, %d unchanged (file sha256 %s)",
        path.name, added, len(plans) - added, digest[:12],
    )
    return ImportResult(
        added=added,
        unchanged=len(plans) - added,
        file_sha256=digest,
        set_sha256=regime_set_sha256(conn),
        rows=tuple(list_regimes(conn)),
    )


# ---------------------------------------------------------------------------
# The manifest contribution (E-03)
# ---------------------------------------------------------------------------
def manifest_section(conn: sqlite3.Connection) -> dict[str, object]:
    """The DnD contribution to the run manifest: the logging-regime set by
    content hash, with its rows (hashes, dates, provenance — no path, no
    pseudonym)."""
    rows = [asdict(g) for g in list_regimes(conn)]
    return {
        "logging_regimes": {
            "count": len(rows),
            "set_sha256": regime_set_sha256(conn),
            "rows": rows,
        }
    }
