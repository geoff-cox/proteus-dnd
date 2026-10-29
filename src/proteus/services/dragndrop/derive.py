"""
DnD derive (plan items B1.2/B1.3): core ``events`` → ``dnd_events``.

The DnD service's Stage-1 materializer in the parse-once/derive-per-service
design (plan §2.3). It classifies each DnD-relevant event row into a typed
kind and extracts the kind's payload:

    dragNdrop-drop   → move / takeback   Action is ``<card> -> <zone>``; a
                                         destination of ``dragzone`` is a
                                         takeback.
    dragNdrop        → check             The **Input** column is authoritative
                                         (rule 6): Action holds the same JSON
                                         but is truncated for wide boards —
                                         visible in the sample export. The
                                         feedback classification carries the
                                         grade.
    dragNdrop-reset  → reset             Recorded bare; the board returns to
                                         the initial state X0.
    page             → page_view         Not part of any attempt, but kept
                                         because page rows help delimit
                                         attempts (B3.1).

Parsing is tolerant by design: a malformed payload is written with
``parse_ok = 0`` and a logged warning, never dropped — the row is still
evidence. A check configuration that parses but references labels outside
the positional grammar (the "orphan" case) keeps ``parse_ok = 1`` with the
oddity recorded in ``parse_note``; whether those labels exist on the board
is the B2.4 validation's job.

Re-deriving is idempotent: ``dnd_events.event_id`` is the events PK, so a
duplicate is skipped, never re-inserted.

The governance filter (dnd/0016, :mod:`account_roles`; E-02 ruling R1 (a),
2026-10-19): a row whose ``student_id`` is ruled ``exclude = 1`` in
``dnd_account_roles`` is withheld here — page rows included (J1) — and
never reaches ``dnd_events``, while ``events`` keeps every row. The pass
counts what it withheld (``DeriveResult.excluded``, kept apart from
``skipped`` so the Stage 2.2 collision tripwire keeps its meaning, J3),
records the ruled set's content hash in its run configuration and the
withheld counts in its notes (R12), and refuses to run when an excluded id
already has derived rows (R7 — the rulings must be loaded before the first
derive: ``init`` → ``dnd roles import`` → ``ingest --derive``). With an
empty table the pass is exactly as before. No id value reaches a log line
or a message (M6).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from proteus.core import runs
from proteus.services.dragndrop import account_roles, decode

log = logging.getLogger(__name__)

# Version of this stage's parse/classification semantics (the journal's
# "parser version"), recorded on each pass's pipeline_runs provenance row
# (F22). Bump when classification or payload parsing changes meaning.
#   1: the original classification and payload parsing (B1.2/B1.3).
#   2 (2026-10-19): the governance filter, dnd/0016 — rows of an excluded
#      account are withheld; the run configuration records
#      `roles_set_sha256`; the notes record the withheld count.
STAGE_VERSION = 2

# Raw Selection values derive consumes. The three dragNdrop values are the
# service's claimed selections (see service.py); 'page' is shared.
_MOVE_SELECTION = "dragNdrop-drop"
_CHECK_SELECTION = "dragNdrop"
_RESET_SELECTION = "dragNdrop-reset"
_PAGE_SELECTION = "page"
DERIVED_SELECTIONS: tuple[str, ...] = (
    _CHECK_SELECTION,
    _MOVE_SELECTION,
    _RESET_SELECTION,
    _PAGE_SELECTION,
)

# Structured SQLite error names that mean "this event is already derived" —
# the idempotent skip (same convention as core.ingest.events).
_DEDUP_ERROR_NAMES = frozenset(
    {"SQLITE_CONSTRAINT_PRIMARYKEY", "SQLITE_CONSTRAINT_UNIQUE"}
)


@dataclass(frozen=True)
class DndEventRow:
    """One classified dnd_events row, ready to insert."""

    event_id: str
    kind: str  # 'move' | 'takeback' | 'check' | 'reset' | 'page_view'
    source_label: str | None = None
    zone_label: str | None = None
    configuration_json: str | None = None
    feedback: str | None = None
    parse_ok: bool = True
    parse_note: str | None = None


@dataclass(frozen=True)
class DeriveResult:
    """Summary of one DnD derive pass."""

    service: str
    import_id: int | None  # filter used, None = all imports
    question_id: str | None  # filter used, None = all questions
    seen: int  # DnD-relevant events examined
    inserted: int
    skipped: int  # duplicates already present in dnd_events
    malformed: int  # rows written with parse_ok = 0
    run_id: int  # the pass's pipeline_runs provenance row (F22)
    # Rows of excluded accounts withheld (dnd/0016, J3): counted under the
    # pass's filters, never inserted and never `skipped` — so `skipped`
    # still means "already derived" (the Stage 2.2 collision tripwire).
    excluded: int = 0


@contextmanager
def _write_locked(conn: sqlite3.Connection) -> Iterator[None]:
    """``BEGIN IMMEDIATE`` before the governance checks, so the R7 tripwire,
    the ``raw_rows`` check, the snapshot of the excluded ids, the hash in the
    run configuration and the inserts all see one state of
    ``dnd_account_roles``. Without it (the Copilot round on PR #238) the pass
    took its transaction only at the first insert: an import on another
    connection could land its first exclusion in between the snapshot and
    the inserts — the importer saw no derived rows and succeeded, derive saw
    no excluded id and wrote the account's rows under ``excluded=0`` while
    recording the new hash. Under the lock that importer waits for the pass,
    then meets the derived rows and refuses (R7 at import). A caller already
    inside a transaction keeps its own and commits or rolls back the pass
    itself — the pattern of :mod:`account_roles`, :mod:`aliases` and
    :mod:`logging_regimes`."""
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


def derive_events(
    conn: sqlite3.Connection,
    import_id: int | None = None,
    question_id: str | None = None,
) -> DeriveResult:
    """
    Materialize ``dnd_events`` from the DnD-relevant rows of ``events``.

    If `import_id` is given, only that import's events are derived; if
    `question_id` is given, only events for that problem (note that page
    rows carry the *page's* name in problem_name, so a question filter
    excludes them).

    Every classified row is written — malformed payloads land with
    ``parse_ok = 0`` and a warning rather than being dropped. The whole pass
    runs in one transaction under a write lock taken before the governance
    checks (:func:`_write_locked`); duplicates are skipped (idempotent
    re-derive).

    Every pass — including an idempotent no-op re-derive — records a
    ``pipeline_runs`` row carrying its configuration (F22); because the pass
    is a single transaction, the run row commits with the data it describes
    (a failed pass rolls back both).

    The governance filter (dnd/0016, R1 (a)): when ``dnd_account_roles``
    holds an excluded id, that id's rows — page rows included (J1) — are
    withheld under the same import/question filters and counted as
    ``excluded``; they are never inserted and never ``skipped``. Before
    anything is written, :func:`account_roles.assert_no_derived_rows`
    refuses a database in which an excluded id already has derived rows
    (R7: the rulings were loaded after a derive — delete the import(s) and
    re-ingest, or a fresh ``init``); the refusal names counts, roles and
    pseudonyms, never an id. The run configuration records
    ``roles_set_sha256`` (None with an empty table); a ``raw_rows`` claim
    that disagrees with the ``events`` count logs one warning, a count
    only. With an empty table the pass is exactly as before. The checks,
    the snapshot of the excluded ids, the hash and the inserts run under one
    ``BEGIN IMMEDIATE`` lock, so an import on another connection cannot land
    its first exclusion in between them (it waits, then meets the derived
    rows and refuses); a caller already inside a transaction keeps its own
    and commits or rolls back the pass itself.
    """
    seen = 0
    inserted = 0
    skipped = 0
    malformed = 0
    excluded = 0
    excluded_students = 0
    # The lock first (the Copilot round on PR #238): the governance checks,
    # the snapshot of the excluded ids, the hash in the run configuration and
    # the inserts see one state of the rulings table.
    with _write_locked(conn):
        # R7: the reverse order (derive, then the rulings) is refused before any
        # insert; the RoleError names counts, roles and pseudonyms, never an id.
        account_roles.assert_no_derived_rows(conn)
        mismatches = account_roles.raw_rows_mismatches(conn)
        if mismatches:
            log.warning(
                "dnd derive: %d ruled account(s) whose raw_rows claim differs from "
                "their events count (the rulings file was written against another "
                "export set, or an import is missing; counts only, no id)",
                mismatches,
            )
        withheld_ids = account_roles.excluded_ids(conn)

        placeholders = ", ".join("?" for _ in DERIVED_SELECTIONS)
        where = f"selection IN ({placeholders})"
        params: list[object] = list(DERIVED_SELECTIONS)
        if import_id is not None:
            where += " AND import_id = ?"
            params.append(import_id)
        if question_id is not None:
            where += " AND problem_name = ?"
            params.append(question_id)
        # `IS NOT NULL` guards the three-valued trap: SQLite allows NULL in a
        # TEXT primary key, and `x NOT IN (…, NULL)` is NULL for every x, which
        # would silently withhold every row. The importer refuses an empty id, so
        # only hand SQL can plant one; the guard makes derive safe regardless.
        excluded_set = (
            f"(SELECT anon_student_id FROM {account_roles.TABLE} "
            "WHERE exclude = 1 AND anon_student_id IS NOT NULL)"
        )
        sql = (
            "SELECT event_id, problem_name, selection, action, input, "
            f"feedback_classification FROM events WHERE {where}"
        )
        if withheld_ids:
            sql += f" AND (student_id IS NULL OR student_id NOT IN {excluded_set})"
        # rowid order is insertion order is file order, as in SA derive.
        sql += " ORDER BY import_id, rowid"
        # The withheld rows, under the same selection/import/question filters.
        withheld_sql = (
            "SELECT count(*) AS n, count(DISTINCT student_id) AS k FROM events "
            f"WHERE {where} AND student_id IN {excluded_set}"
        )
        run_id = runs.start_stage_run(
            conn,
            service="dnd",
            stage="derive",
            config={
                "stage_version": STAGE_VERSION,
                "import_id": import_id,
                "question_id": question_id,
                "roles_set_sha256": account_roles.roles_set_sha256(conn),
            },
        )
        if withheld_ids:
            counts = conn.execute(withheld_sql, params).fetchone()
            excluded = int(counts["n"])
            excluded_students = int(counts["k"])
        for event in conn.execute(sql, params):
            seen += 1
            row = classify_event(event)
            if not row.parse_ok:
                malformed += 1
                log.warning(
                    "dnd derive: unparseable %s event %s (question=%r): %s",
                    event["selection"], row.event_id,
                    event["problem_name"], row.parse_note,
                )
            elif row.parse_note:
                log.warning(
                    "dnd derive: event %s (question=%r) flagged: %s",
                    row.event_id, event["problem_name"], row.parse_note,
                )
            try:
                conn.execute(
                    "INSERT INTO dnd_events (event_id, kind, source_label, "
                    "zone_label, configuration_json, feedback, parse_ok, "
                    "parse_note) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        row.event_id,
                        row.kind,
                        row.source_label,
                        row.zone_label,
                        row.configuration_json,
                        row.feedback,
                        1 if row.parse_ok else 0,
                        row.parse_note,
                    ),
                )
                inserted += 1
            except sqlite3.IntegrityError as exc:
                if exc.sqlite_errorname in _DEDUP_ERROR_NAMES:
                    skipped += 1
                    if not row.parse_ok:
                        malformed -= 1  # counted above, but not written
                else:
                    raise
        runs.complete_run(
            conn, run_id,
            notes=(
                f"seen={seen} inserted={inserted} skipped={skipped} "
                f"malformed={malformed} excluded={excluded} "
                f"excluded_students={excluded_students}"
            ),
        )

    log.info(
        "DnD derive: %d event(s) seen, %d inserted, %d skipped, %d malformed, "
        "%d withheld (rows of %d excluded account(s))",
        seen, inserted, skipped, malformed, excluded, excluded_students,
    )
    return DeriveResult(
        service="dnd",
        import_id=import_id,
        question_id=question_id,
        seen=seen,
        inserted=inserted,
        skipped=skipped,
        malformed=malformed,
        run_id=run_id,
        excluded=excluded,
    )


def classify_event(event: sqlite3.Row | dict) -> DndEventRow:
    """
    Classify one events row (anything indexable by column name) into a
    DndEventRow. Never raises on payload content: malformed payloads come
    back with parse_ok=False and a parse_note.
    """
    selection = event["selection"]
    if selection == _MOVE_SELECTION:
        return _classify_move(event)
    if selection == _CHECK_SELECTION:
        return _classify_check(event)
    if selection == _RESET_SELECTION:
        return DndEventRow(event_id=event["event_id"], kind="reset")
    if selection == _PAGE_SELECTION:
        return DndEventRow(event_id=event["event_id"], kind="page_view")
    raise ValueError(f"not a DnD-relevant selection: {selection!r}")


def _classify_move(event) -> DndEventRow:
    """A dragNdrop-drop row: Action is ``<card label> -> <zone label>``."""
    action = (event["action"] or "").strip()
    source, sep, zone = action.partition("->")
    source = source.strip()
    zone = zone.strip()
    if not sep or not source or not zone:
        return DndEventRow(
            event_id=event["event_id"],
            kind="move",
            parse_ok=False,
            parse_note=f"move Action not of the form '<card> -> <zone>': {action!r}",
        )
    kind = "takeback" if zone == decode.DRAGZONE else "move"
    return DndEventRow(
        event_id=event["event_id"],
        kind=kind,
        source_label=source,
        zone_label=zone,
    )


def _classify_check(event) -> DndEventRow:
    """
    A dragNdrop (check) row. The Input column is the authoritative
    configuration (rule 6: never parse Action — it truncates); it is stored
    verbatim in configuration_json so the original evidence survives even
    when it does not parse.
    """
    raw = event["input"] or ""
    question_id = event["problem_name"] or ""
    feedback = event["feedback_classification"] or None

    parse_ok, note = _check_configuration_health(raw, question_id)
    return DndEventRow(
        event_id=event["event_id"],
        kind="check",
        configuration_json=raw if raw else None,
        feedback=feedback,
        parse_ok=parse_ok,
        parse_note=note,
    )


def _check_configuration_health(raw: str, question_id: str) -> tuple[bool, str | None]:
    """
    Decide parse_ok / parse_note for a check row's Input.

    parse_ok means "structurally usable downstream": valid JSON, an object,
    every value a list of strings. Labels outside the positional grammar are
    a *flag* (note, parse_ok stays True) — the orphan case of B1.4.
    """
    if not raw.strip():
        return False, "check row has an empty Input column"
    try:
        configuration = json.loads(raw)
    except json.JSONDecodeError as exc:
        return False, f"check Input is not valid JSON: {exc}"
    if not isinstance(configuration, dict):
        return False, (
            f"check Input is not a JSON object "
            f"(got {type(configuration).__name__})"
        )

    oddities: list[str] = []
    for zone_label, cards in configuration.items():
        if decode.parse_label(zone_label, question_id) is None:
            oddities.append(f"unknown zone label {zone_label!r}")
        if not isinstance(cards, list):
            return False, (
                f"check Input value for {zone_label!r} is not a list "
                f"(got {type(cards).__name__})"
            )
        for card_label in cards:
            if not isinstance(card_label, str):
                return False, (
                    f"check Input list for {zone_label!r} holds a "
                    f"non-string entry: {card_label!r}"
                )
            parsed = decode.parse_label(card_label, question_id)
            if parsed is None or parsed.kind != "drag":
                oddities.append(f"unknown card label {card_label!r}")
    if oddities:
        return True, "; ".join(oddities)
    return True, None
