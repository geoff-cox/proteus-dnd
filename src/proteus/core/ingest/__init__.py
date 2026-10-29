"""
Shared event ingest (Stage 0): parse a file once into the generic ``events``
table so any question-type service can derive from it (plan §2.3).

Two entry points:

* ``ingest_file`` — a not-yet-imported file: register the ``raw_imports`` row
  and write every parsed row to ``events``. Refuses a file already imported
  (UNIQUE file_hash) — re-running is handled at the event level, not by
  re-registering.
* ``backfill_events`` — an already-imported file (its ``raw_imports`` row
  exists, typically from the legacy SA ingest): write its events under the
  existing import_id, touching nothing else. This is what
  ``proteus reingest-events`` runs (item A1.5); re-running it is idempotent
  (duplicate events are skipped).

Neither writes ``responses``: short-answer responses are produced by the SA
service's derive step (A3.2). The legacy SA ingest (``proteus.ingest``) still
writes ``responses`` directly for the pre-A4 Streamlit UI; the two coexist
behind the UNIQUE file_hash and the shared response_id identity.
"""

from __future__ import annotations

import logging
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from proteus.core.ingest import events as events_mod
from proteus.core.ingest.imports import file_hash, register_import
from proteus.core.ingest.readers import datashop, runestone

log = logging.getLogger(__name__)

__all__ = ["ingest_file", "backfill_events", "IngestResult"]

_READERS = {
    datashop.FORMAT_NAME: datashop,
    runestone.FORMAT_NAME: runestone,
}


@dataclass(frozen=True)
class IngestResult:
    """Summary of one event-ingest run."""

    import_id: int
    file_path: Path
    file_format: str
    inserted: int
    skipped: int
    total_seen: int
    per_selection: dict[str, int]


def _detect_format(path: Path) -> str:
    if path.suffix.lower() == ".tab":
        return datashop.FORMAT_NAME
    if path.suffix.lower() == ".csv":
        return runestone.FORMAT_NAME
    raise ValueError(
        f"Cannot auto-detect format for {path.name!r}; pass file_format explicitly"
    )


def _resolve_reader(file_format: str):
    reader = _READERS.get(file_format)
    if reader is None:
        raise ValueError(
            f"Unknown file_format {file_format!r}; known: {sorted(_READERS)}"
        )
    return reader


def _sync_catalog(conn: sqlite3.Connection) -> None:
    """
    Reconcile the question catalog after writing events (item CD-2): every
    distinct non-page problem becomes a ``questions`` row. Imported lazily so
    this module carries no registry import at load time, and run as a side
    effect — the IngestResult stays event-focused; ``proteus sync-catalog``
    reports catalog counts.
    """
    from proteus.core import catalog

    catalog.sync_catalog(conn)


def ingest_file(
    conn: sqlite3.Connection,
    file_path: str | Path,
    file_format: str | None = None,
    notes: str | None = None,
) -> IngestResult:
    """
    Ingest a not-yet-imported file's every row into ``events``.

    Registers the ``raw_imports`` row and returns an ``IngestResult`` with
    per-selection counts. Raises FileExistsError if the exact file content was
    already imported (use ``backfill_events`` to add events to an existing
    import).
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(path)
    if file_format is None:
        file_format = _detect_format(path)
    reader = _resolve_reader(file_format)

    fhash = file_hash(path)
    existing = conn.execute(
        "SELECT import_id FROM raw_imports WHERE file_hash = ?", (fhash,)
    ).fetchone()
    if existing is not None:
        raise FileExistsError(
            f"{path}: file with this exact content already imported as "
            f"import_id={existing['import_id']}; use reingest-events to add events"
        )

    parsed = list(reader.read(path))
    per_selection = dict(Counter(e.selection for e in parsed))
    log.info("Parsed %d events from %s (format=%s)", len(parsed), path.name, file_format)

    import_id = register_import(
        conn,
        file_path=path,
        file_format=file_format,
        row_count=len(parsed),
        notes=notes,
    )
    inserted, skipped = events_mod.write_events(conn, import_id, parsed)
    _sync_catalog(conn)
    return IngestResult(
        import_id=import_id,
        file_path=path,
        file_format=file_format,
        inserted=inserted,
        skipped=skipped,
        total_seen=len(parsed),
        per_selection=per_selection,
    )


def backfill_events(
    conn: sqlite3.Connection,
    file_path: str | Path,
    file_format: str | None = None,
) -> IngestResult:
    """
    Write events for a file that has *already* been imported (item A1.5).

    Looks up the existing ``raw_imports`` row by file hash and writes the
    parsed events under that import_id without touching ``responses``. The
    original export file is required (events are reconstructed from it).
    Idempotent: events already present are skipped.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(path)
    if file_format is None:
        file_format = _detect_format(path)
    reader = _resolve_reader(file_format)

    fhash = file_hash(path)
    row = conn.execute(
        "SELECT import_id FROM raw_imports WHERE file_hash = ?", (fhash,)
    ).fetchone()
    if row is None:
        raise ValueError(
            f"{path}: not yet imported; run `proteus ingest {path.name}` first, "
            f"then reingest-events to backfill the events table"
        )
    import_id = row["import_id"]

    parsed = list(reader.read(path))
    per_selection = dict(Counter(e.selection for e in parsed))
    inserted, skipped = events_mod.write_events(conn, import_id, parsed)
    # CD-1: backfill the filter columns on rows ingested before migration 0008
    # (write_events skipped them as duplicates, leaving the new columns NULL).
    events_mod.backfill_filter_columns(conn, import_id, parsed)
    _sync_catalog(conn)
    return IngestResult(
        import_id=import_id,
        file_path=path,
        file_format=file_format,
        inserted=inserted,
        skipped=skipped,
        total_seen=len(parsed),
        per_selection=per_selection,
    )
