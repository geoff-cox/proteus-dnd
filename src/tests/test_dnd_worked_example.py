"""
The worked-example golden test (plan item B3.6).

This is the Phase-B exit anchor: the six-move four-shape attempt (the
journal's retired ``sec-worked-example`` walk — this test is its frozen
record; the definitions it exercises live in ``sec-checked-state-features``
and ``sec-trajectory-features``) flows end to end — ingest → derive →
sync-boards → reconstruct → score → analyze — and reproduces the
hand-checkable numbers. Per the project rules this baseline is frozen: if it
fails, the change is wrong, not the test.

The headline assertions the plan names (B3.6):

* σ(X₄) = 2 at the first check;
* the first check is Incorrect with both structural decoys leaked;
* X₆ equals the key; and
* g(X₆) = 1.

with the full convergence curve, the cross-check, and the error profile pinned
alongside so the whole pipeline is exercised, not just the two checks.
"""

from __future__ import annotations

import json

import pytest

from proteus.core import ingest as core_ingest
from proteus.services.dragndrop import errors
from proteus.services.dragndrop.derive import derive_events
from proteus.services.dragndrop.process import analyze_all
from proteus.services.dragndrop.reconstruct import reconstruct_all
from proteus.services.dragndrop.scoring import score_checks

Q = "demo-book__demo-shapes-q1"
KEY = (1, 1, 2, 0)
R = 3


@pytest.fixture
def worked(db_conn, golden_dnd_worked_example_tab, four_shapes_board):
    core_ingest.ingest_file(db_conn, golden_dnd_worked_example_tab)
    derive_events(db_conn)
    four_shapes_board(db_conn)
    reconstruct_all(db_conn)
    score_checks(db_conn)
    analyze_all(db_conn)
    return db_conn


def _scores(conn):
    """The two check scores, in check order (Incorrect then Correct)."""
    return conn.execute(
        "SELECT s.*, d.feedback FROM dnd_check_scores s "
        "JOIN dnd_events d USING (event_id) JOIN events e USING (event_id) "
        "ORDER BY e.timestamp"
    ).fetchall()


class TestReplay:
    def test_single_attempt_of_six_moves(self, worked):
        attempt = worked.execute(
            "SELECT * FROM dnd_attempts WHERE student_id = 'WorkedStudent'"
        ).fetchall()
        assert len(attempt) == 1
        metrics = worked.execute(
            "SELECT * FROM dnd_attempt_metrics WHERE attempt_id = ?",
            (attempt[0]["attempt_id"],),
        ).fetchone()
        assert metrics["n_moves"] == 6
        assert metrics["n_takebacks"] == 1
        assert metrics["n_resets"] == 0
        assert metrics["n_checks"] == 2

    def test_state_trajectory(self, worked):
        states = [
            tuple(json.loads(r["s_json"]))
            for r in worked.execute(
                "SELECT s_json FROM dnd_configurations WHERE is_check = 0 "
                "ORDER BY seq"
            )
        ]
        # X0 … X6 of the worked walk (retired subsec-we-attempt; checks
        # excluded — they don't move).
        assert states == [
            (0, 0, 0, 0), (1, 0, 0, 0), (1, 2, 0, 0), (1, 2, 0, 3),
            (1, 2, 2, 3), (1, 1, 2, 3), (1, 1, 2, 0),
        ]

    def test_both_checks_cross_check_clean(self, worked):
        # The replayed state equals the logged Input at both checks (B3.2).
        flags = [
            r["matches_input"]
            for r in worked.execute(
                "SELECT matches_input FROM dnd_configurations "
                "WHERE is_check = 1 ORDER BY seq"
            )
        ]
        assert flags == [1, 1]


class TestScoring:
    def test_first_check_sigma_is_two(self, worked):
        first, _ = _scores(worked)
        # B3.6: σ(X4) = 2.
        assert first["feedback"] == "Incorrect"
        assert first["sigma"] == 2
        assert first["distance"] == 2
        assert json.loads(first["per_card_json"]) == [1, 0, 1, 0]

    def test_first_check_leaks_both_decoys(self, worked):
        # B3.6: the first check is Incorrect with both decoys leaked.
        first, _ = _scores(worked)
        assert first["all_or_nothing"] == 0
        assert first["decoy_source_leak"] == 1  # circle dragged out of the tray
        assert first["decoy_target_leak"] == 1  # … into the empty-slot decoy
        assert first["g"] == pytest.approx(1 / 3)

    def test_second_check_is_the_key(self, worked):
        _, second = _scores(worked)
        # B3.6: X6 = key, g(X6) = 1.
        assert second["feedback"] == "Correct"
        assert second["sigma"] == 4
        assert second["all_or_nothing"] == 1
        assert second["g"] == pytest.approx(1.0)
        assert (second["decoy_source_leak"], second["decoy_target_leak"]) == (0, 0)


class TestProcessAndErrors:
    def test_convergence_curve(self, worked):
        curve = worked.execute(
            "SELECT convergence_json FROM dnd_attempt_metrics"
        ).fetchone()["convergence_json"]
        # The worked walk's d(s_k) curve (retired tab-we-curve; the distance
        # curve itself is subsec-distance-curve).
        assert json.loads(curve) == [3, 2, 2, 3, 2, 1, 0]

    def test_time_to_first_check(self, worked):
        # First drop at 10:00:05, first check at 10:00:40 → 35 seconds.
        t = worked.execute(
            "SELECT time_to_first_check_s FROM dnd_attempt_metrics"
        ).fetchone()["time_to_first_check_s"]
        assert t == 35.0

    def test_first_check_error_profile(self, worked):
        # The worked first check's five-count profile (retired subsec-we-errors
        # numbers; the profile set is that of subsec-error-profile-bars): no
        # omissions, one misplaced decoy, one merge pair, one split pair, no
        # mislabel.
        assert errors.error_profile((1, 2, 2, 3), KEY, R) == errors.ErrorProfile(
            omissions=0, placed_decoys=1, merge_pairs=1, split_pairs=1, mislabels=0
        )
