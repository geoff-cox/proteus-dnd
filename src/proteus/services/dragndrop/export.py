"""
CSV export for the DnD service (plan item B3.7).

The same files-as-views pattern the SA service uses (``shortanswer.export``):
each dataset is a flat, joined view of one stage's table, the shape a
reviewer wants in a spreadsheet, and the CLI dispatches off the ``DATASETS``
registry, which is also published on the service descriptor's
``export_datasets`` so the shared export plumbing can discover it.

Eight datasets cover the DnD outcome, process, and modeling layers:

* ``checks``: every check with its reconstruction context (the logged
  configuration, the attempt it belongs to, the B3.2 cross-check flag, the
  anchor eligibility with its cause, dnd/0012 and dnd/0015, and the
  seeded-replay marker);
* ``check_scores``: the per-check scores (every metric side by side, B3.3);
* ``attempt_metrics``: the per-attempt process metrics (B3.5);
* ``segments``: the materialized check/reset/session/active_work segments
  (F3 + the F9 idle-threshold type, the journal's ``segments``
  analysis-ready table);
* ``configurations``: every reconstructed configuration in replay order
  (the persisted walk the F24 trajectory charts read);
* ``boards``: the registered board specs with the F18 item covariates;
* ``modeling_table``: the F20 attempt-level modeling table
  (subsec-modeling-table): identifiers, unit, outcomes, design covariates,
  features, QC flags, readiness in one row; and
* ``item_summary``: the F20 per-question diagnostic summary
  (tab-item-diagnostic-summary-fields).

Three more close the registry (E-02, PR 2 of 2, ruling R3; the
:mod:`attrition` module assembles them in Python through the
``build_rows`` hook rather than one SQL statement): ``flow``, the
ordered exclusion ledger, ``readiness_split`` and ``exclusion_probe``.
They carry no student id or pseudonym: ``import_sha256`` values,
canonical item identifiers, flag names and counts only.

The F20 crossing/nesting identifiers (course / class / week / chapter, the
``events`` filter columns) ride every event- and attempt-shaped dataset:
checks and scores join their own event directly; attempt-shaped rows take
the representative-event rollup (``_ATTEMPT_IDS_JOIN``).

The offering key (E-08 / E-02, ruling R9 (a), 2026-10-18) closes the
``checks``, ``attempt_metrics``, ``configurations`` and ``modeling_table``
rows as ``import_sha256``: ``raw_imports.file_hash``, the sha256 the
manifest's ``body.imports[]`` carries beside the offering pseudonym, so an
archive-only reader (the table generator, the attrition ledger) can
stratify by offering, term and development status without the private
map. The pseudonym itself is never exported. Additive, last column, every
pre-existing column byte-identical (the PR #219 posture); ``segments``,
``check_scores``, ``boards`` and ``item_summary`` do not carry it. Checks
read the key off their own event; attempt-shaped rows read it off the
attempt's *window* (``_ATTEMPT_IMPORT_JOIN``: the events of the attempt's
student and logged identifier, in the attempt's session, between
``started_at`` and ``ended_at``),
not off the configurations, so an attempt whose every move was skipped in
replay (an off-board-only trace: X0 has no event and a skipped move writes
no configuration) still carries its import (PR #236 Copilot review,
2026-10-19).

Leak vocabulary (F28): the stored ``decoy_source_leak`` / ``decoy_target_leak``
columns are the journal's **decoy-card leak** L_P and **decoy-slot leak** L_R
(subsec-decoy-leak-features), counts, not indicators. The column names stay as
stored (a rename would be a migration plus export/UI churn); the dataset
descriptions below carry the journal names instead, so a reader who meets the
column in a CSV header can find its definition in the book.

Every dataset accepts the same ``question_id`` filter; each SQL builder uses
it where it applies.
"""

from __future__ import annotations

import csv
import json
import logging
import sqlite3
from dataclasses import dataclass
from typing import IO, Callable, Iterable, Sequence

from proteus.services.dragndrop import aliases, attrition, cohort, errors, policy

# "Common" in the item summary's "number of common wrong states" is not
# fixed by the journal (tab-item-diagnostic-summary-fields names the field,
# not the threshold): a non-key final state counts as common when at least
# this many attempts ended on it. Conservative default: shared at all.
# TODO(rule 11): dev/open-todos.md item 22.
DEFAULT_COMMON_STATE_MIN = 2

# The modeling chapter's crossing/nesting identifiers (course, class /
# semester, week, chapter) live on `events`. Checks carry their own event so
# the join is DIRECT; attempt-shaped rows reach events through any of the
# attempt's action events, the `filters.filtered_attempts` rationale (its
# events share one student/course/class, so any is representative; MAX makes
# the pick deterministic). This rollup is unchanged since F20, so every
# pre-existing column stays byte-identical.
_ATTEMPT_IDS_JOIN = (
    "LEFT JOIN (SELECT c.attempt_id AS attempt_id, "
    "MAX(e.course_id) AS course_id, MAX(e.class_id) AS class_id, "
    "MAX(e.week_no) AS week_no, MAX(e.chapter) AS chapter "
    "FROM dnd_configurations c JOIN events e ON e.event_id = c.event_id "
    "GROUP BY c.attempt_id) ids "
)

# The offering key of the attempt-shaped datasets, import_sha256 (E-08 /
# E-02, ruling R9 (a), 2026-10-18): raw_imports.file_hash, the sha256 the
# manifest's body.imports[] carries beside the pseudonym; the pseudonym
# itself is never exported. It is read off the attempt's WINDOW, not off its
# configurations (PR #236 Copilot review, 2026-10-19): an attempt whose
# every move was skipped in replay (an off-board-only trace) has no
# configuration with an event (X0 is written with event_id NULL; a skipped
# move writes no configuration), yet it is an attempt of some import, and an
# empty key would make the archive-only generator refuse the whole archive.
# The window is the events of the attempt's student on its logged
# identifier, in the attempt's session, with started_at <= timestamp <=
# ended_at; started_at / ended_at are the first and last event timestamps of
# the segment, opaque sortable strings exactly like events.timestamp
# (CLAUDE.md rule 6: compared as strings, never cleaned or parsed). The
# session qualifier (second Copilot round, 2026-10-19) is what makes the
# window the segment: a session change is an attempt boundary under the
# default segmentation, so two attempts of one student on one item can share
# a timestamp only across sessions, a bare time window would then hold both
# attempts' events and MAX would hand both the same import. Within one
# session no default rule splits tied timestamps (a page view must fall
# strictly between, a gap of zero is no gap), so the qualified window is the
# segment's events (plus any other logged row of that student, item and
# session in the interval, the same import); under a configuration that lets
# an attempt span sessions the first event is still in the attempt's session
# (dnd_attempts.session_id is segment[0]'s), so the key is never lost. `IS`
# so that a NULL session matches a NULL session; the window needs a non-NULL
# started_at, which every DataShop row has (Time is required). That an
# attempt's events share one import, so MAX is only the deterministic pick,
# is a property of ingest and of the frozen corpus, not of this join: a row
# two exports share collides on event_id and is skipped, attributed to the
# first import (core/ingest/events.py), and the frozen run ingests one export
# per offering. A corpus with overlapping or date-split exports of one
# offering, or a configuration whose attempt spans sessions drawn from two
# imports, would need the import persisted on dnd_attempts (a migration and
# a reconstruct stage bump), dev/open-todos.md item 28.
_ATTEMPT_IMPORT_JOIN = (
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

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Dataset:
    name: str
    columns: tuple[str, ...]
    # A one-clause gloss for pickers, what the `proteus dnd export` help
    # shows beside each choice, and what a generic export surface can label a
    # dataset with. Deliberately required: a dataset added without one is a
    # TypeError at import, which is how the CLI help stopped listing four of
    # eight datasets in the first place.
    summary: str
    description: str
    # The row source, exactly one of the two (E-02 PR 2, the additive
    # hook): build_sql(filters) -> (sql, params), one statement whose result
    # columns are the dataset's (plus derive_row's inputs); or
    # build_rows(conn, filters) -> an iterable of dicts keyed by the
    # dataset's columns, for a dataset assembled in Python (the attrition
    # ledger's three). `filters` is {"question_id": ...} either way.
    build_sql: Callable[[dict], tuple[str, list]] | None = None
    # Optional Python-side derivation for columns SQL cannot express (the F18
    # board covariates): called per row as derive_row(conn, row_dict) and
    # returns extra {column: value} entries merged before writing.
    derive_row: Callable | None = None
    build_rows: Callable[[sqlite3.Connection, dict], Iterable[dict]] | None = None

    def __post_init__(self) -> None:
        if (self.build_sql is None) == (self.build_rows is None):
            raise TypeError(
                f"dataset {self.name!r}: exactly one of build_sql / build_rows "
                "must be set"
            )


def _checks_sql(filters: dict) -> tuple[str, list]:
    # One row per check, with the reconstructed attempt/cross-check context
    # joined in (LEFT, so checks that did not reconstruct still appear).
    sql = (
        "SELECT d.event_id, e.student_id, e.problem_name AS question_id, "
        "e.course_id, e.class_id, e.week_no, e.chapter, "
        "e.session_id, e.timestamp, d.feedback, d.parse_ok, "
        "d.configuration_json, c.attempt_id, c.seq, c.matches_input, "
        # Anchor eligibility (dnd/0012): 1 = reconstructed and compared (no
        # cause stored), 0 = stored not comparable with its cause, NULL = the
        # check never reconstructed. The eligible anchors are the denominator
        # of every agreement rate (subsec-anchor-validation).
        "CASE WHEN c.config_id IS NULL THEN NULL "
        "WHEN c.anchor_cause IS NULL THEN 1 ELSE 0 END AS eligible, "
        "c.anchor_cause, c.disclosure_layer, c.graded_ordinal, "
        # dnd/0014: the item identifier after the alias (equals
        # question_id except for a retired identifier).
        "COALESCE(al.canonical_question_id, e.problem_name) AS canonical_question_id, "
        # dnd/0015: NULL unless the pass seeded resumed attempts (knob 10);
        # then 1 on a seeded attempt's checks, 0 elsewhere.
        "c.seeded, "
        # the offering key (E-08 / E-02, ruling R9 (a), 2026-10-18):
        # raw_imports.file_hash, the sha256 the manifest's body.imports[]
        # carries beside the pseudonym; the pseudonym itself is never
        # exported. The check's own event names the import (direct join).
        "i.file_hash AS import_sha256 "
        "FROM dnd_events d "
        "JOIN events e ON e.event_id = d.event_id "
        "LEFT JOIN raw_imports i ON i.import_id = e.import_id "
        "LEFT JOIN dnd_configurations c "
        "  ON c.event_id = d.event_id AND c.is_check = 1 "
        "LEFT JOIN dnd_question_aliases al ON al.alias_question_id = e.problem_name "
        "WHERE d.kind = 'check'"
    )
    params: list = []
    if filters.get("question_id"):
        sql += " AND e.problem_name = ?"
        params.append(filters["question_id"])
    sql += " ORDER BY e.problem_name, e.student_id, e.timestamp, e.rowid"
    return sql, params


def _check_scores_sql(filters: dict) -> tuple[str, list]:
    # One row per scored check: every scoring variant beside the student,
    # feedback, and attempt context.
    sql = (
        "SELECT s.event_id, s.attempt_id, s.question_id, e.student_id, "
        "e.course_id, e.class_id, e.week_no, e.chapter, "
        "d.feedback, s.sigma, s.distance, s.g, s.sigma_tilde, s.per_card_score, "
        "s.all_or_nothing, s.omissions, s.wrong_slot, "
        "s.decoy_source_leak, s.decoy_target_leak, "
        "s.per_card_json, "
        "COALESCE(al.canonical_question_id, s.question_id) AS canonical_question_id "
        "FROM dnd_check_scores s "
        "JOIN dnd_events d ON d.event_id = s.event_id "
        "JOIN events e ON e.event_id = s.event_id "
        "LEFT JOIN dnd_question_aliases al ON al.alias_question_id = s.question_id"
    )
    params: list = []
    if filters.get("question_id"):
        sql += " WHERE s.question_id = ?"
        params.append(filters["question_id"])
    sql += " ORDER BY s.question_id, e.student_id, e.timestamp, s.event_id"
    return sql, params


def _attempt_metrics_sql(filters: dict) -> tuple[str, list]:
    # One row per attempt: the segmentation context joined with its process
    # metrics (LEFT, so an un-analyzed attempt still appears).
    sql = (
        "SELECT a.attempt_id, a.student_id, a.question_id, "
        "ids.course_id, ids.class_id, ids.week_no, ids.chapter, "
        "a.attempt_index, "
        "a.session_id, a.started_at, a.ended_at, a.segmentation_rule, "
        "a.qc_flags_json, a.readiness, a.feedback_policy, "
        "m.n_moves, m.n_noop_moves, m.n_takebacks, m.n_resets, m.n_checks, "
        "m.n_fixing, m.n_lateral, m.n_breaking, m.excess_moves, "
        "m.path_excess, m.cadence_json, m.post_check_gains_json, "
        "m.responsiveness_json, "
        "m.total_time_s, m.time_to_first_move_s, m.time_to_first_check_s, "
        "m.median_move_gap_s, m.max_action_gap_s, m.active_time_s, "
        "m.idle_threshold_s, "
        "m.first_check_distance, m.final_check_distance, "
        "m.first_check_correct, m.eventual_correct, m.correct_check_ordinal, "
        "m.n_distinct_states, m.n_repeated_states, "
        "m.checked_every_move, m.convergence_json, "
        # F-NEW: the phase-window block (pre / repair) closes the row, so
        # every pre-existing column keeps its position.
        "m.n_effective_moves_pre, m.n_excess_moves_pre, m.n_takebacks_pre, "
        "m.n_resets_pre, m.active_time_pre_s, m.n_effective_moves_repair, "
        "m.n_takebacks_repair, m.n_resets_repair, m.n_checks_repair, "
        "m.active_time_repair_s, m.n_fix_moves_repair, "
        # dnd/0014: the item identifier the attempt was bound under (the
        # stored provenance marker, falling back to the logged identifier).
        "COALESCE(a.canonical_question_id, a.question_id) AS canonical_question_id, "
        # dnd/0015: 1 when the attempt has a prior attempt by the same
        # student on the same logged identifier, and that attempt's id.
        "CASE WHEN a.prior_attempt_id IS NULL THEN 0 ELSE 1 END AS resumed, "
        "a.prior_attempt_id, "
        # the offering key (E-08 / E-02, ruling R9 (a), 2026-10-18), from
        # the attempt-window rollup, see _ATTEMPT_IMPORT_JOIN.
        "imp.import_sha256 "
        "FROM dnd_attempts a "
        "LEFT JOIN dnd_attempt_metrics m ON m.attempt_id = a.attempt_id "
        + _ATTEMPT_IDS_JOIN
        + "ON ids.attempt_id = a.attempt_id "
        + _ATTEMPT_IMPORT_JOIN
        + "ON imp.attempt_id = a.attempt_id"
    )
    params: list = []
    if filters.get("question_id"):
        sql += " WHERE a.question_id = ?"
        params.append(filters["question_id"])
    sql += " ORDER BY a.question_id, a.student_id, a.attempt_index"
    return sql, params


def _segments_sql(filters: dict) -> tuple[str, list]:
    # One row per materialized segment, with the owning attempt's context
    # joined in (student/question, for flat per-segment analysis).
    sql = (
        "SELECT s.segment_id, s.attempt_id, a.student_id, a.question_id, "
        "ids.course_id, ids.class_id, ids.week_no, ids.chapter, "
        "a.attempt_index, s.segment_type, s.segment_index, s.start_seq, "
        "s.end_seq, "
        "COALESCE(a.canonical_question_id, a.question_id) AS canonical_question_id "
        "FROM dnd_segments s "
        "JOIN dnd_attempts a ON a.attempt_id = s.attempt_id "
        + _ATTEMPT_IDS_JOIN
        + "ON ids.attempt_id = s.attempt_id"
    )
    params: list = []
    if filters.get("question_id"):
        sql += " WHERE a.question_id = ?"
        params.append(filters["question_id"])
    sql += (
        " ORDER BY a.question_id, a.student_id, a.attempt_index, "
        "s.segment_type, s.segment_index"
    )
    return sql, params


def _configurations_sql(filters: dict) -> tuple[str, list]:
    # One row per reconstructed configuration in replay order, the persisted
    # walk (seq 0 is X0, event_id/kind NULL there); check rows carry the
    # FP-3 disclosure context alongside.
    sql = (
        "SELECT c.config_id, c.attempt_id, a.student_id, a.question_id, "
        "ids.course_id, ids.class_id, ids.week_no, ids.chapter, "
        "a.attempt_index, c.seq, c.event_id, d.kind, c.is_check, c.is_noop, "
        "c.matches_input, c.anchor_cause, c.disclosure_layer, "
        "c.graded_ordinal, c.s_json, "
        "COALESCE(a.canonical_question_id, a.question_id) AS canonical_question_id, "
        # dnd/0015: the seeded-replay marker (NULL unless the pass seeded).
        "c.seeded, "
        # the offering key (E-08 / E-02, ruling R9 (a), 2026-10-18), from
        # the attempt-window rollup, see _ATTEMPT_IMPORT_JOIN.
        "imp.import_sha256 "
        "FROM dnd_configurations c "
        "JOIN dnd_attempts a ON a.attempt_id = c.attempt_id "
        "LEFT JOIN dnd_events d ON d.event_id = c.event_id "
        + _ATTEMPT_IDS_JOIN
        + "ON ids.attempt_id = c.attempt_id "
        + _ATTEMPT_IMPORT_JOIN
        + "ON imp.attempt_id = c.attempt_id"
    )
    params: list = []
    if filters.get("question_id"):
        sql += " WHERE a.question_id = ?"
        params.append(filters["question_id"])
    sql += " ORDER BY a.question_id, a.student_id, a.attempt_index, c.seq"
    return sql, params


_EMPTY_SHAPE = {
    "num_slots": None, "state_space_size": None,
    "num_decoy_cards": None, "num_decoy_slots": None,
    "key_occupancy": None,
}


def _board_shape(row: dict) -> dict:
    """The board-derivable F18 covariates (tab-item-design-covariates),
    computed from the ``dnd_boards`` columns already on the row (``n_cards``,
    ``n_zones``, ``key_json``), no per-row board load, no card/zone content
    read, so large exports stay one query (PR #155 review).

    ``num_slots`` is R, the stored ``n_zones`` under its journal name (the
    tray is not a slot); ``num_zones`` = R+1 is deliberately not emitted
    (trivially derivable, and the checklist's covariate list omits it
    alongside ``num_cards``). All-``None`` for a row whose key is missing,
    unparseable, or inconsistent with the stored shape (a corrupt board
    reads as no covariates, never a crash).
    """
    n_cards, n_zones = row.get("n_cards"), row.get("n_zones")
    try:
        key = tuple(json.loads(row["key_json"]))
    except (KeyError, TypeError, ValueError):
        return dict(_EMPTY_SHAPE)
    if n_cards is None or n_zones is None or len(key) != n_cards:
        return dict(_EMPTY_SHAPE)
    try:
        key_occupancy = errors.occupancy(key, n_zones)
    except ValueError:  # a key zone off the stored slot count
        return dict(_EMPTY_SHAPE)
    return {
        "num_slots": n_zones,
        "state_space_size": (n_zones + 1) ** n_cards,
        "num_decoy_cards": sum(1 for zone in key if zone == 0),
        "num_decoy_slots": n_zones - len({zone for zone in key if zone != 0}),
        "key_occupancy": json.dumps(list(key_occupancy)),
    }


def _board_covariates(conn: sqlite3.Connection, row: dict) -> dict:
    """The F18 covariates for one boards row: the board shape plus the era
    covariates the board cannot know (the platform version at collection
    time decides ``feedback_policy`` / ``check_cost``); those carry the
    conservative configured defaults until a per-era record exists,
    open-todos item 21. The honest granular record is the per-attempt FP-3
    ``dnd_attempts.feedback_policy``.
    """
    return {
        **_board_shape(row),
        "feedback_policy": policy.DEFAULT_EXPORT_POLICY,
        "check_cost": policy.DEFAULT_CHECK_COST,
        # dnd/0014: the retired identifiers that resolve to this board.
        "aliases": ";".join(aliases.aliases_of(conn, row["question_id"])),
    }


def _modeling_table_sql(filters: dict) -> tuple[str, list]:
    # One row per attempt: the F20 modeling table (subsec-modeling-table),
    # identifiers, the unit marker, outcomes, design covariates, features,
    # QC flags, readiness, assembled from the analysis-ready tables, never
    # from raw logs. `feedback_policy` is the per-attempt FP-3 record (the
    # honest era source, open-todos item 21), not the item-level default.
    sql = (
        "SELECT 'attempt' AS unit, a.attempt_id, a.student_id, "
        "a.question_id, "
        "ids.course_id, ids.class_id, ids.week_no, ids.chapter, "
        "a.session_id, a.attempt_index, b.source_hash, "
        "a.feedback_policy, a.qc_flags_json, a.readiness, "
        # n_zones/key_json are helper columns for _board_shape, not emitted.
        "b.n_cards, b.n_zones, b.key_json, "
        "m.first_check_distance, m.final_check_distance, "
        "m.first_check_correct, m.eventual_correct, m.correct_check_ordinal, "
        "m.n_moves, m.n_noop_moves, m.n_takebacks, m.n_resets, m.n_checks, "
        "m.n_fixing, m.n_lateral, m.n_breaking, m.excess_moves, "
        "m.path_excess, m.n_distinct_states, m.n_repeated_states, "
        "m.cadence_json, m.post_check_gains_json, m.responsiveness_json, "
        "m.total_time_s, m.time_to_first_move_s, m.time_to_first_check_s, "
        "m.median_move_gap_s, m.max_action_gap_s, m.active_time_s, "
        "m.idle_threshold_s, m.checked_every_move, "
        # F-NEW: the phase-window block closes the row (see attempt_metrics).
        "m.n_effective_moves_pre, m.n_excess_moves_pre, m.n_takebacks_pre, "
        "m.n_resets_pre, m.active_time_pre_s, m.n_effective_moves_repair, "
        "m.n_takebacks_repair, m.n_resets_repair, m.n_checks_repair, "
        "m.active_time_repair_s, m.n_fix_moves_repair, "
        # dnd/0014: the item identifier (after the alias); the board join
        # below goes through it, so an aliased attempt carries its
        # successor's source version and design covariates.
        "COALESCE(a.canonical_question_id, a.question_id) AS canonical_question_id, "
        # dnd/0015: resumption (a column, not a QC flag), see attempt_metrics.
        "CASE WHEN a.prior_attempt_id IS NULL THEN 0 ELSE 1 END AS resumed, "
        "a.prior_attempt_id, "
        # the offering key (E-08 / E-02, ruling R9 (a), 2026-10-18), from
        # the attempt-window rollup, see _ATTEMPT_IMPORT_JOIN.
        "imp.import_sha256 "
        "FROM dnd_attempts a "
        "LEFT JOIN dnd_attempt_metrics m ON m.attempt_id = a.attempt_id "
        "LEFT JOIN dnd_boards b "
        "  ON b.question_id = COALESCE(a.canonical_question_id, a.question_id) "
        + _ATTEMPT_IDS_JOIN
        + "ON ids.attempt_id = a.attempt_id "
        + _ATTEMPT_IMPORT_JOIN
        + "ON imp.attempt_id = a.attempt_id"
    )
    params: list = []
    if filters.get("question_id"):
        sql += " WHERE a.question_id = ?"
        params.append(filters["question_id"])
    sql += " ORDER BY a.question_id, a.student_id, a.attempt_index"
    return sql, params


def _modeling_covariates(conn: sqlite3.Connection, row: dict) -> dict:
    """The board-shape covariates for one modeling row, plus ``check_cost``
    from the configured era default (open-todos item 21). ``feedback_policy``
    is NOT derived here, the SQL carries the per-attempt FP-3 record."""
    return {
        **_board_shape(row),
        "check_cost": policy.DEFAULT_CHECK_COST,
    }


def _item_summary_sql(filters: dict) -> tuple[str, list]:
    # The per-question skeleton (n_zones/key_json are _board_shape helper
    # columns, not emitted); everything else derives in Python.
    sql = (
        "SELECT question_id, source_hash, n_cards, n_zones, key_json "
        "FROM dnd_boards"
    )
    params: list = []
    if filters.get("question_id"):
        sql += " WHERE question_id = ?"
        params.append(filters["question_id"])
    sql += " ORDER BY question_id"
    return sql, params


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _item_summary(conn: sqlite3.Connection, row: dict) -> dict:
    """One question's item-level diagnostic summary
    (tab-item-diagnostic-summary-fields), aggregated from the persisted
    stages and the F11 cohort dispersion, the same sources the Explore
    charts read. Attempt aggregates skip NULL metrics (un-analyzed
    attempts); the dispersion and common-wrong-state fields are computed
    over each attempt's **final scored check** (the cheatsheet's default
    unit); ``feedback_policy`` is the item-level configured default
    (open-todos item 21, the per-attempt record lives on the modeling
    table).
    """
    qid = row["question_id"]
    # dnd/0014: the item aggregates every logged identifier bound to this
    # board, its own and its aliases.
    ids = aliases.item_ids(conn, qid)
    in_ids = f"IN ({', '.join('?' * len(ids))})"

    qc = {"n_attempts": 0, "n_replay_ok": 0, "n_parse_error": 0,
          "n_long_idle": 0}
    for a in conn.execute(
        f"SELECT qc_flags_json FROM dnd_attempts WHERE question_id {in_ids}", ids
    ):
        qc["n_attempts"] += 1
        try:
            flags = set(json.loads(a["qc_flags_json"] or "[]"))
        except (TypeError, ValueError):
            flags = set()
        qc["n_replay_ok"] += "replay_ok" in flags
        qc["n_parse_error"] += "parse_error" in flags
        qc["n_long_idle"] += "long_idle_gap" in flags

    agg = conn.execute(
        "SELECT AVG(m.first_check_correct) AS first_rate, "
        "AVG(m.eventual_correct) AS eventual_rate, "
        "AVG(m.first_check_distance) AS mean_first_d, "
        "AVG(m.final_check_distance) AS mean_final_d, "
        "AVG(m.n_moves) AS mean_moves, AVG(m.n_checks) AS mean_checks, "
        "AVG(CASE WHEN m.n_resets > 0 THEN 1.0 WHEN m.n_resets IS NULL "
        "THEN NULL ELSE 0.0 END) AS reset_rate "
        "FROM dnd_attempts a "
        "JOIN dnd_attempt_metrics m ON m.attempt_id = a.attempt_id "
        f"WHERE a.question_id {in_ids}",
        ids,
    ).fetchone()

    times = conn.execute(
        "SELECT m.active_time_s, m.time_to_first_check_s "
        "FROM dnd_attempts a "
        "JOIN dnd_attempt_metrics m ON m.attempt_id = a.attempt_id "
        f"WHERE a.question_id {in_ids}",
        ids,
    ).fetchall()

    errs = conn.execute(
        "SELECT AVG(omissions) AS mean_omissions, "
        "AVG(wrong_slot) AS mean_wrong_slot, "
        "AVG(decoy_source_leak) AS mean_card_leaks, "
        "AVG(decoy_target_leak) AS mean_slot_leaks "
        f"FROM dnd_check_scores WHERE question_id {in_ids}",
        ids,
    ).fetchone()

    # F11 dispersion under the final unit; None when nothing is scored yet.
    try:
        data = cohort.cohort_data(conn, qid, unit="final")
        mean_entropy, max_entropy = data.mean_entropy, data.max_entropy
    except ValueError:  # no synced board behind the row (defensive)
        mean_entropy = max_entropy = None

    # Common wrong states: each attempt's final scored check, grouped by
    # state; a non-key, non-empty state counts when >= DEFAULT_COMMON_STATE_MIN
    # attempts ended on it. The all-tray final (every card back at zone 0) is
    # abandonment, not a shared misconception, on the first real cohort it
    # was the top "common wrong state" on every affected question
    # (open-todos item 22, decided 2026-08-15), so it is excluded here and
    # surfaced as its own count. The key comes off the skeleton row (no
    # board load, PR #155 review).
    try:
        key = tuple(json.loads(row["key_json"]))
    except (KeyError, TypeError, ValueError):
        key = None
    n_common_wrong = 0
    n_abandoned = 0
    if key is not None:
        finals = conn.execute(
            "WITH final_checks AS ("
            "  SELECT c.attempt_id, c.s_json, "
            "  ROW_NUMBER() OVER (PARTITION BY c.attempt_id "
            "                     ORDER BY c.seq DESC) AS rn "
            "  FROM dnd_check_scores cs "
            "  JOIN dnd_configurations c "
            "    ON c.event_id = cs.event_id AND c.is_check = 1 "
            f"  WHERE cs.question_id {in_ids}) "
            "SELECT s_json, COUNT(*) AS n FROM final_checks "
            "WHERE rn = 1 GROUP BY s_json",
            ids,
        ).fetchall()
        for state_row in finals:
            try:
                state = tuple(json.loads(state_row["s_json"]))
            except (TypeError, ValueError):
                continue
            if state == key:
                continue
            if all(zone == 0 for zone in state):
                n_abandoned += state_row["n"]
                continue
            if state_row["n"] >= DEFAULT_COMMON_STATE_MIN:
                n_common_wrong += 1

    return {
        **_board_shape(row),
        "feedback_policy": policy.DEFAULT_EXPORT_POLICY,
        "first_check_correct_rate": agg["first_rate"],
        "eventual_correct_rate": agg["eventual_rate"],
        "mean_first_check_distance": agg["mean_first_d"],
        "mean_final_check_distance": agg["mean_final_d"],
        "mean_moves": agg["mean_moves"],
        "mean_checks": agg["mean_checks"],
        "reset_rate": agg["reset_rate"],
        "median_active_time_s": _median(
            [t["active_time_s"] for t in times if t["active_time_s"] is not None]
        ),
        "median_time_to_first_check_s": _median(
            [t["time_to_first_check_s"] for t in times
             if t["time_to_first_check_s"] is not None]
        ),
        "mean_omissions": errs["mean_omissions"],
        "mean_wrong_slot": errs["mean_wrong_slot"],
        "mean_decoy_card_leaks": errs["mean_card_leaks"],
        "mean_decoy_slot_leaks": errs["mean_slot_leaks"],
        "mean_card_entropy": mean_entropy,
        "max_card_entropy": max_entropy,
        "n_common_wrong_states": n_common_wrong,
        "n_abandoned_final": n_abandoned,
        **qc,
        "aliases": ";".join(ids[1:]),
    }


def _boards_sql(filters: dict) -> tuple[str, list]:
    sql = (
        "SELECT question_id, source_hash, n_cards, n_zones, key_json, built_at "
        "FROM dnd_boards"
    )
    params: list = []
    if filters.get("question_id"):
        sql += " WHERE question_id = ?"
        params.append(filters["question_id"])
    sql += " ORDER BY question_id"
    return sql, params


DATASETS: dict[str, _Dataset] = {
    "checks": _Dataset(
        name="checks",
        columns=(
            "event_id", "student_id", "question_id",
            "course_id", "class_id", "week_no", "chapter",
            "session_id", "timestamp",
            "feedback", "parse_ok", "configuration_json",
            "attempt_id", "seq", "matches_input", "eligible", "anchor_cause",
            "disclosure_layer", "graded_ordinal",
            "canonical_question_id",
            "seeded",
            # the offering key (E-08 / E-02, ruling R9 (a), 2026-10-18):
            # raw_imports.file_hash, the sha256 the manifest's
            # body.imports[] carries beside the pseudonym; the pseudonym
            # itself is never exported.
            "import_sha256",
        ),
        build_sql=_checks_sql,
        summary="logged configurations + reconstruction context, per check",
        description=(
            "Every check with its logged configuration, reconstruction "
            "context (the cross-check result, whether the check is an "
            "eligible anchor, and the cause when it is not, seven causes, "
            "the structural no_move_logging and resumed_attempt before the "
            "five decode causes), and "
            "feedback-policy disclosure layer, the item identifier after "
            "the identifier alias (canonical_question_id equals question_id "
            "except for a retired identifier), and seeded: NULL unless the "
            "pass seeded resumed attempts (D3.2 knob 10), then 1 on the "
            "checks of an attempt replayed from the prior attempt's last "
            "decodable checked configuration and 0 elsewhere. "
            "import_sha256 is the offering key: the import file's sha256 "
            "(raw_imports.file_hash), the value the run manifest lists beside "
            "the offering pseudonym; the pseudonym itself is never exported. "
            "One row per check."
        ),
    ),
    "check_scores": _Dataset(
        name="check_scores",
        columns=(
            "event_id", "attempt_id", "question_id", "student_id",
            "course_id", "class_id", "week_no", "chapter", "feedback",
            "sigma", "distance", "g", "sigma_tilde", "per_card_score",
            "all_or_nothing", "omissions", "wrong_slot",
            "decoy_source_leak", "decoy_target_leak", "per_card_json",
            "canonical_question_id",
        ),
        build_sql=_check_scores_sql,
        summary="every scoring metric side by side, per scored check",
        description=(
            "Per-check scores with every metric side by side, including the "
            "F12 error-profile components (distance = omissions + wrong_slot "
            "+ decoy_source_leak). Under the journal's names, "
            "decoy_source_leak is the decoy-card leak L_P (count of decoy "
            "cards out of the tray) and decoy_target_leak the decoy-slot "
            "leak L_R (count of cards placed into decoy slots, several "
            "cards in one slot each count); one misplacement can count in "
            "both. canonical_question_id is the item identifier after the "
            "identifier alias. One row per scored check."
        ),
    ),
    "attempt_metrics": _Dataset(
        name="attempt_metrics",
        columns=(
            "attempt_id", "student_id", "question_id",
            "course_id", "class_id", "week_no", "chapter", "attempt_index",
            "session_id", "started_at", "ended_at", "segmentation_rule",
            "qc_flags_json", "readiness", "feedback_policy",
            "n_moves", "n_noop_moves", "n_takebacks", "n_resets", "n_checks",
            "n_fixing", "n_lateral", "n_breaking", "excess_moves",
            "path_excess", "cadence_json", "post_check_gains_json",
            "responsiveness_json",
            "total_time_s", "time_to_first_move_s", "time_to_first_check_s",
            "median_move_gap_s", "max_action_gap_s", "active_time_s",
            "idle_threshold_s",
            "first_check_distance", "final_check_distance",
            "first_check_correct", "eventual_correct", "correct_check_ordinal",
            "n_distinct_states", "n_repeated_states",
            "checked_every_move", "convergence_json",
            "n_effective_moves_pre", "n_excess_moves_pre", "n_takebacks_pre",
            "n_resets_pre", "active_time_pre_s", "n_effective_moves_repair",
            "n_takebacks_repair", "n_resets_repair", "n_checks_repair",
            "active_time_repair_s", "n_fix_moves_repair",
            "canonical_question_id",
            "resumed", "prior_attempt_id",
            # the offering key (E-08 / E-02, ruling R9 (a), 2026-10-18):
            # raw_imports.file_hash, the sha256 the manifest's
            # body.imports[] carries beside the pseudonym; the pseudonym
            # itself is never exported.
            "import_sha256",
        ),
        build_sql=_attempt_metrics_sql,
        summary="per-attempt process metrics with segmentation and QC context",
        description=(
            "Per-attempt process metrics with segmentation context, the "
            "QC flags / feature readiness, the feedback-policy era, the "
            "F6 move mix (fixing/lateral/breaking, excess moves, path "
            "excess), the F7 check-structure features (cadence sequence, "
            "post-check gains, responsiveness), the F9 time features "
            "(total/active time, move/action gaps, under the recorded idle "
            "threshold), the F10 outcomes (first/final check distance, "
            "correctness, correct-check ordinal), the F8 repeat counts "
            "(distinct/repeated states over the walk), and the F-NEW "
            "phase-window block: the movement and time features restricted "
            "to the window before the first scored check (_pre; NULL without "
            "a scored check) and to the repair window from the first check "
            "to the first correct check (_repair; NULL unless the first "
            "check was incorrect and the attempt eventually correct), and "
            "canonical_question_id, the item identifier the attempt was bound "
            "under (equals question_id except for a retired identifier), and "
            "the resumption columns resumed (1 when the same student has an "
            "earlier attempt on the same logged identifier) and "
            "prior_attempt_id (that attempt's id; NULL otherwise). "
            "import_sha256 is the offering key: the import file's sha256 "
            "(raw_imports.file_hash), the value the run manifest lists beside "
            "the offering pseudonym; the pseudonym itself is never exported. "
            "One row per attempt."
        ),
    ),
    "segments": _Dataset(
        name="segments",
        columns=(
            "segment_id", "attempt_id", "student_id", "question_id",
            "course_id", "class_id", "week_no", "chapter",
            "attempt_index", "segment_type", "segment_index",
            "start_seq", "end_seq",
            "canonical_question_id",
        ),
        build_sql=_segments_sql,
        summary="materialized check / reset / session / active-work spans",
        description=(
            "Materialized check/reset/session/active_work segments as "
            "inclusive [start_seq, end_seq] spans of each attempt's "
            "configurations (one row per segment; active_work is the F9 "
            "idle-threshold type)."
        ),
    ),
    "configurations": _Dataset(
        name="configurations",
        columns=(
            "config_id", "attempt_id", "student_id", "question_id",
            "course_id", "class_id", "week_no", "chapter",
            "attempt_index", "seq", "event_id", "kind", "is_check", "is_noop",
            "matches_input", "anchor_cause", "disclosure_layer",
            "graded_ordinal", "s_json",
            "canonical_question_id",
            "seeded",
            # the offering key (E-08 / E-02, ruling R9 (a), 2026-10-18):
            # raw_imports.file_hash, the sha256 the manifest's
            # body.imports[] carries beside the pseudonym; the pseudonym
            # itself is never exported.
            "import_sha256",
        ),
        build_sql=_configurations_sql,
        summary="the reconstructed walk itself, in replay order",
        description=(
            "Every reconstructed configuration in replay order, the "
            "persisted walk behind the F24 trajectory charts (one row per "
            "configuration; seq 0 is the initial state X0, or under a seeded "
            "replay the prior attempt's last decodable checked configuration, "
            "which seeded = 1 marks; NULL unless the pass seeded). "
            "import_sha256 is the offering key: the import file's sha256 "
            "(raw_imports.file_hash), the value the run manifest lists beside "
            "the offering pseudonym; the pseudonym itself is never exported."
        ),
    ),
    "boards": _Dataset(
        name="boards",
        columns=(
            "question_id", "source_hash", "n_cards", "n_zones", "key_json",
            "built_at",
            "num_slots", "state_space_size", "num_decoy_cards",
            "num_decoy_slots", "key_occupancy", "feedback_policy",
            "check_cost",
            "aliases",
        ),
        build_sql=_boards_sql,
        derive_row=_board_covariates,
        summary="registered board specs with the item-design covariates",
        description=(
            "Registered board specs with the F18 item-design covariates "
            "(tab-item-design-covariates): num_slots (R; the stored n_zones "
            "under its journal name), state_space_size (R+1)^P, the decoy "
            "counts, the key occupancy o*, and the era covariates "
            "feedback_policy / check_cost (configured defaults until a "
            "per-era record exists, the per-attempt FP-3 column is the "
            "granular source), and aliases: the retired identifiers that "
            "resolve to this board (;-joined; empty for most). One row per "
            "cardsort question, never one per alias."
        ),
    ),
    "modeling_table": _Dataset(
        name="modeling_table",
        columns=(
            "unit", "attempt_id", "student_id", "question_id",
            "course_id", "class_id", "week_no", "chapter",
            "session_id", "attempt_index", "source_hash",
            "feedback_policy", "check_cost", "qc_flags_json", "readiness",
            "n_cards", "num_slots", "state_space_size",
            "num_decoy_cards", "num_decoy_slots", "key_occupancy",
            "first_check_distance", "final_check_distance",
            "first_check_correct", "eventual_correct", "correct_check_ordinal",
            "n_moves", "n_noop_moves", "n_takebacks", "n_resets", "n_checks",
            "n_fixing", "n_lateral", "n_breaking", "excess_moves",
            "path_excess", "n_distinct_states", "n_repeated_states",
            "cadence_json", "post_check_gains_json", "responsiveness_json",
            "total_time_s", "time_to_first_move_s", "time_to_first_check_s",
            "median_move_gap_s", "max_action_gap_s", "active_time_s",
            "idle_threshold_s", "checked_every_move",
            "n_effective_moves_pre", "n_excess_moves_pre", "n_takebacks_pre",
            "n_resets_pre", "active_time_pre_s", "n_effective_moves_repair",
            "n_takebacks_repair", "n_resets_repair", "n_checks_repair",
            "active_time_repair_s", "n_fix_moves_repair",
            "canonical_question_id",
            "resumed", "prior_attempt_id",
            # the offering key (E-08 / E-02, ruling R9 (a), 2026-10-18):
            # raw_imports.file_hash, the sha256 the manifest's
            # body.imports[] carries beside the pseudonym; the pseudonym
            # itself is never exported.
            "import_sha256",
        ),
        build_sql=_modeling_table_sql,
        derive_row=_modeling_covariates,
        summary=(
            "the attempt-level modeling table: identifiers, outcomes, "
            "covariates, features"
        ),
        description=(
            "The F20 attempt-level modeling table (subsec-modeling-table): "
            "crossing/nesting identifiers (student, question, course, class, "
            "week, chapter) with the board's source version, the unit marker, "
            "the F10 outcomes, the F18 design covariates (feedback_policy is "
            "the per-attempt FP-3 record), the baseline features, the F17b "
            "QC flags + readiness, retained even for rows an analysis "
            "will exclude, so inclusion rules stay reportable (readiness is "
            "the default rule; per-analysis inclusion is the analyst's), "
            "and the F-NEW phase-window block (the _pre / _repair "
            "restrictions of the movement and time features behind the RQ3 "
            "contrast), and canonical_question_id, the item identifier after "
            "the identifier alias, which the board columns are joined through, "
            "and the resumption columns resumed / prior_attempt_id (see "
            "attempt_metrics; a column, never a QC flag). "
            "import_sha256 is the offering key: the import file's sha256 "
            "(raw_imports.file_hash), the value the run manifest lists beside "
            "the offering pseudonym; the pseudonym itself is never exported. "
            "Per-item counts are by canonical identifier. One row per attempt."
        ),
    ),
    "item_summary": _Dataset(
        name="item_summary",
        columns=(
            "question_id", "source_hash", "n_cards",
            "num_slots", "state_space_size",
            "num_decoy_cards", "num_decoy_slots", "key_occupancy",
            "feedback_policy",
            "first_check_correct_rate", "eventual_correct_rate",
            "mean_first_check_distance", "mean_final_check_distance",
            "mean_moves", "mean_checks", "reset_rate",
            "median_active_time_s", "median_time_to_first_check_s",
            "mean_omissions", "mean_wrong_slot",
            "mean_decoy_card_leaks", "mean_decoy_slot_leaks",
            "mean_card_entropy", "max_card_entropy", "n_common_wrong_states",
            "n_abandoned_final",
            "n_attempts", "n_replay_ok", "n_parse_error", "n_long_idle",
            "aliases",
        ),
        build_sql=_item_summary_sql,
        derive_row=_item_summary,
        summary="per-question diagnostic summary, one row per cardsort question",
        description=(
            "The F20 per-question diagnostic summary "
            "(tab-item-diagnostic-summary-fields): design covariates, "
            "outcome rates and mean distances, process means (reset_rate = "
            "share of attempts with a reset) and medians, the F12 error "
            "means under the journal's leak names (mean_decoy_card_leaks / "
            "mean_decoy_slot_leaks are the means of the stored "
            "decoy_source_leak / decoy_target_leak), the "
            "F11 dispersion (entropy over each attempt's final scored "
            "check) with the common-wrong-state count "
            "(>= DEFAULT_COMMON_STATE_MIN attempts on one non-key, non-empty "
            "final state, the all-tray final is abandonment, counted "
            "separately as n_abandoned_final), and the F17b QC counts, "
            "aggregated over every logged identifier bound to the board "
            "(the aliases column lists the retired ones). One row per "
            "cardsort question, by canonical identifier."
        ),
    ),
    # The attrition ledger (E-02, PR 2 of 2; ruling R3): three read-time
    # datasets assembled in Python by `attrition`, the ordered exclusion
    # ledger, the readiness split and the exclusion probe. Keys are
    # import_sha256 values, canonical item identifiers and flag names;
    # every other cell is a count (M6). Last in the registry, so the CLI
    # lists them after the eight stage views.
    attrition.FLOW: _Dataset(
        name=attrition.FLOW,
        columns=attrition.FLOW_COLUMNS,
        build_rows=attrition.flow_rows,
        summary=attrition.FLOW_SUMMARY,
        description=attrition.FLOW_DESCRIPTION,
    ),
    attrition.READINESS_SPLIT: _Dataset(
        name=attrition.READINESS_SPLIT,
        columns=attrition.READINESS_SPLIT_COLUMNS,
        build_rows=attrition.readiness_split_rows,
        summary=attrition.READINESS_SPLIT_SUMMARY,
        description=attrition.READINESS_SPLIT_DESCRIPTION,
    ),
    attrition.EXCLUSION_PROBE: _Dataset(
        name=attrition.EXCLUSION_PROBE,
        columns=attrition.EXCLUSION_PROBE_COLUMNS,
        build_rows=attrition.exclusion_probe_rows,
        summary=attrition.EXCLUSION_PROBE_SUMMARY,
        description=attrition.EXCLUSION_PROBE_DESCRIPTION,
    ),
}


def export_dataset(
    conn: sqlite3.Connection,
    dataset: str,
    out: IO[str],
    *,
    question_id: str | None = None,
) -> int:
    """
    Write `dataset` to `out` as CSV; returns the number of data rows written.

    Uses '\\n' as the line terminator (so stdout on Windows doesn't get
    '\\r\\r\\n'); file callers should open with ``newline=''`` as the CLI does.
    """
    try:
        spec = DATASETS[dataset]
    except KeyError as e:
        raise ValueError(
            f"unknown dataset {dataset!r}; known: {sorted(DATASETS)}"
        ) from e

    filters = {"question_id": question_id}
    if spec.build_rows is not None:
        # A Python-assembled dataset: each item a dict keyed by the
        # dataset's columns (a missing key is an error, never a blank).
        rows = iter(spec.build_rows(conn, filters))
    else:
        sql, params = spec.build_sql(filters)
        cursor = conn.execute(sql, params)
        rows = ({c: row[c] for c in row.keys()} for row in cursor)

    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(spec.columns)
    n = 0
    for values in rows:
        if spec.derive_row is not None:
            values.update(spec.derive_row(conn, values))
        missing = [c for c in spec.columns if c not in values]
        if missing:
            raise KeyError(
                f"dnd dataset {dataset!r}: row {n + 1} lacks column(s) {missing}"
            )
        writer.writerow([values[c] for c in spec.columns])
        n += 1
    log.info("Exported %d row(s) from dnd dataset %r", n, dataset)
    return n


def dataset_names() -> Sequence[str]:
    """Datasets the DnD CLI knows how to export."""
    return tuple(DATASETS)
