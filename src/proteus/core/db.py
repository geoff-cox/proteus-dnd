"""
Database access for the PROTEUS pipeline.

Centralises the (very small) amount of low-level SQLite handling we need so
the rest of the package never has to think about pragmas, foreign keys,
transactions, or schema location.

Public surface:

    default_db_path()       The canonical default DB location (<repo>/data/proteus.db),
                            the single source of truth for both the CLI and the UI.
    connect(db_path)        Return a configured sqlite3.Connection.
    bootstrap(conn)         Apply the baseline schema (core + each registered
                            service's schema files) and run migrations.
    core_schema_sql()       The core slice of the frozen v3 baseline, as text.
    reset(db_path)          Delete the database file and re-bootstrap it.
    transaction(conn)       Context manager for an atomic block.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
from importlib import resources
from pathlib import Path
from typing import Iterator

log = logging.getLogger(__name__)


def default_db_path(*, create_parent: bool = False) -> Path:
    """
    The canonical default location for the working PROTEUS database:
    ``<repo>/data/proteus.db``, the ``data/`` directory of the checked-out
    repository, anchored to this file so it is independent of the current
    working directory. The file is git-ignored (``*.db``), so the working
    database never gets committed even though it lives inside the tree.

    This is the single source of truth for the default. Both the CLI
    (``proteus --db`` default) and the Streamlit app resolve their default from
    here, there is no second hand-rolled path computation (DB-1/DB-2).

    By default the function is **pure**: it computes the path without touching
    the filesystem, so it is safe to call for help text, display, or tests.
    Pass ``create_parent=True`` at the point where the default is resolved to
    actually open a file database; it then ensures ``<repo>/data`` exists
    (``mkdir parents=True, exist_ok=True``).

    Dir creation lives here (not in ``connect()``) on purpose: ``connect()`` is
    also called with ``:memory:`` and tmp_path databases, where a ``mkdir``
    would be wrong. A custom ``--db`` path never reaches this helper, so it
    never triggers dir creation either.
    """
    path = _repo_root() / "data" / "proteus.db"
    if create_parent:
        path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _repo_root() -> Path:
    """
    The checked-out repository root. This file is
    ``<repo>/src/proteus/core/db.py``, so the root is three levels above its
    directory (``core`` -> ``proteus`` -> ``src`` -> ``<repo>``). Factored out
    so tests can repoint the default DB at a tmp directory.
    """
    return Path(__file__).resolve().parents[3]


def core_schema_sql() -> str:
    """
    The core (service-agnostic) slice of the frozen version-3 baseline
    schema. Service-owned slices are supplied by each ServiceDescriptor's
    `schema_files` (item A3.5) and applied by `bootstrap` after this one.
    """
    return (
        resources.files("proteus.core").joinpath("schema/core.sql").read_text()
    )


def connect(db_path: str | os.PathLike) -> sqlite3.Connection:
    """
    Open a SQLite connection with PROTEUS conventions applied:

    - Foreign keys enforced (off by default in SQLite, which has bitten us once).
    - Row factory returns sqlite3.Row, so callers can index by column name.
    - Detect_types so TIMESTAMP/DATE columns round-trip as Python objects.

    The caller is responsible for closing the connection (or using it as a
    context manager).
    """
    conn = sqlite3.connect(
        str(db_path),
        detect_types=sqlite3.PARSE_DECLTYPES,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def bootstrap(conn: sqlite3.Connection) -> None:
    """
    Apply the baseline schema to a fresh database, then run any pending
    migrations so a freshly bootstrapped database is provably identical to an
    existing one upgraded by the migration runner (plan item A0.3).

    The baseline (frozen at schema version 3) is composed of the core schema
    plus each registered service's `schema_files`, discovered through the
    registry (item A3.5), together byte-identical DDL to the single
    schema.sql they replaced. The migration runner then applies migration
    files numbered above the baseline head, from core and from each service's
    `migration_dir`, so a new migration is picked up by both fresh bootstraps
    and in-place upgrades from the single source.

    A service whose import fails is skipped with a warning (the registry's
    failure isolation); its tables are simply absent, matching its disabled
    CLI namespace and pages.

    Not idempotent (the baseline uses plain CREATE TABLE); call on a fresh
    database.
    """
    # Lazy imports to avoid a module-load cycle (migrate/registry import
    # proteus, db is imported early); by bootstrap time both are loaded.
    from proteus.core import migrate, registry

    conn.executescript(core_schema_sql())
    for svc in registry.available_services():
        for schema_file in svc.schema_files:
            conn.executescript(Path(schema_file).read_text(encoding="utf-8"))
    conn.commit()

    migrate.migrate(conn, service_sources=registry.migration_sources())
    log.info("Bootstrapped schema into database")


def reset(db_path: str | os.PathLike) -> None:
    """
    Delete the database file (if it exists) and bootstrap a fresh one.

    Intended for interactive development, not production. There is no
    confirmation prompt; if you point this at a real database, it will
    delete it without asking. The CLI wrapper adds a confirmation step.
    """
    p = Path(db_path)
    if p.exists():
        p.unlink()
        log.info("Deleted existing database at %s", p)
    conn = connect(p)
    try:
        bootstrap(conn)
    finally:
        conn.close()


@contextlib.contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """
    Run a block inside a single transaction. Commits on success, rolls back
    on any exception (including KeyboardInterrupt).

    Usage:

        with transaction(conn):
            conn.execute(...)
            conn.execute(...)
    """
    try:
        yield conn
    except BaseException:
        conn.rollback()
        raise
    else:
        conn.commit()
