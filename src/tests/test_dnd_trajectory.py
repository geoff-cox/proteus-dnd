"""
Tests for the F21 trajectory aggregation (``services.dragndrop.trajectory``).

Two layers, matching the module's split:

* the pure estimation pinned to the journal's worked Markov example
  (ch-statistical-modeling ``subsec-markov`` / ``tab-cheat-markov``):
  the illustrative transition matrix over {0,1,2,3} must yield
  **t = (145/63, 95/21, 130/21)**. If this fails, the estimation is wrong,
  not the baseline;
* ``trajectory_data`` flowed through the real pipeline (ingest -> derive ->
  sync-board -> reconstruct) over the golden DnD fixture, whose reference
  attempt walks d = [3, 2, 1, 0, 1, 0, 3] (four placements, check, takeback,
  check, reset: the same walk ``test_dnd_process`` pins), plus the F24
  availability gate and the F17a stale-walk posture.
"""

from __future__ import annotations

import json

import pytest

from proteus.core import ingest as core_ingest
from proteus.core.filters import FilterSpec
from proteus.services.dragndrop import trajectory
from proteus.services.dragndrop.derive import derive_events
from proteus.services.dragndrop.reconstruct import reconstruct_all

Q = "demo-book__demo-shapes-q1"
KEY = (1, 1, 2, 0)

# The journal's illustrative one-step transition probabilities
# (tab-cheat-markov), as counts out of 10 so row-normalizing reproduces them.
JOURNAL_TRANS = {
    1: {0: 7, 1: 1, 2: 1, 3: 1},
    2: {1: 6, 2: 2, 3: 2},
    3: {2: 6, 3: 4},
}


class TestJournalWorkedExample:
    def test_expected_moves_to_solve(self):
        markov = trajectory.distance_markov(JOURNAL_TRANS)
        assert markov is not None
        assert markov.transient == (1, 2, 3)
        assert markov.expected is not None
        by_d = {e.distance: e.moves for e in markov.expected}
        assert by_d[1] == pytest.approx(145 / 63, abs=1e-9)
        assert by_d[2] == pytest.approx(95 / 21, abs=1e-9)
        assert by_d[3] == pytest.approx(130 / 21, abs=1e-9)

    def test_move_mix_rates(self):
        markov = trajectory.distance_markov(JOURNAL_TRANS)
        by_d = {r.distance: r for r in markov.rates}
        assert set(by_d) == {1, 2, 3}
        # d=1: the only distance with absorption mass (fix reaches the key).
        assert by_d[1].n_from == 10
        assert by_d[1].fixing == pytest.approx(0.7)
        assert by_d[1].lateral == pytest.approx(0.1)
        assert by_d[1].breaking == pytest.approx(0.1)
        # 1 -> 3 is a two-step excursion: the residual jump band.
        assert by_d[1].jump == pytest.approx(0.1)
        assert by_d[2].fixing == pytest.approx(0.6)
        assert by_d[2].jump == pytest.approx(0.0)
        # d=3 is the cap: 0.6 fix down to 2, 0.4 lateral (folded excursions).
        assert by_d[3].fixing == pytest.approx(0.6)
        assert by_d[3].lateral == pytest.approx(0.4)
        assert by_d[3].breaking == pytest.approx(0.0)

    def test_transition_matrix_cells(self):
        markov = trajectory.distance_markov(JOURNAL_TRANS)
        cells = {(c.from_d, c.to_d): c for c in markov.matrix}
        # Every displayed row x column 0..d_max is present.
        assert set(cells) == {(f, t) for f in (1, 2, 3) for t in (0, 1, 2, 3)}
        assert cells[(1, 0)].prob == pytest.approx(0.7)
        assert cells[(1, 0)].count == 7
        assert cells[(2, 0)].prob == pytest.approx(0.0)
        assert cells[(3, 2)].prob == pytest.approx(0.6)
        assert markov.d_max == 3
        assert markov.n_transitions == 30

    def test_headline_is_t3_from_the_empty_board(self):
        # subsec-markov: from d*(X0)=3 the cohort expects ~6.19 moves.
        markov = trajectory.distance_markov(JOURNAL_TRANS)
        by_d = {e.distance: e.moves for e in markov.expected}
        assert by_d[3] == pytest.approx(6.19, abs=5e-3)


class TestEstimationEdges:
    def test_no_transitions_is_none(self):
        assert trajectory.distance_markov({}) is None
        assert trajectory.distance_markov({1: {}}) is None

    def test_singular_chain_has_no_finite_expectation(self):
        # A pure self-loop never reaches the solved state: t is not finite.
        markov = trajectory.distance_markov({1: {1: 5}})
        assert markov is not None
        assert markov.transient == (1,)
        assert markov.expected is None
        assert markov.rates[0].lateral == pytest.approx(1.0)

    def test_censored_terminal_makes_expectation_unavailable(self):
        # PR #151 review: the only observed attempt ended UNSOLVED at d=2
        # (a walk-final destination with no outflow). Folding that mass into
        # the absorption deficit would report "1 expected move to solve";
        # the expectation must be unavailable instead.
        markov = trajectory.distance_markov({1: {2: 1}})
        assert markov is not None
        assert markov.expected is None
        # The observed mix and matrix stand: the move was a break.
        assert markov.rates[0].breaking == pytest.approx(1.0)

    def test_censoring_wins_even_beside_real_absorption(self):
        # Five solves and one censored ending: a finite t would silently
        # count the censored ending as a sixth solve.
        markov = trajectory.distance_markov({1: {0: 5, 2: 1}})
        assert markov is not None
        assert markov.expected is None

    def test_solver_matches_the_journal_fundamental_matrix(self):
        q = [[0.1, 0.1, 0.1], [0.6, 0.2, 0.2], [0.0, 0.6, 0.4]]
        t = trajectory.solve_expected_moves([1, 2, 3], q)
        assert t == pytest.approx([145 / 63, 95 / 21, 130 / 21], abs=1e-9)

    def test_pooled_transitions_coarsens_by_distance(self):
        walk = [(0, 0, 0, 0), (1, 0, 0, 0), (1, 1, 0, 0), (1, 1, 2, 0)]
        trans = trajectory.pooled_transitions([walk], KEY)
        assert trans == {3: {2: 1}, 2: {1: 1}, 1: {0: 1}}

    def test_pooled_transitions_no_walks(self):
        assert trajectory.pooled_transitions([], KEY) == {}


# ---------------------------------------------------------------------------
# The real pipeline over the golden DnD fixture
# ---------------------------------------------------------------------------
@pytest.fixture
def reconstructed(db_conn, four_shapes_board, golden_dnd_tab):
    core_ingest.ingest_file(db_conn, golden_dnd_tab)
    derive_events(db_conn)
    four_shapes_board(db_conn)
    reconstruct_all(db_conn)
    return db_conn


X0 = (0, 0, 0, 0)
# DemoStudent1's retained walk (d = 3, 2, 1, 0, 1, 0, 3): four placements,
# the decoy takeback, and the closing reset. DemoStudent2 contributes X0 only.
S1_WALK = [
    X0,
    (1, 0, 0, 0),
    (1, 1, 0, 0),
    (1, 1, 2, 0),
    (1, 1, 2, 3),
    (1, 1, 2, 0),
    X0,
]


class TestTrajectoryData:
    def test_bundle_shape(self, reconstructed):
        data = trajectory.trajectory_data(reconstructed, Q)
        assert data is not None
        assert data.question_id == Q
        assert data.n_attempts == 2
        assert data.n_excluded_attempts == 0
        assert data.start_distance == 3

    def test_network_nodes_pool_visits(self, reconstructed):
        data = trajectory.trajectory_data(reconstructed, Q)
        visits = {n.s: n.visits for n in data.nodes}
        # X0: S1's start + S1's post-reset return + S2's lone X0.
        assert visits[X0] == 3
        # The key-complete state: reached by placement, revisited by takeback.
        assert visits[(1, 1, 2, 0)] == 2
        assert set(visits) == set(S1_WALK)

    def test_network_edges_are_ordinary_moves_only(self, reconstructed):
        data = trajectory.trajectory_data(reconstructed, Q)
        edges = {(e.s_from, e.s_to): e.count for e in data.edges}
        # The four placements and the takeback, one observation each.
        assert edges == {
            (X0, (1, 0, 0, 0)): 1,
            ((1, 0, 0, 0), (1, 1, 0, 0)): 1,
            ((1, 1, 0, 0), (1, 1, 2, 0)): 1,
            ((1, 1, 2, 0), (1, 1, 2, 3)): 1,
            ((1, 1, 2, 3), (1, 1, 2, 0)): 1,
        }
        # The reset's jump back to X0 is not an edge of the configuration graph.
        assert ((1, 1, 2, 0), X0) not in edges

    def test_markov_over_the_reference_walk(self, reconstructed):
        data = trajectory.trajectory_data(reconstructed, Q)
        markov = data.markov
        assert markov is not None
        # Six board-changing transitions, the reset jump (0 -> 3) included.
        assert markov.n_transitions == 6
        assert markov.transient == (1, 2, 3)
        counts = {(c.from_d, c.to_d): c.count for c in markov.matrix}
        assert counts[(1, 0)] == 2  # the third placement and the takeback
        assert counts[(2, 1)] == 1
        assert counts[(3, 2)] == 1
        # Every observed move fixes, so t climbs one move per distance.
        expected = {e.distance: e.moves for e in markov.expected}
        assert expected == {
            1: pytest.approx(1.0),
            2: pytest.approx(2.0),
            3: pytest.approx(3.0),
        }

    def test_attempt_curves_carry_checks_and_outcome(self, reconstructed):
        # F24: the per-attempt distance curves behind the convergence charts.
        data = trajectory.trajectory_data(reconstructed, Q)
        by_label = {c.label: c for c in data.curves}
        assert set(by_label) == {"DemoStudent1", "DemoStudent2"}
        s1 = by_label["DemoStudent1"]
        assert s1.d_curve == (3, 2, 1, 0, 1, 0, 3)
        # Checks grade the decoy-placed board (t=4, d=1) and the post-takeback
        # key (t=5, d=0); the closing reset extends the curve past the solve.
        assert s1.check_tidx == (4, 5)
        assert s1.solved is True
        s2 = by_label["DemoStudent2"]
        assert s2.d_curve == (3,)
        assert s2.solved is False

    def test_filter_narrows_the_attempt_pool(self, reconstructed):
        # DemoStudent2's events are all at 11:00+; a 10:59 bound keeps S1 only.
        spec = FilterSpec(date_to="2026-02-06 10:59:59")
        data = trajectory.trajectory_data(reconstructed, Q, filter_spec=spec)
        assert data.n_attempts == 1
        visits = {n.s: n.visits for n in data.nodes}
        assert visits[X0] == 2  # S2's lone X0 is out of scope
        assert data.markov.n_transitions == 6  # S1 carries every transition
        assert [c.label for c in data.curves] == ["DemoStudent1"]

    def test_filter_selecting_nothing_degrades_to_empty(self, reconstructed):
        spec = FilterSpec(class_id="no-such-class")
        data = trajectory.trajectory_data(reconstructed, Q, filter_spec=spec)
        assert data is not None  # trajectories exist; the filter is the cause
        assert data.n_attempts == 0
        assert data.nodes == ()
        assert data.edges == ()
        assert data.markov is None
        assert data.curves == ()


class TestAvailabilityGate:
    def test_no_board_is_unavailable(self, db_conn):
        assert trajectory.trajectory_data(db_conn, "never-synced") is None

    def test_board_without_reconstruction_is_unavailable(
        self, db_conn, four_shapes_board
    ):
        four_shapes_board(db_conn)
        assert trajectory.trajectory_data(db_conn, Q) is None

    def test_check_only_configuration_set_is_unavailable(self, reconstructed):
        # A broken set: check rows (and their scores) survive but the walk is
        # gone. The v1 charts keep working off the checks; the trajectory
        # bundle must gate rather than pretend an empty cohort walked.
        reconstructed.execute(
            "DELETE FROM dnd_configurations WHERE is_check = 0"
        )
        assert trajectory.trajectory_data(reconstructed, Q) is None


class TestStaleWalks:
    """F17a posture: a board-invalid walk is excluded whole, never raised."""

    @pytest.mark.parametrize(
        "bad_state", ["[0, 0, 0, 0, 0]", "[9, 0, 0, 0]"],
        ids=["wrong-card-count", "zone-out-of-range"],
    )
    def test_stale_attempt_is_excluded_and_counted(self, reconstructed, bad_state):
        attempt_id = reconstructed.execute(
            "SELECT attempt_id FROM dnd_attempts WHERE student_id = 'DemoStudent2'"
        ).fetchone()["attempt_id"]
        reconstructed.execute(
            "UPDATE dnd_configurations SET s_json = ? WHERE attempt_id = ?",
            (bad_state, attempt_id),
        )
        data = trajectory.trajectory_data(reconstructed, Q)
        assert data is not None
        assert data.n_attempts == 1
        assert data.n_excluded_attempts == 1
        # The intact attempt still aggregates in full.
        assert data.markov.n_transitions == 6
        assert json.loads(bad_state) not in [list(n.s) for n in data.nodes]
