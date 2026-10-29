"""
Read-time filter dimensions for narrowing persisted results (checklist CD-3).

The front door filters a question's displayed/exported rows by course and date
(B4.10) — a **read/view bound, never a recompute input**. The B3 analysis
functions stay frozen: they are never given filter parameters; filtering happens
here, after the fact, over already-persisted rows.

Every filterable dimension lives on the core ``events`` table — ``course_id``
plus ``class_id`` / ``week_no`` / ``timestamp`` (the CD-1 columns) — so this
module owns the shared vocabulary (:class:`FilterSpec`) and builds the SQL
conditions over ``events``. Each service joins its own persisted tables back to
``events`` and applies them (``services/dragndrop/filters.py``,
``services/shortanswer/filters.py``); core never names a service table.

``question_id`` is deliberately *not* part of the events conditions: it is
present and indexed on every persisted service table, so services filter it
there directly.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["FilterSpec", "events_conditions", "has_event_filters"]


@dataclass(frozen=True)
class FilterSpec:
    """
    A read-time filter over one question's persisted results.

    All fields are optional; the all-default spec means "no filter".

    ``date_from`` / ``date_to`` are compared **lexically** against a stored
    timestamp string (``events.timestamp`` for DnD; the ISO ``submitted_at`` for
    SA), so they are only chronological for ISO-8601-style values
    (``YYYY-MM-DD[…]``). That is what the DataShop export and the SA derive both
    produce, so day-granular bounds like ``'2026-01-03'`` sort correctly; the
    long-form dates ``parse_timestamp`` also tolerates (e.g. ``'August 18,
    2025'``) would not, and are out of scope for these read-time filters. Pass
    bounds in the same shape as the stored value (e.g. ``'2026-01-03'`` or a
    full ``'2026-01-03 09:00:00'``).
    """

    question_id: str | None = None
    class_id: str | None = None
    course_id: str | None = None
    week_no: str | None = None
    date_from: str | None = None  # inclusive lower bound on events.timestamp
    date_to: str | None = None  # inclusive upper bound on events.timestamp


def has_event_filters(spec: FilterSpec) -> bool:
    """
    Whether ``spec`` constrains any events-only dimension (everything but
    ``question_id``). When False a service can skip the ``events`` join.
    """
    return any(
        value is not None
        for value in (
            spec.class_id,
            spec.course_id,
            spec.week_no,
            spec.date_from,
            spec.date_to,
        )
    )


def events_conditions(
    spec: FilterSpec, alias: str = "e"
) -> tuple[list[str], list[str]]:
    """
    SQL conditions (and their params) over the events-only filter columns.

    Returns ``(conditions, params)``; the caller joins ``conditions`` with
    ``AND``. ``question_id`` is excluded by design — services filter it on their
    own table. ``alias`` is the ``events`` table alias in the caller's query.
    """
    conditions: list[str] = []
    params: list[str] = []
    if spec.class_id is not None:
        conditions.append(f"{alias}.class_id = ?")
        params.append(spec.class_id)
    if spec.course_id is not None:
        conditions.append(f"{alias}.course_id = ?")
        params.append(spec.course_id)
    if spec.week_no is not None:
        conditions.append(f"{alias}.week_no = ?")
        params.append(spec.week_no)
    if spec.date_from is not None:
        conditions.append(f"{alias}.timestamp >= ?")
        params.append(spec.date_from)
    if spec.date_to is not None:
        conditions.append(f"{alias}.timestamp <= ?")
        params.append(spec.date_to)
    return conditions, params
