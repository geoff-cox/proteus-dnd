"""
DnD service cleanup: delete this service's derived rows for one import
(checklist item B4.14).

Deleting an import is a registry-driven cascade — core deletes ``events`` and
``raw_imports`` and never names a service table; each service exposes a handler
that removes *its* derived rows for the import and reports the blast radius. The
baseline FKs are not ``ON DELETE CASCADE`` (``dnd_events`` -> ``events`` is a
plain FK), so the cascade is done here, in dependency order.

Two layers are involved, scoped differently:

* ``dnd_events`` is the typed view of ``events`` and is keyed one-to-one to it,
  so it is deleted **per event** (exactly the import's events).
* the reconstruction/outcome layer (``dnd_attempts``, ``dnd_configurations``,
  ``dnd_segments``, ``dnd_check_scores``, ``dnd_attempt_metrics``) is
  **question-scoped** — B3 reconstructs and scores a whole question at a time
  and clears-and-rebuilds it on every run (CD-3). An import can only contribute whole questions to that
  layer, so removing an import clears the reconstruction of every question the
  import touched; the surviving events for those questions are rebuilt by a
  later ``dnd reconstruct`` / ``dnd score`` (or the Manage-imports re-derive).

``dry_run=True`` counts without deleting (for the confirmation dialog);
``dry_run=False`` deletes and returns the same per-table counts it removed.
"""

from __future__ import annotations

import sqlite3
from collections import OrderedDict

# The DnD questions this import contributed derived events to: distinct
# problem_name over the import's events that have a row in dnd_events.
_AFFECTED_QUESTIONS = (
    "SELECT DISTINCT e.problem_name FROM events e "
    "JOIN dnd_events de ON de.event_id = e.event_id "
    "WHERE e.import_id = ?"
)

# Every event captured for the import (the dnd_events delete scope).
_IMPORT_EVENTS = "SELECT event_id FROM events WHERE import_id = ?"

# Attempts of the affected questions (configs/metrics hang off these).
_AFFECTED_ATTEMPTS = (
    f"SELECT attempt_id FROM dnd_attempts WHERE question_id IN ({_AFFECTED_QUESTIONS})"
)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _operations(import_id: int) -> list[tuple[str, str, tuple[object, ...]]]:
    """``(table, where_clause, params)`` in child-first delete order.

    Children before parents so the deletes satisfy the plain (non-cascade) FKs;
    ``dnd_events`` is last so core can then delete ``events`` without a dangling
    reference.
    """
    return [
        ("dnd_check_scores", f"question_id IN ({_AFFECTED_QUESTIONS})", (import_id,)),
        ("dnd_attempt_metrics", f"attempt_id IN ({_AFFECTED_ATTEMPTS})", (import_id,)),
        ("dnd_segments", f"attempt_id IN ({_AFFECTED_ATTEMPTS})", (import_id,)),
        ("dnd_configurations", f"attempt_id IN ({_AFFECTED_ATTEMPTS})", (import_id,)),
        ("dnd_attempts", f"question_id IN ({_AFFECTED_QUESTIONS})", (import_id,)),
        ("dnd_events", f"event_id IN ({_IMPORT_EVENTS})", (import_id,)),
    ]


def delete_import_rows(
    conn: sqlite3.Connection, import_id: int, *, dry_run: bool = True
) -> "OrderedDict[str, int]":
    """Count (and, unless ``dry_run``, delete) this service's rows for an import.

    Returns an ordered ``{table: rows}`` map. On a database that predates the
    DnD migration (no ``dnd_events`` table) there is nothing to clean up, so an
    empty map is returned rather than raising — the service is simply absent.
    """
    if not _table_exists(conn, "dnd_events"):
        return OrderedDict()

    counts: "OrderedDict[str, int]" = OrderedDict()
    for table, where, params in _operations(import_id):
        # Count before deleting this table; child-first order means a parent's
        # count is never disturbed by an earlier child delete.
        n = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {where}", params
        ).fetchone()[0]
        counts[table] = n
        if not dry_run and n:
            conn.execute(f"DELETE FROM {table} WHERE {where}", params)
    return counts
