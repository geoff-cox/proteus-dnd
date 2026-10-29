"""
Pure Runestone useinfo ``.csv`` reader (core).

Yields one ``Event`` per row — *every* row, with no event-type filter — so a
Runestone export flows through the same shared Stage-0 ingest as a DataShop
``.tab`` (plan §2.3; resolves the `.csv` half of open question #2 for SA: the
DnD service remains `.tab`-only in practice, but the reader seam now covers
both formats).

Column mapping onto the generic event model: ``sid`` → student_id, ``div_id``
→ problem_name, ``event`` → selection, ``act`` → action, ``timestamp`` →
timestamp (verbatim). useinfo has no session/problem-view/input/feedback
columns; those stay empty strings, keeping the event identity hash
well-formed.

Note on timestamps: some Runestone exports contain spreadsheet-mangled
timestamps (e.g. '44:22.8' instead of a full datetime), the result of opening
the CSV in Excel before saving. The reader captures them verbatim — faithful
capture is the point — and ``timestamp_looks_real`` is the policy check the
SA derive applies to runestone-format imports so such rows are refused rather
than silently turned into invented dates (matching the legacy parser).

Read-only: produces ``Event`` objects, never touches the database.
"""

from __future__ import annotations

import csv
import logging
import re
from pathlib import Path
from typing import Iterator

from proteus.core.ingest.events import Event

log = logging.getLogger(__name__)

FORMAT_NAME = "runestone_useinfo_csv"

_REQUIRED_COLUMNS = {"id", "timestamp", "sid", "event", "act", "div_id"}

# Heuristic for spreadsheet-mangled timestamps. A real timestamp will have
# a year and at least one date separator; a mangled one looks like 'MM:SS.s'
# or similar.
_LOOKS_LIKE_REAL_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}|\d{4}/\d{2}/\d{2}")


def timestamp_looks_real(raw: str) -> bool:
    """
    Whether a useinfo timestamp looks like a genuine datetime rather than a
    spreadsheet-mangled fragment. Format knowledge owned here; consulted by
    services deriving from runestone-format imports.

    Inherited verbatim from the legacy parser (frozen SA behavior): it is
    deliberately broader than what ``core.ingest.normalize.parse_timestamp``
    accepts — e.g. a ``YYYY/MM/DD`` date passes here and is then rejected by
    parse_timestamp — so such a row is still skipped, just logged as
    "Unrecognised timestamp format" rather than "spreadsheet-mangled".
    Tightening this regex or widening parse_timestamp would change that
    frozen behavior; if slash-dated Runestone exports ever appear for real,
    add a parse path in normalize.parse_timestamp under a new golden test
    rather than editing either side casually.
    """
    return _LOOKS_LIKE_REAL_TIMESTAMP.search(raw) is not None


def read(path: str | Path) -> Iterator[Event]:
    """
    Yield an ``Event`` for every data row in a Runestone useinfo ``.csv``.

    Raises ValueError if the file has no header or is missing a required
    column. Does not skip rows: each service decides downstream what is and
    isn't usable.
    """
    path = Path(path)
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(f"{path}: file has no header row")
        missing = _REQUIRED_COLUMNS - set(reader.fieldnames)
        if missing:
            raise ValueError(f"{path}: missing required columns: {sorted(missing)}")
        for row in reader:
            yield _row_to_event(row)


def _stripped(row: dict, col: str) -> str:
    return (row.get(col) or "").strip()


def _row_to_event(row: dict) -> Event:
    course = row.get("course_id")
    course_id = (course.strip() or None) if course is not None else None
    return Event(
        student_id=_stripped(row, "sid"),
        session_id="",
        problem_name=_stripped(row, "div_id"),
        selection=_stripped(row, "event"),
        # act kept verbatim — service derive normalizes (SA applies
        # normalize_text), mirroring the DataShop reader's Action handling.
        action=row.get("act") or "",
        input="",
        feedback_text="",
        feedback_classification="",
        problem_view="",
        timestamp=_stripped(row, "timestamp"),
        course_id=course_id,
    )
