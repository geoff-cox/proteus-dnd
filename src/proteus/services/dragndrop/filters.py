"""
Read-time filters over the DnD service's *persisted* results (checklist CD-3).

Pure readers the UI service layer (B4.1) and export (B4.5) call to narrow a
question's already-scored rows by course/date, a read/view bound. The B3
``reconstruct`` / ``score`` functions are untouched and never re-run over a
subset: these helpers query ``dnd_check_scores`` / ``dnd_attempts`` joined to
``events`` (the CD-1 filter columns) and recompute the per-question aggregates
over whatever subset the filter selects.

Linkage:

* ``dnd_check_scores`` joins to ``events`` directly on its check ``event_id``;
* ``dnd_attempts`` has no event column, so it is filtered by the events of its
  actions (``dnd_configurations.event_id``).
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass

from proteus.core.filters import FilterSpec, events_conditions, has_event_filters

__all__ = [
    "filtered_check_scores",
    "filtered_attempts",
    "filtered_aggregates",
    "DnDAggregates",
]


@dataclass(frozen=True)
class DnDAggregates:
    """Per-question aggregates recomputed over a filtered set of check scores."""

    n_attempts: int
    n_checks: int
    mean_g: float | None
    # k (1-based check ordinal within an attempt) -> (n_correct, n_total).
    correct_on_check_k: dict[int, tuple[int, int]]
    # Mean per-check decoy-leak *counts*, the journal's cohort decoy-leak
    # quantity (e.g. 0.30 in tab-cheat-tallies). Averaged over the filtered
    # checks that carry a leak value (NULL leaks drop out of the denominator,
    # as with mean_g). Not a share of checks: on a board with several decoy
    # cards/slots the mean can exceed 1 (F23).
    mean_decoy_source_leak: float | None
    mean_decoy_target_leak: float | None


def _where(conditions: list[str]) -> str:
    return ("WHERE " + " AND ".join(conditions)) if conditions else ""


def filtered_check_scores(
    conn: sqlite3.Connection, spec: FilterSpec
) -> list[sqlite3.Row]:
    """``dnd_check_scores`` rows for the spec, joined to ``events`` by event_id."""
    conditions: list[str] = []
    params: list[str] = []
    if spec.question_id is not None:
        conditions.append("cs.question_id = ?")
        params.append(spec.question_id)
    join = ""
    if has_event_filters(spec):
        join = "JOIN events e ON e.event_id = cs.event_id"
        ev_conditions, ev_params = events_conditions(spec, "e")
        conditions.extend(ev_conditions)
        params.extend(ev_params)
    sql = (
        f"SELECT cs.* FROM dnd_check_scores cs {join} {_where(conditions)} "
        "ORDER BY cs.question_id, cs.event_id"
    )
    return conn.execute(sql, params).fetchall()


def filtered_attempts(
    conn: sqlite3.Connection, spec: FilterSpec
) -> list[sqlite3.Row]:
    """
    ``dnd_attempts`` rows for the spec. An attempt matches the events-only
    dimensions when one of its action events (``dnd_configurations.event_id``)
    matches, its events share the same student/course/class, so any one is
    representative.
    """
    conditions: list[str] = []
    params: list[str] = []
    if spec.question_id is not None:
        conditions.append("a.question_id = ?")
        params.append(spec.question_id)
    if has_event_filters(spec):
        ev_conditions, ev_params = events_conditions(spec, "e")
        extra = (" AND " + " AND ".join(ev_conditions)) if ev_conditions else ""
        conditions.append(
            "EXISTS (SELECT 1 FROM dnd_configurations c "
            "JOIN events e ON e.event_id = c.event_id "
            f"WHERE c.attempt_id = a.attempt_id{extra})"
        )
        params.extend(ev_params)
    sql = (
        f"SELECT a.* FROM dnd_attempts a {_where(conditions)} "
        "ORDER BY a.question_id, a.student_id, a.attempt_index"
    )
    return conn.execute(sql, params).fetchall()


def filtered_aggregates(
    conn: sqlite3.Connection, spec: FilterSpec
) -> DnDAggregates:
    """
    Recompute the per-question aggregates (mean g, correct-on-check-k, mean
    decoy leaks) over the filtered check scores. "Correct" is ``all_or_nothing``
    (the configuration equals the key, how a cardsort is graded). Checks are
    ordered within each attempt by event timestamp to assign the check ordinal k.
    """
    conditions: list[str] = []
    params: list[str] = []
    if spec.question_id is not None:
        conditions.append("cs.question_id = ?")
        params.append(spec.question_id)
    # Always join events (needed for the per-attempt check ordering) and apply
    # the events-only filters when present.
    ev_conditions, ev_params = events_conditions(spec, "e")
    conditions.extend(ev_conditions)
    params.extend(ev_params)
    sql = (
        "SELECT cs.attempt_id, cs.g, cs.all_or_nothing, cs.decoy_source_leak, "
        "cs.decoy_target_leak "
        "FROM dnd_check_scores cs JOIN events e ON e.event_id = cs.event_id "
        f"{_where(conditions)} "
        # The F2 sort key: numeric-cast problem_view breaks timestamp ties
        # before the event-id fallback, the same ordering cohort._read_checks
        # uses, so the check ordinal k agrees between Scoring and Explore.
        "ORDER BY cs.attempt_id, e.timestamp, "
        "CAST(e.problem_view AS INTEGER), cs.event_id"
    )
    rows = conn.execute(sql, params).fetchall()

    g_values = [r["g"] for r in rows if r["g"] is not None]
    src_leaks = [r["decoy_source_leak"] for r in rows if r["decoy_source_leak"] is not None]
    tgt_leaks = [r["decoy_target_leak"] for r in rows if r["decoy_target_leak"] is not None]

    per_attempt: dict[int, list[int | None]] = defaultdict(list)
    for r in rows:  # already ordered by (attempt_id, timestamp)
        per_attempt[r["attempt_id"]].append(r["all_or_nothing"])
    correct_on_check_k: dict[int, tuple[int, int]] = {}
    for flags in per_attempt.values():
        for k, all_or_nothing in enumerate(flags, start=1):
            n_correct, n_total = correct_on_check_k.get(k, (0, 0))
            correct_on_check_k[k] = (
                n_correct + (1 if all_or_nothing == 1 else 0),
                n_total + 1,
            )

    return DnDAggregates(
        n_attempts=len({r["attempt_id"] for r in rows}),
        n_checks=len(rows),
        mean_g=(sum(g_values) / len(g_values)) if g_values else None,
        correct_on_check_k=dict(sorted(correct_on_check_k.items())),
        mean_decoy_source_leak=(sum(src_leaks) / len(src_leaks)) if src_leaks else None,
        mean_decoy_target_leak=(sum(tgt_leaks) / len(tgt_leaks)) if tgt_leaks else None,
    )
