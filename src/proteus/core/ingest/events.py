"""
The generic event model: one logged DataShop action, its content-derived
identity, and the DB write path (plan §2.3).

An ``Event`` is the post-parse representation of a single row of an export.
Parsers (``core.ingest.readers.*``) produce ``Event`` objects; all database
writes happen here, so the readers stay pure. Event identity is a hash of
``student | problem | timestamp | selection | problem_view`` — enough to make
re-ingesting the same file idempotent (a duplicate is a PK conflict, skipped),
exactly as ``responses.response_id`` does for SA ingest.

Field normalization is deliberately minimal: identity/key fields are stripped
of surrounding whitespace; ``action`` / ``input`` / ``feedback_text`` are kept
verbatim so each service's derive step can normalize them as it needs (SA
applies ``normalize_text``; never normalize here). Timestamps are stored as
opaque strings and parsed downstream.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from dataclasses import dataclass
from typing import Iterable

from proteus.core.db import transaction

log = logging.getLogger(__name__)

# Truncated SHA-256 length for event_id; matches the response_id convention.
_ID_LENGTH = 16

# Structured SQLite error names (Python >=3.11) for the constraint conflicts
# that mean "this exact event is already stored" — the idempotent skip. Using
# the error name rather than parsing the message text keeps dedup detection
# stable across SQLite/Python builds and avoids swallowing other integrity
# errors (e.g. a bad import_id foreign key).
_DEDUP_ERROR_NAMES = frozenset(
    {"SQLITE_CONSTRAINT_PRIMARYKEY", "SQLITE_CONSTRAINT_UNIQUE"}
)


@dataclass(frozen=True)
class Event:
    """One logged action from an export, normalized just enough to persist."""

    student_id: str
    session_id: str
    problem_name: str
    selection: str
    action: str
    input: str
    feedback_text: str
    feedback_classification: str
    problem_view: str
    timestamp: str
    course_id: str | None = None
    # CD-1 filter dimensions: a read/view bound for the front door, never part
    # of event identity (see event_id) so they cannot perturb the SA golden.
    # Populated by the DataShop reader; absent in formats that don't carry them
    # (Runestone useinfo), where they stay None.
    class_id: str | None = None
    week_no: str | None = None
    chapter: str | None = None
    subchapter: str | None = None

    @property
    def event_id(self) -> str:
        """
        Deterministic content hash of the event's identity fields. Two ingests
        of the same logged row produce the same event_id, which is what makes
        re-ingest idempotent. The CD-1 filter columns are deliberately excluded
        so adding them leaves every event_id — and the SA derive — unchanged.

        Identity contract (A5/BUG-05 owner decision, 2026-08-23): identity is
        exactly ``student|problem|timestamp|selection|problem_view`` —
        ``action``/``input`` are payload, not identity. The accepted
        consequence: two *distinct* logged actions tying on all five fields
        collide, and the second row is dropped. The standing tripwire is the
        FIX-01 warning below, and its scope is per ``write_events`` call: it
        fires whenever one batch carries two distinct payloads under an id
        (including re-ingest of a collision-bearing file, where both rows
        conflict with the stored one); colliding rows arriving in *separate*
        imports dedup silently — a disclosed limit, tolerable because
        same-instant actions travel in one logging stream and so co-travel
        in a full export. Accepted because zero collisions exist in the real
        32,721-row export and widening identity would change every future
        event_id and the cross-import dedup semantics. Revisit if (a) the
        warning ever fires
        on a real ingest, or (b) the Runestone useinfo reader goes live — its
        rows carry empty ``problem_view``/``session_id``, widening exposure;
        fold its stable ``id`` column into identity then, behind a migration,
        with the golden tests proving the SA derive is unaffected
        (``response_id`` is independent of ``event_id``). See
        dev/decisions/event-identity-contract.md.
        """
        key = (
            f"{self.student_id}|{self.problem_name}|{self.timestamp}"
            f"|{self.selection}|{self.problem_view}"
        )
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:_ID_LENGTH]


def write_events(
    conn: sqlite3.Connection,
    import_id: int,
    events: Iterable[Event],
) -> tuple[int, int]:
    """
    Insert events for one import. Returns (inserted, skipped).

    A duplicate event_id (the same logged row, re-ingested) is skipped, not
    re-inserted — the idempotency mechanism. The whole batch runs in one
    transaction; an unrecognised error rolls everything back, including any
    raw_imports row registered in the same connection-level transaction.
    """
    inserted = 0
    skipped = 0
    # Events seen by THIS call (first insert or first skip per id), so a skip
    # can be attributed: one conflict with a row from a previous call is the
    # normal re-ingest idempotency; a SECOND occurrence of the same id within
    # this batch is an intra-file identity collision (two logged rows sharing
    # student|problem|timestamp|selection|problem_view). When the colliding
    # rows' payloads differ, a real logged action is being dropped — warn
    # loudly instead of letting it hide in the duplicate count. Recording the
    # first *skip* too keeps the warning alive on a re-ingest of a
    # collision-bearing file, where both rows conflict with the stored one.
    seen_this_batch: dict[str, Event] = {}
    with transaction(conn):
        for e in events:
            try:
                conn.execute(
                    "INSERT INTO events (event_id, import_id, student_id, "
                    "session_id, problem_name, selection, action, input, "
                    "feedback_text, feedback_classification, problem_view, "
                    "timestamp, course_id, class_id, week_no, chapter, "
                    "subchapter) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        e.event_id,
                        import_id,
                        e.student_id,
                        e.session_id,
                        e.problem_name,
                        e.selection,
                        e.action,
                        e.input,
                        e.feedback_text,
                        e.feedback_classification,
                        e.problem_view,
                        e.timestamp,
                        e.course_id,
                        e.class_id,
                        e.week_no,
                        e.chapter,
                        e.subchapter,
                    ),
                )
                inserted += 1
                seen_this_batch[e.event_id] = e
            except sqlite3.IntegrityError as exc:
                if exc.sqlite_errorname in _DEDUP_ERROR_NAMES:
                    skipped += 1
                    earlier = seen_this_batch.get(e.event_id)
                    if earlier is not None and earlier != e:
                        log.warning(
                            "Intra-file event_id collision: two rows share "
                            "identity (student=%r problem=%r timestamp=%r "
                            "selection=%r problem_view=%r) but differ in "
                            "payload; the later row was dropped. The identity "
                            "hash excludes action/input, so a second logged "
                            "action in the same instant cannot be stored.",
                            e.student_id, e.problem_name, e.timestamp,
                            e.selection, e.problem_view,
                        )
                    seen_this_batch.setdefault(e.event_id, e)
                else:
                    raise
    log.info("Wrote %d events (%d skipped as duplicates)", inserted, skipped)
    return inserted, skipped


def backfill_filter_columns(
    conn: sqlite3.Connection,
    import_id: int,
    events: Iterable[Event],
) -> int:
    """
    Fill the CD-1 filter columns on this import's pre-0008 events.

    Returns the number of rows actually backfilled. This is the backfill half
    of CD-1: rows ingested before migration 0008 carry NULL for ``class_id`` /
    ``week_no`` / ``chapter`` / ``subchapter``; re-parsing the retained export
    and running this sets them from the same source row, touching nothing else.

    The UPDATE is scoped to ``import_id`` and to rows whose filter columns are
    *still* all NULL — the signature of a pre-0008 ingest (0008 added all four
    at once). So it only fills the blanks and never overwrites an
    already-populated value (e.g. were the retained export later edited). It is
    therefore idempotent — a second run backfills 0 rows — and cannot perturb
    identity, payload, or the SA derive. ``write_events`` already sets these on
    fresh inserts, so for rows it just inserted this is a skip; its job is the
    pre-0008 rows it skipped as duplicates.
    """
    updated = 0
    with transaction(conn):
        for e in events:
            cur = conn.execute(
                "UPDATE events SET class_id = ?, week_no = ?, chapter = ?, "
                "subchapter = ? "
                "WHERE event_id = ? AND import_id = ? "
                "AND class_id IS NULL AND week_no IS NULL "
                "AND chapter IS NULL AND subchapter IS NULL",
                (
                    e.class_id,
                    e.week_no,
                    e.chapter,
                    e.subchapter,
                    e.event_id,
                    import_id,
                ),
            )
            updated += cur.rowcount
    log.info("Backfilled filter columns on %d events", updated)
    return updated
