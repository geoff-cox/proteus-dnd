"""
Extract per-question HTML previews from a built PreTeXt textbook.

Walks a PreTeXt HTML build directory, finds each short-answer question,
and writes a self-contained HTML file for it (with the section's <head>
preserved). Records metadata in the questions table.

The unit of work is the data-component='shortanswer' element: each one
corresponds to one database question_id. Its enclosing <article> with
class 'exercise-like' is the rendering unit (could be an exercise or a
sub-task).
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from bs4 import BeautifulSoup, Tag

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExtractedQuestion:
    question_id: str
    xml_id: str
    parent_xml_id: str | None
    section_id: str
    section_title: str
    exercise_type: str          # 'task' | 'exercise' | 'reading-question' | ...
    standalone_html: str        # complete <!DOCTYPE html>...</html>
    source_hash: str            # SHA-256 of the render input chunk
    metadata: dict


@dataclass(frozen=True)
class SyncResult:
    extracted: int = 0
    updated: int = 0
    unchanged: int = 0
    missing: list[str] = field(default_factory=list)
    orphan: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Finding the unit of work
# ---------------------------------------------------------------------------

def _find_question_units(section_soup: BeautifulSoup) -> Iterator[tuple[Tag, str]]:
    """
    Yield (article, question_id) for every short-answer question in a section.

    The question_id is the id attribute of the inner shortanswer div, which
    matches what's in the responses table. The article is the nearest
    enclosing exercise-like wrapper.
    """
    for sa in section_soup.find_all(attrs={"data-component": "shortanswer"}):
        question_id = sa.get("id")
        if not question_id:
            continue
        article = sa.find_parent("article", class_="exercise-like")
        if article is None:
            log.warning(
                "Found shortanswer %r with no enclosing exercise-like article; skipping",
                question_id,
            )
            continue
        yield article, question_id


def _is_task(article: Tag) -> bool:
    return "task" in (article.get("class") or [])


def _find_parent_exercise(task_article: Tag) -> Tag | None:
    """Walk up to the enclosing exercise-like article that is not itself a task."""
    for ancestor in task_article.parents:
        if not isinstance(ancestor, Tag):
            continue
        if ancestor.name != "article":
            continue
        classes = ancestor.get("class") or []
        if "exercise-like" in classes and "task" not in classes:
            return ancestor
    return None


# ---------------------------------------------------------------------------
# Building the render tree
# ---------------------------------------------------------------------------

def _clone(tag: Tag) -> Tag:
    """Deep clone via reparse, bs4's copy doesn't reliably deep-copy."""
    return BeautifulSoup(str(tag), "html.parser").find(tag.name)


def _build_render_root(article: Tag) -> Tag:
    """
    For a task, clone the parent exercise but remove sibling tasks so only
    this task's content (plus shared context) remains. For a standalone
    exercise, just clone it.
    """
    if not _is_task(article):
        return _clone(article)

    parent = _find_parent_exercise(article)
    if parent is None:
        # Common and correct for PreTeXt <exploration> (and similar)
        # tasks, which have no enclosing <article class="exercise-like">
        # wrapper. The standalone-render fallback produces correct
        # output, so this is DEBUG, not a warning, at WARNING it fires
        # ~195x per ULA sync and drowns out real warnings.
        log.debug(
            "Task %r has no exercise-like parent (likely PreTeXt "
            "<exploration> or similar); rendering standalone",
            article.get("id"),
        )
        return _clone(article)

    task_id = article.get("id")
    parent_clone = _clone(parent)
    # Remove every sibling task except this one.
    for t in parent_clone.find_all("article", class_="task"):
        if t.get("id") != task_id:
            t.decompose()
    return parent_clone


def _strip_reviewer_hidden(root: Tag) -> None:
    """Remove answers and solutions so coders don't see them."""
    # Primary: the <div class="solutions"> wrapper covers all of them.
    for el in root.select("div.solutions"):
        el.decompose()
    # Defensive: anything else marked solution-like.
    for el in root.select(".solution-like"):
        el.decompose()


def _strip_chrome(root: Tag) -> None:
    """Remove visual noise that doesn't belong in a preview."""
    for el in root.select("div.autopermalink"):
        el.decompose()


# ---------------------------------------------------------------------------
# Standalone HTML wrapping
# ---------------------------------------------------------------------------

def _wrap_standalone(
    content: Tag,
    head: Tag,
    latex_macros: Tag | None = None,
) -> str:
    """
    Produce a complete HTML document. The <head> is cloned from the section
    page verbatim. If a latex-macros block is provided, it is injected at
    the top of the body so MathJax registers PreTeXt's custom commands
    before processing the question content.
    """
    head_clone = BeautifulSoup(str(head), "html.parser").find("head")
    macros_html = str(latex_macros) if latex_macros is not None else ""
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        f"{head_clone}\n"
        '<body class="pretext">\n'
        f"{macros_html}\n"
        '<main class="ptx-main">\n'
        '<div class="ptx-content">\n'
        f"{content}\n"
        "</div>\n"
        "</main>\n"
        "</body>\n"
        "</html>\n"
    )


# ---------------------------------------------------------------------------
# Metadata extraction
# ---------------------------------------------------------------------------

def _infer_exercise_type(article: Tag) -> str:
    """Read the first non-'exercise-like' class as the type marker."""
    classes = article.get("class") or []
    for c in classes:
        if c not in ("exercise-like",):
            return c
    return "unknown"


# PreTeXt environments worth recording when a task has no exercise-like
# parent (e.g. <exploration> children render standalone). Ordered by the
# priority to report when an ancestor carries more than one of them.
_RECOGNIZED_ENVIRONMENTS = (
    "exploration",
    "investigation",
    "activity",
    "project",
    "reading-question",
    "exercise",
)


def _infer_containing_environment(article: Tag) -> str:
    """
    Identify the enclosing PreTeXt environment for a task whose
    _find_parent_exercise walk found no exercise-like parent.

    Walks ancestors (nearest first) and returns the first recognized
    environment class on an <article>, <section>, or <div>; 'unknown' if
    none is found.
    """
    for ancestor in article.parents:
        if not isinstance(ancestor, Tag):
            continue
        if ancestor.name not in ("article", "section", "div"):
            continue
        classes = ancestor.get("class") or []
        for env in _RECOGNIZED_ENVIRONMENTS:
            if env in classes:
                return env
    return "unknown"


def _collect_metadata(article: Tag, parent: Tag | None) -> dict:
    """Lightweight structural facts for the metadata_json column."""
    # Containing environment:
    #   - a task's real exercise-like parent's inferred type, if one exists;
    #   - else, for a task with no such parent (e.g. PreTeXt <exploration>
    #     children that render standalone), the nearest recognized
    #     environment ancestor;
    #   - else, for a standalone exercise, its own inferred type.
    if parent is not None:
        containing_environment = _infer_exercise_type(parent)
    elif _is_task(article):
        containing_environment = _infer_containing_environment(article)
    else:
        containing_environment = _infer_exercise_type(article)
    return {
        "exercise_type": _infer_exercise_type(article),
        "containing_environment": containing_environment,
        "has_parent_context": parent is not None,
        "has_hints": bool(article.select(".hint")),
        "n_sibling_tasks": (
            len(parent.find_all("article", class_="task")) if parent else 0
        ),
        "heading_text": (
            article.find(class_="heading").get_text(strip=True)
            if article.find(class_="heading") else None
        ),
    }


# ---------------------------------------------------------------------------
# Section-level processing
# ---------------------------------------------------------------------------

def extract_from_section(section_path: Path) -> Iterator[ExtractedQuestion]:
    """Yield ExtractedQuestion for every short-answer question in one section file."""
    soup = BeautifulSoup(section_path.read_text(encoding="utf-8"), "html.parser")

    # Positive filter: a real PreTeXt section page has BOTH
    # `<body class="pretext">` AND a `<div id="latex-macros">` block.
    # Auxiliary files PreTeXt emits alongside section pages, single-
    # question `_preview_*.html` previews, knowl pop-up files under
    # `knowl/`, index helpers, contain shortanswer elements but fail
    # one or both signals. Extracting from them produces broken
    # standalone HTML (missing macros) or, worse, crashes the DB INSERT
    # because their task wrappers have no `id` attribute. A single
    # positive check covers every current and future class of auxiliary
    # file; the cost is that a textbook legitimately without custom
    # macros wouldn't be extractable, a rare edge case worth
    # revisiting only if it ever arises.
    body = soup.find("body")
    has_pretext_body = (
        body is not None and "pretext" in (body.get("class") or [])
    )
    latex_macros = _find_latex_macros(soup)
    if not (has_pretext_body and latex_macros is not None):
        # DEBUG only when the file contains shortanswer(s), the skip is
        # expected for auxiliary files now, not a tripwire. Files with
        # no shortanswers (chapter intros, indexes, etc.) are skipped
        # silently to avoid log noise.
        has_shortanswer = (
            soup.find("div", attrs={"data-component": "shortanswer"}) is not None
        )
        if has_shortanswer:
            log.debug(
                "Skipping %s: contains shortanswer(s) but is not a real "
                "PreTeXt section page (body class or latex-macros missing).",
                section_path,
            )
        return

    head = soup.find("head")
    if head is None:
        log.warning("%s has no <head>; skipping", section_path)
        return

    section_id = section_path.stem  # e.g. 'sec-pivots'
    section_title = (soup.find("title").get_text(strip=True) if soup.find("title") else section_id)

    for article, question_id in _find_question_units(soup):
        is_task = _is_task(article)
        parent = _find_parent_exercise(article) if is_task else None
        parent_xml_id = parent.get("id") if parent else None

        render_root = _build_render_root(article)
        _strip_reviewer_hidden(render_root)
        _strip_chrome(render_root)

        # Hash before wrapping, so changes to the head don't churn all rows.
        source_hash = hashlib.sha256(str(render_root).encode("utf-8")).hexdigest()

        standalone_html = _wrap_standalone(render_root, head, latex_macros)

        yield ExtractedQuestion(
            question_id=question_id,
            xml_id=article.get("id"),
            parent_xml_id=parent_xml_id,
            section_id=section_id,
            section_title=section_title,
            exercise_type="task" if is_task else _infer_exercise_type(article),
            standalone_html=standalone_html,
            source_hash=source_hash,
            metadata=_collect_metadata(article, parent),
        )

def _find_latex_macros(soup: BeautifulSoup) -> Tag | None:
    r"""
    Find the <div id="latex-macros"> block that registers PreTeXt's custom
    LaTeX commands with MathJax. Must be present in the body for matrix
    rendering, \amp, \lt, \gt, and similar to work.
    """
    return soup.find("div", id="latex-macros")


# ---------------------------------------------------------------------------
# Orchestrator: walk a built textbook, write preview files, upsert rows
# ---------------------------------------------------------------------------

def _lookup_book_xml_id(conn: sqlite3.Connection, textbook_id: int) -> str:
    row = conn.execute(
        "SELECT book_xml_id FROM textbooks WHERE textbook_id = ?",
        (textbook_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"No textbooks row for textbook_id={textbook_id}")
    return row["book_xml_id"]


# A SQL LIKE escape character. '!' was chosen because it does not appear in
# any expected book_xml_id and is not a LIKE metacharacter itself.
_LIKE_ESCAPE = "!"


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone() is not None


def _escape_like(s: str) -> str:
    """Escape LIKE metacharacters so the string matches literally under ESCAPE '!'."""
    return (
        s.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", _LIKE_ESCAPE + "%")
        .replace("_", _LIKE_ESCAPE + "_")
    )


def sync_questions(
    conn: sqlite3.Connection,
    build_dir: Path,
    output_dir: Path,
    textbook_id: int,
) -> SyncResult:
    """
    Walk a built PreTeXt textbook tree and cache one standalone HTML preview
    per short-answer question. Idempotent via source_hash: a question whose
    rendered chunk hasn't changed produces no file write and no DB write and
    is counted as `unchanged`.

    Always extracts the full catalog (no `only_responded` parameter, see
    Phase 3.5 design decisions in proteus_ui/proteus_ui_guide.md).

    Files are written to `output_dir/<textbook_id>/<xml_id>.html`. The
    `preview_path` column records the path relative to `output_dir`.
    If `build_dir/_static` exists, it is mirrored to
    `output_dir/<textbook_id>/_static/` so the cloned `<head>`'s relative
    asset URLs resolve when the file is served. Callers point
    `output_dir` at Streamlit's static directory
    (`proteus_ui/streamlit_app/static/previews`) to wire previews into
    the UI; the path-to-URL helper lives in
    `proteus_ui.services.questions_service`.

    Per-question, the DB write happens before the file write so that a
    failed INSERT/UPDATE can't leave an orphan preview file on disk: an
    exception aborts the loop before commit, the implicit transaction rolls
    back, and no file is written for the failing question.

    If an existing `questions` row's `textbook_id` does not match the
    requested `textbook_id`, the function raises `ValueError` rather than
    silently re-associating the row, the PK is `question_id` alone, so
    cross-textbook collisions would otherwise corrupt the catalog.

    The two reconciliation lists in the returned SyncResult are asymmetric:

    - `missing`: question_ids that appear in `responses` (scoped to this
      textbook by the `<book_xml_id>__` prefix on question_id) but have no
      row in `questions`. This is a real data-quality warning (xml:id typo,
      wrong book version, mangled export) and is logged at WARNING.

    - `orphan`: question_ids in `questions` for this textbook with no
      matching response. This is the normal, expected default, never logged
      as a warning. Phase 3.5 explicitly says do not treat this as a
      problem; it lives here as an informational count.
    """
    book_xml_id = _lookup_book_xml_id(conn, textbook_id)

    target_dir = output_dir / str(textbook_id)
    target_dir.mkdir(parents=True, exist_ok=True)

    extracted_count = 0
    updated_count = 0
    unchanged_count = 0

    for section_path in sorted(build_dir.rglob("*.html")):
        for q in extract_from_section(section_path):
            existing = conn.execute(
                "SELECT textbook_id, source_hash FROM questions "
                "WHERE question_id = ?",
                (q.question_id,),
            ).fetchone()

            if (
                existing is not None
                and existing["textbook_id"] is not None
                and existing["textbook_id"] != textbook_id
            ):
                raise ValueError(
                    f"question_id {q.question_id!r} already exists under "
                    f"textbook_id={existing['textbook_id']} but sync_questions "
                    f"was called with textbook_id={textbook_id}; refusing to "
                    f"silently re-associate."
                )

            preview_rel = f"{textbook_id}/{q.xml_id}.html"
            metadata_json = json.dumps(q.metadata, sort_keys=True)
            preview_path = target_dir / f"{q.xml_id}.html"

            if existing is None:
                conn.execute(
                    "INSERT INTO questions ("
                    "question_id, textbook_id, xml_id, parent_xml_id, "
                    "section_id, section_title, preview_path, metadata_json, "
                    "source, source_hash"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        q.question_id,
                        textbook_id,
                        q.xml_id,
                        q.parent_xml_id,
                        q.section_id,
                        q.section_title,
                        preview_rel,
                        metadata_json,
                        "auto_extracted",
                        q.source_hash,
                    ),
                )
                preview_path.write_text(q.standalone_html, encoding="utf-8")
                extracted_count += 1
            elif existing["textbook_id"] is None:
                # Adopt an unassociated catalog row (CD-2): attach the real
                # textbook, xml_id, and preview, upgrading source from
                # 'catalog' to 'auto_extracted' (SP-5 flip-to-analyzable path).
                conn.execute(
                    "UPDATE questions SET "
                    "textbook_id = ?, "
                    "xml_id = ?, "
                    "parent_xml_id = ?, "
                    "section_id = ?, "
                    "section_title = ?, "
                    "preview_path = ?, "
                    "metadata_json = ?, "
                    "source = 'auto_extracted', "
                    "source_hash = ?, "
                    "extracted_at = datetime('now') "
                    "WHERE question_id = ?",
                    (
                        textbook_id,
                        q.xml_id,
                        q.parent_xml_id,
                        q.section_id,
                        q.section_title,
                        preview_rel,
                        metadata_json,
                        q.source_hash,
                        q.question_id,
                    ),
                )
                preview_path.write_text(q.standalone_html, encoding="utf-8")
                updated_count += 1
            elif existing["source_hash"] == q.source_hash and preview_path.is_file():
                unchanged_count += 1
            else:
                # A matching hash whose preview file is gone from disk or is
                # not a regular file (e.g. output_dir wiped between syncs)
                # falls through here and is counted as `updated`, a
                # degenerate "needs writing" case rather than a silent
                # `unchanged` that writes nothing.
                conn.execute(
                    "UPDATE questions SET "
                    "xml_id = ?, "
                    "parent_xml_id = ?, "
                    "section_id = ?, "
                    "section_title = ?, "
                    "preview_path = ?, "
                    "metadata_json = ?, "
                    "source_hash = ?, "
                    "extracted_at = datetime('now') "
                    "WHERE question_id = ?",
                    (
                        q.xml_id,
                        q.parent_xml_id,
                        q.section_id,
                        q.section_title,
                        preview_rel,
                        metadata_json,
                        q.source_hash,
                        q.question_id,
                    ),
                )
                preview_path.write_text(q.standalone_html, encoding="utf-8")
                updated_count += 1

    conn.commit()

    # The cloned <head> in each standalone preview references PreTeXt's
    # asset bundle with relative URLs like `_static/pretext.css` and
    # `_static/mathjax/...`. For those to resolve when the file is
    # served from `output_dir/<textbook_id>/<xml>.html`, `_static/` must
    # exist as a sibling. Mirror it here so a single sync_questions call
    # produces a self-contained, browser-loadable preview tree.
    #
    # Idempotent via `dirs_exist_ok=True`. PreTeXt's asset directory is
    # not large; a per-textbook copy is acceptable and keeps multiple
    # textbook versions self-contained.
    src_static = build_dir / "_static"
    if src_static.is_dir():
        dst_static = target_dir / "_static"
        # The DB rows and per-question HTML have already been committed
        # and written by the loop above; the asset mirror is auxiliary.
        # An OSError here (permissions, disk full, transient I/O) should
        # not invalidate the otherwise-successful sync, the catalog is
        # intact, and a re-run will pick up where we left off thanks to
        # dirs_exist_ok. Log at WARNING so the failure is visible; the
        # verification procedure's checkpoint 2 will surface broken
        # rendering if assets are actually missing in the browser.
        try:
            shutil.copytree(src_static, dst_static, dirs_exist_ok=True)
            log.info("Mirrored %s -> %s", src_static, dst_static)
        except OSError as exc:
            log.warning(
                "Failed to mirror %s -> %s (%s); the catalog and preview "
                "HTML are intact but theme/CSS and MathJax assets may not "
                "render until a successful re-run.",
                src_static, dst_static, exc,
            )
    else:
        log.warning(
            "No _static/ directory under %s; preview pages will load but "
            "theme/CSS and MathJax assets will be missing.",
            build_dir,
        )

    # Scope responses to this textbook with a SQL prefix filter so SQLite
    # can use the existing index on responses.question_id instead of
    # materializing every distinct question_id. The responses table belongs
    # to the Short Answer service: in a tree without it (the public export,
    # cc-task-public-export PR 1) nothing has been answered as far as this
    # sync can tell, so every catalogued question is "not yet answered" and
    # nothing is "answered but not catalogued".
    prefix_pattern = _escape_like(f"{book_xml_id}__") + "%"
    if _table_exists(conn, "responses"):
        responded_in_textbook = {
            row["question_id"]
            for row in conn.execute(
                "SELECT DISTINCT question_id FROM responses "
                "WHERE question_id LIKE ? ESCAPE ?",
                (prefix_pattern, _LIKE_ESCAPE),
            )
        }
    else:
        responded_in_textbook = set()
    catalogued = {
        row["question_id"]
        for row in conn.execute(
            "SELECT question_id FROM questions WHERE textbook_id = ?",
            (textbook_id,),
        )
    }
    missing = sorted(responded_in_textbook - catalogued)
    orphan = sorted(catalogued - responded_in_textbook)

    log.info(
        "sync_questions complete for textbook_id=%s: %d extracted, %d updated, %d unchanged",
        textbook_id, extracted_count, updated_count, unchanged_count,
    )
    if missing:
        log.warning(
            "%d response question_id(s) have no matching entry in the build: %s",
            len(missing), missing,
        )
    if orphan:
        log.info(
            "%d catalogued question(s) have no response yet (expected default)",
            len(orphan),
        )

    return SyncResult(
        extracted=extracted_count,
        updated=updated_count,
        unchanged=unchanged_count,
        missing=missing,
        orphan=orphan,
    )