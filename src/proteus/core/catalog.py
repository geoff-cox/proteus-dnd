"""
The question catalog (checklist item CD-2).

Every distinct ``problem_name`` that appears in ``events`` (page views excluded)
is surfaced as a ``questions`` row so the front door can list it, serviced or
not, and so ``question_id`` stays the single join key across the catalog,
question source (SP-2), and every service's tables.

Two entry points:

* :func:`sync_catalog`, the idempotent upsert run at the end of ingest and as
  ``proteus sync-catalog``. It materializes a ``questions`` row per question,
  with an inferred type, leaving ``textbook_id`` NULL (the book is identified
  later, at upload/sync, the ``.tab`` carries no book id) and ``source`` set to
  ``'catalog'``. It only writes *per-question metadata*, never a copy of any
  event, and never clobbers a type a textbook source-sync already set.
* :func:`question_catalog`, a read-only projection the UI (B4.9) consumes: per
  question, its stored type, whether a service handles it, the classes/courses
  it appears in, first/last-seen, and event count, all aggregated from
  ``events`` at read time (``events`` is the single source of truth, P-1).

Type inference is driven by the registry, not hard-coded: a question is
*serviced* when one of its DataShop selections is in a service's
``selection_values`` (SA claims ``shortanswer``; DnD claims ``dragNdrop`` and
its drop/reset variants). A serviced question's stored ``question_type`` is the
service's single PreTeXt element tag (``shortanswer`` / ``cardsort``), keeping
the catalog consistent with the source-sync taxonomy; an unserviced question
(``mChoice``, ``poll``, ``sage``, …) keeps its selection value as the type and
is shown but labeled "no analysis service installed" by the UI (B4.11).
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from typing import Iterable

from proteus.core import registry
from proteus.core.db import transaction

log = logging.getLogger(__name__)

__all__ = [
    "sync_catalog",
    "question_catalog",
    "infer_question_type",
    "CatalogSyncResult",
    "CatalogEntry",
]

# Selections that are activity, not questions (CD-2 inclusion rule). Only `page`
# is pinned by the checklist ("page views are not questions"); every other
# non-page selection is catalogued and shown, serviced or not (B4.11). Real
# exports also carry navigation/admin selections (admin, dashboard, assignments,
# knowl, …). Most arrive with an empty problem_name and are dropped anyway; the
# few that don't surface as unserviced types. Broadening this set to treat some
# of those as non-question activity is a deliberate refinement, not a silent
# pick. TODO(plan §7): confirm the non-question selection list with the
# maintainer before excluding more than `page`.
EXCLUDED_SELECTIONS: frozenset[str] = frozenset({"page"})


@dataclass(frozen=True)
class CatalogSyncResult:
    """Summary of one :func:`sync_catalog` run."""

    total: int  # distinct question-bearing problems in events
    created: int  # new questions rows this run inserted
    serviced: int  # of total, how many a service claims
    unserviced: int  # of total, how many have no installed service
    by_type: dict[str, int]  # inferred question_type -> count


@dataclass(frozen=True)
class CatalogEntry:
    """One question as the front door sees it (read-time projection)."""

    question_id: str
    question_type: str | None  # stored on the questions row (may be None)
    # questions.title: the unit's <title> as verbatim inner XML (FU-3), may
    # carry PreTeXt markup; strip for plain-text display. None when the
    # question has no synced source or the unit has no title.
    title: str | None
    serviced_by: str | None  # service name claiming it, or None (unserviced)
    has_source: bool  # questions.source_xml present (preview/board buildable)
    n_events: int
    first_seen: str | None
    last_seen: str | None
    classes: tuple[str, ...]
    courses: tuple[str, ...]
    selections: tuple[str, ...]


def _selection_index(
    services: Iterable[registry.ServiceDescriptor] | None = None,
) -> dict[str, tuple[str, str | None]]:
    """
    Map each claimed DataShop selection value to ``(service_name, element_tag)``.

    ``element_tag`` is the service's PreTeXt question type when it claims exactly
    one (SA -> ``shortanswer``, DnD -> ``cardsort``), else None, the conservative
    default for a future service that claims several, where no single tag is the
    obvious catalog label.
    """
    if services is None:
        services = registry.available_services()
    index: dict[str, tuple[str, str | None]] = {}
    for svc in services:
        element = next(iter(svc.question_types)) if len(svc.question_types) == 1 else None
        for selection in svc.selection_values:
            index[selection] = (svc.name, element)
    return index


def infer_question_type(
    selections: Iterable[str],
    *,
    index: dict[str, tuple[str, str | None]],
) -> tuple[str | None, str | None]:
    """
    Return ``(question_type, serviced_by)`` for a question's set of selections.

    Serviced: the owning service's element tag (falling back to the matching
    selection if the service declares no single tag). Unserviced: the question's
    own selection value, ``serviced_by=None``. Selections are considered in
    sorted order so the result is deterministic.
    """
    ordered = sorted(s for s in selections if s)
    for selection in ordered:
        if selection in index:
            service_name, element = index[selection]
            return (element or selection), service_name
    return (ordered[0] if ordered else None), None


def _excluded_placeholders() -> str:
    return ", ".join("?" for _ in EXCLUDED_SELECTIONS)


def sync_catalog(conn: sqlite3.Connection) -> CatalogSyncResult:
    """
    Reconcile the ``questions`` catalog with ``events`` (idempotent).

    Creates a ``questions`` row for every distinct non-page problem not already
    present, with its inferred ``question_type``, ``textbook_id`` NULL, and
    ``source='catalog'``. For a question that already has a row, a missing
    ``question_type`` is filled but an existing one is never overwritten, a
    textbook source-sync owns that column once it has run.
    """
    index = _selection_index()
    # A12/BUG-12: a NULL selection previously vanished silently, NULL
    # fails `NOT IN`, so a question-bearing row with no selection was
    # dropped from the catalog with no trace. The chosen rule mirrors the
    # problem_name idiom: excluded explicitly, and reported as
    # data-quality noise below rather than silently (the column is
    # nullable, but no reader writes NULL, such a row is malformed
    # input, not a question type).
    n_null_selection = conn.execute(
        "SELECT count(*) FROM events WHERE selection IS NULL "
        "AND problem_name IS NOT NULL AND problem_name <> ''"
    ).fetchone()[0]
    if n_null_selection:
        log.warning(
            "Catalog sync: %d question-bearing event(s) have a NULL "
            "selection and are excluded from the catalog, malformed "
            "rows worth investigating (they entered outside the "
            "ingest readers, which always set selection).",
            n_null_selection,
        )
    rows = conn.execute(
        "SELECT DISTINCT problem_name, selection FROM events "
        "WHERE selection IS NOT NULL "
        f"AND selection NOT IN ({_excluded_placeholders()}) "
        "AND problem_name IS NOT NULL AND problem_name <> ''",
        tuple(EXCLUDED_SELECTIONS),
    ).fetchall()

    selections_by_q: dict[str, set[str]] = {}
    for row in rows:
        selections_by_q.setdefault(row["problem_name"], set()).add(row["selection"])

    existing = {
        r["question_id"]
        for r in conn.execute("SELECT question_id FROM questions").fetchall()
    }

    created = 0
    serviced = 0
    unserviced = 0
    by_type: dict[str, int] = {}
    with transaction(conn):
        for question_id in sorted(selections_by_q):
            question_type, service_name = infer_question_type(
                selections_by_q[question_id], index=index
            )
            if question_type is not None:
                by_type[question_type] = by_type.get(question_type, 0) + 1
            if service_name is not None:
                serviced += 1
            else:
                unserviced += 1

            if question_id in existing:
                # Fill a missing type only; never clobber a source-synced value.
                conn.execute(
                    "UPDATE questions SET question_type = ? "
                    "WHERE question_id = ? AND question_type IS NULL",
                    (question_type, question_id),
                )
            else:
                # xml_id is NOT NULL but unknown without source; the problem_name
                # is the best available identifier until SP-2 attaches source.
                conn.execute(
                    "INSERT INTO questions (question_id, textbook_id, xml_id, "
                    "metadata_json, source, question_type) "
                    "VALUES (?, NULL, ?, '{}', 'catalog', ?)",
                    (question_id, question_id, question_type),
                )
                created += 1

    log.info(
        "Catalog sync: %d questions (%d created, %d serviced, %d unserviced)",
        len(selections_by_q), created, serviced, unserviced,
    )
    return CatalogSyncResult(
        total=len(selections_by_q),
        created=created,
        serviced=serviced,
        unserviced=unserviced,
        by_type=by_type,
    )


def _split_concat(value: str | None) -> tuple[str, ...]:
    """Split a GROUP_CONCAT(DISTINCT …) result into a sorted tuple, dropping
    the empty/None pieces (GROUP_CONCAT already skips NULLs)."""
    if not value:
        return ()
    return tuple(sorted(p for p in value.split(",") if p))


def question_catalog(conn: sqlite3.Connection) -> list[CatalogEntry]:
    """
    The read-time catalog projection for the front door (B4.9), one entry per
    distinct non-page problem in ``events``.

    Classes, courses, first/last-seen, and event count are aggregated from
    ``events`` (the source of truth); ``question_type`` / ``has_source`` come
    from the materialized ``questions`` row when present; ``serviced_by`` is
    computed from the question's selections against the registry.
    """
    index = _selection_index()
    rows = conn.execute(
        "SELECT e.problem_name AS question_id, "
        "       q.question_type AS question_type, "
        "       q.title AS title, "
        "       (q.source_xml IS NOT NULL) AS has_source, "
        "       COUNT(*) AS n_events, "
        "       MIN(e.timestamp) AS first_seen, "
        "       MAX(e.timestamp) AS last_seen, "
        "       GROUP_CONCAT(DISTINCT e.class_id) AS class_ids, "
        "       GROUP_CONCAT(DISTINCT e.course_id) AS course_ids, "
        "       GROUP_CONCAT(DISTINCT e.selection) AS selections "
        "FROM events e LEFT JOIN questions q ON q.question_id = e.problem_name "
        # NULL selection excluded explicitly (A12/BUG-12), the same rule
        # sync_catalog applies and warns about; without the IS NOT NULL
        # the NOT IN already dropped such rows, but silently.
        "WHERE e.selection IS NOT NULL "
        f"AND e.selection NOT IN ({_excluded_placeholders()}) "
        "AND e.problem_name IS NOT NULL AND e.problem_name <> '' "
        "GROUP BY e.problem_name ORDER BY e.problem_name",
        tuple(EXCLUDED_SELECTIONS),
    ).fetchall()

    entries: list[CatalogEntry] = []
    for row in rows:
        selections = _split_concat(row["selections"])
        _, serviced_by = infer_question_type(selections, index=index)
        entries.append(
            CatalogEntry(
                question_id=row["question_id"],
                question_type=row["question_type"],
                title=row["title"],
                serviced_by=serviced_by,
                has_source=bool(row["has_source"]),
                n_events=row["n_events"],
                first_seen=row["first_seen"],
                last_seen=row["last_seen"],
                classes=_split_concat(row["class_ids"]),
                courses=_split_concat(row["course_ids"]),
                selections=selections,
            )
        )
    return entries
