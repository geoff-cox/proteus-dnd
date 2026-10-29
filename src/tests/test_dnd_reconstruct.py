"""
Tests for DnD reconstruction (plan items B3.1/B3.2).

Two anchors:

* the ``golden_dnd.tab`` fixture (the reference page's ``sec-raw-data``
  trace) drives the end-to-end replay, the idempotent re-run, and the
  graceful handling of its pathological second student;
* small synthesized timelines drive attempt segmentation (session change,
  idle gap, page view, reset policy) and the B3.2 cross-check, where a
  deliberately dropped move makes the replayed state disagree with the
  state the check logged.
"""

from __future__ import annotations

import json

import pytest

from proteus.core import ingest as core_ingest
from proteus.services.dragndrop import board
from proteus.services.dragndrop.derive import derive_events
from proteus.services.dragndrop.reconstruct import (
    SegmentationConfig,
    determine_n_cards,
    reconstruct_all,
)

Q = "demo-book__demo-shapes-q1"


# ---------------------------------------------------------------------------
# Helpers to synthesize an events timeline without writing a .tab by hand.
# ---------------------------------------------------------------------------


def _seed(conn, rows):
    """Insert raw `events` rows, then derive them into dnd_events.

    `rows` is a list of dicts with keys: eid, student, session, problem,
    selection, and optionally action, input, feedback, ts, pv.
    """
    import_id = conn.execute(
        "INSERT INTO raw_imports (file_path, file_hash, file_format, row_count) "
        "VALUES ('synthetic.tab', ?, 'datashop_tab', ?)",
        (f"hash-{conn.total_changes}", len(rows)),
    ).lastrowid
    for i, r in enumerate(rows):
        conn.execute(
            "INSERT INTO events (event_id, import_id, student_id, session_id, "
            "problem_name, selection, action, input, feedback_text, "
            "feedback_classification, problem_view, timestamp, course_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                r["eid"], import_id, r["student"], r["session"], r["problem"],
                r["selection"], r.get("action", ""), r.get("input", ""), "",
                r.get("feedback", ""), r.get("pv", str(i + 1)),
                r["ts"], "course",
            ),
        )
    conn.commit()
    derive_events(conn)


def _move(eid, ts, source, zone, **kw):
    return {
        "eid": eid, "selection": "dragNdrop-drop",
        "action": f"{source} -> {zone}", "ts": ts,
        "student": kw.get("student", "S"), "session": kw.get("session", "100"),
        "problem": kw.get("problem", Q),
    }


def _check(eid, ts, config, feedback="Incorrect", **kw):
    payload = json.dumps(config)
    return {
        "eid": eid, "selection": "dragNdrop", "action": payload, "input": payload,
        "feedback": feedback, "ts": ts,
        "student": kw.get("student", "S"), "session": kw.get("session", "100"),
        "problem": kw.get("problem", Q),
    }


def _reset(eid, ts, **kw):
    return {
        "eid": eid, "selection": "dragNdrop-reset", "action": "reset", "ts": ts,
        "student": kw.get("student", "S"), "session": kw.get("session", "100"),
        "problem": kw.get("problem", Q),
    }


def _configs(conn, attempt_id):
    return conn.execute(
        "SELECT seq, event_id, s_json, is_check, matches_input, anchor_cause, "
        "is_noop FROM dnd_configurations WHERE attempt_id = ? ORDER BY seq",
        (attempt_id,),
    ).fetchall()


def _states(conn, attempt_id):
    return [tuple(json.loads(c["s_json"])) for c in _configs(conn, attempt_id)]


# ---------------------------------------------------------------------------
# End-to-end replay on the golden fixture
# ---------------------------------------------------------------------------


_FOUR_SHAPES = """
<cardsort>
  <match><premise>equilateral triangle</premise><premise>right triangle</premise><response>Triangle</response></match>
  <match><premise>square</premise><response>Quadrilateral</response></match>
  <match><response>Pentagon</response></match>
  <match><premise>circle</premise></match>
</cardsort>
"""


def _sync_board(conn, question_id=Q, xml=_FOUR_SHAPES):
    """Register the four-shape board (P=4, key s*=(1,1,2,0)) for `question_id`."""
    conn.execute(
        "INSERT OR IGNORE INTO textbooks (textbook_id, git_repo_url, git_ref, "
        "book_xml_id, build_dir) VALUES (1, 'r', 'v', 'demo-book', 'b')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO questions (question_id, textbook_id, xml_id, "
        "metadata_json, source) VALUES (?, 1, 'x', '{}', 'auto_extracted')",
        (question_id,),
    )
    board.write_board(conn, board.board_from_source(question_id, xml), "hash")
    conn.commit()


@pytest.fixture
def reconstructed(db_conn, golden_dnd_tab):
    # The realistic pipeline order: sync the board first so P is authoritative
    # and the fixture's off-board move (drag9) is treated as a gap, not placed.
    core_ingest.ingest_file(db_conn, golden_dnd_tab)
    derive_events(db_conn)
    _sync_board(db_conn)
    result = reconstruct_all(db_conn)
    return db_conn, result


class TestGoldenReplay:
    def test_result_counts(self, reconstructed):
        _, result = reconstructed
        assert result.questions == 1
        # DemoStudent1 (full attempt) + DemoStudent2 (pathological).
        assert result.attempts == 2
        assert result.checks == 4
        assert result.mismatches == 0
        # DemoStudent2's truncated check (malformed) and orphan-label check
        # (unknown card) are not eligible anchors: stored with their cause,
        # never compared (dnd/0012).
        assert result.non_eligible == 2
        # No drop in the fixture re-targets a card's current zone.
        assert result.noop_moves == 0
        # DemoStudent2's garbage move and off-board move cannot be replayed.
        assert result.skipped_actions == 2
        # Every fixture row carries a distinct (timestamp, problem_view).
        assert result.ambiguous_traces == 0

    def test_student1_replays_the_raw_data_trace(self, reconstructed):
        conn, _ = reconstructed
        attempt = conn.execute(
            "SELECT attempt_id FROM dnd_attempts WHERE student_id = 'DemoStudent1'"
        ).fetchone()
        # X0 → four placements → check → takeback → check → reset.
        assert _states(conn, attempt["attempt_id"]) == [
            (0, 0, 0, 0),  # X0
            (1, 0, 0, 0),
            (1, 1, 0, 0),
            (1, 1, 2, 0),
            (1, 1, 2, 3),  # the first (Incorrect) check, decoy in z3
            (1, 1, 2, 3),  # check row (state unchanged)
            (1, 1, 2, 0),  # takeback returns the decoy to the tray = the key
            (1, 1, 2, 0),  # the second (Correct) check
            (0, 0, 0, 0),  # reset → X0
        ]

    def test_checks_are_flagged_and_cross_checked(self, reconstructed):
        conn, _ = reconstructed
        attempt = conn.execute(
            "SELECT attempt_id FROM dnd_attempts WHERE student_id = 'DemoStudent1'"
        ).fetchone()
        checks = [c for c in _configs(conn, attempt["attempt_id"]) if c["is_check"]]
        assert len(checks) == 2
        # Both checks match the configuration their Input logged (B3.2).
        assert all(c["matches_input"] == 1 for c in checks)

    def test_attempt_records_segmentation_rule(self, reconstructed):
        conn, _ = reconstructed
        rule = conn.execute(
            "SELECT segmentation_rule FROM dnd_attempts "
            "WHERE student_id = 'DemoStudent1'"
        ).fetchone()["segmentation_rule"]
        assert rule.startswith("initial")
        assert "split_on:" in rule  # the policy is recorded alongside

    def test_pathological_student_degrades_gracefully(self, reconstructed):
        conn, _ = reconstructed
        attempt = conn.execute(
            "SELECT attempt_id FROM dnd_attempts WHERE student_id = 'DemoStudent2'"
        ).fetchone()
        # Two checks at the empty board, neither an eligible anchor: the
        # truncated one is malformed, the orphan-label one names a card off
        # the item map. Both are stored not comparable with their cause and
        # never compared (cc-task-anchor-eligibility, dnd/0012 — before it
        # the orphan check was compared on its best-effort decode, which
        # happened to equal the empty board, and read matches_input = 1).
        configs = _configs(conn, attempt["attempt_id"])
        assert [tuple(json.loads(c["s_json"])) for c in configs] == [
            (0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0),
        ]
        checks = [c for c in configs if c["is_check"]]
        assert [(c["matches_input"], c["anchor_cause"]) for c in checks] == [
            (None, "malformed"), (None, "unknown_card"),
        ]

    def test_rerun_is_idempotent(self, reconstructed):
        conn, _ = reconstructed
        before = conn.execute(
            "SELECT count(*) AS n FROM dnd_configurations"
        ).fetchone()["n"]
        again = reconstruct_all(conn)
        after = conn.execute(
            "SELECT count(*) AS n FROM dnd_configurations"
        ).fetchone()["n"]
        assert again.attempts == 2
        assert before == after  # cleared and rebuilt, not duplicated

    def test_n_cards_prefers_the_board(self, reconstructed):
        conn, _ = reconstructed
        # The board is authoritative: P=4, not the off-board drag9 label.
        assert determine_n_cards(conn, Q) == 4


class TestDetermineNCards:
    def test_inferred_from_drag_labels_without_a_board(self, db_conn):
        _seed(db_conn, [
            _move("a", "2026-02-06 10:00:00.000000", f"{Q}_drag1", f"{Q}_drop1"),
            _move("b", "2026-02-06 10:00:05.000000", f"{Q}_drag3", f"{Q}_drop1"),
        ])
        # Highest drag index seen, best-effort when no board is synced.
        assert determine_n_cards(db_conn, Q) == 3

    def test_none_when_nothing_to_go_on(self, db_conn):
        assert determine_n_cards(db_conn, "no-such-question") is None


# ---------------------------------------------------------------------------
# Attempt segmentation (B3.1)
# ---------------------------------------------------------------------------


def _n_attempts(conn, student="S"):
    return conn.execute(
        "SELECT count(*) AS n FROM dnd_attempts WHERE student_id = ?",
        (student,),
    ).fetchone()["n"]


def _rules(conn, student="S"):
    return [
        row["segmentation_rule"].split(";")[0]
        for row in conn.execute(
            "SELECT segmentation_rule FROM dnd_attempts WHERE student_id = ? "
            "ORDER BY attempt_index",
            (student,),
        )
    ]


class TestSegmentation:
    def test_session_change_starts_a_new_attempt(self, db_conn):
        _seed(db_conn, [
            _move("a", "2026-02-06 10:00:00.000000", f"{Q}_drag1", f"{Q}_drop1", session="1"),
            _move("b", "2026-02-06 10:00:05.000000", f"{Q}_drag2", f"{Q}_drop1", session="2"),
        ])
        reconstruct_all(db_conn)
        assert _n_attempts(db_conn) == 2
        assert _rules(db_conn) == ["initial", "session_change"]

    def test_long_gap_starts_a_new_attempt(self, db_conn):
        _seed(db_conn, [
            _move("a", "2026-02-06 10:00:00.000000", f"{Q}_drag1", f"{Q}_drop1"),
            # Same session, but more than 30 minutes later.
            _move("b", "2026-02-06 11:30:00.000000", f"{Q}_drag2", f"{Q}_drop1"),
        ])
        reconstruct_all(db_conn)
        assert _n_attempts(db_conn) == 2
        assert _rules(db_conn)[1].startswith("gap>")

    def test_page_view_between_events_starts_a_new_attempt(self, db_conn):
        _seed(db_conn, [
            _move("a", "2026-02-06 10:00:00.000000", f"{Q}_drag1", f"{Q}_drop1"),
            {
                "eid": "p", "selection": "page", "action": "viewassignment",
                "problem": "sec-demo-shapes", "student": "S", "session": "100",
                "ts": "2026-02-06 10:00:03.000000",
            },
            _move("b", "2026-02-06 10:00:05.000000", f"{Q}_drag2", f"{Q}_drop1"),
        ])
        reconstruct_all(db_conn)
        assert _n_attempts(db_conn) == 2
        assert _rules(db_conn) == ["initial", "page_view"]

    def test_reset_is_within_attempt_by_default(self, db_conn):
        _seed(db_conn, [
            _move("a", "2026-02-06 10:00:00.000000", f"{Q}_drag1", f"{Q}_drop1"),
            _reset("r", "2026-02-06 10:00:05.000000"),
            _move("b", "2026-02-06 10:00:10.000000", f"{Q}_drag2", f"{Q}_drop1"),
        ])
        reconstruct_all(db_conn)
        # Conservative default: a reset restarts the board, not the attempt.
        assert _n_attempts(db_conn) == 1

    def test_split_on_reset_is_configurable(self, db_conn):
        _seed(db_conn, [
            _move("a", "2026-02-06 10:00:00.000000", f"{Q}_drag1", f"{Q}_drop1"),
            _reset("r", "2026-02-06 10:00:05.000000"),
            _move("b", "2026-02-06 10:00:10.000000", f"{Q}_drag2", f"{Q}_drop1"),
        ])
        reconstruct_all(db_conn, config=SegmentationConfig(split_on_reset=True))
        assert _n_attempts(db_conn) == 2
        assert _rules(db_conn) == ["initial", "reset"]


# ---------------------------------------------------------------------------
# B3.2 cross-check: a dropped move makes the replay disagree with the Input
# ---------------------------------------------------------------------------


class TestCrossCheck:
    def test_dropped_move_is_flagged_as_a_mismatch(self, db_conn):
        # Only card 1 is actually moved, but the check's Input claims both
        # cards 1 and 2 are in drop1 — the signature of a missing move row.
        _seed(db_conn, [
            _move("m1", "2026-02-06 10:00:00.000000", f"{Q}_drag1", f"{Q}_drop1"),
            _check(
                "c1", "2026-02-06 10:00:05.000000",
                {f"{Q}_drop1": [f"{Q}_drag1", f"{Q}_drag2"]},
            ),
        ])
        result = reconstruct_all(db_conn)
        assert result.mismatches == 1
        check = db_conn.execute(
            "SELECT s_json, matches_input FROM dnd_configurations "
            "WHERE is_check = 1"
        ).fetchone()
        assert check["matches_input"] == 0
        # The stored state is the replayed one (card 2 still in the tray).
        assert tuple(json.loads(check["s_json"])) == (1, 0)

    def test_matching_check_is_not_a_mismatch(self, db_conn):
        _seed(db_conn, [
            _move("m1", "2026-02-06 10:00:00.000000", f"{Q}_drag1", f"{Q}_drop1"),
            _move("m2", "2026-02-06 10:00:02.000000", f"{Q}_drag2", f"{Q}_drop1"),
            _check(
                "c1", "2026-02-06 10:00:05.000000",
                {f"{Q}_drop1": [f"{Q}_drag1", f"{Q}_drag2"]},
            ),
        ])
        result = reconstruct_all(db_conn)
        assert result.mismatches == 0
        check = db_conn.execute(
            "SELECT matches_input FROM dnd_configurations WHERE is_check = 1"
        ).fetchone()
        assert check["matches_input"] == 1


class TestNoopMove:
    """A drop whose destination equals the card's current zone (b = a) is a
    no-op: the journal's replay rules (subsec-reconstruction-rules) say it must
    be flagged ``noop_move`` — not dropped, not replayed as a move."""

    def test_repeat_drop_to_the_same_zone_is_flagged(self, db_conn):
        _seed(db_conn, [
            _move("m1", "2026-02-06 10:00:00.000000", f"{Q}_drag1", f"{Q}_drop1"),
            # The same card released over the zone it already occupies.
            _move("m2", "2026-02-06 10:00:05.000000", f"{Q}_drag1", f"{Q}_drop1"),
            _move("m3", "2026-02-06 10:00:10.000000", f"{Q}_drag2", f"{Q}_drop2"),
        ])
        result = reconstruct_all(db_conn)
        assert result.noop_moves == 1
        assert result.skipped_actions == 0  # flagged, not skipped

        attempt = db_conn.execute("SELECT attempt_id FROM dnd_attempts").fetchone()
        configs = _configs(db_conn, attempt["attempt_id"])
        # The flagged row is still written (the event stays traceable) and
        # carries the unchanged state; the replay continues past it.
        assert [(c["is_noop"], tuple(json.loads(c["s_json"]))) for c in configs] == [
            (0, (0, 0)),  # X0
            (0, (1, 0)),
            (1, (1, 0)),  # the no-op drop: state unchanged, flagged
            (0, (1, 2)),
        ]

    def test_takeback_of_a_tray_card_is_flagged(self, db_conn):
        # A card dragged out of the tray and released back over it: the
        # destination (zone 0) equals the card's current zone, so b = a = 0.
        _seed(db_conn, [
            _move("t", "2026-02-06 10:00:00.000000", f"{Q}_drag1", "dragzone"),
        ])
        result = reconstruct_all(db_conn)
        assert result.noop_moves == 1
        noop = db_conn.execute(
            "SELECT is_noop, s_json FROM dnd_configurations WHERE seq = 1"
        ).fetchone()
        assert noop["is_noop"] == 1
        assert json.loads(noop["s_json"]) == [0]


class TestRowOrdering:
    """F2: replay order is the journal's recommended sort key — timestamp,
    then numeric-cast ``problem_view``, then file order (rowid) as the
    last-resort fallback. Traces where consecutive rows still tie on
    (timestamp, problem_view) are counted ``ambiguous_traces`` (the journal's
    ``row_order_ambiguous``; flag persistence rides F17b)."""

    TS = "2026-02-06 10:00:00.000000"

    def test_problem_view_breaks_timestamp_ties(self, db_conn):
        # File order says drop2 then drop1; problem_view says the opposite.
        # Under (timestamp, rowid) the final zone would be 1 — the replay must
        # follow problem_view and end at zone 2.
        _seed(db_conn, [
            {**_move("m1", self.TS, f"{Q}_drag1", f"{Q}_drop2"), "pv": "2"},
            {**_move("m2", self.TS, f"{Q}_drag1", f"{Q}_drop1"), "pv": "1"},
        ])
        result = reconstruct_all(db_conn)
        assert result.ambiguous_traces == 0
        attempt = db_conn.execute("SELECT attempt_id FROM dnd_attempts").fetchone()
        assert _states(db_conn, attempt["attempt_id"]) == [(0,), (1,), (2,)]

    def test_problem_view_compares_numerically_not_lexically(self, db_conn):
        # problem_view is a TEXT column and '10' < '9' lexically; the numeric
        # cast must order 9 before 10 (drop1 first), ending at zone 2.
        _seed(db_conn, [
            {**_move("m1", self.TS, f"{Q}_drag1", f"{Q}_drop2"), "pv": "10"},
            {**_move("m2", self.TS, f"{Q}_drag1", f"{Q}_drop1"), "pv": "9"},
        ])
        result = reconstruct_all(db_conn)
        assert result.ambiguous_traces == 0
        attempt = db_conn.execute("SELECT attempt_id FROM dnd_attempts").fetchone()
        assert _states(db_conn, attempt["attempt_id"]) == [(0,), (1,), (2,)]

    def test_surviving_tie_counts_the_trace_ambiguous(self, db_conn):
        # Same timestamp AND same problem_view: the rowid fallback decides,
        # the replay still completes, and the trace is counted (and logged).
        _seed(db_conn, [
            {**_move("m1", self.TS, f"{Q}_drag1", f"{Q}_drop1"), "pv": "1"},
            {**_move("m2", self.TS, f"{Q}_drag1", f"{Q}_drop2"), "pv": "1"},
        ])
        result = reconstruct_all(db_conn)
        assert result.ambiguous_traces == 1
        attempt = db_conn.execute("SELECT attempt_id FROM dnd_attempts").fetchone()
        assert _states(db_conn, attempt["attempt_id"]) == [(0,), (1,), (2,)]

    def test_ambiguity_counts_traces_not_ties(self, db_conn):
        # Three tied rows in one student's trace still count once — the unit
        # is the trace (student × question timeline), not the tie pair.
        _seed(db_conn, [
            {**_move("m1", self.TS, f"{Q}_drag1", f"{Q}_drop1"), "pv": "1"},
            {**_move("m2", self.TS, f"{Q}_drag1", f"{Q}_drop2"), "pv": "1"},
            {**_move("m3", self.TS, f"{Q}_drag1", f"{Q}_drop3"), "pv": "1"},
        ])
        result = reconstruct_all(db_conn)
        assert result.ambiguous_traces == 1


def _segments(conn, student="S"):
    """{segment_type: [(start_seq, end_seq), …]} of the student's attempt."""
    rows = conn.execute(
        "SELECT s.segment_type, s.start_seq, s.end_seq FROM dnd_segments s "
        "JOIN dnd_attempts a ON a.attempt_id = s.attempt_id "
        "WHERE a.student_id = ? ORDER BY s.segment_type, s.segment_index",
        (student,),
    ).fetchall()
    spans: dict[str, list[tuple[int, int]]] = {}
    for r in rows:
        spans.setdefault(r["segment_type"], []).append(
            (r["start_seq"], r["end_seq"])
        )
    return spans


class TestSegments:
    """F3/F9: the journal's check/reset/session segments materialized in
    ``dnd_segments`` as inclusive [start_seq, end_seq] spans, written during
    reconstruction, plus the clock-based active_work type F9 delivered."""

    def test_golden_trace_segments(self, reconstructed):
        conn, _ = reconstructed
        # DemoStudent1's replay: seq 0 = X0, 1–4 moves, 5 check, 6 takeback,
        # 7 check, 8 reset (test_student1_replays_the_raw_data_trace).
        spans = _segments(conn, "DemoStudent1")
        # Checks close the spans they end; the reset row is the tail span.
        assert spans["check"] == [(0, 5), (6, 7), (8, 8)]
        # The one reset ends the single reset span — no tail after it.
        assert spans["reset"] == [(0, 8)]
        # One sitting: a single session span.
        assert spans["session"] == [(0, 8)]
        # No gap in the golden trace reaches the analytic idle threshold
        # (F9), so the attempt is one active-work run.
        assert spans["active_work"] == [(0, 8)]

    def test_no_checks_is_one_open_segment(self, db_conn):
        _seed(db_conn, [
            _move("m1", "2026-02-06 10:00:00.000000", f"{Q}_drag1", f"{Q}_drop1"),
            _move("m2", "2026-02-06 10:00:05.000000", f"{Q}_drag2", f"{Q}_drop2"),
        ])
        _sync_board(db_conn)
        reconstruct_all(db_conn)
        spans = _segments(db_conn)
        assert spans["check"] == [(0, 2)]
        assert spans["reset"] == [(0, 2)]

    def test_midway_reset_splits_reset_segments(self, db_conn):
        _seed(db_conn, [
            _move("m1", "2026-02-06 10:00:00.000000", f"{Q}_drag1", f"{Q}_drop1"),
            _reset("r1", "2026-02-06 10:00:05.000000"),
            _move("m2", "2026-02-06 10:00:10.000000", f"{Q}_drag2", f"{Q}_drop2"),
        ])
        _sync_board(db_conn)
        reconstruct_all(db_conn)  # a reset stays within the attempt by default
        spans = _segments(db_conn)
        # seq: 0 = X0, 1 = move, 2 = reset, 3 = move.
        assert spans["reset"] == [(0, 2), (3, 3)]
        assert spans["check"] == [(0, 3)]  # no check: one open span

    def test_session_change_splits_session_segments(self, db_conn):
        _seed(db_conn, [
            _move("m1", "2026-02-06 10:00:00.000000", f"{Q}_drag1", f"{Q}_drop1", session="1"),
            _move("m2", "2026-02-06 10:00:05.000000", f"{Q}_drag2", f"{Q}_drop1", session="2"),
        ])
        _sync_board(db_conn)
        reconstruct_all(
            db_conn, config=SegmentationConfig(split_on_session_change=False)
        )
        spans = _segments(db_conn)
        # seq 0 (X0) joins the first event's session; the boundary sits
        # between the two moves.
        assert spans["session"] == [(0, 1), (2, 2)]

    def test_long_idle_gap_splits_active_work_and_flags_the_attempt(
        self, db_conn
    ):
        # A 10-minute pause: far over the analytic idle threshold (300 s),
        # far under the segmentation gap (1800 s) — the attempt survives as
        # one unit, but the clock evidence is recorded (F9).
        _seed(db_conn, [
            _move("m1", "2026-02-06 10:00:00.000000", f"{Q}_drag1", f"{Q}_drop1"),
            _move("m2", "2026-02-06 10:10:00.000000", f"{Q}_drag2", f"{Q}_drop1"),
            _check("c1", "2026-02-06 10:10:05.000000",
                   {f"{Q}_drop1": [f"{Q}_drag1", f"{Q}_drag2"]}),
        ])
        _sync_board(db_conn)
        reconstruct_all(db_conn)
        assert db_conn.execute(
            "SELECT count(*) AS n FROM dnd_attempts"
        ).fetchone()["n"] == 1
        # seq: 0 = X0, 1 = m1, 2 = m2, 3 = check. The idle boundary sits
        # between the two moves; X0 (no timestamp) joins the first run.
        spans = _segments(db_conn)
        assert spans["active_work"] == [(0, 1), (2, 3)]
        assert spans["session"] == [(0, 3)]  # not a session boundary
        # The QC rollup carries the journal's long_idle_gap warning, so
        # readiness demotes to limited (time features are suspect, the
        # verified replay is not).
        row = db_conn.execute(
            "SELECT qc_flags_json, readiness FROM dnd_attempts"
        ).fetchone()
        assert "long_idle_gap" in set(json.loads(row["qc_flags_json"]))
        assert row["readiness"] == "limited"

    def test_custom_idle_threshold_is_respected(self, db_conn):
        # The same trace under a huge δ_max: no flag, one active-work run.
        _seed(db_conn, [
            _move("m1", "2026-02-06 10:00:00.000000", f"{Q}_drag1", f"{Q}_drop1"),
            _move("m2", "2026-02-06 10:10:00.000000", f"{Q}_drag2", f"{Q}_drop1"),
            _check("c1", "2026-02-06 10:10:05.000000",
                   {f"{Q}_drop1": [f"{Q}_drag1", f"{Q}_drag2"]}),
        ])
        _sync_board(db_conn)
        reconstruct_all(db_conn, idle_threshold_s=1200.0)
        spans = _segments(db_conn)
        assert spans["active_work"] == [(0, 3)]
        row = db_conn.execute(
            "SELECT qc_flags_json, readiness FROM dnd_attempts"
        ).fetchone()
        assert "long_idle_gap" not in set(json.loads(row["qc_flags_json"]))
        assert row["readiness"] == "ready"

    def test_rerun_does_not_duplicate_segments(self, reconstructed):
        conn, _ = reconstructed
        before = conn.execute("SELECT count(*) AS n FROM dnd_segments").fetchone()["n"]
        reconstruct_all(conn)
        after = conn.execute("SELECT count(*) AS n FROM dnd_segments").fetchone()["n"]
        assert before == after  # cleared and rebuilt, not appended

    def test_spans_tile_the_configurations(self, reconstructed):
        # Every segment type partitions the attempt's seq range exactly:
        # contiguous, non-overlapping, covering 0..max(seq).
        conn, _ = reconstructed
        for attempt in conn.execute("SELECT attempt_id FROM dnd_attempts"):
            last = conn.execute(
                "SELECT max(seq) AS m FROM dnd_configurations WHERE attempt_id = ?",
                (attempt["attempt_id"],),
            ).fetchone()["m"]
            for seg_type in ("check", "reset", "session", "active_work"):
                spans = conn.execute(
                    "SELECT start_seq, end_seq FROM dnd_segments "
                    "WHERE attempt_id = ? AND segment_type = ? "
                    "ORDER BY segment_index",
                    (attempt["attempt_id"], seg_type),
                ).fetchall()
                expected_start = 0
                for span in spans:
                    assert span["start_seq"] == expected_start, seg_type
                    assert span["end_seq"] >= span["start_seq"], seg_type
                    expected_start = span["end_seq"] + 1
                assert expected_start == last + 1, seg_type


def _attempt_qc(conn, student="S"):
    """(flags, readiness) of the student's single attempt."""
    row = conn.execute(
        "SELECT qc_flags_json, readiness FROM dnd_attempts WHERE student_id = ?",
        (student,),
    ).fetchone()
    return set(json.loads(row["qc_flags_json"])), row["readiness"]


class TestQcRollup:
    """F17b: reconstruct rolls each attempt's per-row signals up into the
    journal's QC flags and the three-level feature readiness, persisted on
    ``dnd_attempts`` (migration dnd/0003). The vocabulary and the readiness
    policy live in ``qc.py``; these tests drive real timelines through the
    replay and read the persisted rollup."""

    TS = "2026-02-06 10:00:{s:02d}.000000"

    def _ts(self, s):
        return self.TS.format(s=s)

    def test_clean_verified_attempt_is_ready(self, db_conn):
        _seed(db_conn, [
            _move("m1", self._ts(0), f"{Q}_drag1", f"{Q}_drop1"),
            _check("c1", self._ts(5), {f"{Q}_drop1": [f"{Q}_drag1"]}),
        ])
        _sync_board(db_conn)
        reconstruct_all(db_conn)
        flags, readiness = _attempt_qc(db_conn)
        assert flags == {"replay_ok"}
        assert readiness == "ready"

    def test_noop_stays_ready(self, db_conn):
        _seed(db_conn, [
            _move("m1", self._ts(0), f"{Q}_drag1", f"{Q}_drop1"),
            _move("m2", self._ts(3), f"{Q}_drag1", f"{Q}_drop1"),  # no-op
            _check("c1", self._ts(5), {f"{Q}_drop1": [f"{Q}_drag1"]}),
        ])
        _sync_board(db_conn)
        reconstruct_all(db_conn)
        flags, readiness = _attempt_qc(db_conn)
        assert flags == {"replay_ok", "noop_move"}
        assert readiness == "ready"

    def test_missing_board_excludes(self, db_conn):
        _seed(db_conn, [
            _move("m1", self._ts(0), f"{Q}_drag1", f"{Q}_drop1"),
            _check("c1", self._ts(5), {f"{Q}_drop1": [f"{Q}_drag1"]}),
        ])
        reconstruct_all(db_conn)  # no board synced
        flags, readiness = _attempt_qc(db_conn)
        assert "missing_source_map" in flags
        assert readiness == "exclude"

    def test_duplicate_cards_check_is_invalid_state(self, db_conn):
        # The audit's silently-resolved case: a check JSON putting one card
        # in two zones. The attempt flags and excludes; since dnd/0012 the
        # check is also not an eligible anchor (never compared — see
        # TestAnchorEligibility), so replay_ok cannot ride along.
        _seed(db_conn, [
            _move("m1", self._ts(0), f"{Q}_drag1", f"{Q}_drop1"),
            _check("c1", self._ts(5), {
                f"{Q}_drop1": [f"{Q}_drag1"],
                f"{Q}_drop2": [f"{Q}_drag1"],
            }),
        ])
        _sync_board(db_conn)
        reconstruct_all(db_conn)
        flags, readiness = _attempt_qc(db_conn)
        assert "invalid_state" in flags
        assert "replay_ok" not in flags
        assert readiness == "exclude"

    def test_dropped_move_flags_check_mismatch(self, db_conn):
        _seed(db_conn, [
            _move("m1", self._ts(0), f"{Q}_drag1", f"{Q}_drop1"),
            _check("c1", self._ts(5),
                   {f"{Q}_drop1": [f"{Q}_drag1", f"{Q}_drag2"]}),
        ])
        _sync_board(db_conn)
        reconstruct_all(db_conn)
        flags, readiness = _attempt_qc(db_conn)
        assert "check_mismatch" in flags
        assert "replay_ok" not in flags
        assert readiness == "limited"

    def test_order_tie_flags_the_attempt(self, db_conn):
        ts = self._ts(0)
        _seed(db_conn, [
            {**_move("m1", ts, f"{Q}_drag1", f"{Q}_drop1"), "pv": "1"},
            {**_move("m2", ts, f"{Q}_drag1", f"{Q}_drop2"), "pv": "1"},
        ])
        _sync_board(db_conn)
        reconstruct_all(db_conn)
        flags, readiness = _attempt_qc(db_conn)
        assert "row_order_ambiguous" in flags
        assert readiness == "limited"

    def test_relogged_row_flags_duplicate_row(self, db_conn):
        # Identical (timestamp, kind, payload) but a different problem_view:
        # ingest's content-hash dedup does not catch it, the rollup must.
        ts = self._ts(0)
        _seed(db_conn, [
            {**_move("m1", ts, f"{Q}_drag1", f"{Q}_drop1"), "pv": "1"},
            {**_move("m2", ts, f"{Q}_drag1", f"{Q}_drop1"), "pv": "2"},
        ])
        _sync_board(db_conn)
        reconstruct_all(db_conn)
        flags, _ = _attempt_qc(db_conn)
        assert "duplicate_row" in flags

    def test_same_timestamp_checks_with_different_feedback_are_not_duplicates(
        self, db_conn
    ):
        # The whole payload must repeat: two checks sharing a timestamp and
        # configuration but graded differently are distinct rows.
        ts = self._ts(0)
        config = {f"{Q}_drop1": [f"{Q}_drag1"]}
        _seed(db_conn, [
            {**_check("c1", ts, config, feedback="Incorrect"), "pv": "1"},
            {**_check("c2", ts, config, feedback="Correct"), "pv": "2"},
        ])
        _sync_board(db_conn)
        reconstruct_all(db_conn)
        flags, _ = _attempt_qc(db_conn)
        assert "duplicate_row" not in flags

    def test_session_boundary_inside_attempt_flags_session_break(self, db_conn):
        # Reachable only when session splitting is off — the default config
        # turns this boundary into an attempt boundary instead.
        _seed(db_conn, [
            _move("m1", self._ts(0), f"{Q}_drag1", f"{Q}_drop1", session="1"),
            _move("m2", self._ts(5), f"{Q}_drag2", f"{Q}_drop1", session="2"),
        ])
        _sync_board(db_conn)
        reconstruct_all(
            db_conn, config=SegmentationConfig(split_on_session_change=False)
        )
        assert _n_attempts(db_conn) == 1
        flags, readiness = _attempt_qc(db_conn)
        assert "session_break" in flags
        assert readiness == "limited"

    def test_golden_fixture_rollup(self, reconstructed):
        conn, _ = reconstructed
        s1 = conn.execute(
            "SELECT qc_flags_json, readiness FROM dnd_attempts "
            "WHERE student_id = 'DemoStudent1'"
        ).fetchone()
        assert json.loads(s1["qc_flags_json"]) == ["replay_ok"]
        assert s1["readiness"] == "ready"
        # DemoStudent2: an unparseable move (parse_error), an orphan-label
        # check and an off-board move (both invalid_state).
        s2 = conn.execute(
            "SELECT qc_flags_json, readiness FROM dnd_attempts "
            "WHERE student_id = 'DemoStudent2'"
        ).fetchone()
        s2_flags = set(json.loads(s2["qc_flags_json"]))
        assert {"parse_error", "invalid_state"} <= s2_flags
        assert "replay_ok" not in s2_flags
        assert s2["readiness"] == "exclude"


def _check_disclosure(conn, student="S"):
    """[(disclosure_layer, graded_ordinal)] for the student's checks, in seq."""
    return [
        (r["disclosure_layer"], r["graded_ordinal"])
        for r in conn.execute(
            "SELECT c.disclosure_layer, c.graded_ordinal FROM dnd_configurations c "
            "JOIN dnd_attempts a ON a.attempt_id = c.attempt_id "
            "WHERE a.student_id = ? AND c.is_check = 1 ORDER BY c.seq",
            (student,),
        )
    ]


class TestFeedbackPolicy:
    """FP-3: reconstruct classifies each check's disclosure layer under the
    attempt's feedback_policy era (default legacy), persisting
    ``dnd_configurations.disclosure_layer`` / ``.graded_ordinal`` and
    ``dnd_attempts.feedback_policy``. The gate and vocabulary live in
    ``policy.py``; these drive real timelines through the replay.

    The four-shape board has ℓ₀ = 3 non-decoy cards (card 4 is the decoy)."""

    def _layered_timeline(self, db_conn):
        # 2 placed (refused), then 3 placed and checked three times unmoved.
        cfg2 = {f"{Q}_drop1": [f"{Q}_drag1", f"{Q}_drag2"]}
        cfg3 = {f"{Q}_drop1": [f"{Q}_drag1", f"{Q}_drag2", f"{Q}_drag3"]}
        _seed(db_conn, [
            _move("m1", "2026-02-06 10:00:00.000000", f"{Q}_drag1", f"{Q}_drop1"),
            _move("m2", "2026-02-06 10:00:01.000000", f"{Q}_drag2", f"{Q}_drop1"),
            _check("c1", "2026-02-06 10:00:02.000000", cfg2),
            _move("m3", "2026-02-06 10:00:03.000000", f"{Q}_drag3", f"{Q}_drop1"),
            _check("c2", "2026-02-06 10:00:04.000000", cfg3),
            _check("c3", "2026-02-06 10:00:05.000000", cfg3),
            _check("c4", "2026-02-06 10:00:06.000000", cfg3),
        ])
        _sync_board(db_conn)

    def test_layered_gate_refusal_and_highlight_threshold(self, db_conn):
        self._layered_timeline(db_conn)
        reconstruct_all(db_conn, feedback_policy="layered")
        # c1 refused (placed 2 < 3); c2/c3/c4 graded — count, count, then
        # highlight on the third graded check (the "press twice unmoved to
        # unlock φ3" path). Refused presses don't advance the graded ordinal.
        assert _check_disclosure(db_conn) == [
            ("refused", None),
            ("count", 1),
            ("count", 2),
            ("highlight", 3),
        ]
        policy_val = db_conn.execute(
            "SELECT DISTINCT feedback_policy FROM dnd_attempts"
        ).fetchone()[0]
        assert policy_val == "layered"

    def test_legacy_grades_every_check_at_highlight(self, db_conn):
        # Same timeline, default (legacy) era: every check graded, always
        # highlight, ordinals run 1..4 (the placed=2 check is graded too).
        self._layered_timeline(db_conn)
        reconstruct_all(db_conn)
        assert _check_disclosure(db_conn) == [
            ("highlight", 1),
            ("highlight", 2),
            ("highlight", 3),
            ("highlight", 4),
        ]
        assert db_conn.execute(
            "SELECT DISTINCT feedback_policy FROM dnd_attempts"
        ).fetchone()[0] == "legacy"

    def test_golden_fixture_is_legacy_highlight(self, reconstructed):
        conn, _ = reconstructed
        # DemoStudent1's two checks, default legacy era.
        assert _check_disclosure(conn, "DemoStudent1") == [
            ("highlight", 1),
            ("highlight", 2),
        ]

    def test_layered_without_board_cannot_classify(self, db_conn):
        # No board ⇒ ℓ₀ unknown ⇒ layered checks are left unclassified (NULL),
        # rather than guessed.
        _seed(db_conn, [
            _move("m1", "2026-02-06 10:00:00.000000", f"{Q}_drag1", f"{Q}_drop1"),
            _check("c1", "2026-02-06 10:00:02.000000",
                   {f"{Q}_drop1": [f"{Q}_drag1"]}),
        ])
        reconstruct_all(db_conn, feedback_policy="layered")  # no board synced
        assert _check_disclosure(db_conn) == [(None, None)]

    def test_unknown_policy_raises(self, db_conn):
        with pytest.raises(ValueError, match="feedback_policy must be one of"):
            reconstruct_all(db_conn, feedback_policy="telepathic")


class TestOffBoardSlot:
    def test_drop_beyond_n_zones_is_skipped_when_board_synced(self, db_conn):
        # drag1 is a valid card, but drop7 is off the R=3 board (a B2.4 gap on
        # the slot side). With a board synced it must be skipped, not replayed.
        _seed(db_conn, [
            _move("m", "2026-02-06 10:00:00.000000", f"{Q}_drag1", f"{Q}_drop7"),
        ])
        _sync_board(db_conn)  # R = 3
        result = reconstruct_all(db_conn)
        assert result.skipped_actions == 1
        states = [
            json.loads(r["s_json"])
            for r in db_conn.execute("SELECT s_json FROM dnd_configurations")
        ]
        # The off-board slot never entered the state vector.
        assert all(7 not in s for s in states)

    def test_drop_taken_at_face_value_without_a_board(self, db_conn):
        # No board ⇒ no R bound, so the slot is replayed at face value (the
        # same best-effort posture as the inferred card count).
        _seed(db_conn, [
            _move("m", "2026-02-06 10:00:00.000000", f"{Q}_drag1", f"{Q}_drop7"),
        ])
        result = reconstruct_all(db_conn)
        assert result.skipped_actions == 0
        last = db_conn.execute(
            "SELECT s_json FROM dnd_configurations ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        assert json.loads(last["s_json"]) == [7]  # P inferred as 1 from drag1


# ---------------------------------------------------------------------------
# Anchor eligibility (cc-task-anchor-eligibility; paper1-decisions.md
# 2026-09-28 "Anchor eligibility — FROZEN"; migration dnd/0012)
# ---------------------------------------------------------------------------


def _check_row(conn):
    """The single check's stored comparison: (s_json, matches_input, cause)."""
    row = conn.execute(
        "SELECT s_json, matches_input, anchor_cause FROM dnd_configurations "
        "WHERE is_check = 1"
    ).fetchone()
    return json.loads(row["s_json"]), row["matches_input"], row["anchor_cause"]


def _raw_check(eid, ts, raw_input, **kw):
    """A check row whose Input is given verbatim (for malformed payloads)."""
    return {
        "eid": eid, "selection": "dragNdrop", "action": raw_input,
        "input": raw_input, "feedback": kw.get("feedback", "Incorrect"),
        "ts": ts, "student": kw.get("student", "S"),
        "session": kw.get("session", "100"), "problem": kw.get("problem", Q),
    }


class TestAnchorEligibility:
    """One fixture per case of the brief's table. An anchor is eligible when
    its payload decodes to a unique, board-valid configuration under the
    synced board (P = 4, R = 3); every other check is stored not comparable
    (``matches_input`` NULL — out of the agreement denominator) with its
    cause, never compared, and never counted as a mismatch. ``parse_error``
    / ``invalid_state`` flag as before; ``replay_ok`` keeps its meaning (at
    least one check, every check eligible and agreeing)."""

    TS = "2026-02-06 10:00:{s:02d}.000000"

    def _ts(self, s):
        return self.TS.format(s=s)

    def test_well_formed_board_valid_payload_is_compared(self, db_conn):
        _seed(db_conn, [
            _move("m1", self._ts(0), f"{Q}_drag1", f"{Q}_drop1"),
            _check("c1", self._ts(5), {f"{Q}_drop1": [f"{Q}_drag1"]}),
        ])
        _sync_board(db_conn)
        result = reconstruct_all(db_conn)
        assert _check_row(db_conn) == ([1, 0, 0, 0], 1, None)
        assert (result.mismatches, result.non_eligible) == (0, 0)
        assert _attempt_qc(db_conn) == ({"replay_ok"}, "ready")

    def test_empty_payload_is_malformed(self, db_conn):
        # parse_ok = 0 at derive: never decoded, never compared; the replayed
        # state is still written as the check row.
        _seed(db_conn, [
            _move("m1", self._ts(0), f"{Q}_drag1", f"{Q}_drop1"),
            _raw_check("c1", self._ts(5), ""),
        ])
        _sync_board(db_conn)
        result = reconstruct_all(db_conn)
        assert _check_row(db_conn) == ([1, 0, 0, 0], None, "malformed")
        assert (result.mismatches, result.non_eligible) == (0, 1)
        assert _attempt_qc(db_conn) == ({"parse_error"}, "limited")

    def test_payload_that_is_not_a_json_object_is_malformed(self, db_conn):
        _seed(db_conn, [
            _move("m1", self._ts(0), f"{Q}_drag1", f"{Q}_drop1"),
            _raw_check("c1", self._ts(5), '["' + Q + '_drag1"]'),
        ])
        _sync_board(db_conn)
        result = reconstruct_all(db_conn)
        assert _check_row(db_conn) == ([1, 0, 0, 0], None, "malformed")
        assert result.non_eligible == 1
        assert _attempt_qc(db_conn) == ({"parse_error"}, "limited")

    def test_card_listed_under_two_zones_is_duplicate_card(self, db_conn):
        # Before dnd/0012 the first-zone decode (1, 0, 0, 0) equalled the
        # replay and the attempt read replay_ok beside invalid_state.
        _seed(db_conn, [
            _move("m1", self._ts(0), f"{Q}_drag1", f"{Q}_drop1"),
            _check("c1", self._ts(5), {
                f"{Q}_drop1": [f"{Q}_drag1"],
                f"{Q}_drop2": [f"{Q}_drag1"],
            }),
        ])
        _sync_board(db_conn)
        result = reconstruct_all(db_conn)
        assert _check_row(db_conn) == ([1, 0, 0, 0], None, "duplicate_card")
        assert (result.mismatches, result.non_eligible) == (0, 1)
        assert _attempt_qc(db_conn) == ({"invalid_state"}, "exclude")

    def test_card_label_outside_1_to_p_is_unknown_card(self, db_conn):
        # An in-grammar index beyond P and an orphan label from another
        # item are the same cause: the card is not on this board.
        _seed(db_conn, [
            _move("m1", self._ts(0), f"{Q}_drag1", f"{Q}_drop1"),
            _check("c1", self._ts(5), {
                f"{Q}_drop1": [f"{Q}_drag1", f"{Q}_drag9", "other-question_drag1"],
            }),
        ])
        _sync_board(db_conn)
        result = reconstruct_all(db_conn)
        assert _check_row(db_conn) == ([1, 0, 0, 0], None, "unknown_card")
        assert (result.mismatches, result.non_eligible) == (0, 1)
        assert _attempt_qc(db_conn) == ({"invalid_state"}, "exclude")

    def test_zone_label_outside_the_grammar_is_unknown_zone(self, db_conn):
        # An undecodable zone label makes the check non-eligible even when
        # nothing is listed under it (the payload names a zone this board
        # does not have); the cards it holds would otherwise read as in
        # the tray.
        _seed(db_conn, [
            _move("m1", self._ts(0), f"{Q}_drag1", f"{Q}_drop1"),
            _check("c1", self._ts(5), {
                f"{Q}_drop1": [f"{Q}_drag1"],
                "mystery-zone": [],
            }),
        ])
        _sync_board(db_conn)
        result = reconstruct_all(db_conn)
        assert _check_row(db_conn) == ([1, 0, 0, 0], None, "unknown_zone")
        assert (result.mismatches, result.non_eligible) == (0, 1)
        assert _attempt_qc(db_conn) == ({"invalid_state"}, "exclude")

    def test_slot_index_beyond_r_is_offboard_slot(self, db_conn):
        # The move to drop7 is skipped (R = 3) and the payload carries the
        # same off-board slot. Before dnd/0012 the payload was decoded at
        # face value and surfaced as check_mismatch; now the check is not an
        # eligible anchor, flags invalid_state, and is never compared.
        _seed(db_conn, [
            _move("m1", self._ts(0), f"{Q}_drag1", f"{Q}_drop1"),
            _move("m2", self._ts(2), f"{Q}_drag2", f"{Q}_drop7"),
            _check("c1", self._ts(5), {
                f"{Q}_drop1": [f"{Q}_drag1"],
                f"{Q}_drop7": [f"{Q}_drag2"],
            }),
        ])
        _sync_board(db_conn)
        result = reconstruct_all(db_conn)
        assert _check_row(db_conn) == ([1, 0, 0, 0], None, "offboard_slot")
        assert (result.mismatches, result.non_eligible) == (0, 1)
        assert result.skipped_actions == 1
        flags, readiness = _attempt_qc(db_conn)
        assert flags == {"invalid_state"}
        assert readiness == "exclude"

    def test_empty_offboard_slot_still_disqualifies(self, db_conn):
        # "Every slot index lies in 1..R" is read on the payload's zone keys,
        # occupied or not — the same rule as an unknown zone label.
        _seed(db_conn, [
            _move("m1", self._ts(0), f"{Q}_drag1", f"{Q}_drop1"),
            _check("c1", self._ts(5), {
                f"{Q}_drop1": [f"{Q}_drag1"],
                f"{Q}_drop7": [],
            }),
        ])
        _sync_board(db_conn)
        reconstruct_all(db_conn)
        assert _check_row(db_conn) == ([1, 0, 0, 0], None, "offboard_slot")
        assert _attempt_qc(db_conn) == ({"invalid_state"}, "exclude")

    def test_offboard_slot_without_a_board_is_compared_at_face_value(
        self, db_conn
    ):
        # No board ⇒ no R bound anywhere: the slot is replayed and decoded at
        # face value and the anchor is eligible (missing_source_map excludes
        # the attempt regardless).
        _seed(db_conn, [
            _move("m1", self._ts(0), f"{Q}_drag1", f"{Q}_drop7"),
            _check("c1", self._ts(5), {f"{Q}_drop7": [f"{Q}_drag1"]}),
        ])
        result = reconstruct_all(db_conn)
        assert _check_row(db_conn) == ([7], 1, None)
        assert result.non_eligible == 0
        assert _attempt_qc(db_conn) == (
            {"replay_ok", "missing_source_map"}, "exclude",
        )

    def test_check_in_an_attempt_flagged_by_a_move_is_still_compared(
        self, db_conn
    ):
        # The attempt is invalid_state because of the off-board *move*; the
        # check's own payload is clean, so it is an eligible anchor, enters
        # the denominator, and agrees — the flag set is {replay_ok,
        # invalid_state} and readiness still reads exclude.
        _seed(db_conn, [
            _move("m1", self._ts(0), f"{Q}_drag1", f"{Q}_drop1"),
            _move("m2", self._ts(2), f"{Q}_drag9", f"{Q}_drop1"),
            _check("c1", self._ts(5), {f"{Q}_drop1": [f"{Q}_drag1"]}),
        ])
        _sync_board(db_conn)
        result = reconstruct_all(db_conn)
        assert _check_row(db_conn) == ([1, 0, 0, 0], 1, None)
        assert (result.mismatches, result.non_eligible) == (0, 0)
        assert result.skipped_actions == 1
        assert _attempt_qc(db_conn) == (
            {"replay_ok", "invalid_state"}, "exclude",
        )

    def test_first_applicable_cause_is_stored(self, db_conn):
        # A payload failing several tests stores the first in the vocabulary
        # order (qc.ANCHOR_CAUSE_ORDER); the rest are only logged.
        _seed(db_conn, [
            _move("m1", self._ts(0), f"{Q}_drag1", f"{Q}_drop1"),
            _check("c1", self._ts(5), {
                f"{Q}_drop1": [f"{Q}_drag1"],
                f"{Q}_drop2": [f"{Q}_drag1", f"{Q}_drag9"],
                "mystery-zone": [],
                f"{Q}_drop7": [],
            }),
        ])
        _sync_board(db_conn)
        reconstruct_all(db_conn)
        assert _check_row(db_conn)[1:] == (None, "duplicate_card")

    def test_non_eligible_check_blocks_replay_ok_without_a_mismatch(
        self, db_conn
    ):
        # replay_ok semantics unchanged: every check must be eligible and
        # agree. One eligible agreeing check plus one non-eligible check is
        # neither replay_ok nor check_mismatch.
        _seed(db_conn, [
            _move("m1", self._ts(0), f"{Q}_drag1", f"{Q}_drop1"),
            _check("c1", self._ts(5), {f"{Q}_drop1": [f"{Q}_drag1"]}),
            _raw_check("c2", self._ts(8), ""),
        ])
        _sync_board(db_conn)
        result = reconstruct_all(db_conn)
        assert (result.checks, result.mismatches, result.non_eligible) == (2, 0, 1)
        flags, readiness = _attempt_qc(db_conn)
        assert flags == {"parse_error"}
        assert readiness == "limited"
        rows = db_conn.execute(
            "SELECT matches_input, anchor_cause FROM dnd_configurations "
            "WHERE is_check = 1 ORDER BY seq"
        ).fetchall()
        assert [tuple(r) for r in rows] == [(1, None), (None, "malformed")]
