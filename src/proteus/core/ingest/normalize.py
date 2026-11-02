"""
Shared text and timestamp normalization (plan §1.3, core infrastructure).

Moved verbatim from ``proteus.ingest.common`` in item A3.2: both helpers are
needed by every service's derive step (SA today, DnD in Phase B), so they live
in core. ``proteus.ingest.common`` re-exports them for legacy callers.

Per CLAUDE.md rule 6, timestamps are never "cleaned": ``parse_timestamp`` is
the one sanctioned way to turn an export's opaque timestamp string into a
timezone-aware datetime, and every derive step must use it so all services
agree on what a timestamp means.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone


def normalize_text(s: str) -> str:
    """
    Apply Unicode NFC normalisation and collapse trailing whitespace.

    We deliberately do NOT collapse internal whitespace: students sometimes
    use line breaks meaningfully (e.g. to separate matrix rows from prose),
    and we don't want to flatten that signal away.
    """
    if s is None:
        return ""
    return unicodedata.normalize("NFC", s).rstrip()


# English month names for the long-form date path in parse_timestamp. We
# map them explicitly rather than relying on strptime's %B, whose accepted
# spelling depends on the process LC_TIME locale: under a non-English
# locale a valid 'August 18, 2025' would fail to parse and the row would be
# silently skipped, the very failure that long-form support exists to
# prevent.
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}

# 'Monday, August 18, 2025' or 'August 18, 2025': the leading weekday is
# optional and ignored, and the day may be 1 or 2 digits ('August 8, 2025').
_LONG_FORM_DATE_RE = re.compile(
    r"^(?:[A-Za-z]+,\s*)?([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})$"
)


def parse_timestamp(raw: str) -> datetime:
    """
    Parse a timestamp string from an export file into a timezone-aware
    datetime. Accepts a few common formats; raises ValueError on
    unrecognised input.

    DataShop exports use 'YYYY-MM-DD HH:MM:SS.ffffff' without a tz; we
    interpret these as UTC. If a future export carries explicit tz info,
    add a parse path here rather than guessing downstream.

    Some DataShop exports carry long-form calendar dates instead, e.g.
    'Monday, August 18, 2025' or 'August 18, 2025'. These are date-only,
    so we anchor them at midnight UTC, consistent with the naive-is-UTC
    convention above. They are parsed with an explicit English month table
    (see _MONTHS) rather than strptime's locale-sensitive %A/%B, so a
    non-English process locale cannot cause valid dates to be skipped.
    """
    raw = raw.strip()
    # ISO 8601 with explicit offset
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        pass
    # DataShop's format with microseconds, no tz
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    # Long-form calendar dates, e.g. 'Monday, August 18, 2025' or
    # 'August 18, 2025'. Date-only, so anchored at midnight UTC; parsed
    # locale-independently via _MONTHS rather than strptime's %A/%B.
    m = _LONG_FORM_DATE_RE.match(raw)
    if m:
        month = _MONTHS.get(m.group(1).lower())
        if month is not None:
            try:
                return datetime(
                    int(m.group(3)), month, int(m.group(2)),
                    tzinfo=timezone.utc,
                )
            except ValueError:
                pass  # e.g. day out of range, fall through to the raise
    raise ValueError(f"Unrecognised timestamp format: {raw!r}")
