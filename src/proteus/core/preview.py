"""
Shared question-preview build pipeline (checklist item SP-3).

One preview capability all services share: take a question's cached source
(``questions.source_xml``, written by ``sync-sources``) → assemble a minimal
standalone PreTeXt document → build it to web-ready HTML (portable / self-
contained, SP-4 + PV-2) → prune the residual visible chrome (PV-3) → cache the
result on the filesystem (parallel to ``data/question_sources``) keyed by
``question_id`` and a hash of the source. Builds happen lazily on first request
and are served from cache thereafter; a changed source (hash mismatch) triggers
a rebuild. The front door (B4.9) embeds the cached HTML directly; the preview is
a property of the selected question regardless of which service (if any) handles
its type.

The HTML build step is a pluggable ``PreviewBuilder``. The default is the real
``pretext``-backed builder when the bundled toolchain is installed (SP-4),
falling back to ``unavailable`` when it is not; :func:`placeholder_builder`
remains a dependency-free stub callers can pass explicitly. A question with no
source yields ``no_source`` (the front door shows SP-5's "source not synced");
``builder=None`` yields ``unavailable`` (a buildable question with no toolchain);
a build that raises yields ``error`` (the front door stays up).
"""

from __future__ import annotations

import hashlib
import html as _html
import logging
import os
import sqlite3
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import pretext as _pretext
from .preview_prune import prune_preview_html
from .textbooks import validate_book_xml_id

log = logging.getLogger(__name__)

__all__ = [
    "render_preview",
    "render_assembled",
    "assemble_standalone",
    "placeholder_builder",
    "default_builder",
    "source_digest",
    "PreviewResult",
    "DEFAULT_PREVIEW_ROOT",
]

# Built previews cache here, parallel to data/question_sources.
DEFAULT_PREVIEW_ROOT = Path("data") / "question_previews"

# Cache-format version for the *built* preview. The cache is keyed by
# cache_name + a digest of the build inputs, but the cached HTML also depends on
# the build/prune PIPELINE (the portable publication file, the prune selectors,
# the macros injected into the wrapper, the <xref> resolution, …), which the
# digest cannot see. Bump this whenever the pipeline changes shape so previews
# cached by an older pipeline (e.g. the pre-portable, full-chrome, local-_static/
# build, or one before macros/xref handling) are invalidated and rebuilt even
# when their source is unchanged. It is stored in the cache sidecar alongside a
# digest of the build inputs; a missing prefix (a pre-versioning sidecar) or a
# mismatch forces a rebuild.
#   v4 (B4.15/LM-2): the build digest now folds in the wrapper's title and the
#   section-wrap option, not just source + macros, so render_assembled is
#   cache-correct for synthesized sources that vary the wrapper.
#   v5: assemble_standalone now strips the `component` attribute, so questions
#   authored with a non-"proteus" component (e.g. "proteus-playground") no longer
#   build blank, previews cached blank by v4 are invalidated and rebuilt.
#   v6: assemble_standalone now strips authored <solution>/<answer>/<hint>, so a
#   coder's preview never shows the answer (SA solution suppression).
#   v7 (FU-2): first-level <xref> targets resolve into the wrapper (a references
#   section holding the synced targets' content) instead of neutralizing to a
#   bare literal, and unresolvable targets render a readable labeled stub. The
#   build digest folds in the resolved targets' hashes, so previews cached by
#   the v6 neutralization are invalidated and rebuilt.
PREVIEW_CACHE_VERSION = "7"

# A builder turns an assembled standalone PreTeXt document into web-ready HTML.
PreviewBuilder = Callable[[str], str]

# Sentinel for render_preview's builder argument: resolve the default builder at
# call time (so toolchain availability is checked per call, not at import).
_DEFAULT_BUILDER = object()


def default_builder() -> PreviewBuilder | None:
    """
    The bundled PreTeXt HTML builder when the toolchain is installed (SP-4), else
    None so a buildable question reports ``unavailable``.
    """
    return _pretext.build_html if _pretext.pretext_available() else None


@dataclass(frozen=True)
class PreviewResult:
    """The outcome of a preview request."""

    question_id: str
    status: str  # 'built' | 'cached' | 'no_source' | 'unavailable' | 'error'
    html: str | None
    path: Path | None
    source_hash: str | None
    error: str | None = None


def source_digest(source_xml: str) -> str:
    """The cache key component for a question's source (sha256 hex)."""
    return hashlib.sha256(source_xml.encode("utf-8")).hexdigest()


def _validate_cache_key(book: str, question_id: str) -> None:
    """
    Defense-in-depth: ``book`` and ``question_id`` both become filesystem path
    components under the cache root, so reject values that could escape it.
    ``book`` reuses the book-id path-component rule from ``textbooks``; the
    ``question_id`` filename must not contain separators, be ``.``/``..``, or
    embed a null byte.
    """
    validate_book_xml_id(book)
    if (
        not question_id
        or question_id in (".", "..")
        or "/" in question_id
        or "\\" in question_id
        or "\x00" in question_id
    ):
        raise ValueError(
            f"invalid question_id {question_id!r}: it is used as a filesystem "
            "path component (no separators, '.', '..' or null bytes)"
        )


def _atomic_write(path: Path, text: str) -> None:
    """
    Write ``text`` to ``path`` atomically: a temp file in the same directory
    followed by ``os.replace``. Concurrent readers never observe a partially
    written file, and a crash mid-write cannot leave a corrupt cache entry
    (the destination is either the old file or the complete new one).
    """
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


_XML_ID = "{http://www.w3.org/XML/1998/namespace}id"

# The wrapper's own xml:ids. Each is a *base*, not a fixed value: a repeated
# xml:id is an invalid PreTeXt document, so when the content already carries
# one of these, authored in the snippet, or arriving with FU-2 pulled-in
# content, the wrapper suffixes until free (:func:`_free_id`; FU-4). With no
# collision the bases are used verbatim, so the ordinary wrapper is unchanged.
_ARTICLE_ID = "preview"  # the article (both wrap modes)
_SECTION_ID = "preview-section"  # the titled section around block content
_REFERENCES_SECTION_ID = "preview-references"  # FU-2 pulled-in xref targets


def _free_id(base: str, taken: set[str]) -> str:
    """``base`` if it is not in ``taken``, else the first free candidate
    (``base-2``, ``base-3``, …). The chosen id is added to ``taken``, so successive
    picks against the same set stay mutually distinct."""
    candidate = base
    suffix = 2
    while candidate in taken:
        candidate = f"{base}-{suffix}"
        suffix += 1
    taken.add(candidate)
    return candidate


def _collect_ids(xml_text: str) -> set[str]:
    """Every ``xml:id`` a serialized snippet carries, the taken-set the wrapper
    ids must avoid (FU-4). Unparseable text yields none, matching the pipeline's
    graceful contract: such a snippet passes through unchanged and the wrapper
    keeps its default ids."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return set()
    return {e.get(_XML_ID) for e in root.iter() if e.get(_XML_ID)}


def _stub_xref(elem: ET.Element) -> None:
    """Convert one ``<xref>`` element in place into a readable labeled stub.

    ``<em>[reference: X]</em>``: labeled with the author's display text when the
    xref carries any, else the ``ref`` target, else a bare ``[reference]``. The
    element's tail text (the surrounding prose) is untouched. Used for targets
    that cannot be resolved into the wrapper: unsynced ids (figures, sections,
    other books) and every reference nested inside pulled-in content (the FU-2
    one-level bound).
    """
    label = "".join(elem.itertext()).strip() or (elem.get("ref") or "").strip()
    elem.tag = "em"
    elem.attrib.clear()
    elem.text = f"[reference: {label}]" if label else "[reference]"
    for child in list(elem):
        elem.remove(child)


def _xref_refs(source_xml: str) -> tuple[str, ...]:
    """The distinct ``ref`` targets of a snippet's ``<xref>`` elements, in
    document order, what :func:`render_preview` looks up among the synced
    questions. Unparseable source yields none (the resolver degrades the same
    way)."""
    try:
        root = ET.fromstring(source_xml)
    except ET.ParseError:
        return ()
    seen: set[str] = set()
    refs: list[str] = []
    for elem in root.iter("xref"):
        ref = elem.get("ref")
        if ref and ref not in seen:
            seen.add(ref)
            refs.append(ref)
    return tuple(refs)


def _resolve_xrefs(
    source_xml: str, targets: dict[str, str] | None = None
) -> tuple[str, str | None]:
    """
    Resolve first-level ``<xref>`` cross-references in a question snippet so the
    standalone preview builds (FU-2; the 2026-07-03 decision: resolve from the
    synced ``questions.source_xml`` rows).

    ``targets`` maps an ``<xref ref="X">`` target id to the synced source of the
    question it names. Returns ``(snippet, references)``:

    - An xref whose target is already **inside the snippet** (the question's own
      unit id, a task label, promoted to an ``xml:id`` when it is only a
      Runestone ``label``) is left intact; it resolves against the wrapper
      itself and must never be pulled in (a duplicate ``xml:id`` breaks the
      build).
    - An xref whose target is a **synced question** is left intact and the
      target's content is pulled into ``references``, a serialized
      ``<section xml:id="preview-references">`` the wrapper appends after the
      question, so the toolchain resolves the reference in-document and the
      reader sees the referenced content on the same page. Each target is
      pulled once, carries ``xml:id`` equal to the ref, and every reference
      *nested inside* the pulled-in content is stubbed, never resolved, the
      one-level bound; no recursive pull-in.
    - Any other xref (a figure, a section, another book, anything not among
      the synced questions) becomes a readable labeled stub
      (:func:`_stub_xref`), not a bare code and not a dangling reference.

    ``references`` is ``None`` when nothing was pulled in. ``source_xml`` is
    ElementTree-serialized output from sync, so it re-parses cleanly; if it does
    not, it is returned unchanged (a dangling xref is the toolchain's problem,
    never a crash in the front door). An unparseable *target* cannot be pulled
    in, so its references fall back to the stub.
    """
    try:
        root = ET.fromstring(source_xml)
    except ET.ParseError:
        return source_xml, None
    # Collect first, then mutate: renaming tags / clearing children mid-walk
    # would disturb the iterator.
    xrefs = [elem for elem in root.iter() if elem.tag == "xref"]
    if not xrefs:
        return source_xml, None

    targets = dict(targets or {})
    local_ids = {e.get(_XML_ID) for e in root.iter() if e.get(_XML_ID)}
    # Only a label-ONLY element is a promotion candidate. An element can carry
    # both (real in this repo: <exercise xml:id="reading-EE-8"
    # label="reading-EE-8-v1">), and promoting the label onto it would overwrite
    # its canonical xml:id, stranding any xref that targets the canonical id.
    # A label reference to an already-identified element degrades to a stub
    # instead, which costs a reference but never corrupts an anchor (PR #160
    # review).
    local_labels = {
        e.get("label"): e
        for e in root.iter()
        if e.get("label") and not e.get(_XML_ID)
    }

    pulled: dict[str, ET.Element] = {}
    pull_order: list[str] = []
    for elem in xrefs:
        ref = elem.get("ref")
        if ref and ref in local_ids:
            continue  # resolves against the snippet itself; leave intact
        if ref and ref in local_labels:
            # Identified by a Runestone label only: promote it to an xml:id so
            # the toolchain can resolve the reference, then leave it intact.
            local_labels[ref].set(_XML_ID, ref)
            local_ids.add(ref)
            continue
        if ref and ref not in pulled and ref in targets:
            try:
                target_root = ET.fromstring(targets.pop(ref))
            except ET.ParseError:
                target_root = None  # cannot pull in; fall through to the stub
            if target_root is not None:
                if target_root.get(_XML_ID) != ref:
                    # The synced unit may be identified by a label; the appended
                    # copy must carry the ref as its xml:id to resolve.
                    target_root.set(_XML_ID, ref)
                # One-level bound: references nested inside pulled-in content
                # are stubbed, never resolved, no recursive pull-in. (Collect
                # first, then mutate, same iterator discipline as above.)
                for nested in [e for e in target_root.iter() if e.tag == "xref"]:
                    _stub_xref(nested)
                pulled[ref] = target_root
                pull_order.append(ref)
        if ref and ref in pulled:
            continue  # resolves against the pulled-in copy; leave intact
        _stub_xref(elem)

    references: str | None = None
    if pull_order:
        # The wrapper's section id must not duplicate one the snippet or a
        # pulled-in target already carries: a repeated xml:id is an invalid
        # document, and it would fail the build precisely when references
        # resolve. Suffix until free (PR #160 review).
        taken = set(local_ids)
        for target_root in pulled.values():
            taken |= {e.get(_XML_ID) for e in target_root.iter() if e.get(_XML_ID)}
        section = ET.Element("section")
        section.set(_XML_ID, _free_id(_REFERENCES_SECTION_ID, taken))
        ET.SubElement(section, "title").text = "Referenced content"
        for ref in pull_order:
            section.append(pulled[ref])
        references = ET.tostring(section, encoding="unicode")
    return ET.tostring(root, encoding="unicode"), references


def _lookup_xref_targets(
    conn: sqlite3.Connection, source_xml: str, textbook_id: int | None
) -> dict[str, str]:
    """
    The snippet's first-level ``<xref>`` targets that are synced questions of the
    same textbook: ``{ref: source_xml}`` (FU-2). ``<xref ref="…">`` names an
    ``xml:id``, which is exactly ``questions.xml_id``, so this is a plain core
    read scoped to the question's own book, a target in another book is
    deliberately not found (it renders as a labeled stub). A question with no
    textbook association resolves nothing.
    """
    if textbook_id is None:
        return {}
    targets: dict[str, str] = {}
    for ref in _xref_refs(source_xml):
        row = conn.execute(
            "SELECT source_xml FROM questions "
            "WHERE textbook_id = ? AND xml_id = ? AND source_xml IS NOT NULL",
            (textbook_id, ref),
        ).fetchone()
        if row is not None:
            targets[ref] = row["source_xml"]
    return targets


def _strip_components(source_xml: str) -> str:
    """Remove the ``component`` attribute from a question snippet so the
    standalone preview always builds it.

    The preview publication file pins ``<version include="proteus"/>``, so
    PreTeXt's build *filters source by ``component``*: an element whose
    ``component`` is not the included version (e.g. ``component="proteus-playground"``)
    is dropped from the build, and the preview renders **blank**. A standalone,
    single-question preview wants the question shown whatever authoring version it
    belongs to (and rendered as a plain question, not an interactive widget), so
    the attribute is removed; a no-``component`` element is always included.
    ``source_xml`` is ElementTree-serialized output from sync, so it re-parses
    cleanly; if it does not, or carries no ``component``, it is returned unchanged.
    """
    try:
        root = ET.fromstring(source_xml)
    except ET.ParseError:
        return source_xml
    carriers = [elem for elem in root.iter() if "component" in elem.attrib]
    if not carriers:
        return source_xml
    for elem in carriers:
        del elem.attrib["component"]
    return ET.tostring(root, encoding="unicode")


# Authored worked-answer elements a coder's preview must never show.
# Scope (A20/BUG-20 owner decision, 2026-08-22): this blinding guarantee
# covers SA components only. A built cardsort preview deliberately keeps its
# answer key (the dragndrop-data JSON's `match` array) because DnD has no
# blind-coding stage and its preview audiences are analyst surfaces that
# already read key_json. If DnD blind coding ever runs, that decision must
# be revisited with a neutralizing render mode.
_SOLUTION_LIKE = frozenset({"solution", "answer", "hint"})


def _strip_solutions(source_xml: str) -> str:
    """Remove authored ``<solution>`` / ``<answer>`` / ``<hint>`` from a question
    snippet so a coder's preview never reveals the answer.

    Short-answer questions carry worked solutions in source; the preview shows
    only the question (statement + the response prompt). This is the SA
    solution-suppression that was deferred until SA previews existed (parked
    in the open-questions file of 2026-07 and retired when this landed;
    ``dev/open-todos.md`` is the live list), done here, at the build seam, so
    it covers every preview (single exercise and synthesized task alike)
    regardless of the publication file. ``source_xml`` re-parses cleanly
    (ElementTree output from sync); if it does not, or carries no solution-like
    element, it is returned unchanged.
    """
    try:
        root = ET.fromstring(source_xml)
    except ET.ParseError:
        return source_xml
    # ElementTree has no parent pointers; map child -> parent, then remove.
    parents = {child: parent for parent in root.iter() for child in parent}
    targets = [elem for elem in root.iter() if elem.tag in _SOLUTION_LIKE]
    if not targets:
        return source_xml
    for elem in targets:
        parent = parents.get(elem)
        if parent is not None:
            parent.remove(elem)
    return ET.tostring(root, encoding="unicode")


def assemble_standalone(
    source_xml: str,
    *,
    title: str,
    macros: str | None = None,
    wrap_in_section: bool = True,
    xref_targets: dict[str, str] | None = None,
) -> str:
    """
    Wrap a PreTeXt snippet in a minimal standalone document the build toolchain
    can compile. The exact skeleton is validated/refined against the real
    toolchain in SP-4.

    ``macros`` is the book's ``<docinfo><macros>`` element (captured by
    ``sync_book_sources``); when present it is injected into the wrapper's
    ``<docinfo>`` so math that uses the book's LaTeX macros renders.

    ``xref_targets`` maps first-level ``<xref>`` target ids to synced question
    source (FU-2; :func:`render_preview` looks them up via
    :func:`_lookup_xref_targets`). A resolvable reference is kept intact and its
    target's content rides a "Referenced content" section appended after the
    question, so the toolchain resolves it in-document; anything else becomes a
    readable labeled stub (:func:`_resolve_xrefs`). Pulled-in content passes
    through the same component/solution stripping as the question itself, so a
    referenced question's answer never leaks into the preview.

    ``wrap_in_section`` (default True) puts the snippet inside a titled
    ``<section>``, right for *block* content like a question ``<exercise>``. Pass
    False when ``source_xml`` is **already a** ``<section>`` (e.g. the DnD legend,
    B4.15): the section goes straight under ``<article>``, since PreTeXt forbids a
    ``<section>`` nested inside another ``<section>`` (the references section is a
    sibling section either way).

    The wrapper's own ids (``preview``, ``preview-section``) are bases, not
    fixed values: when the content already carries one, authored, or arriving
    with pulled-in xref content, the wrapper suffixes until free (FU-4), since
    a repeated ``xml:id`` is an invalid document that would fail the question's
    own preview build.
    """
    safe_title = _html.escape(title)
    resolved, references = _resolve_xrefs(source_xml, xref_targets)
    body = _strip_solutions(_strip_components(resolved))
    if references is not None:
        references = _strip_solutions(_strip_components(references))
    # FU-4: pick wrapper ids that do not collide with the ids the content
    # actually carries. The chosen ids are a pure function of content the build
    # digest already covers, so no PREVIEW_CACHE_VERSION bump: a non-colliding
    # snippet assembles byte-identically to before, and a colliding one never
    # had a cached page, its build failed on the duplicate id, and failures
    # are not cached.
    taken = _collect_ids(body)
    if references is not None:
        taken |= _collect_ids(references)
    article_id = _free_id(_ARTICLE_ID, taken)
    docinfo = f"  <docinfo>\n    {macros}\n  </docinfo>\n" if macros else ""
    if wrap_in_section:
        content = (
            f'    <section xml:id="{_free_id(_SECTION_ID, taken)}">\n'
            "      <title>Question</title>\n"
            f"      {body}\n"
            "    </section>\n"
        )
    else:
        content = f"    {body}\n"
    if references is not None:
        content += f"    {references}\n"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<pretext>\n"
        f"{docinfo}"
        f'  <article xml:id="{article_id}">\n'
        f"    <title>{safe_title}</title>\n"
        f"{content}"
        "  </article>\n"
        "</pretext>\n"
    )


def placeholder_builder(standalone_doc: str) -> str:
    """
    The SP-3 stub builder: minimal HTML embedding the assembled source, clearly
    labeled as a placeholder until the real PreTeXt HTML toolchain is bundled
    (SP-4). Deterministic and dependency-free, so the cache pipeline is fully
    exercised now.
    """
    return (
        "<!doctype html>\n"
        "<html><head><meta charset='utf-8'>"
        "<title>Question preview (placeholder)</title></head>\n"
        "<body>\n"
        "<p><em>Preview placeholder: the PreTeXt HTML toolchain is not yet "
        "bundled (SP-4). Showing the assembled source.</em></p>\n"
        f"<pre>{_html.escape(standalone_doc)}</pre>\n"
        "</body></html>\n"
    )


def render_assembled(
    source_xml: str,
    *,
    book: str,
    cache_name: str,
    title: str,
    macros: str | None = None,
    wrap_in_section: bool = True,
    xref_targets: dict[str, str] | None = None,
    cache_root: Path | str | None = None,
    builder: PreviewBuilder | None | object = _DEFAULT_BUILDER,
) -> PreviewResult:
    """
    Build (and cache) one assembled PreTeXt ``source_xml`` snippet, the generic
    engine behind :func:`render_preview`.

    ``wrap_in_section`` and ``xref_targets`` are forwarded to
    :func:`assemble_standalone` (pass False when ``source_xml`` is already a
    ``<section>``; ``xref_targets`` is the FU-2 first-level resolution map).

    Assembles ``source_xml`` into a standalone document (injecting ``macros`` and
    resolving/stubbing ``<xref>``s), builds it with ``builder``, prunes residual
    chrome, and caches the result under ``cache_root/book/{cache_name}.html``
    keyed by the pipeline version + a digest of every wrapper input (source,
    macros, title, section-wrap, resolved xref targets), so reusing a
    ``cache_name`` with a different title/wrap still rebuilds rather than serving
    stale HTML, and an edit to a *referenced* question rebuilds the previews
    that pulled it in. :func:`render_preview`
    calls this with the stored question source and ``cache_name = question_id``;
    other callers (the DnD legend, SP-3 reuse) pass *synthesized* source and a
    distinct ``cache_name`` so the cache never collides with the question's own
    preview. ``unavailable`` when no builder is available; ``error`` when the
    build raises. ``PreviewResult.question_id`` carries ``cache_name``.
    """
    if cache_root is None:
        cache_root = DEFAULT_PREVIEW_ROOT

    digest = source_digest(source_xml)
    # The sidecar stores the pipeline version with a digest of *every* input that
    # shapes the assembled wrapper, the source, the injected macros, the title,
    # the section-wrap option, and the resolved xref targets' hashes (FU-2: a
    # target edit must rebuild the previews that pulled it in), so a page built
    # by an older pipeline, before the macros changed, or under a different
    # wrapper, misses the cache and rebuilds. (title/wrap matter because a caller
    # may legitimately reuse a cache_name across them; both in-tree callers pin
    # them per cache_name, but the key must not assume that.)
    # PreviewResult.source_hash stays the bare source digest (the public
    # contract); these wrapper inputs ride only in the cache token.
    targets_key = ";".join(
        f"{ref}={source_digest(target)}"
        for ref, target in sorted((xref_targets or {}).items())
    )
    build_digest = source_digest(
        "\x00".join(
            [
                source_xml,
                macros or "",
                title,
                "section" if wrap_in_section else "bare",
                targets_key,
            ]
        )
    )
    cache_token = f"{PREVIEW_CACHE_VERSION}:{build_digest}"
    _validate_cache_key(book, cache_name)
    cache_dir = Path(cache_root) / book
    html_path = cache_dir / f"{cache_name}.html"
    hash_path = cache_dir / f"{cache_name}.source_hash"

    if (
        html_path.is_file()
        and hash_path.is_file()
        and hash_path.read_text(encoding="utf-8").strip() == cache_token
    ):
        return PreviewResult(
            cache_name, "cached", html_path.read_text(encoding="utf-8"), html_path, digest
        )

    if builder is _DEFAULT_BUILDER:
        builder = default_builder()
    if builder is None:
        return PreviewResult(cache_name, "unavailable", None, None, digest)

    try:
        built_html = builder(
            assemble_standalone(
                source_xml, title=title, macros=macros,
                wrap_in_section=wrap_in_section, xref_targets=xref_targets,
            )
        )
    except Exception as exc:  # a build failure must not crash the front door
        log.warning("Preview build failed for %s", cache_name, exc_info=True)
        return PreviewResult(cache_name, "error", None, None, digest, str(exc))

    # Strip residual chrome once here, at build time, so the cached page is the
    # final embeddable HTML, the embed component does no HTML surgery, and a
    # cache hit serves this pruned page without re-pruning (PV-3 / PV-4).
    pruned_html = prune_preview_html(built_html)

    cache_dir.mkdir(parents=True, exist_ok=True)
    # Write the HTML before the sidecar so a reader that sees a matching token is
    # guaranteed the HTML is fully present; both writes are atomic. The sidecar
    # carries the pipeline version (cache_token), not the bare digest.
    _atomic_write(html_path, pruned_html)
    _atomic_write(hash_path, cache_token)
    log.info("Built preview for %s (source_hash=%s)", cache_name, digest[:12])
    return PreviewResult(cache_name, "built", pruned_html, html_path, digest)


def render_preview(
    conn: sqlite3.Connection,
    question_id: str,
    *,
    cache_root: Path | None = None,
    builder: PreviewBuilder | None | object = _DEFAULT_BUILDER,
) -> PreviewResult:
    """
    Return a question's preview HTML, building and caching it lazily.

    Serves from the filesystem cache when the stored ``source_hash`` sidecar
    still matches the current source; (re)builds otherwise. On a (re)build the
    built HTML is run through :func:`prune_preview_html` (PV-3) before caching, so
    the cached/returned page is the final embeddable HTML and a cache hit never
    re-prunes. First-level ``<xref>`` targets are resolved from the synced
    questions of the question's own book and ride the wrapper and the cache key
    (FU-2), so editing a referenced question rebuilds this preview too.
    ``builder`` defaults to the bundled PreTeXt toolchain
    (:func:`default_builder`); pass an explicit callable to override or ``None``
    to force the ``unavailable`` state. ``no_source`` when the question has no
    cached source; ``unavailable`` when it is buildable but no builder is
    available; ``error`` when the build raises.
    """
    try:
        row = conn.execute(
            "SELECT q.source_xml AS source_xml, q.textbook_id AS textbook_id, "
            "t.book_xml_id AS book_xml_id, t.macros AS macros "
            "FROM questions q LEFT JOIN textbooks t ON t.textbook_id = q.textbook_id "
            "WHERE q.question_id = ?",
            (question_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        # A database that predates the textbooks.macros column (migration 0012)
        # still previews, just without the book's macros in the wrapper.
        row = conn.execute(
            "SELECT q.source_xml AS source_xml, q.textbook_id AS textbook_id, "
            "t.book_xml_id AS book_xml_id "
            "FROM questions q LEFT JOIN textbooks t ON t.textbook_id = q.textbook_id "
            "WHERE q.question_id = ?",
            (question_id,),
        ).fetchone()
    source_xml = row["source_xml"] if row is not None else None
    if not source_xml:
        return PreviewResult(question_id, "no_source", None, None, None)
    macros = row["macros"] if "macros" in row.keys() else None
    book = (row["book_xml_id"] if row is not None else None) or question_id.split(
        "__", 1
    )[0]

    return render_assembled(
        source_xml,
        book=book,
        cache_name=question_id,
        title=question_id,
        macros=macros,
        xref_targets=_lookup_xref_targets(conn, source_xml, row["textbook_id"]),
        cache_root=cache_root,
        builder=builder,
    )
