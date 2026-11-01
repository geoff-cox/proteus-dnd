"""
Tests for the DnD read-time filters (checklist CD-3).

The helpers narrow persisted ``dnd_check_scores`` / ``dnd_attempts`` by
course/date (via the CD-1 ``events`` columns) and recompute the per-question
aggregates over the filtered subset (without re-running B3). Rows are inserted
directly so the filter/aggregate logic is exercised in isolation.
"""

from __future__ import annotations

import pytest

from proteus.core.filters import FilterSpec
from proteus.services.dragndrop import filters

Q = "q1"
Q2 = "q2"


def _import(conn) -> int:
    return conn.execute(
        "INSERT INTO raw_imports (file_path, file_hash, file_format, row_count) "
        "VALUES ('f.tab', 'h', 'datashop_tab', 0)"
    ).lastrowid


def _add_attempt(
    conn, import_id, *, student, question, attempt_index,
    class_id, course_id, week_no, checks,
):
    """checks: list of (g, all_or_nothing, decoy_src, decoy_tgt, timestamp)."""
    attempt_id = conn.execute(
        "INSERT INTO dnd_attempts (student_id, question_id, attempt_index, session_id) "
        "VALUES (?, ?, ?, 'sess')",
        (student, question, attempt_index),
    ).lastrowid
    for i, (g, aon, src, tgt, ts) in enumerate(checks):
        eid = f"{student}-{question}-{attempt_index}-{i}"
        conn.execute(
            "INSERT INTO events (event_id, import_id, student_id, problem_name, "
            "selection, problem_view, timestamp, course_id, class_id, week_no) "
            "VALUES (?, ?, ?, ?, 'dragNdrop', '1', ?, ?, ?, ?)",
            (eid, import_id, student, question, ts, course_id, class_id, week_no),
        )
        conn.execute("INSERT INTO dnd_events (event_id, kind) VALUES (?, 'check')", (eid,))
        conn.execute(
            "INSERT INTO dnd_configurations (attempt_id, seq, event_id, s_json, is_check) "
            "VALUES (?, ?, ?, '[]', 1)",
            (attempt_id, i + 1, eid),
        )
        conn.execute(
            "INSERT INTO dnd_check_scores (event_id, attempt_id, question_id, sigma, "
            "distance, g, sigma_tilde, all_or_nothing, decoy_source_leak, "
            "decoy_target_leak) VALUES (?, ?, ?, 0, 0, ?, 0, ?, ?, ?)",
            (eid, attempt_id, question, g, aon, src, tgt),
        )
    conn.commit()
    return attempt_id


@pytest.fixture
def seeded(db_conn):
    import_id = _import(db_conn)
    # A1: class 1 / course C1 / week 8. Two checks (miss then key).
    _add_attempt(
        db_conn, import_id, student="s1", question=Q, attempt_index=1,
        class_id="1", course_id="C1", week_no="8",
        checks=[
            (0.5, 0, 1, 0, "2026-01-01 09:00:00"),
            (1.0, 1, 0, 0, "2026-01-01 09:05:00"),
        ],
    )
    # A2: class 2 / course C2 / week 9. One check (key).
    _add_attempt(
        db_conn, import_id, student="s2", question=Q, attempt_index=1,
        class_id="2", course_id="C2", week_no="9",
        checks=[(1.0, 1, 0, 0, "2026-01-02 10:00:00")],
    )
    # A different question, to prove question_id isolates.
    _add_attempt(
        db_conn, import_id, student="s3", question=Q2, attempt_index=1,
        class_id="1", course_id="C1", week_no="8",
        checks=[(0.0, 0, 0, 1, "2026-01-01 09:00:00")],
    )
    return db_conn


class TestFilteredCheckScores:
    def test_by_question(self, seeded):
        rows = filters.filtered_check_scores(seeded, FilterSpec(question_id=Q))
        assert len(rows) == 3
        assert {r["question_id"] for r in rows} == {Q}

    def test_by_class(self, seeded):
        rows = filters.filtered_check_scores(seeded, FilterSpec(question_id=Q, class_id="1"))
        assert len(rows) == 2  # only A1

    def test_by_course(self, seeded):
        rows = filters.filtered_check_scores(seeded, FilterSpec(question_id=Q, course_id="C2"))
        assert len(rows) == 1  # only A2

    def test_by_date_range(self, seeded):
        rows = filters.filtered_check_scores(
            seeded, FilterSpec(question_id=Q, date_from="2026-01-02")
        )
        assert len(rows) == 1  # only A2's check is on/after Jan 2


class TestFilteredAttempts:
    def test_by_question(self, seeded):
        rows = filters.filtered_attempts(seeded, FilterSpec(question_id=Q))
        assert {r["student_id"] for r in rows} == {"s1", "s2"}

    def test_by_class(self, seeded):
        rows = filters.filtered_attempts(seeded, FilterSpec(question_id=Q, class_id="1"))
        assert {r["student_id"] for r in rows} == {"s1"}

    def test_by_week(self, seeded):
        rows = filters.filtered_attempts(seeded, FilterSpec(question_id=Q, week_no="9"))
        assert {r["student_id"] for r in rows} == {"s2"}


class TestFilteredAggregates:
    def test_unfiltered_question(self, seeded):
        agg = filters.filtered_aggregates(seeded, FilterSpec(question_id=Q))
        assert agg.n_attempts == 2
        assert agg.n_checks == 3
        assert agg.mean_g == pytest.approx((0.5 + 1.0 + 1.0) / 3)
        # check-1: s1 miss (0), s2 key (1) -> 1/2; check-2: s1 key -> 1/1.
        assert agg.correct_on_check_k == {1: (1, 2), 2: (1, 1)}
        assert agg.mean_decoy_source_leak == pytest.approx(1 / 3)
        assert agg.mean_decoy_target_leak == pytest.approx(0.0)

    def test_filtered_by_class_recomputes_over_subset(self, seeded):
        agg = filters.filtered_aggregates(seeded, FilterSpec(question_id=Q, class_id="1"))
        assert agg.n_attempts == 1
        assert agg.n_checks == 2
        assert agg.mean_g == pytest.approx((0.5 + 1.0) / 2)
        assert agg.correct_on_check_k == {1: (0, 1), 2: (1, 1)}
        assert agg.mean_decoy_source_leak == pytest.approx(0.5)

    def test_empty_subset_is_safe(self, seeded):
        agg = filters.filtered_aggregates(seeded, FilterSpec(question_id="nope"))
        assert agg.n_attempts == 0
        assert agg.n_checks == 0
        assert agg.mean_g is None
        assert agg.correct_on_check_k == {}
        assert agg.mean_decoy_source_leak is None

    def test_mean_leak_is_a_count_not_a_share(self, db_conn):
        # Two decoy cards leak on the only check: the mean is 2.0: a mean of
        # per-check *counts* (the journal's cohort decoy-leak quantity), which
        # a share-of-checks reading (or a percent format) cannot represent.
        import_id = _import(db_conn)
        _add_attempt(
            db_conn, import_id, student="s9", question=Q, attempt_index=1,
            class_id="1", course_id="C1", week_no="8",
            checks=[(0.0, 0, 2, 0, "2026-01-03 09:00:00")],
        )
        agg = filters.filtered_aggregates(db_conn, FilterSpec(question_id=Q))
        assert agg.mean_decoy_source_leak == pytest.approx(2.0)
