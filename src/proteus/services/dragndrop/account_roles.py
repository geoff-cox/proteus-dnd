"""
Account roles: the governance rulings on staff, researcher and test
accounts, applied at derive (E-02, PR 1 of 2; ``cc-task-attrition-ledger``
§4.1; owner rulings 2026-10-19 at the brief's Checkpoint 1).

The Paper 1 staff-account rulings name the platform accounts that are not
students — instructor self-accounts, researcher and test accounts — and,
for each, whether its rows are excluded from the analysis. The decision
(R1 (a)) is that the exclusion bites at **derive**: a row of an excluded
account never reaches ``dnd_events``, so no later stage, export or guard
needs a filter of its own, while core ``events`` keeps every row (rule 5 —
``events`` is the single source of truth; ``dnd_events`` is a re-derivable
projection). Page rows of an excluded account are withheld too (J1), and
the filter is global by id (J2): one ruling per platform id, whatever the
offering — the pseudonym is documentation.

The mechanism mirrors the identifier alias (dnd/0014, :mod:`aliases`) and
the logging regime (dnd/0015, :mod:`logging_regimes`):

* ``dnd_account_roles`` (migration dnd/0016) — one row per ruled platform id
  (``anon_student_id``, exactly as ``events.student_id`` carries it), with
  the role, the exclude flag, the offering pseudonym of the ruling, the
  rulings file's claim of the id's raw row count, who ruled and when, and
  the provenance of the import. A logical reference, no FK (R5): a ruling
  may precede the data it governs.
* **The order of operations** (R1 (a)): ``init`` → ``dnd roles import`` →
  ``ingest --derive``. The table must be loaded before the first derive,
  because derive is where the filter bites; validation at import is
  format-only, since no events exist yet when the file is loaded in order.
  The reverse order is refused (R7): :func:`assert_no_derived_rows` —
  derive's tripwire, run before any insert — and the importer both refuse
  an excluded id that already has ``dnd_events`` rows, naming the counts of
  ids and rows, the roles and the pseudonyms and the remedy (delete the
  import(s) and re-ingest, or a fresh ``init``), never an id.
* :func:`excluded_ids` — the reader derive uses; empty on a database that
  predates dnd/0016, so with an empty table every stage behaves exactly as
  before (the golden tests bootstrap fresh databases).
* :func:`raw_rows_mismatches` — the count of ruled ids whose ``raw_rows``
  claim disagrees with their ``events`` count; derive logs it as one
  warning, a count only.
* Provenance: :func:`roles_set_sha256` hashes the ruled set (id, role,
  exclude); derive records it in its F22 run configuration (stage version
  2) and the run manifest carries counts and hashes through the service
  descriptor (:func:`manifest_section`) — never a row, never an id (J4).

**The rulings file** (the committed format, R2; the shape of the owner's
file of 2026-10-01) is a CSV with ``#`` comment lines and the header::

    pseudonym,anon_student_id,raw_rows,role,exclude,ruled_by,ruled_on,note

``anon_student_id``, ``role`` and ``exclude`` are required, and an empty id
or an empty role is refused; ``exclude`` is yes/no (1/0 and true/false
accepted, case-insensitively; anything else is refused); ``raw_rows`` is an
integer or empty (anything else is refused); ``pseudonym``, ``ruled_by`` and
``ruled_on`` are stored as given; ``note`` is read and **never stored**
(R13). The role vocabulary (:data:`ROLE_VOCABULARY`) is documented, not
enforced. The writers refuse a database that predates dnd/0016 (run
``proteus migrate`` first); the readers answer empty on one.

**The surname rule (M6).** In the real file the ids embed surnames. Nothing
in this module writes an id value to a log line, an exception message, CLI
output or the manifest: refusals and summaries carry counts, roles,
pseudonyms, file names, row ordinals and hashes only. The one command that
takes an id, ``dnd roles remove``, echoes nothing back.
"""

from __future__ import annotations

import csv
import hashlib
import logging
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from proteus.core.db import transaction

log = logging.getLogger(__name__)

__all__ = [
    "FILE_COLUMNS",
    "REQUIRED_COLUMNS",
    "ROLE_VOCABULARY",
    "TABLE",
    "AccountRole",
    "ImportResult",
    "RoleError",
    "add_role",
    "assert_no_derived_rows",
    "counts_by_role",
    "derived_rows_of_excluded",
    "excluded_ids",
    "file_sha256",
    "import_roles",
    "list_roles",
    "load_role_map",
    "manifest_section",
    "raw_rows_mismatches",
    "read_roles_file",
    "remove_role",
    "roles_set_sha256",
]

TABLE = "dnd_account_roles"

# The rulings file's columns (a header row; '#' comment lines are skipped).
# `pseudonym` is a comment column for the reader of the file (J2); `note` is
# read and never stored (R13).
FILE_COLUMNS: tuple[str, ...] = (
    "pseudonym",
    "anon_student_id",
    "raw_rows",
    "role",
    "exclude",
    "ruled_by",
    "ruled_on",
    "note",
)
REQUIRED_COLUMNS: tuple[str, ...] = ("anon_student_id", "role", "exclude")

# The documented role vocabulary — not enforced: the column is free text.
ROLE_VOCABULARY: tuple[str, ...] = ("instructor", "researcher", "test", "student")

_EXCLUDE_TRUE = frozenset({"yes", "1", "true"})
_EXCLUDE_FALSE = frozenset({"no", "0", "false"})


class RoleError(ValueError):
    """A refused account-role row — a malformed file, an id already ruled
    otherwise, an excluded id that already has derived rows. The message
    never carries an id value (M6)."""


@dataclass(frozen=True)
class AccountRole:
    """One ``dnd_account_roles`` row."""

    anon_student_id: str
    role: str
    exclude: bool
    pseudonym: str | None = None
    raw_rows: int | None = None
    ruled_by: str | None = None
    ruled_on: str | None = None
    imported_from: str | None = None  # the rulings file's name, never a path
    imported_sha256: str | None = None


@dataclass(frozen=True)
class ImportResult:
    added: int
    unchanged: int
    file_sha256: str
    set_sha256: str | None
    rows: tuple[AccountRole, ...]  # the whole table after the import


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
def _table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (TABLE,),
    ).fetchone()
    return row is not None


def load_role_map(conn: sqlite3.Connection) -> dict[str, tuple[str, bool]]:
    """``anon_student_id → (role, exclude)`` for every row (empty on a
    database that predates dnd/0016)."""
    if not _table_exists(conn):
        return {}
    return {
        r["anon_student_id"]: (r["role"], bool(r["exclude"]))
        for r in conn.execute(f"SELECT anon_student_id, role, exclude FROM {TABLE}")
    }


def excluded_ids(conn: sqlite3.Connection) -> frozenset[str]:
    """The ids ruled ``exclude = 1`` — the one reader derive filters by.
    Empty on a database that predates dnd/0016, so an absent or empty table
    is a no-op."""
    if not _table_exists(conn):
        return frozenset()
    return frozenset(
        r["anon_student_id"]
        for r in conn.execute(
            f"SELECT anon_student_id FROM {TABLE} "
            "WHERE exclude = 1 AND anon_student_id IS NOT NULL"
        )
    )


def roles_set_sha256(conn: sqlite3.Connection) -> str | None:
    """SHA-256 over the sorted ``id<TAB>role<TAB>exclude`` lines
    (newline-joined; exclude as 0/1); None when the table is empty. The
    content hash derive records in its run configuration and the manifest
    carries — a hash of the ruled set, never an id."""
    lines = sorted(
        f"{sid}\t{role}\t{int(exclude)}"
        for sid, (role, exclude) in load_role_map(conn).items()
    )
    if not lines:
        return None
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def counts_by_role(conn: sqlite3.Connection) -> dict[str, int]:
    """``role → number of ruled ids``, in role order."""
    if not _table_exists(conn):
        return {}
    return {
        r["role"]: r["n"]
        for r in conn.execute(
            f"SELECT role, count(*) AS n FROM {TABLE} GROUP BY role ORDER BY role"
        )
    }


def _row_to_role(r: sqlite3.Row) -> AccountRole:
    return AccountRole(
        anon_student_id=r["anon_student_id"],
        role=r["role"],
        exclude=bool(r["exclude"]),
        pseudonym=r["pseudonym"],
        raw_rows=r["raw_rows"],
        ruled_by=r["ruled_by"],
        ruled_on=r["ruled_on"],
        imported_from=r["imported_from"],
        imported_sha256=r["imported_sha256"],
    )


def list_roles(conn: sqlite3.Connection) -> list[AccountRole]:
    """Every ruling, ordered by its printed, id-free columns (then file
    order), so that `dnd roles list` exposes neither an id value nor the
    lexical order of the ids (M6)."""
    if not _table_exists(conn):
        return []
    return [
        _row_to_role(r)
        for r in conn.execute(
            f"SELECT * FROM {TABLE} ORDER BY pseudonym, role, exclude, ruled_on, rowid"
        )
    ]


def _require_table(conn: sqlite3.Connection) -> None:
    """Writers refuse a database that predates dnd/0016 with a
    :class:`RoleError` naming the remedy, rather than a bare
    ``OperationalError`` from the INSERT or DELETE."""
    if not _table_exists(conn):
        raise RoleError(
            f"the {TABLE} table is missing: this database predates dnd/0016 — "
            "run `proteus migrate` first"
        )


# ---------------------------------------------------------------------------
# The tripwire (R7)
# ---------------------------------------------------------------------------
def derived_rows_of_excluded(conn: sqlite3.Connection) -> list[tuple[str, str | None, int]]:
    """
    One ``(role, pseudonym, n_rows)`` per excluded id that already has rows
    in ``dnd_events`` (``dnd_events JOIN events USING (event_id)``, grouped
    by id, for ids with ``exclude = 1``). Empty when the filter was applied
    in order. The id itself is not returned: the tuple is what a refusal may
    name.
    """
    if not _table_exists(conn):
        return []
    rows = conn.execute(
        "SELECT r.role AS role, r.pseudonym AS pseudonym, count(*) AS n "
        "FROM dnd_events d JOIN events e USING (event_id) "
        f"JOIN {TABLE} r ON r.anon_student_id = e.student_id "
        "WHERE r.exclude = 1 "
        "GROUP BY r.anon_student_id "
        "ORDER BY r.role, r.pseudonym, n, r.rowid"
    )
    return [(r["role"], r["pseudonym"], r["n"]) for r in rows]


def _r7_message(n_ids: int, n_rows: int, roles: list[str], pseudonyms: list[str]) -> str:
    """The R7 refusal: counts, roles and pseudonyms (sorted, deduplicated)
    and the remedy — never an id value."""
    return (
        f"{n_ids} excluded account(s) already have {n_rows} derived row(s) in "
        f"dnd_events (role(s): {', '.join(sorted(set(roles)))}; offering(s): "
        f"{', '.join(sorted(set(pseudonyms))) or 'not recorded'}). The "
        "governance filter bites at derive — the order is init, then "
        "`dnd roles import`, then `ingest --derive` — so those rows must be "
        "derived again under the ruling: delete the import(s) that carry them "
        "(`proteus delete-import`) and re-ingest, or start from a fresh "
        "`init`. No account id is printed."
    )


def assert_no_derived_rows(conn: sqlite3.Connection) -> None:
    """
    Refuse when an excluded id already has rows in ``dnd_events`` — the
    rulings were loaded after a derive (the reverse of R1 (a)'s order), so
    the projection holds rows the ruling withholds. :class:`RoleError` names
    the counts of ids and rows, the roles and the pseudonyms (sorted,
    deduplicated) and the remedy, never an id. derive runs this before any
    insert; a database in which the filter was applied in order passes.
    """
    rows = derived_rows_of_excluded(conn)
    if not rows:
        return
    raise RoleError(
        _r7_message(
            len(rows),
            sum(n for _, _, n in rows),
            [role for role, _, _ in rows],
            [pseudonym for _, pseudonym, _ in rows if pseudonym],
        )
    )


def raw_rows_mismatches(conn: sqlite3.Connection) -> int:
    """
    The number of ruled ids whose ``raw_rows`` claim is not NULL, that have
    at least one ``events`` row, and whose ``count(*)`` from ``events``
    differs from the claim. A count only: derive logs it as one warning.
    """
    if not _table_exists(conn):
        return 0
    row = conn.execute(
        "SELECT count(*) AS n FROM ("
        f"  SELECT r.anon_student_id FROM {TABLE} r "
        "  JOIN events e ON e.student_id = r.anon_student_id "
        "  WHERE r.raw_rows IS NOT NULL "
        "  GROUP BY r.anon_student_id, r.raw_rows "
        "  HAVING count(*) != r.raw_rows"
        ")"
    ).fetchone()
    return int(row["n"])


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


def _derived_rows_of(conn: sqlite3.Connection, anon_student_id: str) -> int:
    """How many ``dnd_events`` rows one id already has (R7 at import)."""
    row = conn.execute(
        "SELECT count(*) AS n FROM dnd_events d JOIN events e USING (event_id) "
        "WHERE e.student_id = ?",
        (anon_student_id,),
    ).fetchone()
    return int(row["n"])


def _yes_no(exclude: bool) -> str:
    return "yes" if exclude else "no"


@dataclass(frozen=True)
class _Plan:
    role: AccountRole
    action: str  # "added" | "unchanged"


def _plan(
    conn: sqlite3.Connection,
    anon_student_id: str,
    role: str,
    exclude: bool,
    *,
    pseudonym: str | None,
    raw_rows: int | None,
    ruled_by: str | None,
    ruled_on: str | None,
    imported_from: str | None,
    imported_sha256: str | None,
    pending: Mapping[str, tuple[str, bool]],
) -> _Plan:
    """
    Validate one row against the table as it stands plus the rows already
    accepted earlier in the same import (``pending``). Raises
    :class:`RoleError` for an empty id or role, an id already recorded with
    another role or exclude value (the same pair is "unchanged"), and an
    ``exclude = yes`` id that already has ``dnd_events`` rows (R7 at
    import; the message has the counts, the role and the pseudonym, never
    the id). Returns the row to write and whether it is new.
    """
    anon_student_id = (anon_student_id or "").strip()
    role = (role or "").strip()
    exclude = bool(exclude)
    if not anon_student_id:
        raise RoleError("an account-role row needs an id (anon_student_id is empty)")
    if not role:
        raise RoleError("an account-role row needs a role (role is empty)")
    current = load_role_map(conn)
    current.update(pending)
    existing = current.get(anon_student_id)
    if existing is not None:
        if existing == (role, exclude):
            return _Plan(AccountRole(anon_student_id, role, exclude), "unchanged")
        raise RoleError(
            f"the account is already ruled {existing[0]}, exclude={_yes_no(existing[1])}, "
            f"and the new row says {role}, exclude={_yes_no(exclude)}; `dnd roles "
            "remove` the recorded row before ruling it again (no id is printed)"
        )
    if exclude:
        n_rows = _derived_rows_of(conn, anon_student_id)
        if n_rows:
            raise RoleError(_r7_message(1, n_rows, [role], [pseudonym] if pseudonym else []))
    return _Plan(
        AccountRole(
            anon_student_id=anon_student_id,
            role=role,
            exclude=exclude,
            pseudonym=pseudonym or None,
            raw_rows=raw_rows,
            ruled_by=ruled_by or None,
            ruled_on=ruled_on or None,
            imported_from=imported_from,
            imported_sha256=imported_sha256,
        ),
        "added",
    )


def _apply(conn: sqlite3.Connection, plan: _Plan) -> None:
    if plan.action != "added":
        return
    a = plan.role
    conn.execute(
        f"INSERT INTO {TABLE} (anon_student_id, role, exclude, pseudonym, raw_rows, "
        "ruled_by, ruled_on, imported_from, imported_sha256) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            a.anon_student_id, a.role, 1 if a.exclude else 0, a.pseudonym,
            a.raw_rows, a.ruled_by, a.ruled_on, a.imported_from, a.imported_sha256,
        ),
    )
    log.info(
        "Account role recorded: %s, exclude=%s (offering %s)",
        a.role, _yes_no(a.exclude), a.pseudonym or "not recorded",
    )


def add_role(
    conn: sqlite3.Connection,
    anon_student_id: str,
    role: str,
    exclude: bool,
    *,
    pseudonym: str | None = None,
    raw_rows: int | None = None,
    ruled_by: str | None = None,
    ruled_on: str | None = None,
) -> str:
    """Record one ruling; returns ``"added"`` or ``"unchanged"`` (the same
    role and exclude value already recorded). Refusals raise
    :class:`RoleError` and write nothing. Validation and the write run under
    one write lock."""
    _require_table(conn)
    with _write_locked(conn):
        plan = _plan(
            conn, anon_student_id, role, exclude,
            pseudonym=pseudonym, raw_rows=raw_rows, ruled_by=ruled_by,
            ruled_on=ruled_on, imported_from=None, imported_sha256=None,
            pending={},
        )
        _apply(conn, plan)
    return plan.action


def remove_role(conn: sqlite3.Connection, anon_student_id: str) -> bool:
    """Remove one ruling; True when a row was deleted. Rows already withheld
    are not restored — re-run derive, which inserts them."""
    _require_table(conn)
    with transaction(conn):
        cur = conn.execute(
            f"DELETE FROM {TABLE} WHERE anon_student_id = ?",
            ((anon_student_id or "").strip(),),
        )
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# The rulings file
# ---------------------------------------------------------------------------
def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_exclude(raw: str | None, *, where: str) -> bool:
    token = (raw or "").strip().lower()
    if token in _EXCLUDE_TRUE:
        return True
    if token in _EXCLUDE_FALSE:
        return False
    raise RoleError(f"{where}: exclude must be yes or no (1/0 and true/false accepted)")


def _parse_raw_rows(raw: str | None, *, where: str) -> int | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        raise RoleError(f"{where}: raw_rows must be an integer or empty") from None


def read_roles_file(path: Path) -> list[dict[str, object]]:
    """
    The rows of a rulings file: a CSV with the :data:`FILE_COLUMNS` header
    (:data:`REQUIRED_COLUMNS` required, the rest optional); lines starting
    with ``#`` and blank lines are skipped. ``exclude`` reads as a bool
    (yes/no, 1/0, true/false, case-insensitively; anything else is refused),
    ``raw_rows`` as an int or None, every other cell as a stripped string or
    None when empty; ``note`` is discarded (R13). A refusal names the file
    and the data row's ordinal, never a cell. The CSV is parsed strictly
    (the second Copilot round on PR #238): an unterminated quote in a note
    would otherwise absorb every row after it — a kept row's note swallowing
    the excluded row behind it, the import succeeding with the kept row
    alone — and the ``#`` line filter can create that condition by dropping
    a multi-line note's continuation line; a malformed file is refused whole
    before anything is written, with the parser's own words and a row
    ordinal, never a cell.
    """
    name = Path(path).name
    lines = [
        line
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not lines:
        raise RoleError(f"{name}: no header row")
    reader = csv.DictReader(lines, strict=True)
    try:
        fields = tuple(reader.fieldnames or ())
    except csv.Error as exc:
        raise RoleError(f"{name}: the header is not well-formed CSV ({exc})") from None
    missing = [c for c in REQUIRED_COLUMNS if c not in fields]
    if missing:
        raise RoleError(
            f"{name}: header lacks {missing}; expected the columns "
            f"{list(FILE_COLUMNS)}"
        )
    rows: list[dict[str, object]] = []
    ordinal = 0
    try:
        for raw in reader:
            ordinal += 1
            where = f"{name}: data row {ordinal}"
            row: dict[str, object] = {
                c: ((raw.get(c) or "").strip() or None)
                for c in FILE_COLUMNS
                if c != "note"
            }
            row["exclude"] = _parse_exclude(raw.get("exclude"), where=where)
            row["raw_rows"] = _parse_raw_rows(raw.get("raw_rows"), where=where)
            rows.append(row)
    except csv.Error as exc:
        raise RoleError(
            f"{name}: not well-formed CSV after data row {ordinal} ({exc}); an "
            "unterminated quote in a note absorbs the rows after it — nothing imported"
        ) from None
    return rows


def import_roles(conn: sqlite3.Connection, path: Path) -> ImportResult:
    """
    Load a rulings file. Every row is validated before anything is written,
    and validation and writing share one write lock, so a refused row
    leaves the table untouched; a second import of the same file adds
    nothing and reports the same set hash (idempotent). The file's name and
    SHA-256 are recorded on each row it adds. Nothing logged names an id.
    """
    path = Path(path)
    digest = file_sha256(path)
    rows = read_roles_file(path)
    _require_table(conn)
    pending: dict[str, tuple[str, bool]] = {}
    plans: list[_Plan] = []
    with _write_locked(conn):
        for row in rows:
            plan = _plan(
                conn,
                str(row["anon_student_id"] or ""),
                str(row["role"] or ""),
                bool(row["exclude"]),
                pseudonym=row["pseudonym"],  # type: ignore[arg-type]
                raw_rows=row["raw_rows"],  # type: ignore[arg-type]
                ruled_by=row["ruled_by"],  # type: ignore[arg-type]
                ruled_on=row["ruled_on"],  # type: ignore[arg-type]
                imported_from=path.name,
                imported_sha256=digest,
                pending=pending,
            )
            if plan.action == "added":
                pending[plan.role.anon_student_id] = (plan.role.role, plan.role.exclude)
            plans.append(plan)
        for plan in plans:
            _apply(conn, plan)
    added = sum(1 for p in plans if p.action == "added")
    log.info(
        "Account-role import %s: %d added, %d unchanged (file sha256 %s)",
        path.name, added, len(plans) - added, digest[:12],
    )
    return ImportResult(
        added=added,
        unchanged=len(plans) - added,
        file_sha256=digest,
        set_sha256=roles_set_sha256(conn),
        rows=tuple(list_roles(conn)),
    )


# ---------------------------------------------------------------------------
# The manifest contribution (E-03)
# ---------------------------------------------------------------------------
def manifest_section(conn: sqlite3.Connection) -> dict[str, object]:
    """The DnD contribution to the run manifest: the account-role set by
    content hash with its counts (ruled, excluded, by role) and the rulings
    files it was loaded from (name and sha256). Counts and hashes only — no
    row, no id, no path (J4)."""
    rows = list_roles(conn)
    files = sorted(
        {
            (a.imported_from, a.imported_sha256)
            for a in rows
            if a.imported_from is not None or a.imported_sha256 is not None
        },
        key=lambda pair: (pair[0] or "", pair[1] or ""),
    )
    return {
        "account_roles": {
            "count": len(rows),
            "excluded": sum(1 for a in rows if a.exclude),
            "by_role": counts_by_role(conn),
            "set_sha256": roles_set_sha256(conn),
            "files": [{"name": name, "sha256": sha} for name, sha in files],
        }
    }
