"""
Import deletion and blast-radius reporting (checklist item B4.14).

Deleting an import is a cascade: the ``raw_imports`` row, its ``events``, and
**every** service's derived rows for those events. The baseline FKs are *not*
``ON DELETE CASCADE`` (verified: ``events`` -> ``raw_imports``, ``responses``
-> ``raw_imports``, ``dnd_events`` -> ``events`` are all plain references), so
the cascade is driven from the registry instead of relying on the database:
each ``ServiceDescriptor`` exposes ``delete_import_rows`` and core only ever
deletes its own tables (``events``, ``raw_imports``). Core never names a
service table — adding question type N+1 needs no edit here.

Two entry points share one shape (``BlastRadius``):

* :func:`blast_radius` — a dry run: count what *would* be deleted, per table,
  for the confirmation dialog. A service that errors while counting is
  contained (its failure is recorded, the rest still reports).
* :func:`delete_import` — the commit: delete everything in one transaction,
  service rows first (so no event is still referenced), then ``events``, then
  ``raw_imports``. A service error here aborts the whole delete (the
  transaction rolls back) rather than leaving a half-deleted import.
"""

from __future__ import annotations

import sqlite3
from collections import OrderedDict
from dataclasses import dataclass

from proteus.core import db, registry


@dataclass(frozen=True)
class ServiceBlast:
    """One service's contribution to an import's blast radius."""

    name: str
    title: str
    counts: "OrderedDict[str, int]"  # table -> rows (would be / were) deleted
    error: str | None = None  # contained failure while counting, or None

    @property
    def total(self) -> int:
        return sum(self.counts.values())


@dataclass(frozen=True)
class BlastRadius:
    """Everything an import delete touches, core plus each service."""

    import_id: int
    exists: bool
    core_counts: "OrderedDict[str, int]"  # {"events": n, "raw_imports": n}
    services: tuple[ServiceBlast, ...]

    @property
    def total(self) -> int:
        return sum(self.core_counts.values()) + sum(s.total for s in self.services)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _import_exists(conn: sqlite3.Connection, import_id: int) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM raw_imports WHERE import_id = ?", (import_id,)
        ).fetchone()
        is not None
    )


def _core_counts(conn: sqlite3.Connection, import_id: int) -> "OrderedDict[str, int]":
    """Core rows the delete removes: the import's events and the row itself."""
    counts: "OrderedDict[str, int]" = OrderedDict()
    if _table_exists(conn, "events"):
        counts["events"] = conn.execute(
            "SELECT COUNT(*) FROM events WHERE import_id = ?", (import_id,)
        ).fetchone()[0]
    counts["raw_imports"] = conn.execute(
        "SELECT COUNT(*) FROM raw_imports WHERE import_id = ?", (import_id,)
    ).fetchone()[0]
    return counts


def _service_blasts(
    conn: sqlite3.Connection, import_id: int, *, dry_run: bool, contain: bool
) -> tuple[ServiceBlast, ...]:
    """Run each service's delete handler.

    ``contain=True`` (dry run) records a failing service and keeps going so the
    dialog can still show the rest. ``contain=False`` (commit) lets the error
    propagate so the surrounding transaction rolls the whole delete back.
    """
    out: list[ServiceBlast] = []
    for svc in registry.available_services():
        handler = svc.delete_import_rows
        if handler is None:
            continue
        if contain:
            try:
                counts = handler(conn, import_id, dry_run=dry_run)
            except Exception as exc:  # noqa: BLE001 — isolation boundary
                out.append(
                    ServiceBlast(svc.name, svc.title, OrderedDict(), error=str(exc))
                )
                continue
        else:
            counts = handler(conn, import_id, dry_run=dry_run)
        out.append(ServiceBlast(svc.name, svc.title, OrderedDict(counts)))
    return tuple(out)


def blast_radius(conn: sqlite3.Connection, import_id: int) -> BlastRadius:
    """Count what deleting ``import_id`` would remove, per table, without
    touching anything. A service that fails to count is contained."""
    exists = _import_exists(conn, import_id)
    return BlastRadius(
        import_id=import_id,
        exists=exists,
        core_counts=_core_counts(conn, import_id),
        services=_service_blasts(conn, import_id, dry_run=True, contain=True),
    )


def delete_import(conn: sqlite3.Connection, import_id: int) -> BlastRadius:
    """Delete an import and every derived row for it, atomically.

    Returns the realized :class:`BlastRadius` (the rows actually removed). If
    the import does not exist, returns a non-``exists`` radius and deletes
    nothing. A service handler that raises aborts the whole delete.
    """
    if not _import_exists(conn, import_id):
        return BlastRadius(
            import_id=import_id,
            exists=False,
            core_counts=_core_counts(conn, import_id),
            services=(),
        )

    with db.transaction(conn):
        # Service rows first: once they're gone, no event is still referenced.
        services = _service_blasts(conn, import_id, dry_run=False, contain=False)
        # Count core rows before removing them, so the result reflects the
        # delete; events delete before raw_imports (events FK raw_imports).
        core_counts = _core_counts(conn, import_id)
        if "events" in core_counts:
            conn.execute("DELETE FROM events WHERE import_id = ?", (import_id,))
        conn.execute("DELETE FROM raw_imports WHERE import_id = ?", (import_id,))

    return BlastRadius(
        import_id=import_id,
        exists=True,
        core_counts=core_counts,
        services=services,
    )
