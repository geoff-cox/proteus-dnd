"""
Pure DataShop ``.tab`` reader (core).

Yields one ``Event`` per row — *every* row, with no Selection filter. This is
the shared Stage-0 reader that replaces the SA-specific
``proteus.ingest.datashop`` (which filtered ``Selection == 'shortanswer'`` and
silently dropped every drag-and-drop and page-view row). Filtering by question
type now happens per service, at derive time, over the ``events`` table.

Read-only: produces ``Event`` objects, never touches the database.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Iterator

from proteus.core.ingest.events import Event

log = logging.getLogger(__name__)

FORMAT_NAME = "datashop_tab"

# Columns required to form an event's identity. Problem View is included
# because it is part of event_id (plan §2.3); requiring it keeps the identity
# well-formed rather than letting an omitted column collapse to "" for every
# row (which would inflate event_id collisions). Action and the other
# DnD-relevant columns (Input, Feedback Text, Session Id, …) are optional: an
# export may legitimately omit some, and an event with an empty Action is
# still an event.
_REQUIRED_COLUMNS = {
    "Time",
    "Anon Student Id",
    "Problem Name",
    "Selection",
    "Problem View",
}


def read(path: str | Path) -> Iterator[Event]:
    """
    Yield an ``Event`` for every data row in a DataShop ``.tab`` file.

    Raises ValueError if the file has no header or is missing a required
    column. Does not skip rows: faithful capture of the whole log is the point
    (each service decides downstream what is and isn't usable).
    """
    path = Path(path)
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        if reader.fieldnames is None:
            raise ValueError(f"{path}: file has no header row")
        missing = _REQUIRED_COLUMNS - set(reader.fieldnames)
        if missing:
            raise ValueError(f"{path}: missing required columns: {sorted(missing)}")
        for row in reader:
            yield _row_to_event(row)


def _stripped(row: dict, col: str) -> str:
    return (row.get(col) or "").strip()


def _stripped_or_none(row: dict, col: str) -> str | None:
    """A trimmed column value, or None when the column is absent or empty.

    Used for the optional metadata dimensions (course/class/week/chapter): a
    blank cell or a column an export omits both become NULL rather than '',
    matching how course_id has always been handled.
    """
    value = row.get(col)
    if value is None:
        return None
    return value.strip() or None


def _row_to_event(row: dict) -> Event:
    return Event(
        student_id=_stripped(row, "Anon Student Id"),
        session_id=_stripped(row, "Session Id"),
        problem_name=_stripped(row, "Problem Name"),
        selection=_stripped(row, "Selection"),
        # action / input / feedback_text kept verbatim — service derive
        # normalizes (e.g. SA applies normalize_text to action).
        action=row.get("Action") or "",
        input=row.get("Input") or "",
        feedback_text=row.get("Feedback Text") or "",
        feedback_classification=_stripped(row, "Feedback Classification"),
        problem_view=_stripped(row, "Problem View"),
        timestamp=_stripped(row, "Time"),
        course_id=_stripped_or_none(row, "CF (Institution)"),
        # CD-1 filter dimensions, stored verbatim (opaque, like every other
        # event column); a read/view bound, not part of event identity.
        class_id=_stripped_or_none(row, "Class"),
        week_no=_stripped_or_none(row, "CF (Week No)"),
        chapter=_stripped_or_none(row, "Level (Chapter)"),
        subchapter=_stripped_or_none(row, "Level (SubChapter)"),
    )
