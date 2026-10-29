"""
Tests for the shared read-time filter vocabulary (checklist CD-3).

`core.filters` owns `FilterSpec` and builds SQL conditions over the events-only
dimensions (class/course/week/date); `question_id` is intentionally excluded
(services filter it on their own table).
"""

from __future__ import annotations

from proteus.core.filters import FilterSpec, events_conditions, has_event_filters


class TestFilterSpec:
    def test_defaults_are_all_none(self):
        spec = FilterSpec()
        assert (spec.question_id, spec.class_id, spec.course_id) == (None, None, None)
        assert (spec.week_no, spec.date_from, spec.date_to) == (None, None, None)


class TestHasEventFilters:
    def test_false_for_empty_or_question_only(self):
        assert has_event_filters(FilterSpec()) is False
        assert has_event_filters(FilterSpec(question_id="q1")) is False

    def test_true_for_any_event_dimension(self):
        assert has_event_filters(FilterSpec(class_id="1")) is True
        assert has_event_filters(FilterSpec(course_id="C1")) is True
        assert has_event_filters(FilterSpec(week_no="8")) is True
        assert has_event_filters(FilterSpec(date_from="2026-01-01")) is True
        assert has_event_filters(FilterSpec(date_to="2026-02-01")) is True


class TestEventsConditions:
    def test_empty_spec_yields_nothing(self):
        assert events_conditions(FilterSpec()) == ([], [])

    def test_question_id_is_excluded(self):
        # question_id is a service-table filter, never an events condition.
        assert events_conditions(FilterSpec(question_id="q1")) == ([], [])

    def test_builds_conditions_and_params_with_alias(self):
        spec = FilterSpec(
            question_id="q1", class_id="1", course_id="C1", week_no="8",
            date_from="2026-01-01", date_to="2026-02-01",
        )
        conds, params = events_conditions(spec, alias="ev")
        assert conds == [
            "ev.class_id = ?",
            "ev.course_id = ?",
            "ev.week_no = ?",
            "ev.timestamp >= ?",
            "ev.timestamp <= ?",
        ]
        assert params == ["1", "C1", "8", "2026-01-01", "2026-02-01"]

    def test_default_alias_is_e(self):
        conds, _ = events_conditions(FilterSpec(class_id="1"))
        assert conds == ["e.class_id = ?"]
