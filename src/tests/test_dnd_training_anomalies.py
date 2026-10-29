"""
The training-anomalies regression fixture (manual-audit training kit v1.1; the
protocol's 2026-10-15 "Training supplement" amendment line).

``golden_dnd_training_anomalies.tab`` holds two hand-made attempts on the demo
item, built so that each carries exactly the quality-control flags behind the
reviewer codes NOOP, GAP, DUP and TIE (protocol §7's flag-to-code map), which
the two §11 packets never exercise:

* ``TrainingStudent3`` — a drop to the zone the card already occupies
  (``noop_move``), a reset, then a drop 320 s later (``long_idle_gap``: over
  the 300 s analytic threshold, under the 1800 s attempt boundary).
* ``TrainingStudent4`` — a drop repeated at the same timestamp with a new
  Problem View (``duplicate_row``, and the ``noop_move`` a repeated drop is),
  then a drop and a CHECK logged at one timestamp and Problem View
  (``row_order_ambiguous``: file order decided the replay). The tie is a drop
  and a CHECK because two drops at one timestamp and Problem View share an
  event identity (student, problem, timestamp, selection, Problem View) and
  the later one is dropped at ingest.

Every CHECK payload decodes to the replayed state, so every anchor is
eligible and agrees, and both attempts read ``limited`` (warning flags, no
excluding condition) — the ``limited`` stratum of the audit, double-reviewed.
Frozen under the DnD golden rule of ``CLAUDE.md``: if this fails, the replay
changed, not the fixture; the training kit's expected answers are typed from
a hand replay under §6 and ``audit_training_kit.py build`` refuses when the
pipeline disagrees with them.
"""

from __future__ import annotations

import json

import pytest

from proteus.core import ingest as core_ingest
from proteus.services.dragndrop.derive import derive_events
from proteus.services.dragndrop.process import analyze_all
from proteus.services.dragndrop.reconstruct import reconstruct_all
from proteus.services.dragndrop.scoring import score_checks

FIXTURE = "golden_dnd_training_anomalies.tab"
T3, T4 = "TrainingStudent3", "TrainingStudent4"

# Per attempt: the flag set, the readiness, and the states at each CHECK and
# after the last row (the §6 rules' answers; the kit types the same).
PINNED = {
    T3: {
        "flags": {"replay_ok", "noop_move", "long_idle_gap"},
        "readiness": "limited",
        "checks": [(1, 2, 0, 0), (0, 0, 3, 0)],
        "final": (0, 0, 3, 0),
        "noop_rows": 1,
    },
    T4: {
        "flags": {"replay_ok", "row_order_ambiguous", "noop_move", "duplicate_row"},
        "readiness": "limited",
        "checks": [(1, 2, 0, 0), (1, 2, 0, 0)],
        "final": (1, 2, 0, 0),
        "noop_rows": 1,
    },
}


@pytest.fixture
def anomalies(db_conn, fixtures_dir, four_shapes_board):
    core_ingest.ingest_file(db_conn, fixtures_dir / FIXTURE)
    derive_events(db_conn)
    four_shapes_board(db_conn)
    reconstruct_all(db_conn)
    score_checks(db_conn)
    analyze_all(db_conn)
    return db_conn


def _attempt(conn, student):
    rows = conn.execute(
        "SELECT * FROM dnd_attempts WHERE student_id = ? ORDER BY attempt_index", (student,)
    ).fetchall()
    assert len(rows) == 1, (student, len(rows))
    return rows[0]


class TestSegmentation:
    @pytest.mark.parametrize("student", [T3, T4])
    def test_one_attempt_each(self, anomalies, student):
        attempt = _attempt(anomalies, student)
        # One sitting: the 320 s gap and the tie split nothing under the
        # primary configuration (session, page view, gap > 1800 s).
        assert attempt["segmentation_rule"].startswith("initial;")
        assert attempt["prior_attempt_id"] is None

    def test_all_rows_ingested(self, anomalies):
        # The repeated drop and the tied CHECK differ from their neighbours
        # in Problem View or selection, so no row collides at ingest: 7 + 7
        # question rows plus the two page views.
        n = anomalies.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        assert n == 16


class TestFlagsAndReadiness:
    @pytest.mark.parametrize("student", [T3, T4])
    def test_flag_set(self, anomalies, student):
        attempt = _attempt(anomalies, student)
        assert set(json.loads(attempt["qc_flags_json"])) == PINNED[student]["flags"]

    @pytest.mark.parametrize("student", [T3, T4])
    def test_readiness_is_limited(self, anomalies, student):
        assert _attempt(anomalies, student)["readiness"] == PINNED[student]["readiness"]

    @pytest.mark.parametrize("student", [T3, T4])
    def test_one_noop_row(self, anomalies, student):
        attempt = _attempt(anomalies, student)
        n = anomalies.execute(
            "SELECT COUNT(*) FROM dnd_configurations WHERE attempt_id = ? AND is_noop = 1",
            (attempt["attempt_id"],),
        ).fetchone()[0]
        assert n == PINNED[student]["noop_rows"]


class TestReplay:
    @pytest.mark.parametrize("student", [T3, T4])
    def test_checks_are_eligible_matching_anchors(self, anomalies, student):
        attempt = _attempt(anomalies, student)
        checks = anomalies.execute(
            "SELECT matches_input, anchor_cause FROM dnd_configurations "
            "WHERE attempt_id = ? AND is_check = 1 ORDER BY seq",
            (attempt["attempt_id"],),
        ).fetchall()
        assert [(c["matches_input"], c["anchor_cause"]) for c in checks] == [(1, None), (1, None)]

    @pytest.mark.parametrize("student", [T3, T4])
    def test_replayed_states(self, anomalies, student):
        attempt = _attempt(anomalies, student)
        checks = [
            tuple(json.loads(r["s_json"]))
            for r in anomalies.execute(
                "SELECT s_json FROM dnd_configurations WHERE attempt_id = ? AND is_check = 1 "
                "ORDER BY seq", (attempt["attempt_id"],)
            )
        ]
        final = tuple(json.loads(anomalies.execute(
            "SELECT s_json FROM dnd_configurations WHERE attempt_id = ? ORDER BY seq DESC LIMIT 1",
            (attempt["attempt_id"],),
        ).fetchone()["s_json"]))
        assert checks == PINNED[student]["checks"]
        assert final == PINNED[student]["final"]
