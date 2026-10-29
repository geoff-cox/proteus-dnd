"""
Question-source sync (plan §2.6; the A1.6 slice required by B2.2).

Walks a PreTeXt *source* tree (``*.ptx``, or the older ``*.xml`` extension some
books still use) once and makes question source first-class core infrastructure:

1. **Filesystem cache** — ``<output_dir>/<book_xml_id>/<question_id>.ptx``:
   one PreTeXt snippet per question, human-inspectable, safe to commit
   alongside the raw exports.
2. **Database authority** — ``questions.source_xml`` stores the same snippet
   so every derived artifact (board spec, key, preview) carries provenance
   back to the exact source it was built from, and ``questions.title`` stores
   the snippet's authored ``<title>`` as verbatim inner XML (FU-3; NULL when
   the unit has no title). Re-sync is idempotent on the snippet text and the
   title.
3. **Dispatch** — each registered service's ``source_handler`` is invoked for
   the questions whose ``question_type`` it claims (the descriptor's
   ``question_types``); the union of claimed tags is also what makes an
   element a question during the walk.

A question is recognized either by a *claimed element tag* (e.g. ``<cardsort>``)
or, for short answer, by PreTeXt *structure* — an ``<exercise>`` whose
``<statement>`` is followed by a ``<response>`` (there is no ``<shortanswer>``
source tag in the wild; ``<response/>`` is the marker, a direct child of the
exercise). Its unit is the nearest ancestor carrying an ``xml:id`` (or a
Runestone ``label`` attribute) — the exercise itself for a short answer — whose
id names the question: ``question_id = <book_xml_id>__<xml_id>`` — exactly the
Problem Name the DataShop export logs.

A **multi-part** question (a container with ``<task>`` children — an
``<exercise>``, ``<exploration>``, ``<project>``, …) is several questions:
Runestone logs each ``<task>`` under its own label. Each task with a ``<response>``
becomes its own short-answer question — the container's ``<introduction>`` merged
ahead of the task's ``<statement>`` so the preview stands alone, and authored
``<solution>``/``<answer>`` dropped — while a differently-typed task (e.g. one
holding a ``<cardsort>``) is named by that task's label through the tag path.

Distinct from the legacy HTML-preview sync (``proteus.sync.questions``),
which walks a *built* tree and owns ``preview_path``/``source_hash``; the two
write disjoint columns, so running both is safe. Unifying previews onto this
path is the remaining piece of A1.6 and stays TODO.

Limitations, recorded: snippets are re-serialized by ElementTree (stable and
deterministic, but not byte-verbatim — comments are dropped), and a source
file using unresolved custom entities or xi:include indirection fails to
parse and is skipped with a warning. Both are acceptable for the question
files this walks today; revisit if a real book trips them.
"""

from __future__ import annotations

import copy
import json
import logging
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = Path("data") / "question_sources"

_XML_ID = "{http://www.w3.org/XML/1998/namespace}id"


@dataclass(frozen=True)
class ExtractedSource:
    """One question's source snippet, found during the walk."""

    question_id: str
    xml_id: str
    question_type: str
    section_id: str  # the source file's stem, e.g. 'sec-bases'
    source_xml: str
    title: str | None  # the unit's <title> as verbatim inner XML (FU-3)


@dataclass(frozen=True)
class SourceSyncResult:
    extracted: int = 0  # new questions rows
    updated: int = 0  # rows whose snippet or type changed
    unchanged: int = 0
    # rows whose snippet was unchanged but whose stored title was missing/stale
    # — the migration-0014 backfill path (FU-3)
    titles_backfilled: int = 0
    skipped_files: tuple[str, ...] = ()  # files that failed to parse
    # service name -> handler status -> count (e.g. {'dnd': {'created': 3}})
    handled: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Walking the source tree
# ---------------------------------------------------------------------------


def _element_id(element: ET.Element) -> str | None:
    """The question-naming id of an element: xml:id, or Runestone label."""
    return element.get(_XML_ID) or element.get("label")


def _has_direct_response(element: ET.Element) -> bool:
    """Whether ``element`` has a direct ``<response>`` child — the PreTeXt
    short-answer marker. Direct (not descendant) is what distinguishes it from a
    ``<cardsort>``, whose ``<response>`` elements are nested inside ``<match>``."""
    return any(child.tag == "response" for child in element)


def _is_shortanswer_exercise(element: ET.Element) -> bool:
    """Whether ``element`` is a single PreTeXt short-answer question.

    PreTeXt has no ``<shortanswer>`` source tag: a free-response/short-answer
    question is an ``<exercise>`` whose ``<statement>`` is followed by a
    ``<response>`` (usually the empty ``<response/>``). The exercise itself is
    the naming unit. Multi-part exercises (``<task>`` children) are handled
    separately — see :func:`_synthesize_task_question`.
    """
    return element.tag == "exercise" and _has_direct_response(element)


_TASK_NON_QUESTION = frozenset(
    {"statement", "title", "solution", "answer", "hint"}
)


def _unit_title(unit: ET.Element) -> str | None:
    """The verbatim inner XML of ``unit``'s direct ``<title>`` child, or None.

    Inner XML — text plus serialized child markup, tails included — not
    ``itertext``: the FU-3 decision stores a math-bearing title exactly as
    authored (e.g. ``Multiplying by <m>A</m>``), leaving readable degradation
    to the display layer's tag-stripper. An absent or empty title is ``None``.
    """
    title = unit.find("title")
    if title is None:
        return None
    parts = [title.text or ""]
    parts.extend(ET.tostring(child, encoding="unicode") for child in title)
    return "".join(parts).strip() or None


def _synthesize_task_question(container: ET.Element, task: ET.Element) -> str:
    """Build a standalone ``<exercise>`` snippet for one ``<task>`` of a
    multi-part PreTeXt question.

    Runestone logs each ``<task>`` under its own label, so a multi-part container
    (an ``<exercise>``, ``<exploration>``, …) is really several questions. Each
    becomes a self-contained exercise: the container's ``<introduction>`` (shared
    context, e.g. a matrix the parts refer to) is merged ahead of the task's own
    ``<statement>`` so the preview stands alone, the task keeps only its
    question element(s) (``<response/>``, …) — authored ``<solution>`` /
    ``<answer>`` / ``<hint>`` are dropped so a coder never sees the answer — and
    the title is the task's own or the container's.

    The snippet is otherwise a faithful copy of the source: the ``component``
    attribute (e.g. ``component="proteus-playground"``) is *preserved* here and
    stripped for buildability at the preview-assembly seam
    (:func:`proteus.core.preview._strip_components`), the same path a non-task
    short-answer exercise takes. Keeping that stripping in one place means the
    stored ``source_xml`` stays a true copy of the source for every SA question,
    task or not.
    """
    new_ex = ET.Element("exercise")
    label = _element_id(task)
    if label is not None:
        new_ex.set("label", label)

    title = task.find("title")
    if title is None:
        title = container.find("title")
    if title is not None:
        new_ex.append(copy.deepcopy(title))

    statement = ET.SubElement(new_ex, "statement")
    introduction = container.find("introduction")
    if introduction is not None:
        for child in list(introduction):
            statement.append(copy.deepcopy(child))
    task_statement = task.find("statement")
    if task_statement is not None:
        for child in list(task_statement):
            statement.append(copy.deepcopy(child))

    # Keep only the question input element(s) after the statement (the
    # <response/>, …); drop the statement/title (handled above) and any
    # solution/answer/hint so the preview never carries the answer.
    for child in list(task):
        if child.tag not in _TASK_NON_QUESTION:
            new_ex.append(copy.deepcopy(child))

    return ET.tostring(new_ex, encoding="unicode")


def extract_from_file(
    path: Path, claimed_types: frozenset[str]
) -> tuple[list[ExtractedSource], bool]:
    """
    Extract every claimed question element from one PreTeXt source file
    (``.ptx`` or ``.xml`` — the suffix is irrelevant here; this parses content).
    Returns ``(extracted, parsed_ok)``; a file that fails to parse yields
    ``([], False)`` and a warning rather than aborting the walk.

    question_id here is the bare xml_id — the caller prefixes the book.
    """
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        log.warning("Skipping %s: not parseable as XML (%s)", path, exc)
        return [], False

    found: list[ExtractedSource] = []
    seen_ids: set[str] = set()
    sa_claimed = "shortanswer" in claimed_types

    def _add(
        xml_id: str | None,
        question_type: str,
        source_xml: str | None,
        title: str | None,
    ) -> None:
        """Record one question, deduped on its id; warn and skip a question that
        has no identifiable unit (no xml:id/label)."""
        if xml_id is None or source_xml is None:
            log.warning(
                "%s: found a %s question element with no identified unit "
                "(xml:id or label); skipping",
                path, question_type,
            )
            return
        if xml_id in seen_ids:
            log.warning(
                "%s: id %r contains more than one question element; "
                "keeping the first", path, xml_id,
            )
            return
        seen_ids.add(xml_id)
        found.append(
            ExtractedSource(
                question_id=xml_id,  # prefixed by the caller
                xml_id=xml_id,
                question_type=question_type,
                section_id=path.stem,
                source_xml=source_xml,
                title=title,
            )
        )

    # Depth-first with an explicit ancestor chain (ElementTree has no parent
    # pointers); `unit` is the nearest identified ancestor of a claimed element.
    stack: list[tuple[ET.Element, list[ET.Element]]] = [(root, [root])]
    while stack:
        element, chain = stack.pop()

        # 1. A claimed element tag (e.g. <cardsort>): the unit is its nearest
        #    identified ancestor — the exercise, or the <task> for a multi-part
        #    exercise, which is what names the question.
        if element.tag in claimed_types:
            unit = next(
                (a for a in reversed(chain) if _element_id(a) is not None), None
            )
            _add(
                _element_id(unit) if unit is not None else None,
                element.tag,
                ET.tostring(unit, encoding="unicode") if unit is not None else None,
                _unit_title(unit) if unit is not None else None,
            )
            continue

        # 2. A single short-answer exercise: <exercise> with a direct <response>.
        if sa_claimed and _is_shortanswer_exercise(element):
            _add(
                _element_id(element),
                "shortanswer",
                ET.tostring(element, encoding="unicode"),
                _unit_title(element),
            )
            continue

        # 3. A <task> with a direct <response> is its own short-answer question,
        #    named by the task's label, with its container's <introduction>
        #    merged in. <task> can appear in any container (<exercise>,
        #    <exploration>, <project>, …), so this keys off the task element, not
        #    the parent's tag. A differently-typed task (e.g. one holding a
        #    <cardsort>) is named by its task label through the tag path (case 1)
        #    on descent; a poll/<query> task claims no serviced type and is
        #    skipped.
        if sa_claimed and element.tag == "task" and _has_direct_response(element):
            container = chain[-2] if len(chain) >= 2 else element
            _add(
                _element_id(element),
                "shortanswer",
                _synthesize_task_question(container, element),
                # The task's own title, else the container's — mirroring
                # _synthesize_task_question exactly, so the stored title always
                # matches the <title> the synthesized snippet embeds. The
                # fallback keys off the title *element* being absent, not its
                # content: an authored-but-empty task <title/> is embedded (and
                # stored as None), never swapped for the container's (PR #159
                # review).
                _unit_title(element)
                if element.find("title") is not None
                else _unit_title(container),
            )
            continue

        # reversed() keeps document order once popped off the stack
        for child in reversed(list(element)):
            stack.append((child, chain + [child]))
    return found, True


# ---------------------------------------------------------------------------
# Sync: cache files + questions rows + handler dispatch
# ---------------------------------------------------------------------------


def _require_title_column(conn: sqlite3.Connection) -> None:
    """Fail fast on a database that predates migration 0014: the upsert writes
    ``questions.title``, and a bare OperationalError mid-walk (or after a
    clone) would be a worse message — same rationale as the ``last_synced_at``
    check in :func:`sync_book_sources` (B4.13 review)."""
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(questions)")}
    if "title" not in columns:
        raise ValueError(
            "this database predates the questions.title column; run "
            "`proteus migrate` before syncing sources"
        )


def _lookup_book_xml_id(conn: sqlite3.Connection, textbook_id: int) -> str:
    row = conn.execute(
        "SELECT book_xml_id FROM textbooks WHERE textbook_id = ?",
        (textbook_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"No textbooks row for textbook_id={textbook_id}")
    return row["book_xml_id"]


def _merged_metadata(existing_json: str | None, question_type: str) -> str:
    """metadata_json keeps question_type too — 0006 back-compat, one release."""
    metadata: dict = {}
    if existing_json:
        try:
            metadata = json.loads(existing_json)
        except json.JSONDecodeError:
            metadata = {}
    metadata["question_type"] = question_type
    return json.dumps(metadata, sort_keys=True)


def sync_question_sources(
    conn: sqlite3.Connection,
    source_dir: Path,
    textbook_id: int,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    services: tuple | None = None,
) -> SourceSyncResult:
    """
    Walk `source_dir` for question source, write the filesystem cache and
    ``questions`` rows, then dispatch each service's source_handler over the
    question types it claims.

    `services` defaults to the registry's available services; tests inject
    fakes. Idempotent: a question whose snippet and type are unchanged
    produces no DB write and is counted `unchanged`.

    A question id declared by more than one file in the walk keeps the first
    (sorted) file's content; each later re-declaration is skipped with a
    warning naming both files, mirroring extract_from_file's intra-file
    keep-the-first rule (BUG-03).

    Raises ValueError if an extracted question_id already belongs to a
    different textbook (same refusal as the preview sync).
    """
    if services is None:
        # Imported here, not at module load: core reaches services only
        # through the registry, and only when actually syncing.
        from proteus.core import registry

        services = registry.available_services()

    claimed_types = frozenset().union(*(s.question_types for s in services))
    book_xml_id = _lookup_book_xml_id(conn, textbook_id)
    target_dir = Path(output_dir) / book_xml_id
    extracted_count = 0
    updated_count = 0
    unchanged_count = 0
    titles_backfilled_count = 0
    skipped_files: list[str] = []

    if claimed_types:
        _require_title_column(conn)
        # PreTeXt source ships as either `.ptx` or the older `.xml` extension
        # (e.g. the ula book's section*.xml); walk both, or a `.xml`-only book
        # extracts nothing. extract_from_file keys off content, not the suffix,
        # and the clone path (sync_book_sources) routes through here too, so this
        # one glob fixes --book and --source-dir alike. The snippet *output*
        # stays `.ptx` (re-serialized PreTeXt) regardless of the input extension.
        source_paths = sorted(
            path
            for pattern in ("*.ptx", "*.xml")
            for path in Path(source_dir).rglob(pattern)
        )
        seen_ids: dict[str, Path] = {}
        for path in source_paths:
            questions, parsed_ok = extract_from_file(path, claimed_types)
            if not parsed_ok:
                skipped_files.append(str(path))
                continue
            for q in questions:
                question_id = f"{book_xml_id}__{q.xml_id}"
                # Cross-file duplicate of an id already written this walk:
                # keep the first, mirroring the intra-file rule above —
                # last-file-wins would silently flip the stored content (and
                # churn `updated` counts) on every sync (BUG-03).
                first_file = seen_ids.get(question_id)
                if first_file is not None:
                    log.warning(
                        "%s: id %r was already extracted from %s in this "
                        "sync; keeping the first",
                        path, q.xml_id, first_file,
                    )
                    continue
                seen_ids[question_id] = path
                outcome = _upsert_question(
                    conn, q, question_id=question_id, textbook_id=textbook_id
                )
                if outcome == "extracted":
                    extracted_count += 1
                elif outcome == "updated":
                    updated_count += 1
                elif outcome == "title_backfilled":
                    titles_backfilled_count += 1
                else:
                    unchanged_count += 1
                cache_path = target_dir / f"{question_id}.ptx"
                # A title backfill leaves the snippet itself unchanged, so the
                # cache file needs no rewrite either.
                if (
                    outcome not in ("unchanged", "title_backfilled")
                    or not cache_path.is_file()
                ):
                    target_dir.mkdir(parents=True, exist_ok=True)
                    cache_path.write_text(q.source_xml, encoding="utf-8")
        conn.commit()

    handled = dispatch_source_handlers(
        conn, services=services, textbook_id=textbook_id
    )

    log.info(
        "sync_question_sources complete for textbook_id=%s: %d extracted, "
        "%d updated, %d unchanged, %d title(s) backfilled, %d file(s) skipped",
        textbook_id, extracted_count, updated_count, unchanged_count,
        titles_backfilled_count, len(skipped_files),
    )
    return SourceSyncResult(
        extracted=extracted_count,
        updated=updated_count,
        unchanged=unchanged_count,
        titles_backfilled=titles_backfilled_count,
        skipped_files=tuple(skipped_files),
        handled=handled,
    )


_XI_INCLUDE = "{http://www.w3.org/2001/XInclude}include"


def _resolve_xinclude(
    base_dir: Path, elem: ET.Element
) -> tuple[ET.Element, Path] | None:
    """Parse an ``<xi:include>``'s target relative to ``base_dir`` → ``(root,
    target_dir)``, or ``None`` if it has no href or cannot be read/parsed."""
    href = elem.get("href")
    if not href:
        return None
    target = base_dir / href
    try:
        return ET.parse(target).getroot(), target.parent
    except (ET.ParseError, OSError):
        return None


def _child_element(
    parent: ET.Element,
    base_dir: Path,
    want: str,
    *,
    stop_at: tuple[str, ...] = (),
) -> tuple[ET.Element, Path] | None:
    """
    The first ``want`` child of ``parent`` — a literal child, or one pulled in via
    ``<xi:include>`` (resolved relative to ``base_dir``: the common split where
    authors keep ``<docinfo>`` in ``bookinfo.ptx`` or the ``<macros>`` in their
    own file). Returns ``(element, base_dir')`` where ``base_dir'`` is where a
    *further* include nested inside that element resolves from — the included
    file's own directory when it arrived via xi:include, else ``base_dir``
    unchanged. Scanning stops if a ``stop_at`` sibling is reached first (a PreTeXt
    ``<docinfo>`` must precede ``<book>``/``<article>``).
    """
    for child in parent:
        if child.tag == want:
            return child, base_dir
        if child.tag == _XI_INCLUDE:
            resolved = _resolve_xinclude(base_dir, child)
            if resolved is not None and resolved[0].tag == want:
                return resolved
        elif child.tag in stop_at:
            return None
    return None


def _extract_book_macros(source_dir: Path) -> str | None:
    """
    Return the book's ``<docinfo><macros>`` element (serialized) from its main
    PreTeXt file, or ``None``.

    Only the main/top file has a ``<pretext>`` root (chapter/section files are
    fragment-rooted). Its ``<docinfo>`` — and the ``<macros>`` within it — may be
    literal or pulled in via ``<xi:include>``; **most authors keep ``<docinfo>``
    in a separate ``bookinfo.ptx`` and import it**, so both levels of include are
    followed. The standalone question-preview wrapper injects the macros so math
    that uses them renders (PR #69 follow-up, issue 2). Best-effort: a
    missing/unparseable file, an unresolvable include, or a book that declares no
    macros all yield ``None`` — macros enrich the preview but never gate a sync
    (CLAUDE.md rule 9).
    """
    preferred = (source_dir / "source" / "main.ptx", source_dir / "main.ptx")
    seen: set[Path] = set()
    candidates: list[Path] = []
    for p in preferred:
        if p.is_file():
            candidates.append(p)
            seen.add(p)
    for p in sorted(source_dir.rglob("*.ptx")) + sorted(source_dir.rglob("*.xml")):
        if p not in seen:
            candidates.append(p)
            seen.add(p)

    for path in candidates:
        try:
            root = ET.parse(path).getroot()
        except (ET.ParseError, OSError):
            continue
        if root.tag != "pretext":
            continue
        # The main file. <docinfo> precedes <book>/<article> and may be literal
        # or xi:included; the <macros> inside it likewise.
        found = _child_element(
            root, path.parent, "docinfo", stop_at=("book", "article")
        )
        if found is None:
            continue  # not the authoritative main file (no <docinfo>); keep looking
        docinfo, docinfo_dir = found
        macros = _child_element(docinfo, docinfo_dir, "macros")
        if macros is None:
            return None  # main file found, but it declares no macros
        text = ET.tostring(macros[0], encoding="unicode").strip()
        return text or None
    return None



def sync_book_sources(
    conn: sqlite3.Connection,
    book_xml_id: str,
    *,
    clone_root: Path | None = None,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    services: tuple | None = None,
    cloner=None,
) -> SourceSyncResult:
    """
    Clone a registered book's public repo and sync its question sources (SP-2).

    Looks the book up in the registry (SP-1), clones ``git_repo_url`` at the
    registered ref into ``clone_root/<book_xml_id>``, records the local clone
    path on ``textbooks.source_dir``, then runs :func:`sync_question_sources`
    over the clone. ``cloner`` is injectable (defaults to
    :func:`proteus.core.clone.clone_repo`) so the extract path can be tested
    without git.

    Raises ValueError if the book is not registered; the cloner raises
    :class:`proteus.core.clone.CloneError` if the clone itself fails.
    """
    from proteus.core import textbooks as textbooks_mod

    # Defense-in-depth: the id becomes a clone/cache directory name. Registration
    # already enforces this, but never build a path from an unchecked value.
    textbooks_mod.validate_book_xml_id(book_xml_id)

    row = textbooks_mod.get_textbook(conn, book_xml_id)
    if row is None:
        raise ValueError(
            f"textbook {book_xml_id!r} is not registered; run "
            "`proteus textbook add` first"
        )

    # Fail fast on a database that predates migration 0011: the last_synced_at
    # stamp below needs the column, and discovering that *after* the clone +
    # extract would waste the work and confuse the user (B4.13 review).
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(textbooks)")}
    if "last_synced_at" not in columns:
        raise ValueError(
            "this database predates the textbooks.last_synced_at column; run "
            "`proteus migrate` before syncing sources"
        )
    # Same fail-fast for questions.title (migration 0014, FU-3), also checked
    # here so a stale database is caught before the clone, not after it.
    _require_title_column(conn)

    if cloner is None:
        from proteus.core.clone import clone_repo as cloner
    if clone_root is None:
        from proteus.core.clone import DEFAULT_CLONE_ROOT

        clone_root = DEFAULT_CLONE_ROOT

    clone_path = cloner(
        row["git_repo_url"],
        Path(clone_root) / book_xml_id,
        git_ref=row["git_ref"],
    )
    # Record where the source now lives locally (the source_dir column has been
    # nullable-until-importer; this is the importer).
    conn.execute(
        "UPDATE textbooks SET source_dir = ? WHERE textbook_id = ?",
        (str(clone_path), row["textbook_id"]),
    )
    conn.commit()

    # Capture the book's LaTeX macros (<docinfo><macros>) so the standalone
    # question-preview wrapper can inject them (PR #69 follow-up, issue 2). Gated
    # on the column existing — `columns` was read above; a database that predates
    # migration 0012 simply skips this, since macros enrich the preview but must
    # never gate a sync. NULL is written when the book declares no macros.
    if "macros" in columns:
        conn.execute(
            "UPDATE textbooks SET macros = ? WHERE textbook_id = ?",
            (_extract_book_macros(Path(clone_path)), row["textbook_id"]),
        )
        conn.commit()

    result = sync_question_sources(
        conn,
        source_dir=Path(clone_path),
        textbook_id=row["textbook_id"],
        output_dir=output_dir,
        services=services,
    )
    # Record the successful sync time for the Textbooks screen (B4.13). Stamped
    # only after the extract succeeds; a clone/extract failure leaves it NULL.
    conn.execute(
        "UPDATE textbooks SET last_synced_at = datetime('now') WHERE textbook_id = ?",
        (row["textbook_id"],),
    )
    conn.commit()
    return result


def _upsert_question(
    conn: sqlite3.Connection,
    q: ExtractedSource,
    *,
    question_id: str,
    textbook_id: int,
) -> str:
    """Insert/update one questions row; returns extracted / updated /
    title_backfilled / unchanged."""
    existing = conn.execute(
        "SELECT textbook_id, question_type, source_xml, title, metadata_json "
        "FROM questions WHERE question_id = ?",
        (question_id,),
    ).fetchone()

    if existing is None:
        conn.execute(
            "INSERT INTO questions (question_id, textbook_id, xml_id, "
            "section_id, metadata_json, source, question_type, source_xml, "
            "title) "
            "VALUES (?, ?, ?, ?, ?, 'auto_extracted', ?, ?, ?)",
            (
                question_id,
                textbook_id,
                q.xml_id,
                q.section_id,
                _merged_metadata(None, q.question_type),
                q.question_type,
                q.source_xml,
                q.title,
            ),
        )
        return "extracted"

    if existing["textbook_id"] is None:
        # Adopt an unassociated catalog row (CD-2): events materialized it with
        # textbook_id NULL, source='catalog', a placeholder xml_id, and no
        # source. Syncing the book attaches the real textbook / xml_id /
        # section_id and the source snippet, upgrading it to an extracted
        # source — the SP-5 "syncing flips a catalog question to analyzable"
        # path, not the cross-textbook collision guarded below.
        conn.execute(
            "UPDATE questions SET textbook_id = ?, xml_id = ?, section_id = ?, "
            "source = 'auto_extracted', question_type = ?, source_xml = ?, "
            "title = ?, metadata_json = ?, extracted_at = datetime('now') "
            "WHERE question_id = ?",
            (
                textbook_id,
                q.xml_id,
                q.section_id,
                q.question_type,
                q.source_xml,
                q.title,
                _merged_metadata(existing["metadata_json"], q.question_type),
                question_id,
            ),
        )
        return "updated"

    if existing["textbook_id"] != textbook_id:
        raise ValueError(
            f"question_id {question_id!r} already exists under "
            f"textbook_id={existing['textbook_id']} but sync was called with "
            f"textbook_id={textbook_id}; refusing to silently re-associate."
        )
    if (
        existing["source_xml"] == q.source_xml
        and existing["question_type"] == q.question_type
    ):
        if existing["title"] == q.title:
            return "unchanged"
        # Snippet unchanged but the stored title is missing/stale — the
        # migration-0014 backfill (FU-3): a row synced before the column
        # existed is repopulated by simply re-running sync-sources. The title
        # is derived from the snippet, so outside the backfill this branch is
        # unreachable (a title change implies a snippet change).
        conn.execute(
            "UPDATE questions SET title = ?, extracted_at = datetime('now') "
            "WHERE question_id = ?",
            (q.title, question_id),
        )
        return "title_backfilled"
    conn.execute(
        "UPDATE questions SET question_type = ?, source_xml = ?, title = ?, "
        "metadata_json = ?, extracted_at = datetime('now') "
        "WHERE question_id = ?",
        (
            q.question_type,
            q.source_xml,
            q.title,
            _merged_metadata(existing["metadata_json"], q.question_type),
            question_id,
        ),
    )
    return "updated"


def dispatch_source_handlers(
    conn: sqlite3.Connection,
    services: tuple,
    textbook_id: int | None = None,
) -> dict[str, dict[str, int]]:
    """
    Invoke each service's source_handler over the synced questions whose
    question_type it claims. A handler exception is contained per question
    (logged, counted as 'error') — one broken question must not abort the
    sync, and one broken service must not block another.
    """
    handled: dict[str, dict[str, int]] = {}
    for svc in services:
        if svc.source_handler is None or not svc.question_types:
            continue
        placeholders = ", ".join("?" for _ in svc.question_types)
        sql = f"SELECT * FROM questions WHERE question_type IN ({placeholders})"
        params: list[object] = sorted(svc.question_types)
        if textbook_id is not None:
            sql += " AND textbook_id = ?"
            params.append(textbook_id)
        counts: dict[str, int] = {}
        for row in conn.execute(sql, params).fetchall():
            try:
                status = svc.source_handler(conn, row) or "handled"
            except Exception:  # noqa: BLE001 - isolation boundary
                log.warning(
                    "source_handler of service %r failed for question %r",
                    svc.name, row["question_id"], exc_info=True,
                )
                status = "error"
            counts[status] = counts.get(status, 0) + 1
        handled[svc.name] = counts
    conn.commit()
    return handled
