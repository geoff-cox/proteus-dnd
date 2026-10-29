"""
The DnD service descriptor (plan §2.2/§2.5; scaffold of Phase B).

Discovered only via proteus.core.registry. The descriptor grows field by
field as the B checklist lands: the schema (B1.1) arrives as this service's
migration 0001, derive with B1.2/B1.3, the board source handler with B2.2.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from proteus.core.registry import Analysis, ServiceDescriptor
from proteus.services.dragndrop import (
    account_roles,
    aliases,
    board,
    cleanup,
    logging_regimes,
)
from proteus.services.dragndrop import cli as dnd_cli
from proteus.services.dragndrop import derive as derive_mod
from proteus.services.dragndrop import export as export_mod

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def manifest_section(conn: sqlite3.Connection) -> dict[str, object]:
    """The DnD contribution to the run manifest (E-03): the identifier-alias
    set (dnd/0014) and the logging-regime set (dnd/0015), each by content
    hash with its rows, and the account-role set (dnd/0016) by content hash
    with counts and file hashes only — never a row, never an id (J4). Three
    keys under ``services.dnd``; the manifest version stays at 2 (ruling
    2026-10-06 — the version tracks the structure, and a further key inside
    a service's section is content)."""
    return {
        **aliases.manifest_section(conn),
        **logging_regimes.manifest_section(conn),
        **account_roles.manifest_section(conn),
    }


def status(conn: sqlite3.Connection) -> dict[str, object]:
    """
    The DnD contribution to `proteus status` (item A2.4 pattern): derived
    event counts by kind, parse failures, and registered boards. Degrades to
    a hint on a database that predates the dnd migration.
    """
    if not _table_exists(conn, "dnd_events"):
        return {"dnd": "— (dnd tables missing; run `proteus migrate`)"}

    by_kind = {
        row["kind"]: row["n"]
        for row in conn.execute(
            "SELECT kind, count(*) AS n FROM dnd_events GROUP BY kind"
        )
    }
    total = sum(by_kind.values())
    bits = ", ".join(f"{kind}={n}" for kind, n in sorted(by_kind.items()))
    parse_failures = conn.execute(
        "SELECT count(*) AS n FROM dnd_events WHERE parse_ok = 0"
    ).fetchone()["n"]
    boards = conn.execute("SELECT count(*) AS n FROM dnd_boards").fetchone()["n"]

    contribution: dict[str, object] = {
        "events": f"{total} ({bits})" if by_kind else "0",
        "boards": boards,
    }
    if _table_exists(conn, account_roles.TABLE):
        # The staff-account rulings (dnd/0016): counts only, never an id.
        ruled = account_roles.load_role_map(conn)
        excluded = sum(1 for _, is_excluded in ruled.values() if is_excluded)
        contribution["roles"] = f"{excluded} excluded of {len(ruled)} ruled"
    if parse_failures:
        contribution["parse fail"] = parse_failures
    return contribution


DESCRIPTOR = ServiceDescriptor(
    name="dnd",
    title="Drag-and-Drop",
    # The Selection values this service claims. `page` rows are *also*
    # consumed by derive (they delimit attempts, B3.1) but are a shared
    # selection, not a claimed one.
    selection_values=frozenset({"dragNdrop", "dragNdrop-drop", "dragNdrop-reset"}),
    register_cli=dnd_cli.register_cli,
    status=status,
    migration_dir=MIGRATIONS_DIR,
    derive=derive_mod.derive_events,
    question_types=frozenset({"cardsort"}),
    source_handler=board.sync_board_for_question,
    # Every dataset of the export registry: the eight SQL-backed stage views
    # and, since E-02 PR 2, the attrition ledger's three Python-assembled
    # datasets (`export.build_rows`: flow, readiness_split, exclusion_probe).
    # The descriptor publishes what `dnd export` offers, as it has since F3;
    # the pin in test_dnd_export.py grows with the registry, as it did at
    # F3, PR #151 and F20.
    export_datasets=dict(export_mod.DATASETS),
    delete_import_rows=cleanup.delete_import_rows,
    # The identifier-alias set (dnd/0014) and the logging-regime set
    # (dnd/0015), each by content hash, for the run manifest (E-03).
    manifest=manifest_section,
    # The ordered front-door surface for this type (item B4.12): the type
    # landing and question selector, then the two analyses — attempts, then
    # scoring/export. The shell renders these in order, naming no service.
    analyses=(
        Analysis("overview", "Overview", "🧩", "pages/dnd/Overview.py"),
        Analysis("questions", "Questions", "🗂️", "pages/dnd/Questions.py"),
        Analysis("attempts", "Attempts", "🎬", "pages/dnd/Attempts.py"),
        Analysis("scoring", "Scoring / Export", "🧮", "pages/dnd/Scoring.py"),
        # The deepest, most-derived surface (EX-6): a filter-aware cohort
        # visualization gallery; placed last in the analyses tuple.
        Analysis("explore", "Explore", "📊", "pages/dnd/Explore.py"),
    ),
    # DnD rows carry every shared filter dimension (course/class/week/date on
    # `events`); the row filter renders exactly these.
    filter_dimensions=("course", "class", "week", "date"),
)
