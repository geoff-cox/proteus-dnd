"""
The attrition ledger (E-02, PR 2 of 2; ``cc-task-attrition-ledger`` §4.2;
owner rulings 2026-10-19): three read-time export datasets that count what
each exclusion step of the manuscript's ``subsec-analysis-unit`` removes.
They ride the export registry (ruling R3) and are written by the existing
``dnd export <dataset> -o``; the archive's ``validation/flow.csv`` is the
procedure's copy of ``flow``.

* ``flow``, the ordered exclusion ledger: one row per (step, quantity,
  breakdown, key) with ``rows_in``, ``rows_lost``, ``rows_out``, the step's
  fixed reason and ``n_students``, for eight quantities, students,
  ``items_logged`` and ``items_canonical`` (R4: both counts, so the table
  generator picks the Items-row convention, O-123), ``item_traces_logged``
  and ``item_traces_canonical`` (an item trace is one student's events on
  one item across attempts), attempts, events and checks, pooled and per
  offering. It fills ``tab-corpus-flow`` (Raw → Authorized → Source-aligned
  → Reconstructed → Ready) and procedure row 3.7.
* ``readiness_split``, the attempts by readiness (``ready``, ``limited``,
  ``exclude``; ``not_computed`` for a NULL readiness, surfaced rather than
  hidden), pooled and by offering, item, source version, flag and flag
  combination (procedure row 3.5).
* ``exclusion_probe``, the "are exclusions differential?" cross-tab of
  readiness × eligible-anchor presence × first-check correctness × eventual
  correctness, counts only, never a test statistic (procedure row 3.6; R14:
  the full cross-tab pooled, the two marginal families by offering).

The steps, in the manuscript's order: 0 ``raw``, 1 ``governance`` (the
instructor, researcher and test accounts ruled ``exclude`` in
``dnd_account_roles``), 2 ``question_type`` (rows of other components, page
views included), 3 ``source_alignment`` (no board after the identifier
alias), 4 ``event_parsing``, 5 ``state_validity``, 6 ``readiness``, then two
attempt-only siblings that both start from the Ready count, 7
``anchor_coverage`` (ready attempts with no eligible anchor) and 8
``rq3_subset`` (ready attempts that are not eventually correct with a scored
check, ``contrast.read_population``'s rule under the default
``ContrastConfig``). The manuscript's seventh step, analysis-specific
missingness, is per feature and belongs to the RQ3 denominators, not here.

Table-literal semantics (R9 (b)): "Reconstructed" is every reconstructed
attempt, the readiness-blind agreement population. The attempts quantity
loses rows only at step 3 (``missing_source_map``) and step 6 (readiness).
Steps 4 and 5 lose checks (a malformed payload; a payload that does not
decode to a unique, board-valid configuration) and events (rows whose
payload did not parse; moves the replay skipped as off-board) and lose no
attempt; the attempts carrying ``parse_error`` / ``invalid_state`` are
visible in ``readiness_split``'s ``by_flag`` breakdown. Students, items and
item traces telescope by survival: an entity is lost at the first step after
which it has no surviving row (steps 0-2, over the rows of the three claimed
Selections, R11 (a)) or no surviving attempt (steps 3-6); items at Ready
are items with at least one ready attempt (J9). Attempts have no value
before step 3 and the other quantities none after step 6: those cells are
empty, and the generator renders "—" (J10). Telescoping holds by
construction: ``rows_in`` of step k+1 is ``rows_out`` of step k for every
quantity, breakdown and key, and steps 7 and 8 both read Ready as ``rows_in``.

Decode outcomes versus stored causes (J8, R8 (a)): the step-4 and step-5
check counts are *decode outcomes*, each check's verbatim
``configuration_json`` is re-decoded at read time with
:func:`decode.decode_configuration` against the item's board (the
alias-aware :func:`board.load_board`; P and R as replay had them), and the
malformed test of reconstruct's cross-check and the off-board rules of its
move replay are reproduced here, never imported. They are never read off
``dnd_configurations.anchor_cause``, which stores the first cause in
precedence order and so hides a decode failure under a structural cause
(``no_move_logging``, ``resumed_attempt``). The generator's cause tables are
the stored causes; the dataset descriptions say which is which.

Keys (M5, M6): the offering key is ``import_sha256``, ``raw_imports
.file_hash`` for a raw row, the attempt-window rollup of ``export``'s
attempt-shaped datasets for an attempt (the text of that join is
:data:`ATTEMPT_IMPORT_JOIN`, verbatim), items are canonical identifiers,
flags are flag names, and everything else is a count. No student id,
pseudonym, path or surname reaches an output cell, a log line or a message.

Which attempt holds a row (the step-6 question: that attempt's readiness):
the attempt whose replay wrote the row's configuration
(``dnd_configurations.event_id``, the segment's actual members), and for a
row the replay skipped (off-board or unparseable, so no configuration) the
attempt whose window holds it, two overlapping windows told apart by the
row's nearest replayed neighbours in reconstruct's own action order
(:func:`_membership`; the Copilot round on PR #239, the window alone gave a
row to the lowest attempt whose window held it, and two same-session
attempts share a timestamp when a session change falls inside a tied
second).

Two readings the spec leaves to the implementation. A card-to-zone row of a
retained account that no attempt holds (or that was never derived)
is a step-3 loss of the events and checks quantities under that step's one
fixed reason, a second reason row would break the one-row-per-key shape,
and :func:`flow_rows` logs the count (expected zero on a consistent
database). Per offering, a raw row is keyed by its own import and an
attempt by its window's rollup (``MAX`` over the window's rows), so a
by_offering series telescopes across the step-2/3 seam only when every
attempt window lies in one import, the case when each offering is one
export file (the per-offering tab set); a window split across two imports
would count its rows under both imports before step 3 and its attempt
under the larger hash from step 3 on.
No cell guard (R10 (a)): every by-offering and by-item row carries
``n_students`` so the generator applies the five-student rule at display
time; governance losses are emitted per offering, unguarded (J5).
``--question <logged id>`` scopes every quantity of every dataset to that
identifier (J7). Plain SQL plus small Python, no DataFrame library; every
dataset is sorted and byte-deterministic, two exports of one database are
identical.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass

from proteus.services.dragndrop import account_roles, aliases, decode, qc
from proteus.services.dragndrop.board import load_board
from proteus.services.dragndrop.reconstruct import determine_n_cards

log = logging.getLogger(__name__)

__all__ = [
    "ALL",
    "ATTEMPT_IMPORT_JOIN",
    "DND_SELECTIONS",
    "EXCLUSION_PROBE",
    "EXCLUSION_PROBE_COLUMNS",
    "EXCLUSION_PROBE_DESCRIPTION",
    "EXCLUSION_PROBE_SUMMARY",
    "FLOW",
    "FLOW_COLUMNS",
    "FLOW_DESCRIPTION",
    "FLOW_SUMMARY",
    "NONE",
    "NOT_COMPUTED",
    "NO_FLAG",
    "POOLED",
    "QUANTITIES",
    "READINESS_SPLIT",
    "READINESS_SPLIT_COLUMNS",
    "READINESS_SPLIT_DESCRIPTION",
    "READINESS_SPLIT_SUMMARY",
    "STEP_NAMES",
    "STEP_REASONS",
    "exclusion_probe_rows",
    "flow_rows",
    "readiness_split_rows",
]

# The three claimed Selection values of the DnD component (service.py). A
# row with any other Selection (page views included) is a question-type
# loss (step 2); a student counts when at least one of their rows carries one
# of these (R11 (a)); an item is a problem_name carried by one of these.
DND_SELECTIONS: tuple[str, ...] = ("dragNdrop", "dragNdrop-drop", "dragNdrop-reset")
_CHECK_SELECTION = "dragNdrop"
_MOVE_KINDS = frozenset({"move", "takeback"})

FLOW = "flow"
READINESS_SPLIT = "readiness_split"
EXCLUSION_PROBE = "exclusion_probe"

# Value vocabulary shared by the datasets.
POOLED = "pooled"
ALL = "all"  # a marginal cell of the probe
NOT_COMPUTED = "not_computed"  # a NULL readiness, surfaced rather than hidden
NO_FLAG = "(no flag)"  # the by_flag key of an empty flag set
# The key of a row whose breakdown value is missing: a board-less item's
# source version, a NULL first-check / eventual correctness (no scored
# check), an attempt with no window event or no stored flag set (none by
# construction after reconstruct).
NONE = "none"

STEP_NAMES: tuple[str, ...] = (
    "raw",
    "governance",
    "question_type",
    "source_alignment",
    "event_parsing",
    "state_validity",
    "readiness",
    "anchor_coverage",
    "rq3_subset",
)

# One fixed reason per step; the number fill quotes them.
STEP_REASONS: tuple[str, ...] = (
    "raw: every row of the imported exports",
    "governance: rows of instructor, researcher and test accounts "
    "(dnd_account_roles, exclude = 1); offerings outside the manifest: none "
    "by construction",
    "question type: rows of components other than card-to-zone items, and "
    "page views",
    "source alignment: attempts on a logged identifier with no board after "
    "the identifier alias (missing_source_map)",
    "event parsing: rows whose payload did not parse (parse_ok = 0) and "
    "checks whose payload is malformed; attempts carrying parse_error are "
    "retained as limited (readiness_split, by_flag)",
    "state validity: check payloads that do not decode to a unique, "
    "board-valid configuration (re-decoded at read time; a check under a "
    "structural cause is counted by its payload) and moves naming an "
    "off-board card or slot; attempts carrying invalid_state are counted at "
    "readiness",
    "readiness: attempts in the exclude and limited classes (readiness NULL "
    "reported as not_computed)",
    "anchor coverage: ready attempts with no eligible anchor",
    "RQ3 subset: ready attempts with a scored check that are not eventually "
    "correct",
)

QUANTITIES: tuple[str, ...] = (
    "students",
    "items_logged",
    "items_canonical",
    "item_traces_logged",
    "item_traces_canonical",
    "attempts",
    "events",
    "checks",
)
# The quantities that telescope by survival (over rows at steps 0-2, over
# attempts at steps 3-6).
_DERIVED: tuple[str, ...] = QUANTITIES[:5]

FLOW_COLUMNS: tuple[str, ...] = (
    "step", "step_name", "quantity", "breakdown", "key",
    "rows_in", "rows_lost", "rows_out", "reason", "n_students",
)
READINESS_SPLIT_COLUMNS: tuple[str, ...] = (
    "breakdown", "key", "readiness", "n_attempts", "n_students",
)
EXCLUSION_PROBE_COLUMNS: tuple[str, ...] = (
    "breakdown", "key", "readiness", "has_eligible_anchor",
    "first_check_correct", "eventual_correct", "n_attempts", "n_students",
)

FLOW_SUMMARY = (
    "the ordered exclusion ledger: rows in / lost / out per step, quantity "
    "and offering"
)
FLOW_DESCRIPTION = (
    "The ordered exclusion ledger (E-02; procedure row 3.7; tab-corpus-flow): "
    "one row per step (0 raw, 1 governance, 2 question_type, "
    "3 source_alignment, 4 event_parsing, 5 state_validity, 6 readiness, and "
    "the attempt-only siblings 7 anchor_coverage and 8 rq3_subset, both from "
    "the Ready count), quantity (students, items_logged, items_canonical, "
    "item_traces_logged, item_traces_canonical, attempts, events, checks), "
    "breakdown (pooled, by_offering) and key (pooled, or the offering's "
    "import_sha256), with rows_in, rows_lost, rows_out, the step's fixed "
    "reason and n_students (distinct students among the quantity's surviving "
    "entities at rows_out). Table-literal semantics (R9 (b)): Reconstructed "
    "is every reconstructed attempt; attempts lose rows at steps 3 and 6 "
    "only, checks and events at steps 4 and 5 too; students, items and "
    "traces telescope by survival (items at Ready are items with a ready "
    "attempt). Attempts have no value before step 3 and the other quantities "
    "none after step 6 (empty cells). The step-4 and step-5 check counts are "
    "decode outcomes, every check payload re-decoded at read time against "
    "the item's board, never the stored anchor_cause, which keeps the "
    "structural cause first in precedence (the generator's cause tables are "
    "the stored causes). A row's attempt is the one whose replay wrote its "
    "configuration; a row the replay skipped falls back to the attempt whose "
    "window holds it. A card-to-zone row no attempt holds (or never "
    "derived) is counted in step 3's events and checks losses under "
    "the step's fixed reason, and the export logs the count (expected zero). "
    "Per offering, a raw row is keyed by its own import and an attempt by "
    "its window's rollup, so a by_offering series telescopes across the "
    "step-2/3 seam when every attempt window lies in one import (each "
    "offering one export file). No cell guard: the generator applies the "
    "five-student rule from n_students. Keys are import_sha256 values; no "
    "student id or pseudonym appears."
)
READINESS_SPLIT_SUMMARY = (
    "attempts by readiness, pooled and by offering / item / source version / "
    "flag"
)
READINESS_SPLIT_DESCRIPTION = (
    "The readiness split (procedure row 3.5): attempts by readiness (ready, "
    "limited, exclude; not_computed for a NULL readiness), with n_attempts "
    "and n_students, pooled and by_offering (import_sha256), by_item (the "
    "canonical identifier after the identifier alias), by_source_version "
    "(dnd_boards.source_hash of the canonical item; none for a board-less "
    "item), by_flag (each flag the attempt carries, in qc.FLAG_ORDER; "
    "(no flag) for an empty set, an attempt counts once per flag, so this "
    "breakdown alone does not sum to the pooled split) and "
    "by_flag_combination (the stored qc_flags_json string). Every "
    "reconstructed attempt, readiness-blind; every breakdown but by_flag "
    "sums to the pooled split. No cell guard: n_students is for the "
    "generator's five-student rule."
)
EXCLUSION_PROBE_SUMMARY = (
    "readiness x eligible anchor x first-check x eventual correctness, "
    "counts only"
)
EXCLUSION_PROBE_DESCRIPTION = (
    "The exclusion probe (procedure row 3.6; the Discussion's 'are "
    "exclusions differential?' question, answered as a count table and never "
    "as a test statistic): attempts by readiness (ready, limited, exclude, "
    "not_computed), has_eligible_anchor (1 when at least one check is an "
    "eligible anchor, dnd_configurations.is_check = 1 with anchor_cause "
    "NULL, else 0), first_check_correct and eventual_correct (1, 0, or none "
    "when dnd_attempt_metrics holds NULL: no scored check), with n_attempts "
    "and n_students. Pooled: the full cross-tab plus two marginal families, "
    "readiness x eventual_correct and has_eligible_anchor x eventual_correct "
    "(the other two columns all); by_offering (import_sha256): the two "
    "marginal families only (R14). A cell that does not occur is absent. No "
    "cell guard: n_students is for the generator's five-student rule."
)

# The offering key of an attempt, import_sha256 read off the attempt's
# WINDOW: the events of the attempt's student on its logged identifier, in
# the attempt's session, with started_at <= timestamp <= ended_at (opaque
# sortable strings compared as strings, CLAUDE.md rule 6; `IS` so that a
# NULL session matches a NULL session). The text of export's
# `_ATTEMPT_IMPORT_JOIN`, verbatim, so the ledger keys an attempt exactly as
# the attempt-shaped datasets do (export documents why the window, not the
# configurations, and why MAX is only the deterministic pick); the two must
# stay identical. Kept here rather than imported because export imports this
# module to register the datasets.
ATTEMPT_IMPORT_JOIN = (
    "LEFT JOIN (SELECT a.attempt_id AS attempt_id, "
    "MAX(i.file_hash) AS import_sha256 "
    "FROM dnd_attempts a "
    "JOIN events e ON e.student_id = a.student_id "
    "  AND e.problem_name = a.question_id "
    "  AND e.session_id IS a.session_id "
    "  AND e.timestamp >= a.started_at AND e.timestamp <= a.ended_at "
    "JOIN raw_imports i ON i.import_id = e.import_id "
    "GROUP BY a.attempt_id) imp "
)

# The loss step of a row or attempt that survives the whole chain.
_SURVIVES = 99
_FLAG_NO_BOARD = "missing_source_map"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _scope(
    filters: Mapping[str, object], column: str = "e.problem_name"
) -> tuple[str, list]:
    """The optional ``AND <column> = ?`` clause of the ``--question`` scope
    (J7: a logged identifier; every quantity of every dataset is scoped to
    it) and its parameters, empty without the filter."""
    question_id = filters.get("question_id")
    if question_id:
        return f" AND {column} = ?", [question_id]
    return "", []


def _flags(text: str | None) -> frozenset[str]:
    """The stored flag set; empty when the column is NULL or unreadable."""
    try:
        return frozenset(json.loads(text or "[]"))
    except (TypeError, ValueError):
        return frozenset()


def _bump(entities: dict, key: object, loss: int) -> None:
    """Record that `key` has a row (or attempt) lost at `loss`: an entity
    survives step k while any of its rows does, so it keeps the maximum."""
    if loss > entities.get(key, -1):
        entities[key] = loss


def _survivors(entities: Mapping[object, int], step: int) -> int:
    return sum(1 for loss in entities.values() if loss > step)


class _Items:
    """Per logged identifier, cached: the canonical identifier
    (:func:`aliases.resolve_question_id`) and the board shape replay had,
    P and R from the alias-aware board, else P inferred from the drag labels
    with no R bound (:func:`reconstruct.determine_n_cards`), else None."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._canonical: dict[str, str] = {}
        self._shape: dict[str, tuple[int, int | None] | None] = {}

    def canonical(self, question_id: str) -> str:
        if question_id not in self._canonical:
            self._canonical[question_id] = aliases.resolve_question_id(
                self._conn, question_id
            )
        return self._canonical[question_id]

    def shape(self, question_id: str) -> tuple[int, int | None] | None:
        if question_id not in self._shape:
            board = load_board(self._conn, question_id)
            if board is not None:
                self._shape[question_id] = (board.n_cards, board.n_zones)
            else:
                n_cards = determine_n_cards(self._conn, question_id)
                self._shape[question_id] = (
                    (n_cards, None) if n_cards is not None else None
                )
        return self._shape[question_id]


def _payload(configuration_json: str | None) -> dict | None:
    """A check's parsed payload, or None when it is ``malformed``, the test
    of reconstruct's cross-check, reproduced: empty, not JSON, or not a JSON
    object (``parse_ok = 0`` is tested by the caller first)."""
    if not configuration_json:
        return None
    try:
        payload = json.loads(configuration_json)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _check_cause(
    payload: Mapping[str, object], question_id: str,
    shape: tuple[int, int | None] | None,
) -> str | None:
    """The decode-side anchor cause of a parsed check payload, None when it
    decodes to a unique, board-valid configuration. Labels are parsed under
    the logged identifier (the positional grammar anchors on it) against the
    board's P and R; without an R bound no slot test applies, as in replay.
    ``no_board`` when not even P is known (a board removed after
    reconstruct; unreachable on a consistent database, where such an attempt
    carries missing_source_map and is lost at step 3)."""
    if shape is None:
        return "no_board"
    n_cards, n_zones = shape
    return decode.decode_configuration(payload, question_id, n_cards, n_zones).cause


def _off_board(
    source_label: str | None, zone_label: str | None, question_id: str,
    shape: tuple[int, int | None] | None,
) -> bool:
    """Whether a parseable move names an off-board card or slot, the rules
    under which reconstruct's replay skips it, reproduced: the source is not
    a ``drag`` label with an index in 1..P, the zone does not decode (or is
    a card label), or it is a ``drop`` with an index outside 1..R when a
    board gives R. The dragzone (a takeback) is always replayable."""
    if shape is None:
        return True
    n_cards, n_zones = shape
    card = decode.parse_label(source_label or "", question_id)
    if card is None or card.kind != "drag" or not (1 <= card.index <= n_cards):
        return True
    zone = decode.parse_label(zone_label or "", question_id)
    if zone is None or zone.kind == "drag":
        return True
    if zone.kind == "drop" and n_zones is not None and not (1 <= zone.index <= n_zones):
        return True
    return False


# ---------------------------------------------------------------------------
# The attempts: one loader for the three datasets
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _Attempt:
    attempt_id: int
    student_id: str
    question_id: str  # the logged identifier
    canonical_id: str  # after the identifier alias
    session_id: str | None
    started_at: str | None
    ended_at: str | None
    offering: str  # import_sha256 (the window rollup), or NONE
    flags: frozenset[str]
    flags_json: str | None
    readiness: str | None
    loss: int  # 3 (missing_source_map), 6 (not ready) or _SURVIVES
    has_anchor: bool  # at least one eligible anchor (step 7)
    first_check_correct: int | None
    eventual_correct: int | None

    @property
    def rq3(self) -> bool:
        """``contrast.read_population``'s rule under the default
        ``ContrastConfig``: a scored check and eventually correct (step 8)."""
        return self.first_check_correct is not None and self.eventual_correct == 1


_ATTEMPTS_SQL = (
    "SELECT a.attempt_id, a.student_id, a.question_id, a.session_id, "
    "a.started_at, a.ended_at, a.qc_flags_json, a.readiness, "
    "m.first_check_correct, m.eventual_correct, "
    # 1 when at least one check of the attempt is an eligible anchor
    # (compared: no cause stored), 0 when every check carries a cause, NULL
    # without a check.
    "anc.has_anchor, imp.import_sha256 "
    "FROM dnd_attempts a "
    "LEFT JOIN dnd_attempt_metrics m ON m.attempt_id = a.attempt_id "
    "LEFT JOIN (SELECT attempt_id, "
    "  MAX(CASE WHEN anchor_cause IS NULL THEN 1 ELSE 0 END) AS has_anchor "
    "  FROM dnd_configurations WHERE is_check = 1 GROUP BY attempt_id) anc "
    "  ON anc.attempt_id = a.attempt_id "
    + ATTEMPT_IMPORT_JOIN
    + "ON imp.attempt_id = a.attempt_id "
    "WHERE 1 = 1"
)


def _load_attempts(
    conn: sqlite3.Connection,
    filters: Mapping[str, object],
    excluded: frozenset[str],
    items: _Items,
) -> list[_Attempt]:
    """Every reconstructed attempt in scope, with its loss step. An attempt
    of an excluded account (none by R7, derive refuses the reverse order)
    is governance's, counted nowhere: the universe is the same in the three
    datasets, so their attempt totals agree."""
    scope_sql, params = _scope(filters, "a.question_id")
    attempts: list[_Attempt] = []
    for r in conn.execute(_ATTEMPTS_SQL + scope_sql + " ORDER BY a.attempt_id", params):
        if r["student_id"] in excluded:
            continue
        flags = _flags(r["qc_flags_json"])
        if _FLAG_NO_BOARD in flags:
            loss = 3
        elif r["readiness"] != qc.READY:
            loss = 6
        else:
            loss = _SURVIVES
        attempts.append(
            _Attempt(
                attempt_id=r["attempt_id"],
                student_id=r["student_id"],
                question_id=r["question_id"],
                canonical_id=items.canonical(r["question_id"]),
                session_id=r["session_id"],
                started_at=r["started_at"],
                ended_at=r["ended_at"],
                offering=r["import_sha256"] or NONE,
                flags=flags,
                flags_json=r["qc_flags_json"],
                readiness=r["readiness"],
                loss=loss,
                has_anchor=r["has_anchor"] == 1,
                first_check_correct=r["first_check_correct"],
                eventual_correct=r["eventual_correct"],
            )
        )
    return attempts


_Windows = dict[tuple[str, str, str | None], list[_Attempt]]


def _windows(attempts: list[_Attempt]) -> _Windows:
    """The attempt windows by (student, logged identifier, session)."""
    windows: _Windows = defaultdict(list)
    for a in attempts:
        if a.started_at is not None and a.ended_at is not None:
            windows[(a.student_id, a.question_id, a.session_id)].append(a)
    return windows


def _candidates(
    windows: _Windows, student_id: str | None, question_id: str | None,
    session_id: str | None, timestamp: str | None,
) -> list[_Attempt]:
    """The attempts whose window holds the row (the window rule, as strings),
    in attempt-id order. One under the default segmentation except when a
    session change falls inside a tied second: within a session the gap and
    page-view rules leave consecutive windows disjoint, but a session
    sequence A → B → A at one timestamp gives two A windows that both hold
    it (a reset split, off by default, can do the same within a session)."""
    if timestamp is None:
        return []
    return [
        a for a in windows.get((student_id, question_id, session_id), ())
        if a.started_at <= timestamp <= a.ended_at
    ]


# reconstruct's action order (`_timeline`): timestamp, then the numeric
# problem_view, then rowid, the order its segments are contiguous in.
_ORDERED_ROWS_SQL = (
    "SELECT e.event_id, e.student_id, e.problem_name, d.kind "
    "FROM events e JOIN dnd_events d ON d.event_id = e.event_id "
    "WHERE d.kind != 'page_view'"
)
_ORDER = (
    " ORDER BY e.student_id, e.problem_name, e.timestamp, "
    "CAST(e.problem_view AS INTEGER), e.rowid"
)
_Neighbours = tuple[int | None, str | None, int | None]


class _Membership:
    """Which attempt holds a row (the Copilot round on PR #239). ``members``
    is the replay's own record, ``dnd_configurations.event_id`` → attempt,
    for every row it replayed; ``neighbours`` gives a row the replay skipped
    (off-board or unparseable: no configuration) its nearest replayed
    neighbours in action order (the attempt and kind of the one before, the
    attempt of the one after); the windows are the fallback's candidates."""

    def __init__(self, attempts, members, neighbours, windows) -> None:
        self.attempts: dict[int, _Attempt] = attempts
        self.members: dict[str, int] = members
        self.neighbours: dict[str, _Neighbours] = neighbours
        self.windows: _Windows = windows

    def attempt_of(self, r: sqlite3.Row) -> _Attempt | None:
        """The row's attempt, or None when no attempt holds it (a stray).
        A replayed row: its member attempt. A skipped row: the attempt whose
        window holds it; of two (a session change inside a tied second), the
        one a replayed neighbour places it in, segments are contiguous in
        action order and a session change is always a boundary, so the
        neighbour on the row's side of the boundary shares its session and
        window; both neighbours in candidates (a reset split, off by
        default) means the row follows the reset exactly when the neighbour
        before it is one; no replayed neighbour at all leaves the lowest
        attempt id."""
        attempt = self.attempts.get(self.members.get(r["event_id"], -1))
        if attempt is not None:
            return attempt
        candidates = _candidates(
            self.windows, r["student_id"], r["problem_name"], r["session_id"], r["timestamp"],
        )
        if len(candidates) <= 1:
            return candidates[0] if candidates else None
        ids = {a.attempt_id: a for a in candidates}
        before, before_kind, after = self.neighbours.get(r["event_id"], (None, None, None))
        if before in ids and after in ids and before != after:
            return ids[after] if before_kind == "reset" else ids[before]
        if before in ids:
            return ids[before]
        if after in ids:
            return ids[after]
        return candidates[0]


def _membership(
    conn: sqlite3.Connection, filters: Mapping[str, object], attempts: list[_Attempt]
) -> _Membership:
    """The replay's record of which attempt holds each row, plus the
    neighbours of the rows it skipped: one ordered pass over the derived
    non-page rows in scope, grouped by student and logged identifier."""
    by_id = {a.attempt_id: a for a in attempts}
    members = {
        r["event_id"]: r["attempt_id"]
        for r in conn.execute(
            "SELECT event_id, attempt_id FROM dnd_configurations WHERE event_id IS NOT NULL"
        )
    }
    neighbours: dict[str, _Neighbours] = {}
    scope_sql, params = _scope(filters)
    group_key: tuple | None = None
    group: list[tuple[str, int | None, str]] = []  # (event_id, member attempt, kind)

    def resolve(rows: list[tuple[str, int | None, str]]) -> None:
        last: tuple[int | None, str | None] = (None, None)
        before: dict[str, tuple[int | None, str | None]] = {}
        for event_id, attempt_id, kind in rows:
            if attempt_id is not None:
                last = (attempt_id, kind)
            else:
                before[event_id] = last
        nxt: int | None = None
        for event_id, attempt_id, _kind in reversed(rows):
            if attempt_id is not None:
                nxt = attempt_id
            else:
                neighbours[event_id] = (*before[event_id], nxt)

    for r in conn.execute(_ORDERED_ROWS_SQL + scope_sql + _ORDER, params):
        key = (r["student_id"], r["problem_name"])
        if key != group_key:
            resolve(group)
            group, group_key = [], key
        group.append((r["event_id"], members.get(r["event_id"]), r["kind"]))
    resolve(group)
    return _Membership(by_id, members, neighbours, _windows(attempts))


# ---------------------------------------------------------------------------
# flow
# ---------------------------------------------------------------------------
_ROWS_SQL = (
    "SELECT e.event_id, e.student_id, e.problem_name, e.selection, e.session_id, "
    "e.timestamp, i.file_hash AS import_sha256, "
    "d.kind, d.parse_ok, d.source_label, d.zone_label, d.configuration_json "
    "FROM events e "
    "LEFT JOIN raw_imports i ON i.import_id = e.import_id "
    "LEFT JOIN dnd_events d ON d.event_id = e.event_id "
    "WHERE 1 = 1"
)


def _row_loss(
    r: sqlite3.Row, excluded: frozenset[str], membership: _Membership, items: _Items
) -> tuple[int, bool]:
    """The step at which one raw row leaves the chain (or ``_SURVIVES``),
    and whether the row is a *stray*, a card-to-zone row of a retained
    account that no attempt holds (:meth:`_Membership.attempt_of`: not a
    replayed row of any attempt and inside no attempt window), or never
    derived: a step-3 loss under that step's fixed reason, expected zero,
    which :func:`flow_rows` surfaces as a count.

    1. its student is an excluded account (every Selection);
    2. its Selection is not one of the three claimed values;
    3. it is a stray, or its attempt carries ``missing_source_map``;
    4. its payload did not parse (``parse_ok = 0``), or it is a check whose
       payload is malformed;
    5. it is a check whose payload decodes with a cause, or a parseable move
       the replay skipped as off-board;
    6. its attempt is not ready.
    """
    student_id = r["student_id"]
    if student_id in excluded:
        return 1, False
    if r["selection"] not in DND_SELECTIONS:
        return 2, False
    question_id = r["problem_name"]
    attempt = membership.attempt_of(r)
    kind = r["kind"]
    if attempt is None or kind is None:
        return 3, True
    if attempt.loss == 3:
        return 3, False
    if not r["parse_ok"]:
        return 4, False
    if kind == "check":
        payload = _payload(r["configuration_json"])
        if payload is None:
            return 4, False
        if _check_cause(payload, question_id, items.shape(question_id)) is not None:
            return 5, False
    elif kind in _MOVE_KINDS:
        if _off_board(r["source_label"], r["zone_label"], question_id, items.shape(question_id)):
            return 5, False
    if attempt.loss == 6:
        return 6, False
    return _SURVIVES, False


class _Tally:
    """The counters of one breakdown key (pooled, or one offering)."""

    def __init__(self) -> None:
        # Rows and checks: the total in scope and the count lost at each
        # step (index = step; step 0 loses nothing), plus, for n_students,
        # each student's maximum loss step over their rows.
        self.events_total = 0
        self.events_lost = [0] * 7
        self.events_students: dict[str, int] = {}
        self.checks_total = 0
        self.checks_lost = [0] * 7
        self.checks_students: dict[str, int] = {}
        # The derived quantities: each entity's maximum loss step over its
        # DnD rows (steps 0-2) and over its attempts (steps 3-6).
        self.row_entities: dict[str, dict[object, int]] = {q: {} for q in _DERIVED}
        self.attempt_entities: dict[str, dict[object, int]] = {q: {} for q in _DERIVED}
        # Attempts: the universe, the losses, and the distinct students
        # among the survivors of steps 3 (= 4 = 5), 6, 7 and 8.
        self.attempts_total = 0
        self.attempts_lost = {3: 0, 6: 0, 7: 0, 8: 0}
        self.attempt_students: dict[int, set[str]] = {3: set(), 6: set(), 7: set(), 8: set()}

    def add_row(self, r: sqlite3.Row, loss: int, canonical: str | None) -> None:
        student_id, question_id = r["student_id"], r["problem_name"]
        self.events_total += 1
        if loss <= 6:
            self.events_lost[loss] += 1
        if student_id is not None:
            _bump(self.events_students, student_id, loss)
        if r["selection"] == _CHECK_SELECTION:
            self.checks_total += 1
            if loss <= 6:
                self.checks_lost[loss] += 1
            if student_id is not None:
                _bump(self.checks_students, student_id, loss)
        if r["selection"] not in DND_SELECTIONS:
            return
        entities = self.row_entities
        if student_id is not None:
            _bump(entities["students"], student_id, loss)
        if question_id is not None:
            _bump(entities["items_logged"], question_id, loss)
            _bump(entities["items_canonical"], canonical, loss)
            if student_id is not None:
                _bump(entities["item_traces_logged"], (student_id, question_id), loss)
                _bump(entities["item_traces_canonical"], (student_id, canonical), loss)

    def add_attempt(self, a: _Attempt) -> None:
        self.attempts_total += 1
        if a.loss == 3:
            self.attempts_lost[3] += 1
        else:
            self.attempt_students[3].add(a.student_id)
            if a.loss == 6:
                self.attempts_lost[6] += 1
            else:
                self.attempt_students[6].add(a.student_id)
                if a.has_anchor:
                    self.attempt_students[7].add(a.student_id)
                else:
                    self.attempts_lost[7] += 1
                if a.rq3:
                    self.attempt_students[8].add(a.student_id)
                else:
                    self.attempts_lost[8] += 1
        entities = self.attempt_entities
        _bump(entities["students"], a.student_id, a.loss)
        _bump(entities["items_logged"], a.question_id, a.loss)
        _bump(entities["items_canonical"], a.canonical_id, a.loss)
        _bump(entities["item_traces_logged"], (a.student_id, a.question_id), a.loss)
        _bump(entities["item_traces_canonical"], (a.student_id, a.canonical_id), a.loss)

    def _derived_out(self, quantity: str, step: int) -> int:
        source = self.row_entities if step <= 2 else self.attempt_entities
        return _survivors(source[quantity], step)

    def flow(self, breakdown: str, key: str) -> list[dict]:
        rows: list[dict] = []

        def emit(step: int, quantity: str, rows_in, rows_lost, rows_out, n_students) -> None:
            rows.append({
                "step": step,
                "step_name": STEP_NAMES[step],
                "quantity": quantity,
                "breakdown": breakdown,
                "key": key,
                "rows_in": rows_in,
                "rows_lost": rows_lost,
                "rows_out": rows_out,
                "reason": STEP_REASONS[step],
                "n_students": n_students,
            })

        # Rows and checks: counted through step 6; empty at the attempt-only
        # steps 7 and 8.
        for quantity, total, lost, students in (
            ("events", self.events_total, self.events_lost, self.events_students),
            ("checks", self.checks_total, self.checks_lost, self.checks_students),
        ):
            remaining = total
            for step in range(len(STEP_NAMES)):
                if step > 6:
                    emit(step, quantity, None, None, None, None)
                    continue
                rows_in = remaining
                remaining = rows_in - lost[step]
                emit(step, quantity, rows_in, lost[step], remaining, _survivors(students, step))

        # Students, items and traces: by survival; n_students is the
        # surviving students of the same step.
        for quantity in _DERIVED:
            previous = 0
            for step in range(len(STEP_NAMES)):
                if step > 6:
                    emit(step, quantity, None, None, None, None)
                    continue
                rows_out = self._derived_out(quantity, step)
                rows_in = rows_out if step == 0 else previous
                emit(
                    step, quantity, rows_in, rows_in - rows_out, rows_out,
                    self._derived_out("students", step),
                )
                previous = rows_out

        # Attempts: no value before step 3 (J10); losses at 3 and 6; steps 7
        # and 8 are siblings, both from Ready.
        total = self.attempts_total
        aligned = total - self.attempts_lost[3]
        ready = aligned - self.attempts_lost[6]
        students = self.attempt_students
        attempt_steps = {
            3: (total, self.attempts_lost[3], aligned, len(students[3])),
            4: (aligned, 0, aligned, len(students[3])),
            5: (aligned, 0, aligned, len(students[3])),
            6: (aligned, self.attempts_lost[6], ready, len(students[6])),
            7: (ready, self.attempts_lost[7], ready - self.attempts_lost[7], len(students[7])),
            8: (ready, self.attempts_lost[8], ready - self.attempts_lost[8], len(students[8])),
        }
        for step in range(len(STEP_NAMES)):
            if step < 3:
                emit(step, "attempts", None, None, None, None)
            else:
                emit(step, "attempts", *attempt_steps[step])
        return rows


def flow_rows(conn: sqlite3.Connection, filters: Mapping[str, object]) -> list[dict]:
    """The ``flow`` dataset: every row of ``events`` in scope walked once
    through the chain (:func:`_row_loss`), the attempts once
    (:func:`_load_attempts`), pooled and per offering; sorted by (step,
    quantity, breakdown, key)."""
    scope_sql, params = _scope(filters)
    excluded = account_roles.excluded_ids(conn)
    items = _Items(conn)
    attempts = _load_attempts(conn, filters, excluded, items)
    membership = _membership(conn, filters, attempts)

    tallies: dict[str, _Tally] = {POOLED: _Tally()}

    def tally(offering: str) -> _Tally:
        if offering not in tallies:
            tallies[offering] = _Tally()
        return tallies[offering]

    for a in attempts:
        tallies[POOLED].add_attempt(a)
        tally(a.offering).add_attempt(a)
    strays = 0
    for r in conn.execute(_ROWS_SQL + scope_sql, params):
        loss, stray = _row_loss(r, excluded, membership, items)
        strays += stray
        question_id = r["problem_name"]
        canonical = items.canonical(question_id) if question_id is not None else None
        tallies[POOLED].add_row(r, loss, canonical)
        tally(r["import_sha256"] or NONE).add_row(r, loss, canonical)
    if strays:
        # A count only (M6): no attempt holds the rows (not replayed, inside
        # no attempt window) or they were never derived, zero on a
        # consistent database.
        log.warning(
            "dnd flow: %d card-to-zone row(s) in scope are held by no attempt "
            "(no replayed configuration, inside no attempt window) or were "
            "never derived; counted under step 3 (source alignment) with the "
            "step's fixed reason",
            strays,
        )

    rows: list[dict] = []
    for offering, t in tallies.items():
        if offering == POOLED:
            rows.extend(t.flow(POOLED, POOLED))
        else:
            rows.extend(t.flow("by_offering", offering))
    rows.sort(key=lambda row: (row["step"], row["quantity"], row["breakdown"], row["key"]))
    return rows


# ---------------------------------------------------------------------------
# readiness_split
# ---------------------------------------------------------------------------
def _count_cells(
    cells: dict[tuple, tuple[int, set[str]]], key: tuple, student_id: str
) -> None:
    n, students = cells.get(key, (0, set()))
    students.add(student_id)
    cells[key] = (n + 1, students)


def readiness_split_rows(conn: sqlite3.Connection, filters: Mapping[str, object]) -> list[dict]:
    """The ``readiness_split`` dataset: every reconstructed attempt in scope
    by readiness, under six breakdowns; sorted by (breakdown, key,
    readiness)."""
    excluded = account_roles.excluded_ids(conn)
    items = _Items(conn)
    attempts = _load_attempts(conn, filters, excluded, items)
    source_hash = {
        r["question_id"]: r["source_hash"]
        for r in conn.execute("SELECT question_id, source_hash FROM dnd_boards")
    }
    cells: dict[tuple, tuple[int, set[str]]] = {}
    for a in attempts:
        readiness = a.readiness if a.readiness is not None else NOT_COMPUTED
        keys = [
            (POOLED, POOLED),
            ("by_offering", a.offering),
            ("by_item", a.canonical_id),
            ("by_source_version", source_hash.get(a.canonical_id, NONE)),
            ("by_flag_combination", a.flags_json if a.flags_json is not None else NONE),
        ]
        flags = [f for f in qc.FLAG_ORDER if f in a.flags]
        flags += sorted(a.flags - set(qc.FLAG_ORDER))
        keys.extend(("by_flag", f) for f in flags or [NO_FLAG])
        for breakdown, key in keys:
            _count_cells(cells, (breakdown, key, readiness), a.student_id)
    return [
        {
            "breakdown": breakdown,
            "key": key,
            "readiness": readiness,
            "n_attempts": n,
            "n_students": len(students),
        }
        for (breakdown, key, readiness), (n, students) in sorted(cells.items())
    ]


# ---------------------------------------------------------------------------
# exclusion_probe
# ---------------------------------------------------------------------------
def _level(value: int | None) -> str:
    """A stored 0/1 outcome as its probe level; ``none`` for NULL (no scored
    check)."""
    return NONE if value is None else str(int(value))


def exclusion_probe_rows(conn: sqlite3.Connection, filters: Mapping[str, object]) -> list[dict]:
    """The ``exclusion_probe`` dataset: the pooled cross-tab with its two
    marginal families, and the two families per offering (R14); only the
    cells that occur; sorted by every key column."""
    excluded = account_roles.excluded_ids(conn)
    items = _Items(conn)
    attempts = _load_attempts(conn, filters, excluded, items)
    cells: dict[tuple, tuple[int, set[str]]] = {}
    for a in attempts:
        readiness = a.readiness if a.readiness is not None else NOT_COMPUTED
        anchor = "1" if a.has_anchor else "0"
        first = _level(a.first_check_correct)
        eventual = _level(a.eventual_correct)
        marginals = (
            (readiness, ALL, ALL, eventual),
            (ALL, anchor, ALL, eventual),
        )
        _count_cells(cells, (POOLED, POOLED, readiness, anchor, first, eventual), a.student_id)
        for cell in marginals:
            _count_cells(cells, (POOLED, POOLED, *cell), a.student_id)
            _count_cells(cells, ("by_offering", a.offering, *cell), a.student_id)
    return [
        {
            "breakdown": breakdown,
            "key": key,
            "readiness": readiness,
            "has_eligible_anchor": anchor,
            "first_check_correct": first,
            "eventual_correct": eventual,
            "n_attempts": n,
            "n_students": len(students),
        }
        for (breakdown, key, readiness, anchor, first, eventual), (n, students)
        in sorted(cells.items())
    ]
