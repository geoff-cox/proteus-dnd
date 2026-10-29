"""
Forward-only schema migration runner (plan item A0.2).

A deliberately small, dependency-free runner — the plan (§6, R3) explicitly
rejects Alembic in favour of a ~100-line custom apply step. It exists because
migration ``0002`` was, until now, applied by hand (``sqlite3 db < file.sql``)
and Phases A and B both add schema that must land safely on existing
databases.

What it does
------------
* Reads the current schema version from the ``schema_version`` table.
* Discovers ``NNNN_description.sql`` files in the core migrations directory
  (and, optionally, in registered service migration directories).
* Applies the pending ones — those numbered above the current version — in
  ascending order, each inside its own transaction.
* Refuses to proceed on a malformed migration (missing transaction wrapper,
  or a core migration that does not record its own ``schema_version`` row).

Migration file contract
------------------------
Each core migration file MUST:

1. Be named ``NNNN_short_description.sql`` with a zero-padded integer prefix.
2. Wrap its body in a single ``BEGIN; … COMMIT;`` transaction. The runner
   relies on the file's own transaction for atomicity (SQLite's
   ``executescript`` cannot itself be wrapped without committing first), and
   validates the wrapper is present so a forgotten one fails loudly instead
   of applying non-atomically.
3. End by recording its version, e.g.
   ``INSERT INTO schema_version (version, description) VALUES (4, '…');``

This mirrors the existing ``0002_add_textbooks_questions.sql``.

Two version axes
----------------
Core migrations are tracked by the long-standing ``schema_version`` table.
Service migrations (added in Phase B) live in service-owned directories with
their own independent ``NNNN`` numbering, so they cannot share that single
integer. The runner tracks each service source in an ``applied_migrations``
ledger keyed by ``(source, version)``.

Service sources are supplied by the registry: ``proteus migrate`` passes
``core.registry.migration_sources()`` (each available service's
``migration_dir``), applied in ``(core, service)`` order and tracked in the
``applied_migrations`` ledger.

``db.bootstrap`` applies the same service sources after the baseline schema,
so fresh-bootstrap parity extends to service tables from the moment a
service's first migration lands (B1.1).
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import proteus

log = logging.getLogger(__name__)

# The core migrations directory. Kept at its historical location
# (proteus/migrations) for now; the plan moves it under core/ in a later
# session, at which point this constant is the single thing to update.
CORE_MIGRATIONS_DIR: Path = Path(proteus.__file__).resolve().parent / "migrations"

CORE_SOURCE = "core"

# NNNN_description.sql — a four-digit zero-padded version, an underscore, a
# description, then .sql. Exactly four digits matches the documented contract
# and every migration in the tree; a non-padded name like '1_init.sql' does
# not match and is ignored rather than silently treated as a migration.
_MIGRATION_RE = re.compile(r"^(\d{4})_.+\.sql$")

# Strip SQL line comments (-- …) so the transaction-wrapper check sees only
# executable text.
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")


class MigrationError(RuntimeError):
    """Raised when a migration is malformed or fails to apply."""


@dataclass(frozen=True)
class Migration:
    """One discovered migration file."""

    version: int
    name: str  # filename stem, e.g. '0002_add_textbooks_questions'
    path: Path
    source: str = CORE_SOURCE


@dataclass(frozen=True)
class MigrationResult:
    """Outcome of a `migrate` call."""

    applied: tuple[Migration, ...]
    pending: tuple[Migration, ...]
    final_version: int
    dry_run: bool


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def discover_migrations(
    migrations_dir: Path = CORE_MIGRATIONS_DIR,
    source: str = CORE_SOURCE,
) -> list[Migration]:
    """
    Return the migrations in `migrations_dir`, ascending by version.

    Files that do not match ``NNNN_*.sql`` are ignored (e.g. a README).
    Raises MigrationError if two files in the directory claim the same
    version — an ambiguity the runner must not guess past.
    """
    directory = Path(migrations_dir)
    if not directory.is_dir():
        return []

    found: dict[int, Migration] = {}
    for path in sorted(directory.iterdir()):
        if not path.is_file():
            continue
        m = _MIGRATION_RE.match(path.name)
        if not m:
            continue
        version = int(m.group(1))
        if version in found:
            raise MigrationError(
                f"duplicate migration version {version} in {directory}: "
                f"{found[version].path.name} and {path.name}"
            )
        found[version] = Migration(
            version=version, name=path.stem, path=path, source=source
        )
    return [found[v] for v in sorted(found)]


# ---------------------------------------------------------------------------
# Version reads
# ---------------------------------------------------------------------------


def current_version(conn: sqlite3.Connection) -> int:
    """
    The highest applied core schema version, or 0 if the schema_version
    table is missing or empty.
    """
    if not _table_exists(conn, "schema_version"):
        return 0
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def _service_version(conn: sqlite3.Connection, source: str) -> int:
    if not _table_exists(conn, "applied_migrations"):
        return 0
    row = conn.execute(
        "SELECT MAX(version) FROM applied_migrations WHERE source = ?",
        (source,),
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def pending_migrations(
    conn: sqlite3.Connection,
    migrations_dir: Path = CORE_MIGRATIONS_DIR,
) -> list[Migration]:
    """Core migrations numbered above the current core version."""
    cur = current_version(conn)
    return [m for m in discover_migrations(migrations_dir) if m.version > cur]


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def migrate(
    conn: sqlite3.Connection,
    migrations_dir: Path = CORE_MIGRATIONS_DIR,
    *,
    service_sources: Sequence[tuple[str, Path]] = (),
    dry_run: bool = False,
) -> MigrationResult:
    """
    Apply all pending migrations and return a MigrationResult.

    Core migrations (from `migrations_dir`) are applied first, ascending by
    version, then each service source in `service_sources` order. With
    `dry_run=True` nothing is executed; the result lists what *would* run.

    Each migration runs inside its own transaction (the file's own
    ``BEGIN/COMMIT``); a failure rolls that file back and aborts the run, so
    the database is never left at a half-applied version.
    """
    plan: list[Migration] = list(pending_migrations(conn, migrations_dir))
    for source_name, source_dir in service_sources:
        svc_cur = _service_version(conn, source_name)
        plan.extend(
            m
            for m in discover_migrations(source_dir, source=source_name)
            if m.version > svc_cur
        )

    if dry_run:
        for m in plan:
            log.info("Would apply migration %s (%s)", m.name, m.source)
        return MigrationResult(
            applied=(),
            pending=tuple(plan),
            final_version=current_version(conn),
            dry_run=True,
        )

    applied: list[Migration] = []
    for m in plan:
        _apply_one(conn, m)
        applied.append(m)
        log.info("Applied migration %s (%s)", m.name, m.source)

    return MigrationResult(
        applied=tuple(applied),
        pending=(),
        final_version=current_version(conn),
        dry_run=False,
    )


def _apply_one(conn: sqlite3.Connection, m: Migration) -> None:
    sql = m.path.read_text(encoding="utf-8")
    _require_transaction_wrapper(m, sql)
    if m.source == CORE_SOURCE:
        # Validate the schema_version record is present in the file *before*
        # running any DDL. The file owns its BEGIN/COMMIT, so once executescript
        # commits, a missing version row can no longer be rolled back — the
        # schema would be changed while the runner raises, breaking the
        # no-half-applied guarantee. Checking the text first keeps the reject
        # path side-effect-free.
        _require_version_record(m, sql)

    # A rebuild-style migration (0009, 0015) toggles the connection-level
    # foreign_keys PRAGMA OFF outside its BEGIN/COMMIT (the PRAGMA is a no-op
    # inside a transaction). A rollback restores the data but not connection
    # state, so on failure the file's closing PRAGMA never runs and the
    # connection would keep enforcement off — silently violating the
    # db.connect guarantee for any caller that catches MigrationError.
    # Snapshot the value and restore it either way.
    prior_fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    try:
        conn.executescript(sql)
    except Exception as exc:  # noqa: BLE001 - re-raised as MigrationError
        # The file's BEGIN left a transaction open on failure; undo any
        # partial work so the database stays at the prior version.
        conn.rollback()
        raise MigrationError(f"migration {m.name} failed to apply: {exc}") from exc
    finally:
        # Runs in autocommit on both paths (executescript committed, or the
        # rollback above ran), so the PRAGMA takes effect.
        conn.execute(f"PRAGMA foreign_keys={'ON' if prior_fk else 'OFF'}")

    if m.source == CORE_SOURCE:
        # Belt-and-suspenders: the pre-execution check above already proved the
        # file records this version, so this should be unreachable. The file
        # has COMMITted, so there is nothing left to roll back; surface any
        # inconsistency loudly rather than trust it.
        applied_version = current_version(conn)
        if applied_version != m.version:
            raise MigrationError(
                f"migration {m.name} recorded schema_version {applied_version}, "
                f"expected {m.version}"
            )
    else:
        # Service migrations are recorded by the runner in the ledger.
        _record_service_migration(conn, m)


def _record_service_migration(conn: sqlite3.Connection, m: Migration) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS applied_migrations ("
        "  source      TEXT NOT NULL,"
        "  version     INTEGER NOT NULL,"
        "  name        TEXT NOT NULL,"
        "  applied_at  TEXT NOT NULL DEFAULT (datetime('now')),"
        "  PRIMARY KEY (source, version)"
        ")"
    )
    conn.execute(
        "INSERT INTO applied_migrations (source, version, name) VALUES (?, ?, ?)",
        (m.source, m.version, m.name),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_transaction_wrapper(m: Migration, sql: str) -> None:
    """
    Enforce the migration-file contract that the body is wrapped in a single
    BEGIN … COMMIT. SQLite's executescript cannot be wrapped by the caller
    without first committing, so atomicity is the file's responsibility; this
    turns a forgotten wrapper into a clear error rather than a silent
    non-atomic apply.
    """
    stripped = _LINE_COMMENT_RE.sub("", sql)
    has_begin = re.search(r"(?im)^\s*BEGIN\b", stripped) is not None
    has_commit = re.search(r"(?im)^\s*COMMIT\b", stripped) is not None
    if not (has_begin and has_commit):
        raise MigrationError(
            f"migration {m.name} must wrap its body in BEGIN; … COMMIT; "
            f"(found BEGIN={has_begin}, COMMIT={has_commit})"
        )


def _require_version_record(m: Migration, sql: str) -> None:
    """
    Enforce that a core migration records its own version in the file: an
    ``INSERT INTO schema_version`` whose first value is the migration's
    version (the conventional ``(version, description) VALUES (N, '…')`` form,
    as in 0002).

    Validated against the SQL text *before* execution so a migration that
    forgets it is rejected without ever committing its DDL — the file's own
    COMMIT would otherwise persist schema changes while the runner raised.
    """
    stripped = _LINE_COMMENT_RE.sub("", sql)
    pattern = re.compile(
        r"INSERT\s+INTO\s+schema_version\b.*?VALUES\s*\(\s*" + str(m.version) + r"\b",
        re.IGNORECASE | re.DOTALL,
    )
    if pattern.search(stripped) is None:
        raise MigrationError(
            f"migration {m.name} must record its version with "
            f"INSERT INTO schema_version (version, description) "
            f"VALUES ({m.version}, '…'); none found"
        )


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None
