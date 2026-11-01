"""
Tests for DnD process metrics (plan item B3.5).

The pure helpers are pinned to the retired worked attempt's convergence
curve and cadence (the definitions live in ``subsec-distance-curve`` /
``subsec-check-cadence``); the
writer is exercised on the golden fixture, whose first student places four
cards, checks, takes the decoy back, checks again, and resets.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from proteus.core import ingest as core_ingest
from proteus.services.dragndrop import process, trajectory
from proteus.services.dragndrop.derive import derive_events
from proteus.services.dragndrop.reconstruct import reconstruct_all
from proteus.services.dragndrop.scoring import score_checks

KEY = (1, 1, 2, 0)


def _times(*offsets_s: float) -> list[datetime]:
    """Datetimes at the given second offsets from a fixed origin."""
    origin = datetime(2026, 2, 6, 10, 0, 0)
    return [origin + timedelta(seconds=s) for s in offsets_s]


class TestPureHelpers:
    def test_worked_example_convergence_curve(self):
        # X0..X6 of the reference attempt → d = (3, 2, 2, 3, 2, 1, 0).
        states = [
            (0, 0, 0, 0), (1, 0, 0, 0), (1, 2, 0, 0), (1, 2, 0, 3),
            (1, 2, 2, 3), (1, 1, 2, 3), (1, 1, 2, 0),
        ]
        assert process.convergence_curve(states, KEY) == (3, 2, 2, 3, 2, 1, 0)

    def test_cadence_gaps(self):
        # Checks after moves 4 and 6 → a single inter-check gap of two moves.
        assert process.cadence_gaps([4, 6]) == [2]
        assert process.cadence_gaps([]) == []

    def test_cadence_sequence(self):
        # The running example (tab-running-example-baseline-features) pins
        # the first-check cadence n0 = 4; checks after moves 4 and 5 give
        # the full sequence (4, 1). A checkless attempt has no n0.
        assert process.cadence_sequence([4, 5]) == [4, 1]
        assert process.cadence_sequence([4]) == [4]
        assert process.cadence_sequence([]) == []

    def test_post_check_gains(self):
        # The running example pins G1 = 1: first checked state (1,1,2,3) at
        # d=1, second (1,1,2,0) at d=0. A single check has no gain.
        assert process.post_check_gains(
            [(1, 1, 2, 3), (1, 1, 2, 0)], KEY
        ) == [1]
        assert process.post_check_gains([(1, 1, 2, 3)], KEY) == []
        # A negative gain: the next checked state is farther from the key.
        assert process.post_check_gains(
            [(1, 1, 2, 0), (1, 1, 2, 3)], KEY
        ) == [-1]


class TestOutcomeFeatures:
    """The F10 attempt-level outcomes (tab-check-outcome-features), pure."""

    def test_incorrect_then_correct(self):
        # The golden trace's shape: d = 1 then 0.
        o = process.outcome_features([1, 0])
        assert o.first_check_distance == 1
        assert o.final_check_distance == 0
        assert o.first_check_correct == 0
        assert o.eventual_correct == 1
        assert o.correct_check_ordinal == 2

    def test_correct_on_the_first_press(self):
        o = process.outcome_features([0])
        assert (o.first_check_correct, o.eventual_correct) == (1, 1)
        assert o.correct_check_ordinal == 1

    def test_never_correct(self):
        o = process.outcome_features([3, 2, 1])
        assert o.first_check_distance == 3
        assert o.final_check_distance == 1
        assert o.eventual_correct == 0
        assert o.correct_check_ordinal is None  # never, not 0

    def test_eventual_correct_survives_a_later_wrong_check(self):
        # The journal's max_i indicator: reaching the key once sets it even
        # if the student then breaks the board and checks again; the final
        # distance still records the regression.
        o = process.outcome_features([0, 2])
        assert o.eventual_correct == 1
        assert o.correct_check_ordinal == 1
        assert o.final_check_distance == 2

    def test_no_scored_checks_is_all_none(self):
        assert process.outcome_features([]) == process.OutcomeFeatures(
            None, None, None, None, None
        )


class TestTimeFeatures:
    """The F9 basic timing summaries (tab-basic-time-features), pure."""

    def test_golden_trace_numbers(self):
        # DemoStudent1's clock: actions at 5,11,16,36,40,56,60,90 seconds
        # (four moves, check, takeback, check, reset), ordinary moves at
        # 5,11,16,36,56.
        actions = _times(5, 11, 16, 36, 40, 56, 60, 90)
        moves = _times(5, 11, 16, 36, 56)
        t = process.time_features(actions, moves, 300.0)
        assert t.total_time_s == 85.0
        assert t.time_to_first_move_s == 0.0
        # Move gaps 6, 5, 20, 20 → median (6+20)/2.
        assert t.median_move_gap_s == 13.0
        assert t.max_action_gap_s == 30.0  # the pause before the reset
        assert t.active_time_s == 85.0  # nothing exceeds δ_max

    def test_active_time_excludes_long_gaps(self):
        # The journal's exclusion rule: gaps over δ_max contribute nothing
        # (they are dropped, not truncated to the threshold).
        actions = _times(0, 10, 400, 410)
        t = process.time_features(actions, [], 300.0)
        assert t.total_time_s == 410.0
        assert t.max_action_gap_s == 390.0
        assert t.active_time_s == 20.0

    def test_boundary_gap_counts_as_active(self):
        # δ_k > δ_max flags idle (subsec-action-gaps-processing): a gap of
        # exactly δ_max is still active.
        t = process.time_features(_times(0, 300), [], 300.0)
        assert t.active_time_s == 300.0

    def test_undefined_values_are_none_never_zero(self):
        # No moves: first-move and move-gap features are undefined; a lone
        # move has no gap either.
        t = process.time_features(_times(0, 10), [], 300.0)
        assert t.time_to_first_move_s is None
        assert t.median_move_gap_s is None
        t = process.time_features(_times(0, 10), _times(10), 300.0)
        assert t.time_to_first_move_s == 10.0
        assert t.median_move_gap_s is None

    def test_single_action_and_empty_attempts(self):
        # One action: zero elapsed, zero active, no gaps. No actions at all:
        # nothing is computable.
        t = process.time_features(_times(5), [], 300.0)
        assert t.total_time_s == 0.0
        assert t.active_time_s == 0.0
        assert t.max_action_gap_s is None
        t = process.time_features([], [], 300.0)
        assert t == process.TimeFeatures(None, None, None, None, None)


class TestResponsiveness:
    """The F7 responsiveness ρ (subsec-responsiveness-baseline): the fraction of
    the highlighted (wrongly-placed) set moved before the next check, defined
    only for checks that disclosed at the highlight layer."""

    def test_highlighted_cards_is_the_wrongly_placed_set(self):
        # Placed-but-wrong cards are highlighted; cards left in the tray are
        # "left to place", never highlighted (subsec-platform-feedback-
        # policies), and the placed decoy counts as wrongly placed.
        assert process.highlighted_cards((2, 0, 2, 3), KEY) == {0, 3}
        assert process.highlighted_cards(KEY, KEY) == frozenset()
        assert process.highlighted_cards((0, 0, 0, 0), KEY) == frozenset()

    def test_full_response(self):
        # The running example: check 1 highlights the placed decoy (card 4);
        # the takeback moves exactly that card → ρ1 = 1.
        checks = [((1, 1, 2, 3), "highlight"), ((1, 1, 2, 0), "highlight")]
        assert process.responsiveness(checks, [{3}], KEY) == [1.0]

    def test_partial_response(self):
        # Two cards highlighted, one acted on → ρ1 = 1/2.
        checks = [((2, 0, 2, 3), "highlight"), ((1, 0, 2, 3), "highlight")]
        assert process.responsiveness(checks, [{0}], KEY) == [0.5]

    def test_undefined_below_the_highlight_layer(self):
        # A layered attempt's refused and count-layer checks never showed the
        # student a highlighted set: ρ is undefined (None), never zero.
        checks = [((2, 0, 2, 3), "refused"), ((2, 1, 2, 3), "count"),
                  ((1, 1, 2, 0), "highlight")]
        assert process.responsiveness(checks, [{1}, {0, 3}], KEY) == [
            None, None,
        ]

    def test_undefined_when_nothing_is_highlighted(self):
        # |H| = 0 (a check on the key, or on an empty board): undefined.
        checks = [((0, 0, 0, 0), "highlight"), ((1, 0, 0, 0), "highlight")]
        assert process.responsiveness(checks, [{0}], KEY) == [None]

    def test_undefined_for_unclassified_checks(self):
        # A NULL disclosure layer (rows from a pre-FP-3 reconstruct) reads
        # as "not computed", so ρ stays undefined rather than assuming an era.
        checks = [((1, 1, 2, 3), None), ((1, 1, 2, 0), None)]
        assert process.responsiveness(checks, [{3}], KEY) == [None]

    def test_last_check_has_no_pair(self):
        assert process.responsiveness(
            [((1, 1, 2, 3), "highlight")], [], KEY
        ) == []
        assert process.responsiveness([], [], KEY) == []


# The features chapter's running-example walk (tab-running-example-baseline-
# features): four placements, a check, a fixing takeback, a check. the
# journal pins F,L,B = 4,0,1 and E_move = E_path = 5 - 3 = 2. Checks change
# no state and do not appear in the trace.
RUNNING_EXAMPLE_TRACE = [
    (None, (0, 0, 0, 0)),
    ("move", (1, 0, 0, 0)),
    ("move", (1, 1, 0, 0)),
    ("move", (1, 1, 2, 0)),
    ("move", (1, 1, 2, 3)),
    ("takeback", (1, 1, 2, 0)),
]


class TestMoveMix:
    """The F6 classification and excess measures, pinned to the journal."""

    def test_graph_distance(self):
        assert process.graph_distance((0, 0, 0, 0), (1, 1, 2, 0)) == 3
        assert process.graph_distance((1, 2, 3), (1, 2, 3)) == 0

    def test_running_example_numbers(self):
        mix = process.move_mix(RUNNING_EXAMPLE_TRACE, KEY)
        assert (mix.n_fixing, mix.n_lateral, mix.n_breaking) == (4, 0, 1)
        assert mix.excess_moves == 2  # n - (F - B) = 5 - 3 = 2B + L
        assert mix.path_excess == 2  # reset-free: coincides with E_move

    def test_reset_closes_the_segment_and_is_never_classified(self):
        # A reset jumps d from 0 back to 3; that +3 jump must not be counted
        # as breaking, and the post-reset move opens a fresh segment.
        trace = RUNNING_EXAMPLE_TRACE + [
            ("reset", (0, 0, 0, 0)),
            ("move", (1, 0, 0, 0)),
        ]
        mix = process.move_mix(trace, KEY)
        assert (mix.n_fixing, mix.n_lateral, mix.n_breaking) == (5, 0, 1)
        # Segment sums: 2 (first span) + 0 (one-move span) for both measures.
        assert mix.excess_moves == 2
        assert mix.path_excess == 2

    def test_lateral_move(self):
        # Wrong slot → other wrong slot: the board changes, the distance
        # does not.
        trace = [
            (None, (0, 0, 0, 0)),
            ("move", (2, 0, 0, 0)),  # card 1 (keyed 1) into slot 2: lateral
            ("move", (3, 0, 0, 0)),  # ...into slot 3: still lateral
        ]
        mix = process.move_mix(trace, KEY)
        assert (mix.n_fixing, mix.n_lateral, mix.n_breaking) == (0, 2, 0)
        assert mix.excess_moves == 2  # 2B + L
        # One card sits one move from its start: 2 - 1.
        assert mix.path_excess == 1

    def test_keyless_trace_still_measures_path_excess(self):
        mix = process.move_mix(RUNNING_EXAMPLE_TRACE, None)
        assert mix.n_fixing is None
        assert mix.n_lateral is None
        assert mix.n_breaking is None
        assert mix.excess_moves is None
        assert mix.path_excess == 2

    def test_empty_walk(self):
        mix = process.move_mix([(None, (0, 0, 0, 0))], KEY)
        assert (mix.n_fixing, mix.n_lateral, mix.n_breaking) == (0, 0, 0)
        assert mix.excess_moves == 0
        assert mix.path_excess == 0

    def test_empty_trace_is_the_zero_length_walk(self):
        # Unreachable from analyze_all (X0 is always present) but the pure
        # function stays total rather than raising on trace[0].
        assert process.move_mix([], KEY) == process.MoveMix(0, 0, 0, 0, 0)
        assert process.move_mix([], None) == process.MoveMix(
            None, None, None, None, 0
        )

    def test_graph_distance_rejects_mismatched_boards(self):
        # strict zip: a truncated comparison would silently undercount.
        with pytest.raises(ValueError):
            process.graph_distance((0, 0, 0), (0, 0))


class TestAnalyzeWriter:
    @pytest.fixture
    def analyzed(self, db_conn, golden_dnd_tab, four_shapes_board):
        core_ingest.ingest_file(db_conn, golden_dnd_tab)
        derive_events(db_conn)
        four_shapes_board(db_conn)
        reconstruct_all(db_conn)
        score_checks(db_conn)  # F10: analyze reads the persisted outcome layer
        result = process.analyze_all(db_conn)
        return db_conn, result

    def _metrics(self, conn, student):
        return conn.execute(
            "SELECT m.* FROM dnd_attempt_metrics m JOIN dnd_attempts a "
            "USING (attempt_id) WHERE a.student_id = ?",
            (student,),
        ).fetchone()

    def test_result_counts(self, analyzed):
        _, result = analyzed
        assert result.attempts == 2
        assert result.with_convergence == 2  # both share the four-shape board

    def test_student1_move_tallies(self, analyzed):
        conn, _ = analyzed
        m = self._metrics(conn, "DemoStudent1")
        # Four placements + one takeback; one reset; two checks; no no-ops.
        assert m["n_moves"] == 5
        assert m["n_takebacks"] == 1
        assert m["n_resets"] == 1
        assert m["n_checks"] == 2
        assert m["n_noop_moves"] == 0

    def test_student1_time_to_first_check(self, analyzed):
        conn, _ = analyzed
        # First drop at 10:00:05, first check at 10:00:40 → 35 seconds.
        assert self._metrics(conn, "DemoStudent1")["time_to_first_check_s"] == 35.0

    def test_student1_convergence_curve(self, analyzed):
        conn, _ = analyzed
        m = self._metrics(conn, "DemoStudent1")
        # X0 → four placements → (check) → takeback → (check) → reset jumps back.
        assert json.loads(m["convergence_json"]) == [3, 2, 1, 0, 1, 0, 3]

    def test_student1_checked_every_move_probe(self, analyzed):
        conn, _ = analyzed
        # The two checks are one move (the takeback) apart → grader-as-oracle.
        assert self._metrics(conn, "DemoStudent1")["checked_every_move"] == 1

    def test_student1_repeat_state_counts(self, analyzed):
        conn, _ = analyzed
        m = self._metrics(conn, "DemoStudent1")
        # F8: the walk X0 → four placements → takeback → reset has n+1 = 7
        # states; the takeback revisits the key-complete state and the reset
        # revisits X0, so V(Γ) = 5 and R_state = 7 - 5 = 2.
        assert m["n_distinct_states"] == 5
        assert m["n_repeated_states"] == 2

    def test_pathological_student_repeat_state_counts(self, analyzed):
        conn, _ = analyzed
        m = self._metrics(conn, "DemoStudent2")
        # Only X0 reconstructs: one state, nothing revisited.
        assert m["n_distinct_states"] == 1
        assert m["n_repeated_states"] == 0

    def test_pathological_student_has_no_moves(self, analyzed):
        conn, _ = analyzed
        m = self._metrics(conn, "DemoStudent2")
        assert m["n_moves"] == 0
        assert m["n_checks"] == 2
        # Only the empty board reconstructs, so the curve is the single d(X0).
        assert json.loads(m["convergence_json"]) == [3]

    def test_student1_time_features(self, analyzed):
        conn, _ = analyzed
        m = self._metrics(conn, "DemoStudent1")
        # Replayed actions at 5..90 s; ordinary moves at 5,11,16,36,56
        # (see TestTimeFeatures.test_golden_trace_numbers for the clock).
        assert m["total_time_s"] == 85.0
        assert m["time_to_first_move_s"] == 0.0
        assert m["median_move_gap_s"] == 13.0
        assert m["max_action_gap_s"] == 30.0
        assert m["active_time_s"] == 85.0
        # The δ_max in force rides the row, so active_time is self-describing.
        assert m["idle_threshold_s"] == 300.0

    def test_pathological_student_time_features(self, analyzed):
        conn, _ = analyzed
        m = self._metrics(conn, "DemoStudent2")
        # Only the two checks replay (10 s apart); the skipped rows
        # contribute no timestamps, and with no ordinary move the move-based
        # features are undefined (None, never zero).
        assert m["total_time_s"] == 10.0
        assert m["time_to_first_move_s"] is None
        assert m["median_move_gap_s"] is None
        assert m["max_action_gap_s"] == 10.0
        assert m["active_time_s"] == 10.0

    def test_custom_idle_threshold(
        self, db_conn, golden_dnd_tab, four_shapes_board
    ):
        # δ_max = 10 s: DemoStudent1's action gaps 6,5,20,4,16,4,30 keep
        # only 6+5+4+4 = 19 s of active time, and the row records the
        # threshold that produced it.
        core_ingest.ingest_file(db_conn, golden_dnd_tab)
        derive_events(db_conn)
        four_shapes_board(db_conn)
        reconstruct_all(db_conn)
        process.analyze_all(
            db_conn, config=process.ProcessConfig(idle_threshold_s=10.0)
        )
        m = db_conn.execute(
            "SELECT m.* FROM dnd_attempt_metrics m JOIN dnd_attempts a "
            "USING (attempt_id) WHERE a.student_id = 'DemoStudent1'"
        ).fetchone()
        assert m["active_time_s"] == 19.0
        assert m["total_time_s"] == 85.0  # unaffected by δ_max
        assert m["idle_threshold_s"] == 10.0

    def test_student1_outcomes(self, analyzed):
        conn, _ = analyzed
        m = self._metrics(conn, "DemoStudent1")
        # Scored checks d = 1 (Incorrect) then 0 (Correct): eventually
        # correct on the second check.
        assert m["first_check_distance"] == 1
        assert m["final_check_distance"] == 0
        assert m["first_check_correct"] == 0
        assert m["eventual_correct"] == 1
        assert m["correct_check_ordinal"] == 2

    def test_pathological_student_outcomes(self, analyzed):
        conn, _ = analyzed
        m = self._metrics(conn, "DemoStudent2")
        # Only the orphan-label check is scoreable (its Input parsed; the
        # truncated one is not retained in the outcome layer): decoded to
        # the empty board, d = 3, never correct.
        assert m["first_check_distance"] == 3
        assert m["final_check_distance"] == 3
        assert m["first_check_correct"] == 0
        assert m["eventual_correct"] == 0
        assert m["correct_check_ordinal"] is None

    def test_outcomes_null_before_score_runs(
        self, db_conn, golden_dnd_tab, four_shapes_board
    ):
        # Analyze without `dnd score`: the outcome layer is empty, so the
        # five columns stay NULL (not computed) rather than reading as a
        # computed zero.
        core_ingest.ingest_file(db_conn, golden_dnd_tab)
        derive_events(db_conn)
        four_shapes_board(db_conn)
        reconstruct_all(db_conn)
        process.analyze_all(db_conn)
        m = db_conn.execute(
            "SELECT m.* FROM dnd_attempt_metrics m JOIN dnd_attempts a "
            "USING (attempt_id) WHERE a.student_id = 'DemoStudent1'"
        ).fetchone()
        assert m["first_check_distance"] is None
        assert m["final_check_distance"] is None
        assert m["first_check_correct"] is None
        assert m["eventual_correct"] is None
        assert m["correct_check_ordinal"] is None

    def test_student1_check_structure(self, analyzed):
        conn, _ = analyzed
        m = self._metrics(conn, "DemoStudent1")
        # The running example's numbers (tab-running-example-baseline-
        # features): checks after moves 4 and 5 → cadence (n0, n1) = (4, 1);
        # checked states d = 1 then 0 → G1 = 1; the check highlighted the
        # placed decoy (legacy era ⇒ highlight layer) and the takeback moved
        # exactly that card → ρ1 = 1.
        assert json.loads(m["cadence_json"]) == [4, 1]
        assert json.loads(m["post_check_gains_json"]) == [1]
        assert json.loads(m["responsiveness_json"]) == [1.0]

    def test_pathological_student_check_structure(self, analyzed):
        conn, _ = analyzed
        m = self._metrics(conn, "DemoStudent2")
        # Two checks, no replayed moves: cadence (0, 0), no checked-state
        # progress, and ρ undefined: both checks sit on the empty board, so
        # nothing was highlighted (null, never zero).
        assert json.loads(m["cadence_json"]) == [0, 0]
        assert json.loads(m["post_check_gains_json"]) == [0]
        assert json.loads(m["responsiveness_json"]) == [None]

    def test_student1_move_mix(self, analyzed):
        conn, _ = analyzed
        m = self._metrics(conn, "DemoStudent1")
        # Three fixing placements, the breaking decoy placement, the fixing
        # takeback (F=4, L=0, B=1); the trailing reset opens an empty segment
        # that adds nothing to either excess measure.
        assert m["n_fixing"] == 4
        assert m["n_lateral"] == 0
        assert m["n_breaking"] == 1
        assert m["excess_moves"] == 2
        assert m["path_excess"] == 2

    def test_pathological_student_move_mix_is_zero(self, analyzed):
        conn, _ = analyzed
        m = self._metrics(conn, "DemoStudent2")
        # No move replays (unparseable / off-board), so the walk is X0 alone.
        assert (m["n_fixing"], m["n_lateral"], m["n_breaking"]) == (0, 0, 0)
        assert m["excess_moves"] == 0
        assert m["path_excess"] == 0

    def test_rerun_is_idempotent(self, analyzed):
        conn, _ = analyzed
        again = process.analyze_all(conn)
        assert again.attempts == 2
        assert conn.execute(
            "SELECT count(*) AS n FROM dnd_attempt_metrics"
        ).fetchone()["n"] == 2

    def test_convergence_is_none_without_a_board(self, db_conn, golden_dnd_tab):
        core_ingest.ingest_file(db_conn, golden_dnd_tab)
        derive_events(db_conn)
        reconstruct_all(db_conn)  # no board synced
        process.analyze_all(db_conn)
        rows = db_conn.execute(
            "SELECT convergence_json FROM dnd_attempt_metrics"
        ).fetchall()
        assert rows and all(r["convergence_json"] is None for r in rows)

    def test_move_mix_without_a_board(self, db_conn, golden_dnd_tab):
        # F/L/B and E_move need the key and stay NULL; E_path compares each
        # segment's walk to its own endpoints, so it is computed regardless.
        core_ingest.ingest_file(db_conn, golden_dnd_tab)
        derive_events(db_conn)
        reconstruct_all(db_conn)  # no board synced
        process.analyze_all(db_conn)
        m = db_conn.execute(
            "SELECT m.* FROM dnd_attempt_metrics m JOIN dnd_attempts a "
            "USING (attempt_id) WHERE a.student_id = 'DemoStudent1'"
        ).fetchone()
        assert m["n_fixing"] is None
        assert m["n_lateral"] is None
        assert m["n_breaking"] is None
        assert m["excess_moves"] is None
        assert m["path_excess"] == 2

    def test_check_structure_without_a_board(self, db_conn, golden_dnd_tab):
        # The cadence is key-free; the gains and ρ need d★ / the highlighted
        # set, so they stay NULL without a board key.
        core_ingest.ingest_file(db_conn, golden_dnd_tab)
        derive_events(db_conn)
        reconstruct_all(db_conn)  # no board synced
        process.analyze_all(db_conn)
        m = db_conn.execute(
            "SELECT m.* FROM dnd_attempt_metrics m JOIN dnd_attempts a "
            "USING (attempt_id) WHERE a.student_id = 'DemoStudent1'"
        ).fetchone()
        assert json.loads(m["cadence_json"]) == [4, 1]
        assert m["post_check_gains_json"] is None
        assert m["responsiveness_json"] is None


class TestTimeToFirstCheckReplayedClock:
    """A6/BUG-06: ``time_to_first_check_s`` measures from ``action_times[0]``
    (the replayed clock every other F9 feature uses) never from the
    attempt's ``started_at``, which includes rows that failed to replay."""

    Q = "demo-book__demo-shapes-q1"

    def _seed(self, conn, rows):
        # test_dnd_reconstruct's synthesizer, inlined: raw `events` rows →
        # derive_events. Each row: (eid, selection, action, ts).
        import_id = conn.execute(
            "INSERT INTO raw_imports (file_path, file_hash, file_format, "
            "row_count) VALUES ('synthetic.tab', 'h-a6', 'datashop_tab', ?)",
            (len(rows),),
        ).lastrowid
        for i, (eid, selection, action, ts) in enumerate(rows):
            conn.execute(
                "INSERT INTO events (event_id, import_id, student_id, "
                "session_id, problem_name, selection, action, input, "
                "feedback_text, feedback_classification, problem_view, "
                "timestamp, course_id) "
                "VALUES (?, ?, 'S', '100', ?, ?, ?, ?, '', 'Incorrect', ?, "
                "?, 'course')",
                (eid, import_id, self.Q, selection, action, action,
                 str(i + 1), ts),
            )
        conn.commit()
        derive_events(conn)

    @pytest.fixture
    def audit_repro(self, db_conn, four_shapes_board):
        # The audit's BUG-06 repro: a malformed leading drop 60 s before the
        # replayable activity. The garbage row is retained in the segment,
        # (it sets started_at=10:00:00) but the replay skips it, so the
        # replayed clock starts at the 10:01:00 move. Replayed actions at
        # 10:01:00 (move), 10:01:05 (check), 10:01:10 (move).
        payload = json.dumps({f"{self.Q}_drop1": [f"{self.Q}_drag1"]})
        self._seed(db_conn, [
            ("bad", "dragNdrop-drop", "garbage-action",
             "2026-02-06 10:00:00.000000"),
            ("m1", "dragNdrop-drop", f"{self.Q}_drag1 -> {self.Q}_drop1",
             "2026-02-06 10:01:00.000000"),
            ("c1", "dragNdrop", payload, "2026-02-06 10:01:05.000000"),
            ("m2", "dragNdrop-drop", f"{self.Q}_drag2 -> {self.Q}_drop2",
             "2026-02-06 10:01:10.000000"),
        ])
        four_shapes_board(db_conn)
        reconstruct_all(db_conn)
        process.analyze_all(db_conn)
        return db_conn.execute(
            "SELECT m.*, a.started_at FROM dnd_attempt_metrics m "
            "JOIN dnd_attempts a USING (attempt_id)"
        ).fetchone()

    def test_audit_repro_values_are_consistent(self, audit_repro):
        # Pre-A6 the metric read 65.0 (check at 10:01:05 minus the malformed
        # row's 10:00:00 started_at) against total_time_s = 10.0 (impossible).
        # On the replayed clock both come from the same timestamps: 
        # 5.0 ≤ 10.0.
        assert audit_repro["started_at"] == "2026-02-06 10:00:00.000000"
        assert audit_repro["total_time_s"] == 10.0
        assert audit_repro["time_to_first_check_s"] == 5.0

    def test_never_exceeds_total_time(self, audit_repro):
        assert (
            audit_repro["time_to_first_check_s"]
            <= audit_repro["total_time_s"]
        )


class TestWorkedExampleMoveMix:
    """End-to-end on the worked-example fixture (the retired six-move walk):
    six ordinary moves with one wrong-slot placement (lateral) and one decoy
    placement (breaking), both repaired: F,L,B = 4,1,1 (the
    subsec-move-classification counts) and E_move = E_path = 6 - 3 = 3."""

    def test_worked_example_numbers(
        self, db_conn, golden_dnd_worked_example_tab, four_shapes_board
    ):
        core_ingest.ingest_file(db_conn, golden_dnd_worked_example_tab)
        derive_events(db_conn)
        four_shapes_board(db_conn)
        reconstruct_all(db_conn)
        score_checks(db_conn)
        process.analyze_all(db_conn)
        m = db_conn.execute(
            "SELECT m.* FROM dnd_attempt_metrics m JOIN dnd_attempts a "
            "USING (attempt_id) WHERE a.student_id = 'WorkedStudent'"
        ).fetchone()
        assert m["n_moves"] == 6
        assert m["n_fixing"] == 4
        assert m["n_lateral"] == 1
        assert m["n_breaking"] == 1
        assert m["excess_moves"] == 3  # 2B + L = n - (F - B)
        assert m["path_excess"] == 3  # reset-free, X0 → key: coincides
        # F7: checks after moves 4 and 6 → cadence (4, 2); checked states
        # (1,2,2,3) at d=2 then the key → G1 = 2; check 1 highlighted the
        # mislabeled triangle and the leaked decoy, and the repair moved
        # both → ρ1 = 1.
        assert json.loads(m["cadence_json"]) == [4, 2]
        assert json.loads(m["post_check_gains_json"]) == [2]
        assert json.loads(m["responsiveness_json"]) == [1.0]
        # F10: σ=2 (d=2, Incorrect) then the key (correct on check 2).
        assert m["first_check_distance"] == 2
        assert m["final_check_distance"] == 0
        assert m["first_check_correct"] == 0
        assert m["eventual_correct"] == 1
        assert m["correct_check_ordinal"] == 2


class TestCadenceExcludesResets:
    """A reset between checks is not a move, so it must not inflate the
    inter-check cadence (the basis of the grader-as-oracle probe)."""

    Q = "demo-book__demo-shapes-q1"

    def _seed(self, conn, rows):
        import_id = conn.execute(
            "INSERT INTO raw_imports (file_path, file_hash, file_format, "
            "row_count) VALUES ('synthetic.tab', ?, 'datashop_tab', ?)",
            (f"hash-{conn.total_changes}", len(rows)),
        ).lastrowid
        for i, r in enumerate(rows):
            conn.execute(
                "INSERT INTO events (event_id, import_id, student_id, "
                "session_id, problem_name, selection, action, input, "
                "feedback_text, feedback_classification, problem_view, "
                "timestamp, course_id) VALUES (?, ?, 'S', '1', ?, ?, ?, ?, '', "
                "?, ?, ?, 'c')",
                (
                    r["eid"], import_id, r.get("problem", self.Q), r["selection"],
                    r.get("action", ""), r.get("input", ""), r.get("feedback", ""),
                    str(i + 1), r["ts"],
                ),
            )
        conn.commit()
        derive_events(conn)

    def test_reset_between_checks_is_not_counted_as_a_move(self, db_conn):
        cfg = lambda d: json.dumps(d)
        self._seed(db_conn, [
            {"eid": "c1", "selection": "dragNdrop", "input": cfg({}),
             "feedback": "Incorrect", "ts": "2026-02-06 10:00:00.000000"},
            {"eid": "r", "selection": "dragNdrop-reset", "action": "reset",
             "ts": "2026-02-06 10:00:05.000000"},
            {"eid": "m", "selection": "dragNdrop-drop",
             "action": f"{self.Q}_drag1 -> {self.Q}_drop1",
             "ts": "2026-02-06 10:00:10.000000"},
            {"eid": "c2", "selection": "dragNdrop",
             "input": cfg({f"{self.Q}_drop1": [f"{self.Q}_drag1"]}),
             "feedback": "Correct", "ts": "2026-02-06 10:00:15.000000"},
        ])
        reconstruct_all(db_conn)
        process.analyze_all(db_conn)
        m = db_conn.execute("SELECT * FROM dnd_attempt_metrics").fetchone()
        assert m["n_moves"] == 1  # the single drop; the reset is not a move
        assert m["n_resets"] == 1
        # Exactly one move separates the two checks, so the cadence gap is 1
        # (the reset does not count) and the grader-as-oracle probe fires.
        assert m["checked_every_move"] == 1


class TestNoopExclusion:
    """The journal's processing default (subsec-reconstruction-rules /
    g-baseline-flb-counts): rows flagged ``noop_move`` are excluded from move
    counts, the cadence, and the convergence curve (trajectory features count
    state changes, not raw drop rows)."""

    Q = "demo-book__demo-shapes-q1"

    def _seed(self, conn, rows):
        import_id = conn.execute(
            "INSERT INTO raw_imports (file_path, file_hash, file_format, "
            "row_count) VALUES ('synthetic.tab', ?, 'datashop_tab', ?)",
            (f"hash-{conn.total_changes}", len(rows)),
        ).lastrowid
        for i, r in enumerate(rows):
            conn.execute(
                "INSERT INTO events (event_id, import_id, student_id, "
                "session_id, problem_name, selection, action, input, "
                "feedback_text, feedback_classification, problem_view, "
                "timestamp, course_id) VALUES (?, ?, 'S', '1', ?, ?, ?, ?, '', "
                "?, ?, ?, 'c')",
                (
                    r["eid"], import_id, r.get("problem", self.Q), r["selection"],
                    r.get("action", ""), r.get("input", ""), r.get("feedback", ""),
                    str(i + 1), r["ts"],
                ),
            )
        conn.commit()
        derive_events(conn)

    def test_noop_drop_excluded_from_moves_cadence_and_curve(
        self, db_conn, four_shapes_board
    ):
        drop1 = f"{self.Q}_drop1"
        self._seed(db_conn, [
            {"eid": "m1", "selection": "dragNdrop-drop",
             "action": f"{self.Q}_drag1 -> {drop1}",
             "ts": "2026-02-06 10:00:00.000000"},
            {"eid": "c1", "selection": "dragNdrop",
             "input": json.dumps({drop1: [f"{self.Q}_drag1"]}),
             "feedback": "Incorrect", "ts": "2026-02-06 10:00:05.000000"},
            # The same card released over the slot it already occupies: a
            # flagged no-op, not a move.
            {"eid": "m2", "selection": "dragNdrop-drop",
             "action": f"{self.Q}_drag1 -> {drop1}",
             "ts": "2026-02-06 10:00:10.000000"},
            {"eid": "m3", "selection": "dragNdrop-drop",
             "action": f"{self.Q}_drag2 -> {drop1}",
             "ts": "2026-02-06 10:00:15.000000"},
            {"eid": "c2", "selection": "dragNdrop",
             "input": json.dumps({drop1: [f"{self.Q}_drag1", f"{self.Q}_drag2"]}),
             "feedback": "Incorrect", "ts": "2026-02-06 10:00:20.000000"},
        ])
        four_shapes_board(db_conn)
        reconstruct_all(db_conn)
        process.analyze_all(db_conn)
        m = db_conn.execute("SELECT * FROM dnd_attempt_metrics").fetchone()
        # Two state-changing moves; the flagged row is counted separately, so
        # the raw drop count (n_moves + n_noop_moves = 3) stays recoverable.
        assert m["n_moves"] == 2
        assert m["n_noop_moves"] == 1
        # One state-changing move separates the checks (the no-op does not
        # widen the gap to 2), so the grader-as-oracle probe still fires.
        assert m["checked_every_move"] == 1
        # The curve has one point per state change, no duplicate from the
        # no-op: d(X0)=3, then drag1 → drop1 (d=2), drag2 → drop1 (d=1).
        assert json.loads(m["convergence_json"]) == [3, 2, 1]
        # F7: the cadence excludes the flagged no-op too.
        assert json.loads(m["cadence_json"]) == [1, 1]
        # F9: for the clock, though, the no-op drop is activity, the five
        # actions sit 5 s apart, so no gap reaches 10 s (which excluding the
        # no-op row would produce).
        assert m["max_action_gap_s"] == 5.0
        assert m["total_time_s"] == 20.0


class TestLayeredPolicyResponsiveness:
    """Under the layered era (FP-3), a refused press is still a logged check
    (it stays in the cadence and gain sequences) but ρ is defined only for
    highlight-layer checks, so refused/count pairs read as null, never 0."""

    Q = "demo-book__demo-shapes-q1"

    def _seed(self, conn, rows):
        import_id = conn.execute(
            "INSERT INTO raw_imports (file_path, file_hash, file_format, "
            "row_count) VALUES ('synthetic.tab', ?, 'datashop_tab', ?)",
            (f"hash-{conn.total_changes}", len(rows)),
        ).lastrowid
        for i, r in enumerate(rows):
            conn.execute(
                "INSERT INTO events (event_id, import_id, student_id, "
                "session_id, problem_name, selection, action, input, "
                "feedback_text, feedback_classification, problem_view, "
                "timestamp, course_id) VALUES (?, ?, 'S', '1', ?, ?, ?, ?, '', "
                "?, ?, ?, 'c')",
                (
                    r["eid"], import_id, r.get("problem", self.Q), r["selection"],
                    r.get("action", ""), r.get("input", ""), r.get("feedback", ""),
                    str(i + 1), r["ts"],
                ),
            )
        conn.commit()
        derive_events(conn)

    def test_refused_and_count_layer_checks_leave_rho_undefined(
        self, db_conn, four_shapes_board
    ):
        drag = lambda j: f"{self.Q}_drag{j}"
        drop = lambda i: f"{self.Q}_drop{i}"
        self._seed(db_conn, [
            # A check on the empty board: 0 placed < ℓ0 = 3 → refused, but
            # still logged (and still a cadence entry).
            {"eid": "c0", "selection": "dragNdrop", "input": json.dumps({}),
             "feedback": "Incorrect", "ts": "2026-08-01 10:00:00.000000"},
            # Three placements, the first into the wrong slot.
            {"eid": "m1", "selection": "dragNdrop-drop",
             "action": f"{drag(1)} -> {drop(2)}",
             "ts": "2026-08-01 10:00:05.000000"},
            {"eid": "m2", "selection": "dragNdrop-drop",
             "action": f"{drag(2)} -> {drop(1)}",
             "ts": "2026-08-01 10:00:10.000000"},
            {"eid": "m3", "selection": "dragNdrop-drop",
             "action": f"{drag(3)} -> {drop(2)}",
             "ts": "2026-08-01 10:00:15.000000"},
            # 3 placed ≥ ℓ0 → the first *graded* check: count layer, no
            # highlighting yet.
            {"eid": "c1", "selection": "dragNdrop",
             "input": json.dumps(
                 {drop(1): [drag(2)], drop(2): [drag(1), drag(3)]}
             ),
             "feedback": "Incorrect", "ts": "2026-08-01 10:00:20.000000"},
            # The repair moves the wrongly-placed card…
            {"eid": "m4", "selection": "dragNdrop-drop",
             "action": f"{drag(1)} -> {drop(1)}",
             "ts": "2026-08-01 10:00:25.000000"},
            # …and the second graded check lands on the key (still count
            # layer: highlighting starts at the third graded check).
            {"eid": "c2", "selection": "dragNdrop",
             "input": json.dumps(
                 {drop(1): [drag(2), drag(1)], drop(2): [drag(3)]}
             ),
             "feedback": "Correct", "ts": "2026-08-01 10:00:30.000000"},
        ])
        four_shapes_board(db_conn)
        reconstruct_all(db_conn, feedback_policy="layered")
        process.analyze_all(db_conn)
        m = db_conn.execute("SELECT * FROM dnd_attempt_metrics").fetchone()
        # All three logged checks enter the sequences: cadence (0, 3, 1) and
        # gains d = 3 → 1 → 0.
        assert json.loads(m["cadence_json"]) == [0, 3, 1]
        assert json.loads(m["post_check_gains_json"]) == [2, 1]
        # ρ is undefined for both pairs, c0 was refused and c1 disclosed
        # only counts, even though the c1 → c2 repair did move the wrongly-
        # placed card. Null, never 0 (subsec-responsiveness-baseline).
        assert json.loads(m["responsiveness_json"]) == [None, None]


class TestReplayedBasisDivergence:
    """A10/BUG-10 (owner decision: documented, not changed): F7 gains and
    the F24 curve's ``solved`` sit on the replayed walk, while the F10
    outcomes score each check's own logged Input. On a check_mismatch
    attempt the bases disagree; this pins the divergence exactly as the
    docstrings state it, so a change to either basis surfaces
    deliberately."""

    Q = "demo-book__demo-shapes-q1"

    def _seed(self, conn, rows):
        # The synthesizer idiom from TestTimeToFirstCheckReplayedClock:
        # raw `events` rows -> derive_events; rows are (eid, selection,
        # action, ts).
        import_id = conn.execute(
            "INSERT INTO raw_imports (file_path, file_hash, file_format, "
            "row_count) VALUES ('synthetic.tab', 'h-a10', 'datashop_tab', ?)",
            (len(rows),),
        ).lastrowid
        for i, (eid, selection, action, ts) in enumerate(rows):
            conn.execute(
                "INSERT INTO events (event_id, import_id, student_id, "
                "session_id, problem_name, selection, action, input, "
                "feedback_text, feedback_classification, problem_view, "
                "timestamp, course_id) "
                "VALUES (?, ?, 'S', '100', ?, ?, ?, ?, '', 'Incorrect', ?, "
                "?, 'course')",
                (eid, import_id, self.Q, selection, action, action,
                 str(i + 1), ts),
            )
        conn.commit()
        derive_events(conn)

    @pytest.fixture
    def divergent(self, db_conn, four_shapes_board):
        # One replayed placement, then two checks whose Inputs carry the
        # boards the two malformed (unreplayable) moves would have
        # produced. The replay never advances past (1,0,0,0), both
        # checks grade at replayed d=2, while the grader saw d=1 then
        # d=0. Both checks are therefore check_mismatch rows.
        payload1 = json.dumps({
            f"{self.Q}_drop1": [f"{self.Q}_drag1", f"{self.Q}_drag2"],
        })
        payload2 = json.dumps({
            f"{self.Q}_drop1": [f"{self.Q}_drag1", f"{self.Q}_drag2"],
            f"{self.Q}_drop2": [f"{self.Q}_drag3"],
        })
        self._seed(db_conn, [
            ("m1", "dragNdrop-drop", f"{self.Q}_drag1 -> {self.Q}_drop1",
             "2026-02-06 10:00:00.000000"),
            ("bad1", "dragNdrop-drop", "garbage-action",
             "2026-02-06 10:00:05.000000"),
            ("c1", "dragNdrop", payload1, "2026-02-06 10:00:10.000000"),
            ("bad2", "dragNdrop-drop", "garbage-action-2",
             "2026-02-06 10:00:15.000000"),
            ("c2", "dragNdrop", payload2, "2026-02-06 10:00:20.000000"),
        ])
        four_shapes_board(db_conn)
        reconstruct_all(db_conn)
        score_checks(db_conn)
        process.analyze_all(db_conn)
        return db_conn

    def _metrics(self, conn):
        return conn.execute("SELECT * FROM dnd_attempt_metrics").fetchone()

    def test_f10_scores_the_boards_the_grader_saw(self, divergent):
        m = self._metrics(divergent)
        assert m["first_check_distance"] == 1
        assert m["final_check_distance"] == 0
        assert m["eventual_correct"] == 1

    def test_f7_gains_stay_on_the_replayed_walk(self, divergent):
        # The replayed board is (1,0,0,0) at both checks (d=2, gain 0)
        # while the scored distances above fell 1 -> 0 (an Input-basis
        # gain of 1). The documented divergence, pinned.
        m = self._metrics(divergent)
        assert json.loads(m["post_check_gains_json"]) == [0]

    def test_curve_solved_diverges_from_eventual_correct(self, divergent):
        data = trajectory.trajectory_data(divergent, self.Q)
        [curve] = data.curves
        # The replayed walk is X0 -> (1,0,0,0); both checks grade index 1
        # at d=2, so the curve says unsolved, while F10 above says the
        # grader eventually saw the key.
        assert curve.d_curve == (3, 2)
        assert curve.check_tidx[-1] == 1
        assert curve.solved is False
        assert self._metrics(divergent)["eventual_correct"] == 1


class TestPhaseWindowFeatures:
    """The F-NEW phase-window features (``paper1-decisions.md`` 2026-09-28;
    P5 blocks 1 and 3; ``cc-task-phase-window-features``): the movement and
    time families restricted to the window *before the first check* and to
    the *repair* window from the first check up to and including the first
    correct check. The windows are cut at the F10 *scored* checks, so they
    agree with the grouping ``first_check_correct`` / ``eventual_correct``
    defines. Every expected value is computed by hand in its test's
    docstring from the fixture's event list on the four-shape board
    (P=4, R=3, key (1,1,2,0): three cards keyed to slots plus the circle
    decoy, the brief's "three-card fixture")."""

    Q = "demo-book__demo-shapes-q1"

    # --- the pure function on hand-built replayed rows ---------------------

    @staticmethod
    def _rows(*specs):
        """``(seq, kind, state, seconds)`` → ReplayedRow; ``kind`` ``"noop"``
        is a flagged move, ``None`` the X0 row; ``seconds`` None = off the
        clock."""
        origin = datetime(2026, 2, 6, 10, 0, 0)
        return [
            process.ReplayedRow(
                seq=seq,
                kind="move" if kind == "noop" else kind,
                is_check=kind == "check",
                is_noop=kind == "noop",
                state=state,
                ts=None if secs is None else origin + timedelta(seconds=secs),
            )
            for seq, kind, state, secs in specs
        ]

    # DemoStudent1's replayed sequence (the golden trace): four placements,
    # an incorrect check (the decoy placed), a takeback, a correct check, a
    # reset: on the clock at 5, 11, 16, 36 | 40 | 56 | 60 | 90 s.
    GOLDEN = (
        (0, None, (0, 0, 0, 0), None),
        (1, "move", (1, 0, 0, 0), 5),
        (2, "move", (1, 1, 0, 0), 11),
        (3, "move", (1, 1, 2, 0), 16),
        (4, "move", (1, 1, 2, 3), 36),
        (5, "check", (1, 1, 2, 3), 40),
        (6, "takeback", (1, 1, 2, 0), 56),
        (7, "check", (1, 1, 2, 0), 60),
        (8, "reset", (0, 0, 0, 0), 90),
    )

    def test_no_scored_check_is_all_none(self):
        rows = self._rows(*self.GOLDEN)
        out = process.phase_window_features(
            rows, [], process.outcome_features([]), KEY, 300.0
        )
        assert out == process.PhaseWindowFeatures(*([None] * 11))

    def test_golden_trace_by_hand(self):
        # Scored checks at seq 5 (d=1) and 7 (d=0): first incorrect, correct
        # at ordinal 2, so both windows are defined.
        #   pre    = seq 1..4: 4 effective moves, 0 takebacks, 0 resets;
        #            excess = 4 - d_X(X0, (1,1,2,3)) = 4 - 4 = 0;
        #            active = gaps ending at seq ≤ 5: 6+5+20+4 = 35.
        #   repair = seq 6..7: 1 effective move (the takeback, d 1→0: a fix),
        #            1 check (seq 7); active = gaps ending at seq 6, 7:
        #            16+4 = 20.  The reset (seq 8) and its 30 s gap lie
        #            outside both windows: 35 + 20 + 30 = 85 = total time.
        out = process.phase_window_features(
            self._rows(*self.GOLDEN), [5, 7], process.outcome_features([1, 0]),
            KEY, 300.0,
        )
        assert out == process.PhaseWindowFeatures(
            n_effective_moves_pre=4, n_excess_moves_pre=0, n_takebacks_pre=0,
            n_resets_pre=0, active_time_pre_s=35.0,
            n_effective_moves_repair=1, n_takebacks_repair=1,
            n_resets_repair=0, n_checks_repair=1, active_time_repair_s=20.0,
            n_fix_moves_repair=1,
        )

    def test_first_check_correct_defines_pre_only(self):
        # Correct at the first check: block 1 is defined, block 3 is not.
        rows = self._rows(
            (0, None, (0, 0, 0, 0), None),
            (1, "move", (1, 0, 0, 0), 0),
            (2, "move", (1, 1, 0, 0), 5),
            (3, "move", (1, 1, 2, 0), 10),
            (4, "check", (1, 1, 2, 0), 15),
            (5, "move", (1, 1, 3, 0), 20),  # post-check: outside both
        )
        out = process.phase_window_features(
            rows, [4], process.outcome_features([0]), KEY, 300.0
        )
        assert (out.n_effective_moves_pre, out.n_excess_moves_pre) == (3, 0)
        assert out.active_time_pre_s == 15.0
        assert out.n_effective_moves_repair is None
        assert out.n_checks_repair is None
        assert out.active_time_repair_s is None
        assert out.n_fix_moves_repair is None

    def test_pre_counts_are_zero_when_the_first_check_is_the_first_row(self):
        # "A _pre column on an attempt with a first check but zero pre-window
        # events is 0, not NULL": a check pressed on the untouched board.
        rows = self._rows(
            (0, None, (0, 0, 0, 0), None),
            (1, "check", (0, 0, 0, 0), 0),
            (2, "move", (1, 0, 0, 0), 5),
            (3, "check", (1, 0, 0, 0), 10),
        )
        out = process.phase_window_features(
            rows, [1, 3], process.outcome_features([3, 2]), KEY, 300.0
        )
        assert (out.n_effective_moves_pre, out.n_excess_moves_pre) == (0, 0)
        assert (out.n_takebacks_pre, out.n_resets_pre) == (0, 0)
        assert out.active_time_pre_s == 0.0  # no gap ends at or before seq 1
        assert out.n_effective_moves_repair is None  # never correct

    def test_gap_attribution_around_the_first_check(self):
        # Each clock gap belongs to the window of its later row: the gap
        # *into* the first check is pre (the window runs to the check), the
        # gap *out of* it (the feedback-reading pause) is repair.
        rows = self._rows(
            (0, None, (0, 0, 0, 0), None),
            (1, "move", (1, 0, 0, 0), 0),
            (2, "check", (1, 0, 0, 0), 7),     # gap 7 → pre
            (3, "move", (1, 1, 0, 0), 20),     # gap 13 → repair
            (4, "move", (1, 1, 2, 0), 24),     # gap 4 → repair
            (5, "check", (1, 1, 2, 0), 30),    # gap 6 → repair
            (6, "move", (1, 1, 2, 3), 33),     # gap 3 → outside (post-correct)
        )
        out = process.phase_window_features(
            rows, [2, 5], process.outcome_features([2, 0]), KEY, 300.0
        )
        assert out.active_time_pre_s == 7.0
        assert out.active_time_repair_s == 23.0

    def test_gaps_above_the_idle_threshold_are_excluded_per_window(self):
        # Same rows, δ_max = 10 s: the 13 s gap drops out of repair (23 → 10).
        # The whole-attempt active_time rule, applied inside the window.
        rows = self._rows(
            (0, None, (0, 0, 0, 0), None),
            (1, "move", (1, 0, 0, 0), 0),
            (2, "check", (1, 0, 0, 0), 7),
            (3, "move", (1, 1, 0, 0), 20),
            (4, "move", (1, 1, 2, 0), 24),
            (5, "check", (1, 1, 2, 0), 30),
        )
        out = process.phase_window_features(
            rows, [2, 5], process.outcome_features([2, 0]), KEY, 10.0
        )
        assert out.active_time_pre_s == 7.0
        assert out.active_time_repair_s == 10.0

    def test_gap_spanning_an_off_clock_first_check_belongs_to_repair(self):
        # The first check's timestamp does not parse, so the gap from the
        # last pre row to the first repair row literally spans the check;
        # by the later-row rule it is repair, and pre has no gap (0.0).
        rows = self._rows(
            (0, None, (0, 0, 0, 0), None),
            (1, "move", (1, 0, 0, 0), 0),
            (2, "check", (1, 0, 0, 0), None),
            (3, "move", (1, 1, 0, 0), 10),
            (4, "move", (1, 1, 2, 0), 15),
            (5, "check", (1, 1, 2, 0), 20),
        )
        out = process.phase_window_features(
            rows, [2, 5], process.outcome_features([2, 0]), KEY, 300.0
        )
        assert out.active_time_pre_s == 0.0
        assert out.active_time_repair_s == 20.0

    def test_times_are_none_when_nothing_is_on_the_clock(self):
        # Counts stay defined; the two time fields match the whole-attempt
        # active_time_s, which is None with no timestamped row.
        rows = self._rows(
            (0, None, (0, 0, 0, 0), None),
            (1, "move", (1, 0, 0, 0), None),
            (2, "check", (1, 0, 0, 0), None),
            (3, "move", (1, 1, 0, 0), None),
            (4, "move", (1, 1, 2, 0), None),
            (5, "check", (1, 1, 2, 0), None),
        )
        out = process.phase_window_features(
            rows, [2, 5], process.outcome_features([2, 0]), KEY, 300.0
        )
        assert out.n_effective_moves_pre == 1
        assert out.n_effective_moves_repair == 2
        assert out.active_time_pre_s is None
        assert out.active_time_repair_s is None

    def test_unscored_check_rows_sit_inside_the_windows(self):
        # The windows are cut at the *scored* checks. An unscored check row
        # (unparseable payload) before the first scored check lies in pre:
        # not a move, but a row on the clock; one inside repair counts
        # toward n_checks_repair like any replayed check row (the
        # whole-attempt n_checks rule).
        rows = self._rows(
            (0, None, (0, 0, 0, 0), None),
            (1, "check", (0, 0, 0, 0), 0),      # unscored
            (2, "move", (1, 0, 0, 0), 5),
            (3, "check", (1, 0, 0, 0), 10),     # scored, d=2 (first check)
            (4, "check", (1, 0, 0, 0), 15),     # unscored
            (5, "move", (1, 1, 0, 0), 20),
            (6, "move", (1, 1, 2, 0), 25),
            (7, "check", (1, 1, 2, 0), 30),     # scored, d=0 (first correct)
        )
        out = process.phase_window_features(
            rows, [3, 7], process.outcome_features([2, 0]), KEY, 300.0
        )
        assert out.n_effective_moves_pre == 1
        assert out.active_time_pre_s == 10.0  # both gaps end at seq ≤ 3
        assert out.n_checks_repair == 2  # seq 4 (unscored) and seq 7
        assert out.n_fix_moves_repair == 2
        assert out.active_time_repair_s == 20.0

    def test_fix_moves_need_the_key(self):
        rows = self._rows(*self.GOLDEN)
        out = process.phase_window_features(
            rows, [5, 7], process.outcome_features([1, 0]), None, 300.0
        )
        assert out.n_effective_moves_repair == 1
        assert out.n_fix_moves_repair is None

    def test_excess_floor_is_a_guard(self):
        # On a replayed walk the floor cannot bind. Every effective move
        # changes one card, so the pre move count is at least d_X(X0, state
        # checked). The brief states the floor; this pins it on a sequence
        # replay cannot produce (one move, a four-card state at the check).
        rows = self._rows(
            (0, None, (0, 0, 0, 0), None),
            (1, "move", (1, 0, 0, 0), 0),
            (2, "check", (1, 1, 2, 3), 5),
        )
        out = process.phase_window_features(
            rows, [2], process.outcome_features([1]), KEY, 300.0
        )
        assert out.n_excess_moves_pre == 0

    # --- end to end: the writer on the golden fixtures and synthetic traces

    def _seed(self, conn, rows):
        # The synthesizer idiom of TestTimeToFirstCheckReplayedClock: raw
        # `events` rows → derive_events. Each row: (eid, selection, action,
        # seconds after 10:00:00); a check's payload rides in action/input.
        import_id = conn.execute(
            "INSERT INTO raw_imports (file_path, file_hash, file_format, "
            "row_count) VALUES ('synthetic.tab', 'h-fnew', 'datashop_tab', ?)",
            (len(rows),),
        ).lastrowid
        for i, (eid, selection, action, secs) in enumerate(rows):
            minutes, seconds = divmod(secs, 60)
            conn.execute(
                "INSERT INTO events (event_id, import_id, student_id, "
                "session_id, problem_name, selection, action, input, "
                "feedback_text, feedback_classification, problem_view, "
                "timestamp, course_id) "
                "VALUES (?, ?, 'S', '100', ?, ?, ?, ?, '', 'Incorrect', ?, "
                "?, 'course')",
                (
                    eid, import_id, self.Q, selection, action, action,
                    str(i + 1),
                    f"2026-02-06 10:{minutes:02d}:{seconds:02d}.000000",
                ),
            )
        conn.commit()
        derive_events(conn)

    def _move(self, card, zone):
        target = f"{self.Q}_drop{zone}" if zone else "dragzone"
        return "dragNdrop-drop", f"{self.Q}_drag{card} -> {target}"

    def _check(self, **zones):
        # zones: drop index (as a str key) → placed card indices.
        return "dragNdrop", json.dumps({
            f"{self.Q}_drop{z}": [f"{self.Q}_drag{c}" for c in cards]
            for z, cards in zones.items()
        })

    def _run(self, conn, four_shapes_board, rows, idle_threshold_s=None):
        self._seed(conn, rows)
        four_shapes_board(conn)
        reconstruct_all(conn)
        score_checks(conn)
        config = (
            process.ProcessConfig(idle_threshold_s=idle_threshold_s)
            if idle_threshold_s is not None else None
        )
        process.analyze_all(conn, config=config)
        return conn.execute(
            "SELECT m.*, a.readiness FROM dnd_attempt_metrics m "
            "JOIN dnd_attempts a USING (attempt_id)"
        ).fetchone()

    PRE = (
        "n_effective_moves_pre", "n_excess_moves_pre", "n_takebacks_pre",
        "n_resets_pre", "active_time_pre_s",
    )
    REPAIR = (
        "n_effective_moves_repair", "n_takebacks_repair", "n_resets_repair",
        "n_checks_repair", "active_time_repair_s", "n_fix_moves_repair",
    )

    @pytest.fixture
    def golden(self, db_conn, golden_dnd_tab, four_shapes_board):
        core_ingest.ingest_file(db_conn, golden_dnd_tab)
        derive_events(db_conn)
        four_shapes_board(db_conn)
        reconstruct_all(db_conn)
        score_checks(db_conn)
        process.analyze_all(db_conn)
        return db_conn

    def _metrics(self, conn, student):
        return conn.execute(
            "SELECT m.* FROM dnd_attempt_metrics m JOIN dnd_attempts a "
            "USING (attempt_id) WHERE a.student_id = ?",
            (student,),
        ).fetchone()

    def test_golden_student1_incorrect_then_correct_at_the_second_check(
        self, golden
    ):
        """DemoStudent1 (golden_dnd.tab): drops at 5, 11, 16, 36 s placing
        drag1→drop1, drag2→drop1, drag3→drop2, drag4→drop3; Incorrect check
        at 40 s (state (1,1,2,3), d=1); takeback drag4 at 56 s (d=0);
        Correct check at 60 s; reset at 90 s.

        pre (seq 1-4): effective moves 4; takebacks 0; resets 0; excess =
        4 - d_X(X0, (1,1,2,3)) = 4 - 4 = 0; active = 6+5+20+4 = 35 s
        (= time_to_first_check_s, no gap over δ_max).
        repair (seq 6-7): effective moves 1 (the takeback), takebacks 1,
        resets 0, checks 1 (the correct check), fix moves 1 (d 1→0);
        active = 16+4 = 20 s. The reset and its 30 s lie outside both
        windows: 35 + 20 + 30 = 85 = total_time_s."""
        m = self._metrics(golden, "DemoStudent1")
        assert [m[c] for c in self.PRE] == [4, 0, 0, 0, 35.0]
        assert [m[c] for c in self.REPAIR] == [1, 1, 0, 1, 20.0, 1]
        assert m["active_time_pre_s"] == m["time_to_first_check_s"]
        assert (
            m["active_time_pre_s"] + m["active_time_repair_s"] + 30.0
            == m["total_time_s"]
        )

    def test_golden_student2_pre_cut_at_the_scored_check(self, golden):
        """DemoStudent2: a truncated (unscored) check at 11:00:00, the
        orphan-label check at 11:00:10 (scored: decodes to the empty board,
        d=3), then two unreplayable moves. The windows are cut at the
        scored check, so pre is the single unscored check row: 0 effective
        moves, 0 takebacks, 0 resets, excess 0 - d_X(X0, X0) = 0, active =
        the 10 s gap ending at the scored check. Never correct: repair
        NULL."""
        m = self._metrics(golden, "DemoStudent2")
        assert [m[c] for c in self.PRE] == [0, 0, 0, 0, 10.0]
        assert all(m[c] is None for c in self.REPAIR)

    def test_golden_custom_idle_threshold(
        self, db_conn, golden_dnd_tab, four_shapes_board
    ):
        """δ_max = 10 s on DemoStudent1 (cf. test_custom_idle_threshold):
        pre gaps 6, 5, 20, 4 keep 6+5+4 = 15; repair gaps 16, 4 keep 4;
        the trailing 30 s gap is outside both and over δ_max anyway:
        whole-attempt active_time_s = 19 = 15 + 4."""
        core_ingest.ingest_file(db_conn, golden_dnd_tab)
        derive_events(db_conn)
        four_shapes_board(db_conn)
        reconstruct_all(db_conn)
        score_checks(db_conn)
        process.analyze_all(
            db_conn, config=process.ProcessConfig(idle_threshold_s=10.0)
        )
        m = self._metrics(db_conn, "DemoStudent1")
        assert m["active_time_pre_s"] == 15.0
        assert m["active_time_repair_s"] == 4.0
        assert m["active_time_s"] == 19.0
        assert m["n_effective_moves_pre"] == 4  # counts ignore δ_max

    def test_golden_worked_example(
        self, db_conn, golden_dnd_worked_example_tab, four_shapes_board
    ):
        """WorkedStudent (golden_dnd_worked_example.tab): drag1→drop1 (5 s),
        drag2→drop2 (11 s, wrong slot), drag4→drop3 (16 s, decoy placed),
        drag3→drop2 (36 s); Incorrect check at 40 s (state (1,2,2,3), d=2);
        drag2→drop1 at 56 s (d=1); takeback drag4 at 60 s (d=0); Correct
        check at 90 s.

        pre: 4 effective moves, excess 4 - d_X(X0, (1,2,2,3)) = 0, no
        takeback, no reset, active 6+5+20+4 = 35 s.
        repair: 2 effective moves (both fixing), 1 takeback, 0 resets, 1
        check, active 16+4+30 = 50 s."""
        core_ingest.ingest_file(db_conn, golden_dnd_worked_example_tab)
        derive_events(db_conn)
        four_shapes_board(db_conn)
        reconstruct_all(db_conn)
        score_checks(db_conn)
        process.analyze_all(db_conn)
        m = self._metrics(db_conn, "WorkedStudent")
        assert [m[c] for c in self.PRE] == [4, 0, 0, 0, 35.0]
        assert [m[c] for c in self.REPAIR] == [2, 1, 0, 1, 50.0, 2]

    def test_null_before_score_runs(
        self, db_conn, golden_dnd_tab, four_shapes_board
    ):
        # Without `dnd score` there is no scored check to cut the windows
        # at, so every phase column stays NULL beside the NULL F10 outcomes,
        # even though the attempt replayed two check rows.
        core_ingest.ingest_file(db_conn, golden_dnd_tab)
        derive_events(db_conn)
        four_shapes_board(db_conn)
        reconstruct_all(db_conn)
        process.analyze_all(db_conn)
        m = self._metrics(db_conn, "DemoStudent1")
        assert m["n_checks"] == 2
        assert all(m[c] is None for c in self.PRE + self.REPAIR)

    def test_no_check_is_all_null(self, db_conn, four_shapes_board):
        """Two placements and no check: no window is defined."""
        m = self._run(db_conn, four_shapes_board, [
            ("m1", *self._move(1, 1), 0),
            ("m2", *self._move(2, 1), 5),
        ])
        assert m["n_moves"] == 2
        assert all(m[c] is None for c in self.PRE + self.REPAIR)

    def test_first_check_correct(self, db_conn, four_shapes_board):
        """drag1→drop1 (0 s), drag2→drop1 (5 s), drag3→drop2 (10 s), Correct
        check (15 s), then a post-check move drag3→drop3 (20 s).

        pre: 3 effective moves (= the cadence's n_0), excess 3 - d_X(X0,
        key) = 3 - 3 = 0, 0 takebacks, 0 resets, active 5+5+5 = 15 s.
        repair: undefined (first check correct) → NULL; the post-check
        move belongs to neither window."""
        m = self._run(db_conn, four_shapes_board, [
            ("m1", *self._move(1, 1), 0),
            ("m2", *self._move(2, 1), 5),
            ("m3", *self._move(3, 2), 10),
            ("c1", *self._check(**{"1": [1, 2], "2": [3]}), 15),
            ("m4", *self._move(3, 3), 20),
        ])
        assert (m["first_check_correct"], m["eventual_correct"]) == (1, 1)
        assert [m[c] for c in self.PRE] == [3, 0, 0, 0, 15.0]
        assert m["n_effective_moves_pre"] == json.loads(m["cadence_json"])[0]
        assert all(m[c] is None for c in self.REPAIR)

    def test_first_check_incorrect_never_correct(
        self, db_conn, four_shapes_board
    ):
        """drag1→drop2 (0 s; wrong slot), Incorrect check (10 s; state
        (2,0,0,0), d=3), drag1→drop1 (20 s), Incorrect check (30 s; d=2).

        pre: 1 effective move, excess 1 - d_X(X0, (2,0,0,0)) = 0, active
        10 s. repair: undefined (never correct) → NULL."""
        m = self._run(db_conn, four_shapes_board, [
            ("m1", *self._move(1, 2), 0),
            ("c1", *self._check(**{"2": [1]}), 10),
            ("m2", *self._move(1, 1), 20),
            ("c2", *self._check(**{"1": [1]}), 30),
        ])
        assert (m["first_check_correct"], m["eventual_correct"]) == (0, 0)
        assert [m[c] for c in self.PRE] == [1, 0, 0, 0, 10.0]
        assert all(m[c] is None for c in self.REPAIR)

    def test_incorrect_then_correct_at_the_second_check_with_excess(
        self, db_conn, four_shapes_board
    ):
        """drag1→drop2 (0 s; wrong), drag1→drop1 (5 s), drag2→drop1 (10 s),
        Incorrect check (15 s; state (1,1,0,0), d=1), drag3→drop2 (25 s),
        Correct check (30 s).

        pre: 3 effective moves; the state checked, (1,1,0,0), is 2 moves
        from X0, so excess = 3 - 2 = 1; active 5+5+5 = 15 s.
        repair: 1 effective move (d 1→0: a fix), 0 takebacks, 0 resets,
        1 check, active 10+5 = 15 s."""
        m = self._run(db_conn, four_shapes_board, [
            ("m1", *self._move(1, 2), 0),
            ("m2", *self._move(1, 1), 5),
            ("m3", *self._move(2, 1), 10),
            ("c1", *self._check(**{"1": [1, 2]}), 15),
            ("m4", *self._move(3, 2), 25),
            ("c2", *self._check(**{"1": [1, 2], "2": [3]}), 30),
        ])
        assert m["correct_check_ordinal"] == 2
        assert [m[c] for c in self.PRE] == [3, 1, 0, 0, 15.0]
        assert [m[c] for c in self.REPAIR] == [1, 0, 0, 1, 15.0, 1]

    def test_incorrect_with_two_more_incorrect_checks_before_the_correct_one(
        self, db_conn, four_shapes_board
    ):
        """drag1→drop1 (0 s); Incorrect check (10 s; d=2); drag2→drop2
        (20 s; wrong slot, d stays 2: lateral); Incorrect check (30 s);
        drag2→drop1 (40 s; d=1: fix); Incorrect check (50 s); drag3→drop2
        (60 s; d=0: fix); Correct check (70 s; ordinal 4); then post-correct
        drag4→drop3 (80 s) and an Incorrect check (90 s; d=1).

        pre: 1 effective move, excess 1 - 1 = 0, active 10 s.
        repair (after the first check through the fourth): 3 effective
        moves, 0 takebacks, 0 resets, 3 checks (ordinals 2, 3, 4), 2 fix
        moves (the lateral one excluded), active 6 × 10 = 60 s. The two
        post-correct rows (20 s) lie outside: 10 + 60 + 20 = 90 = total."""
        m = self._run(db_conn, four_shapes_board, [
            ("m1", *self._move(1, 1), 0),
            ("c1", *self._check(**{"1": [1]}), 10),
            ("m2", *self._move(2, 2), 20),
            ("c2", *self._check(**{"1": [1], "2": [2]}), 30),
            ("m3", *self._move(2, 1), 40),
            ("c3", *self._check(**{"1": [1, 2]}), 50),
            ("m4", *self._move(3, 2), 60),
            ("c4", *self._check(**{"1": [1, 2], "2": [3]}), 70),
            ("m5", *self._move(4, 3), 80),
            ("c5", *self._check(**{"1": [1, 2], "2": [3], "3": [4]}), 90),
        ])
        assert m["correct_check_ordinal"] == 4
        assert m["final_check_distance"] == 1
        assert [m[c] for c in self.PRE] == [1, 0, 0, 0, 10.0]
        assert [m[c] for c in self.REPAIR] == [3, 0, 0, 3, 60.0, 2]
        assert m["active_time_pre_s"] + m["active_time_repair_s"] + 20.0 == 90.0
        assert m["total_time_s"] == 90.0

    def test_repair_window_with_a_takeback_a_reset_and_a_noop(
        self, db_conn, four_shapes_board
    ):
        """The synthetic-fault trace. Pre: drag1→drop1, drag2→drop1,
        drag3→drop2, drag4→drop3 at 0, 5, 10, 15 s (decoy placed, state
        (1,1,2,3), d=1); Incorrect check at 20 s. Repair: drag4→drop3 again
        at 25 s (a no-op, the card is already there), takeback drag4 at
        30 s (d=0), reset at 35 s (back to X0, d=3), drag1→drop2 at 40 s
        (wrong slot, d stays 3: lateral), drag1→drop1 at 45 s (d=2),
        drag2→drop1 at 50 s (d=1), drag3→drop2 at 55 s (d=0), Correct check
        at 60 s.

        pre: 4 effective moves, excess 4 - 4 = 0, 0 takebacks, 0 resets,
        active 4 × 5 = 20 s.
        repair: effective moves 5 (the no-op excluded, as in n_moves), 1
        takeback, 1 reset, 1 check, 4 fix moves (takeback 1→0, then 3→2,
        2→1, 1→0; the lateral move excluded), active = the eight 5 s gaps
        ending at the rows after the first check (the no-op row is on the
        clock), as it is for active_time_s (= 40 s). Whole attempt: n_moves
        9, n_noop_moves 1, 20 + 40 = 60 = total_time_s."""
        m = self._run(db_conn, four_shapes_board, [
            ("m1", *self._move(1, 1), 0),
            ("m2", *self._move(2, 1), 5),
            ("m3", *self._move(3, 2), 10),
            ("m4", *self._move(4, 3), 15),
            ("c1", *self._check(**{"1": [1, 2], "2": [3], "3": [4]}), 20),
            ("m5", *self._move(4, 3), 25),  # no-op
            ("m6", *self._move(4, 0), 30),  # takeback
            ("r1", "dragNdrop-reset", "reset", 35),
            ("m7", *self._move(1, 2), 40),  # lateral
            ("m8", *self._move(1, 1), 45),
            ("m9", *self._move(2, 1), 50),
            ("m10", *self._move(3, 2), 55),
            ("c2", *self._check(**{"1": [1, 2], "2": [3]}), 60),
        ])
        assert (m["n_moves"], m["n_noop_moves"]) == (9, 1)
        assert (m["n_takebacks"], m["n_resets"], m["n_checks"]) == (1, 1, 2)
        assert m["readiness"] == "ready"  # noop_move is informational
        assert [m[c] for c in self.PRE] == [4, 0, 0, 0, 20.0]
        assert [m[c] for c in self.REPAIR] == [5, 1, 1, 1, 40.0, 4]
        assert m["active_time_pre_s"] + m["active_time_repair_s"] == 60.0

    def test_long_idle_gap_is_excluded_from_the_window_time(
        self, db_conn, four_shapes_board
    ):
        """drag1→drop1 (0 s), drag2→drop1 (400 s, over δ_max = 300),
        drag3→drop2 (405 s), Correct check (410 s). Pre active time keeps
        5 + 5 = 10 s; time_to_first_check_s (a raw difference) reads 410,
        and the attempt is flagged long_idle_gap (limited)."""
        m = self._run(db_conn, four_shapes_board, [
            ("m1", *self._move(1, 1), 0),
            ("m2", *self._move(2, 1), 400),
            ("m3", *self._move(3, 2), 405),
            ("c1", *self._check(**{"1": [1, 2], "2": [3]}), 410),
        ])
        assert m["readiness"] == "limited"
        assert m["active_time_pre_s"] == 10.0
        assert m["time_to_first_check_s"] == 410.0
        assert m["active_time_s"] == 10.0

    def test_unscored_leading_check_row_lies_inside_pre(
        self, db_conn, four_shapes_board
    ):
        """A check whose Input is not valid JSON (0 s; replayed, flagged
        parse_error, never scored), drag1→drop1 (5 s), Incorrect check
        (10 s; d=2, the first *scored* check). The pre window ends at the
        scored check, so it holds the unscored check row and the move: 1
        effective move, excess 1 - 1 = 0, active 5 + 5 = 10 s. The
        whole-attempt time_to_first_check_s (first *replayed* check row)
        reads 0 (the two bases differ only on such limited attempts)."""
        m = self._run(db_conn, four_shapes_board, [
            ("c0", "dragNdrop", "{", 0),
            ("m1", *self._move(1, 1), 5),
            ("c1", *self._check(**{"1": [1]}), 10),
        ])
        assert m["readiness"] == "limited"
        assert m["n_checks"] == 2
        assert [m[c] for c in self.PRE] == [1, 0, 0, 0, 10.0]
        assert m["time_to_first_check_s"] == 0.0
        assert all(m[c] is None for c in self.REPAIR)


class TestSensitivityAlternatives:
    """The two D3.2 alternatives that live on ``ProcessConfig``
    (cc-task-frozen-run-tooling; owner decisions 2026-10-04): knob 3 as the
    read-time truncation at the first correct scored check, and knob 4
    counting the flagged no-op drops as moves. Every expectation is computed
    by hand in the docstring; the defaults are untouched (the golden tests
    above stand)."""

    Q = "demo-book__demo-shapes-q1"

    def _golden(self, conn, golden_dnd_tab, four_shapes_board, config):
        core_ingest.ingest_file(conn, golden_dnd_tab)
        derive_events(conn)
        four_shapes_board(conn)
        reconstruct_all(conn)
        score_checks(conn)
        process.analyze_all(conn, config=config)
        return conn.execute(
            "SELECT m.* FROM dnd_attempt_metrics m JOIN dnd_attempts a "
            "USING (attempt_id) WHERE a.student_id = 'DemoStudent1'"
        ).fetchone()

    def test_truncate_at_first_correct_on_the_golden_trace(
        self, db_conn, golden_dnd_tab, four_shapes_board
    ):
        """DemoStudent1's first correct scored check is at seq 7 (60 s); the
        reset at seq 8 (90 s) is dropped before anything is computed.

        Walk X0, (1,0,0,0), (1,1,0,0), (1,1,2,0), (1,1,2,3), (1,1,2,0): six
        states, five distinct, one repeat; d = 3,2,1,0,1,0; moves 5 (one
        takeback), resets 0, checks 2; F=4, L=0, B=1, E_move = 5 - 3 = 2,
        E_path = 5 - d_X(X0, (1,1,2,0)) = 2 (the empty trailing segment is
        gone); clock 5..60 s: total 55, active 55, max gap 20. Outcomes:
        first d=1, final d=0 (the correct check is now the last), ordinal 2.
        The phase windows end at or before that check and are unchanged."""
        m = self._golden(
            db_conn, golden_dnd_tab, four_shapes_board,
            process.ProcessConfig(truncate_at_first_correct=True),
        )
        assert (m["n_moves"], m["n_takebacks"], m["n_resets"], m["n_checks"]) == (5, 1, 0, 2)
        assert json.loads(m["convergence_json"]) == [3, 2, 1, 0, 1, 0]
        assert (m["n_distinct_states"], m["n_repeated_states"]) == (5, 1)
        assert (m["n_fixing"], m["n_lateral"], m["n_breaking"]) == (4, 0, 1)
        assert (m["excess_moves"], m["path_excess"]) == (2, 2)
        assert (m["total_time_s"], m["active_time_s"], m["max_action_gap_s"]) == (55.0, 55.0, 20.0)
        assert (m["first_check_distance"], m["final_check_distance"]) == (1, 0)
        assert (m["eventual_correct"], m["correct_check_ordinal"]) == (1, 2)
        assert [m[c] for c in TestPhaseWindowFeatures.PRE] == [4, 0, 0, 0, 35.0]
        assert [m[c] for c in TestPhaseWindowFeatures.REPAIR] == [1, 1, 0, 1, 20.0, 1]
        cfg = json.loads(db_conn.execute(
            "SELECT config_json FROM pipeline_runs WHERE stage = 'analyze' "
            "ORDER BY run_id DESC LIMIT 1"
        ).fetchone()["config_json"])
        assert cfg["truncate_at_first_correct"] is True
        assert cfg["exclude_noop_moves"] is True

    def test_truncation_leaves_a_never_correct_attempt_whole(
        self, db_conn, golden_dnd_tab, four_shapes_board
    ):
        # DemoStudent2 is never correct, so nothing is cut: both check rows
        # and the 10 s clock stand exactly as under the default.
        self._golden(
            db_conn, golden_dnd_tab, four_shapes_board,
            process.ProcessConfig(truncate_at_first_correct=True),
        )
        m = db_conn.execute(
            "SELECT m.* FROM dnd_attempt_metrics m JOIN dnd_attempts a "
            "USING (attempt_id) WHERE a.student_id = 'DemoStudent2'"
        ).fetchone()
        assert m["n_checks"] == 2
        assert m["total_time_s"] == 10.0
        assert m["final_check_distance"] == 3

    def test_truncation_leaves_attempts_flags_and_segments_untouched(
        self, db_conn, golden_dnd_tab, four_shapes_board
    ):
        # Read-time option (i): only dnd_attempt_metrics changes.
        self._golden(
            db_conn, golden_dnd_tab, four_shapes_board,
            process.ProcessConfig(truncate_at_first_correct=True),
        )
        a = db_conn.execute(
            "SELECT qc_flags_json, readiness FROM dnd_attempts "
            "WHERE student_id = 'DemoStudent1'"
        ).fetchone()
        assert (a["qc_flags_json"], a["readiness"]) == ('["replay_ok"]', "ready")
        n_configs = db_conn.execute(
            "SELECT count(*) AS n FROM dnd_configurations c JOIN dnd_attempts a "
            "USING (attempt_id) WHERE a.student_id = 'DemoStudent1'"
        ).fetchone()["n"]
        assert n_configs == 9  # X0 + eight replayed rows, the reset included
        spans = db_conn.execute(
            "SELECT segment_type, end_seq FROM dnd_segments s JOIN dnd_attempts a "
            "USING (attempt_id) WHERE a.student_id = 'DemoStudent1' "
            "AND segment_type = 'reset' ORDER BY segment_index"
        ).fetchall()
        assert [r["end_seq"] for r in spans] == [8]

    def test_count_noop_moves_on_the_noop_fixture(
        self, db_conn, four_shapes_board
    ):
        """TestNoopExclusion's trace: drag1→drop1 (0 s), Incorrect check
        (5 s; d=2), drag1→drop1 again (10 s; the flagged no-op), drag2→drop1
        (15 s), Incorrect check (20 s; d=1).

        Counted as a move, the no-op is a lateral step of the walk: moves
        3 (n_noop_moves still 1), walk X0, (1,0,0,0), (1,0,0,0), (1,1,0,0)
        with one repeated state, d = 3,2,2,1, F=2, L=1, B=0, E_move =
        3 - 2 = 1, E_path = 3 - d_X(X0, (1,1,0,0)) = 1, cadence (1, 2) so
        the grader-as-oracle probe no longer fires; the pre window (before
        the first scored check at 5 s) holds one move either way."""
        drop1 = f"{self.Q}_drop1"
        TestNoopExclusion()._seed(db_conn, [
            {"eid": "m1", "selection": "dragNdrop-drop",
             "action": f"{self.Q}_drag1 -> {drop1}",
             "ts": "2026-02-06 10:00:00.000000"},
            {"eid": "c1", "selection": "dragNdrop",
             "input": json.dumps({drop1: [f"{self.Q}_drag1"]}),
             "feedback": "Incorrect", "ts": "2026-02-06 10:00:05.000000"},
            {"eid": "m2", "selection": "dragNdrop-drop",
             "action": f"{self.Q}_drag1 -> {drop1}",
             "ts": "2026-02-06 10:00:10.000000"},
            {"eid": "m3", "selection": "dragNdrop-drop",
             "action": f"{self.Q}_drag2 -> {drop1}",
             "ts": "2026-02-06 10:00:15.000000"},
            {"eid": "c2", "selection": "dragNdrop",
             "input": json.dumps({drop1: [f"{self.Q}_drag1", f"{self.Q}_drag2"]}),
             "feedback": "Incorrect", "ts": "2026-02-06 10:00:20.000000"},
        ])
        four_shapes_board(db_conn)
        reconstruct_all(db_conn)
        score_checks(db_conn)
        process.analyze_all(
            db_conn, config=process.ProcessConfig(count_noop_moves=True)
        )
        m = db_conn.execute("SELECT * FROM dnd_attempt_metrics").fetchone()
        assert (m["n_moves"], m["n_noop_moves"]) == (3, 1)
        assert json.loads(m["convergence_json"]) == [3, 2, 2, 1]
        assert (m["n_distinct_states"], m["n_repeated_states"]) == (3, 1)
        assert (m["n_fixing"], m["n_lateral"], m["n_breaking"]) == (2, 1, 0)
        assert (m["excess_moves"], m["path_excess"]) == (1, 1)
        assert json.loads(m["cadence_json"]) == [1, 2]
        assert m["checked_every_move"] == 0
        assert m["n_effective_moves_pre"] == 1
        cfg = json.loads(db_conn.execute(
            "SELECT config_json FROM pipeline_runs WHERE stage = 'analyze' "
            "ORDER BY run_id DESC LIMIT 1"
        ).fetchone()["config_json"])
        assert cfg["exclude_noop_moves"] is False
        assert cfg["truncate_at_first_correct"] is False
