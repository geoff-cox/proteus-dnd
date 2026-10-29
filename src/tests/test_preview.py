"""
Tests for the shared question-preview pipeline (checklist item SP-3).

Covers assemble → build → cache (keyed by question_id + source_hash), lazy build
then cache-serve, rebuild on source change, and the graceful no-source /
no-toolchain states. The HTML builder is injected, so no real toolchain (SP-4)
is needed.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from proteus.core import preview, textbooks

Q = "demo-book__q1"
SOURCE = "<cardsort><match><premise>x</premise><response>X</response></match></cardsort>"


class _CountingBuilder:
    """A fake PreviewBuilder that records how many times it ran."""

    def __init__(self):
        self.calls = 0

    def __call__(self, doc: str) -> str:
        self.calls += 1
        return f"<html><body>BUILT[{self.calls}] len={len(doc)}</body></html>"


class _CapturingBuilder:
    """A fake PreviewBuilder that records each assembled document it was given."""

    def __init__(self):
        self.docs: list[str] = []

    def __call__(self, doc: str) -> str:
        self.docs.append(doc)
        return f"<html><body>BUILT[{len(self.docs)}]</body></html>"


def _add_question(conn, source_xml=SOURCE, question_id=Q):
    tid = textbooks.add_textbook(
        conn, book_xml_id="demo-book",
        git_repo_url="https://example.test/demo.git", build_dir="/b",
    )
    conn.execute(
        "INSERT INTO questions (question_id, textbook_id, xml_id, metadata_json, "
        "source, source_xml) VALUES (?, ?, 'q1', '{}', 'auto_extracted', ?)",
        (question_id, tid, source_xml),
    )
    conn.commit()
    return tid


def _add_synced_question(conn, tid, xml_id, source_xml, book="demo-book"):
    """A further synced question of an existing textbook (an FU-2 xref target)."""
    conn.execute(
        "INSERT INTO questions (question_id, textbook_id, xml_id, metadata_json, "
        "source, source_xml) VALUES (?, ?, ?, '{}', 'auto_extracted', ?)",
        (f"{book}__{xml_id}", tid, xml_id, source_xml),
    )
    conn.commit()


class TestAssembleStandalone:
    def test_embeds_snippet_in_minimal_pretext(self):
        doc = preview.assemble_standalone(SOURCE, title="demo-book__q1")
        assert doc.startswith("<?xml")
        assert "<pretext>" in doc and "</pretext>" in doc
        assert SOURCE in doc  # snippet embedded verbatim

    def test_injects_book_macros_into_docinfo(self):
        # Issue 2: the book's <macros> ride into the wrapper's <docinfo> so math
        # using the book's LaTeX macros renders.
        macros = "<macros>\\newcommand{\\ZZ}{\\mathbb{Z}}</macros>"
        doc = preview.assemble_standalone(SOURCE, title="t", macros=macros)
        assert "<docinfo>" in doc and "</docinfo>" in doc
        assert macros in doc
        # No macros -> no docinfo section (unchanged minimal wrapper).
        assert "<docinfo>" not in preview.assemble_standalone(SOURCE, title="t")

    def test_stubs_unresolvable_xref_so_the_wrapper_builds(self):
        # FU-2: with no resolvable target (a figure, a section, another book),
        # the xref becomes a readable labeled stub — not a bare code, and no
        # dangling cross-reference reaches the toolchain. Surrounding prose (the
        # element's tail text) is preserved.
        src = '<statement><p>See <xref ref="fig-tree"/> and solve.</p></statement>'
        doc = preview.assemble_standalone(src, title="t")
        assert "<xref" not in doc
        assert "<em>[reference: fig-tree]</em>" in doc
        assert "and solve." in doc  # tail prose preserved

    def test_resolves_xref_target_into_the_wrapper(self):
        # FU-2: a target among the synced questions is pulled into a references
        # section and the <xref> is left intact, so the toolchain resolves it
        # in-document and the reader sees the referenced content on the page.
        src = '<exercise xml:id="q1"><statement><p>See <xref ref="q2"/>.</p></statement></exercise>'
        target = '<exercise xml:id="q2"><statement><p>TARGET-CONTENT</p></statement></exercise>'
        doc = preview.assemble_standalone(src, title="t", xref_targets={"q2": target})
        assert '<xref ref="q2"' in doc  # kept, resolves against the pulled copy
        assert "TARGET-CONTENT" in doc
        assert 'xml:id="preview-references"' in doc
        assert "Referenced content" in doc

    def test_pulled_in_content_is_component_and_solution_stripped(self):
        # Pulled-in content rides the same build seam as the question itself:
        # its component attribute is removed (or the build filters it blank) and
        # its authored solution/answer never reach the preview.
        src = '<p><xref ref="q2"/></p>'
        target = (
            '<exercise xml:id="q2" component="proteus-playground">'
            "<statement><p>T</p></statement><response/>"
            "<solution><p>PULLED-SECRET</p></solution></exercise>"
        )
        doc = preview.assemble_standalone(src, title="t", xref_targets={"q2": target})
        assert "component=" not in doc
        assert "PULLED-SECRET" not in doc and "<solution" not in doc
        assert "<response" in doc  # the referenced question's prompt survives

    def test_strips_component_so_the_build_does_not_filter_it(self):
        # The preview publication pins <version include="proteus"/>, so a
        # component the build doesn't include (e.g. "proteus-playground") would be
        # filtered out and render blank. assemble_standalone removes it.
        src = (
            '<exercise component="proteus-playground" label="q">'
            "<statement><p>Hi</p></statement><response/></exercise>"
        )
        doc = preview.assemble_standalone(src, title="t")
        assert "component=" not in doc
        assert "<response" in doc  # the question itself survives the build filter

    def test_strips_solution_so_the_preview_never_shows_the_answer(self):
        src = (
            "<exercise><statement><p>Q</p></statement><response/>"
            "<solution><p>SECRET-WORKING</p></solution>"
            "<answer><p>SECRET-ANSWER</p></answer></exercise>"
        )
        doc = preview.assemble_standalone(src, title="t")
        assert "SECRET-WORKING" not in doc and "SECRET-ANSWER" not in doc
        assert "<solution" not in doc and "<answer" not in doc
        assert "<response" in doc  # the question prompt stays

    def test_default_wraps_block_content_in_a_section(self):
        assert "preview-section" in preview.assemble_standalone(SOURCE, title="t")

    def test_wrap_in_section_false_places_section_under_article(self):
        # Section-level content (e.g. the DnD legend) must go straight under
        # <article>; PreTeXt forbids a <section> nested inside a <section>.
        import xml.etree.ElementTree as ET

        src = '<section xml:id="leg"><title>Legend</title><p>x</p></section>'
        doc = preview.assemble_standalone(src, title="t", wrap_in_section=False)
        assert "preview-section" not in doc
        article = ET.fromstring(doc).find("article")
        sections = article.findall("section")
        assert len(sections) == 1  # the legend, directly under article
        assert sections[0].find("section") is None  # not nested

    def test_wrapper_ids_keep_their_defaults_without_a_collision(self):
        # FU-4 must not disturb the ordinary wrapper: with no authored collision
        # the bases are used verbatim, so a non-colliding snippet assembles
        # byte-identically to the pre-FU-4 pipeline (which is what lets the
        # cache token stay version-7).
        doc = preview.assemble_standalone(SOURCE, title="t")
        assert 'xml:id="preview"' in doc
        assert 'xml:id="preview-section"' in doc

    def test_wrapper_ids_avoid_authored_collisions(self):
        # FU-4: a question authored with the wrapper's own ids must not produce
        # a duplicate xml:id — an invalid document that fails the question's own
        # preview build. The wrapper moves; the authored ids survive untouched.
        src = (
            '<exercise xml:id="preview">'
            '<statement><p xml:id="preview-section">Q</p></statement></exercise>'
        )
        doc = preview.assemble_standalone(src, title="t")
        assert '<exercise xml:id="preview">' in doc  # authored ids untouched
        assert 'xml:id="preview-2"' in doc  # the article moved
        assert 'xml:id="preview-section-2"' in doc  # the section moved
        ids = [
            e.get("{http://www.w3.org/XML/1998/namespace}id")
            for e in ET.fromstring(doc.encode("utf-8")).iter()
        ]
        ids = [i for i in ids if i]
        assert len(ids) == len(set(ids)), ids

    def test_wrapper_id_suffix_skips_taken_suffixes(self):
        src = (
            '<exercise xml:id="preview-section">'
            '<statement><p xml:id="preview-section-2">Q</p></statement></exercise>'
        )
        doc = preview.assemble_standalone(src, title="t")
        assert 'xml:id="preview-section-3"' in doc

    def test_wrapper_ids_avoid_ids_arriving_with_pulled_in_content(self):
        # The FU-2 pull-in widened the surface: the colliding id may live in the
        # referenced question rather than the snippet itself.
        src = "<p><xref ref='q2'/></p>"
        target = (
            '<exercise xml:id="q2"><statement><p xml:id="preview-section">T</p>'
            "</statement></exercise>"
        )
        doc = preview.assemble_standalone(src, title="t", xref_targets={"q2": target})
        assert 'xml:id="preview-section-2"' in doc  # the wrapper section moved
        ids = [
            e.get("{http://www.w3.org/XML/1998/namespace}id")
            for e in ET.fromstring(doc.encode("utf-8")).iter()
        ]
        ids = [i for i in ids if i]
        assert len(ids) == len(set(ids)), ids


class TestFreeId:
    """The FU-4 suffix-until-free helper shared by the wrapper ids and the FU-2
    references section."""

    def test_returns_base_when_free_and_reserves_it(self):
        taken: set[str] = set()
        assert preview._free_id("preview", taken) == "preview"
        assert "preview" in taken  # reserved, so successive picks stay distinct
        assert preview._free_id("preview", taken) == "preview-2"

    def test_skips_past_taken_suffixes(self):
        assert preview._free_id("x", {"x", "x-2", "x-3"}) == "x-4"


class TestResolveXrefs:
    """FU-2: first-level resolution from the synced sources, replacing the old
    neutralize-to-literal. Unresolvable targets become readable labeled stubs;
    resolvable ones are pulled in once, one level deep, never recursively."""

    def test_leaves_xref_free_source_byte_identical(self):
        # No xref -> returned unchanged (so the verbatim-embed contract holds).
        assert preview._resolve_xrefs(SOURCE, {}) == (SOURCE, None)

    def test_unresolved_target_becomes_readable_labeled_stub(self):
        out, references = preview._resolve_xrefs(
            '<p>See <xref ref="fig-tree"/> here.</p>', {}
        )
        assert references is None
        assert "<xref" not in out
        assert "<em>[reference: fig-tree]</em>" in out  # readable, not a bare code
        assert "here." in out  # tail prose preserved

    def test_stub_label_prefers_authored_text_then_ref_then_generic(self):
        out, _ = preview._resolve_xrefs(
            "<p><xref ref='a'/><xref ref='b'>shown</xref><xref/></p>", {}
        )
        assert "<xref" not in out
        assert "[reference: a]" in out  # ref target
        assert "[reference: shown]" in out  # authored display text wins
        assert "<em>[reference]</em>" in out  # no ref, no text

    def test_synced_target_kept_intact_and_pulled_into_references(self):
        target = (
            '<exercise xml:id="q2"><statement><p>Other question</p></statement>'
            "</exercise>"
        )
        out, references = preview._resolve_xrefs(
            '<p>See <xref ref="q2"/> first.</p>', {"q2": target}
        )
        assert '<xref ref="q2"' in out  # left intact; resolves in-document
        assert "first." in out
        assert references is not None
        assert 'xml:id="preview-references"' in references
        assert "<title>Referenced content</title>" in references
        assert "Other question" in references
        assert 'xml:id="q2"' in references  # the ref's anchor rides the copy

    def test_label_identified_target_gains_the_ref_as_xml_id(self):
        # A synced unit may be identified by a Runestone label only; the pulled
        # copy must still carry the ref as an xml:id or the xref dangles.
        target = '<exercise label="q2"><statement><p>T</p></statement></exercise>'
        out, references = preview._resolve_xrefs(
            '<p><xref ref="q2"/></p>', {"q2": target}
        )
        assert '<xref ref="q2"' in out
        assert 'xml:id="q2"' in references

    def test_nested_reference_is_stubbed_never_resolved(self):
        # The one-level bound: a reference nested inside pulled-in content is
        # stubbed even when its own target is available — no recursive pull-in.
        targets = {
            "q2": '<exercise xml:id="q2"><p>See <xref ref="q3"/> too.</p></exercise>',
            "q3": '<exercise xml:id="q3"><p>THIRD</p></exercise>',
        }
        out, references = preview._resolve_xrefs('<p><xref ref="q2"/></p>', targets)
        assert "<xref" not in references  # nested xref stubbed
        assert "[reference: q3]" in references
        assert "THIRD" not in references  # q3 was not pulled in
        assert references.count("<exercise") == 1

    def test_in_snippet_target_is_not_pulled_in(self):
        # An xref to the question's own unit resolves against the wrapper
        # itself; pulling in the synced copy would duplicate the xml:id.
        src = (
            '<exercise xml:id="q1"><statement><p>Recall <xref ref="q1"/>.</p>'
            "</statement></exercise>"
        )
        out, references = preview._resolve_xrefs(src, {"q1": src})
        assert references is None
        assert '<xref ref="q1"' in out  # intact; resolves locally

    def test_in_snippet_label_is_promoted_to_xml_id(self):
        # A local target identified only by a Runestone label gets the label
        # promoted to an xml:id so the toolchain can resolve the reference.
        src = (
            '<exercise label="q1"><statement><p>Recall <xref ref="q1"/>.</p>'
            "</statement></exercise>"
        )
        out, references = preview._resolve_xrefs(src, {})
        assert references is None
        assert '<xref ref="q1"' in out
        assert 'xml:id="q1"' in out

    def test_label_promotion_never_overwrites_an_existing_xml_id(self):
        # PR #160 review: an element may carry BOTH xml:id and label with
        # different values (real in this repo, e.g.
        # <exercise xml:id="reading-EE-8" label="reading-EE-8-v1">). Promoting
        # the label onto such an element would destroy its canonical anchor and
        # strand any xref that targets it, so only a label-ONLY element is
        # promoted; a label reference to an already-identified element stubs.
        src = (
            '<exercise xml:id="canonical" label="legacy">'
            '<statement><p>See <xref ref="legacy"/> and <xref ref="canonical"/>.</p>'
            "</statement></exercise>"
        )
        out, references = preview._resolve_xrefs(src, {})
        assert references is None
        assert 'xml:id="canonical"' in out  # canonical anchor survives intact
        assert 'xml:id="legacy"' not in out
        assert '<xref ref="canonical"' in out  # still resolves locally
        assert "[reference: legacy]" in out  # the label ref degrades to a stub

    def test_references_section_id_avoids_an_authored_collision(self):
        # PR #160 review: the wrapper's generated section id must not duplicate
        # an id the snippet (or a pulled-in target) already carries — a repeated
        # xml:id is an invalid document and fails the build exactly when
        # references resolve.
        src = (
            '<exercise xml:id="preview-references">'
            '<statement><p><xref ref="q2"/></p></statement></exercise>'
        )
        target = '<exercise xml:id="q2"><p>T</p></exercise>'
        out, references = preview._resolve_xrefs(src, {"q2": target})
        assert references is not None
        section_id = ET.fromstring(references).get(
            "{http://www.w3.org/XML/1998/namespace}id"
        )
        assert section_id != "preview-references"  # sidestepped the collision
        assert section_id.startswith("preview-references")
        # And the assembled document carries each id exactly once.
        doc = preview.assemble_standalone(src, title="t", xref_targets={"q2": target})
        ids = [
            e.get("{http://www.w3.org/XML/1998/namespace}id")
            for e in ET.fromstring(doc.encode("utf-8")).iter()
        ]
        ids = [i for i in ids if i]
        assert len(ids) == len(set(ids)), ids

    def test_repeated_refs_pull_the_target_once(self):
        target = '<exercise xml:id="q2"><p>ONCE</p></exercise>'
        out, references = preview._resolve_xrefs(
            '<p><xref ref="q2"/> and again <xref ref="q2"/></p>', {"q2": target}
        )
        assert out.count('<xref ref="q2"') == 2  # both kept
        assert references.count("ONCE") == 1  # one pulled-in copy

    def test_unparseable_target_falls_back_to_stub(self):
        out, references = preview._resolve_xrefs(
            '<p><xref ref="q2"/></p>', {"q2": "<exercise>unclosed"}
        )
        assert references is None
        assert "<xref" not in out
        assert "[reference: q2]" in out

    def test_tolerates_unparseable_source(self):
        junk = "<p>unclosed"
        assert preview._resolve_xrefs(junk, {}) == (junk, None)


class TestStripComponents:
    def test_removes_component_attribute_anywhere(self):
        out = preview._strip_components(
            '<exercise component="proteus-playground">'
            '<statement component="x"><p>Hi</p></statement><response/></exercise>'
        )
        assert "component=" not in out  # root and nested
        assert "<response" in out  # the question survives

    def test_leaves_component_free_source_byte_identical(self):
        # No component -> returned unchanged (the verbatim-embed contract holds).
        src = '<exercise label="q"><statement><p>Hi</p></statement><response /></exercise>'
        assert preview._strip_components(src) == src

    def test_tolerates_unparseable_source(self):
        junk = "<exercise component='x'>unclosed"
        assert preview._strip_components(junk) == junk


class TestStripSolutions:
    def test_removes_solution_answer_hint_anywhere(self):
        out = preview._strip_solutions(
            "<exercise><statement><p>Q</p></statement><response/>"
            "<solution><p>worked</p></solution><answer><p>42</p></answer>"
            "<hint><p>tip</p></hint></exercise>"
        )
        assert "<solution" not in out and "<answer" not in out and "<hint" not in out
        assert "worked" not in out and "42" not in out and "tip" not in out
        assert "<response" in out  # the question prompt stays
        assert "<statement>" in out

    def test_leaves_solution_free_source_byte_identical(self):
        # No solution-like element -> returned unchanged (verbatim-embed contract).
        src = "<exercise><statement><p>Q</p></statement><response /></exercise>"
        assert preview._strip_solutions(src) == src

    def test_tolerates_unparseable_source(self):
        junk = "<exercise><solution>unclosed"
        assert preview._strip_solutions(junk) == junk


class TestRenderPreview:
    def test_builds_then_serves_from_cache(self, db_conn, tmp_path):
        _add_question(db_conn)
        builder = _CountingBuilder()
        root = tmp_path / "previews"

        first = preview.render_preview(db_conn, Q, cache_root=root, builder=builder)
        assert first.status == "built"
        assert builder.calls == 1
        assert first.path == root / "demo-book" / f"{Q}.html"
        assert first.path.is_file()
        # The sidecar carries the pipeline version prefixed on the build digest
        # (which now folds title/wrap in too, so it is no longer the bare source
        # hash — assert the versioned format rather than the exact digest).
        sidecar = (root / "demo-book" / f"{Q}.source_hash").read_text().strip()
        assert sidecar.startswith(f"{preview.PREVIEW_CACHE_VERSION}:")
        # Atomic writes leave no temp files behind in the cache dir.
        assert sorted(p.name for p in (root / "demo-book").iterdir()) == [
            f"{Q}.html",
            f"{Q}.source_hash",
        ]

        second = preview.render_preview(db_conn, Q, cache_root=root, builder=builder)
        assert second.status == "cached"
        assert builder.calls == 1  # not rebuilt
        assert second.html == first.html

    def test_caches_the_pruned_html_not_the_raw_build(self, db_conn, tmp_path):
        # PV-4: render_preview prunes residual chrome before caching, so both the
        # returned PreviewResult.html and the on-disk cache are the pruned page.
        _add_question(db_conn)

        def chrome_builder(_doc):
            return (
                "<html><body>"
                '<a class="assistive" href="#main">skip</a>'
                '<header id="ptx-masthead">MAST</header>'
                '<ul data-component="dragndrop"><li class="draggable">x</li></ul>'
                "</body></html>"
            )

        root = tmp_path / "p"
        result = preview.render_preview(db_conn, Q, cache_root=root, builder=chrome_builder)
        assert result.status == "built"
        assert "ptx-masthead" not in result.html  # chrome pruned out
        assert "assistive" not in result.html
        assert 'data-component="dragndrop"' in result.html  # interactive kept
        # The cache file is byte-for-byte the pruned HTML the caller received.
        cached = (root / "demo-book" / f"{Q}.html").read_text(encoding="utf-8")
        assert cached == result.html
        assert "ptx-masthead" not in cached

    def test_prune_runs_once_at_build_not_per_render(self, db_conn, tmp_path, monkeypatch):
        # PV-4: pruning happens once at build; a cache hit serves the stored
        # pruned page and does not re-prune.
        _add_question(db_conn)
        calls = {"n": 0}
        real = preview.prune_preview_html

        def counting(html):
            calls["n"] += 1
            return real(html)

        monkeypatch.setattr(preview, "prune_preview_html", counting)
        root = tmp_path / "p"
        first = preview.render_preview(
            db_conn, Q, cache_root=root, builder=preview.placeholder_builder
        )
        second = preview.render_preview(
            db_conn, Q, cache_root=root, builder=preview.placeholder_builder
        )
        assert first.status == "built" and second.status == "cached"
        assert calls["n"] == 1  # pruned at build, not on the cache-serve
        assert second.html == first.html

    def test_rebuilds_when_cache_predates_pipeline_version(self, db_conn, tmp_path):
        # A preview cached by an OLDER pipeline (sidecar holds the bare digest,
        # no version prefix — exactly what the pre-portable build wrote) must be
        # rebuilt even though the source is unchanged; otherwise a stale
        # non-portable, full-chrome page is served (PR #69, the owner's issue 1).
        _add_question(db_conn)
        builder = _CountingBuilder()
        root = tmp_path / "previews"
        cache_dir = root / "demo-book"
        cache_dir.mkdir(parents=True)
        digest = preview.source_digest(SOURCE)
        (cache_dir / f"{Q}.html").write_text(
            "<html><body>STALE NON-PORTABLE</body></html>", encoding="utf-8"
        )
        (cache_dir / f"{Q}.source_hash").write_text(digest, encoding="utf-8")  # old format

        result = preview.render_preview(db_conn, Q, cache_root=root, builder=builder)
        assert result.status == "built"  # not served from the stale cache
        assert builder.calls == 1
        assert "STALE" not in result.html
        # The sidecar is rewritten in the new versioned format.
        assert (cache_dir / f"{Q}.source_hash").read_text().strip().startswith(
            f"{preview.PREVIEW_CACHE_VERSION}:"
        )

    def test_rebuilds_when_source_changes(self, db_conn, tmp_path):
        _add_question(db_conn)
        builder = _CountingBuilder()
        root = tmp_path / "previews"
        preview.render_preview(db_conn, Q, cache_root=root, builder=builder)

        db_conn.execute(
            "UPDATE questions SET source_xml = ? WHERE question_id = ?",
            (SOURCE.replace("premise>x", "premise>y"), Q),
        )
        db_conn.commit()

        rebuilt = preview.render_preview(db_conn, Q, cache_root=root, builder=builder)
        assert rebuilt.status == "built"
        assert builder.calls == 2  # source hash changed -> rebuilt

    def test_no_source_is_graceful(self, db_conn, tmp_path):
        # A catalogued question with no synced source (front door shows SP-5).
        db_conn.execute(
            "INSERT INTO questions (question_id, xml_id, metadata_json, source) "
            "VALUES ('cat__q', 'q', '{}', 'catalog')"
        )
        db_conn.commit()
        result = preview.render_preview(
            db_conn, "cat__q", cache_root=tmp_path / "p", builder=_CountingBuilder()
        )
        assert result.status == "no_source"
        assert result.html is None

    def test_unknown_question_is_no_source(self, db_conn, tmp_path):
        result = preview.render_preview(
            db_conn, "nope__q", cache_root=tmp_path / "p"
        )
        assert result.status == "no_source"

    def test_unavailable_without_a_builder(self, db_conn, tmp_path):
        _add_question(db_conn)
        result = preview.render_preview(
            db_conn, Q, cache_root=tmp_path / "p", builder=None
        )
        assert result.status == "unavailable"
        assert result.html is None
        assert result.source_hash is not None  # source exists; just can't build

    def test_rejects_unsafe_question_id(self, db_conn, tmp_path):
        # Defense-in-depth: a path-traversing id must never build cache paths.
        evil = "demo-book__../../evil"
        _add_question(db_conn, question_id=evil)
        with pytest.raises(ValueError):
            preview.render_preview(
                db_conn, evil, cache_root=tmp_path / "p", builder=_CountingBuilder()
            )

    def test_explicit_placeholder_builder(self, db_conn, tmp_path):
        _add_question(db_conn)
        result = preview.render_preview(
            db_conn, Q, cache_root=tmp_path / "p", builder=preview.placeholder_builder
        )
        assert result.status == "built"
        assert "placeholder" in result.html.lower()
        assert "&lt;cardsort&gt;" in result.html  # escaped source embedded

    def test_default_uses_pretext_when_available(self, db_conn, tmp_path, monkeypatch):
        _add_question(db_conn)
        monkeypatch.setattr(preview._pretext, "pretext_available", lambda: True)
        monkeypatch.setattr(
            preview._pretext, "build_html", lambda doc: f"<html>PRETEXT[{len(doc)}]</html>"
        )
        result = preview.render_preview(db_conn, Q, cache_root=tmp_path / "p")
        assert result.status == "built"
        assert result.html.startswith("<html>PRETEXT[")

    def test_default_unavailable_without_toolchain(self, db_conn, tmp_path, monkeypatch):
        _add_question(db_conn)
        monkeypatch.setattr(preview._pretext, "pretext_available", lambda: False)
        result = preview.render_preview(db_conn, Q, cache_root=tmp_path / "p")
        assert result.status == "unavailable"
        assert result.html is None

    def test_build_error_is_graceful(self, db_conn, tmp_path):
        _add_question(db_conn)

        def boom(_doc):
            raise RuntimeError("toolchain exploded")

        result = preview.render_preview(db_conn, Q, cache_root=tmp_path / "p", builder=boom)
        assert result.status == "error"
        assert result.html is None
        assert result.error is not None and "exploded" in result.error
        # A failed build is not cached, so the next attempt retries.
        assert not (tmp_path / "p" / "demo-book" / f"{Q}.html").exists()

    def test_book_macros_reach_the_assembled_document(self, db_conn, tmp_path):
        # Issue 2: a question's textbook macros are injected into the wrapper that
        # gets built.
        _add_question(db_conn)
        db_conn.execute(
            "UPDATE textbooks SET macros = ? WHERE book_xml_id = 'demo-book'",
            ("<macros>\\newcommand{\\RR}{\\mathbb{R}}</macros>",),
        )
        db_conn.commit()
        builder = _CapturingBuilder()
        preview.render_preview(db_conn, Q, cache_root=tmp_path / "p", builder=builder)
        assert len(builder.docs) == 1
        assert "<docinfo>" in builder.docs[0]
        assert "\\newcommand{\\RR}" in builder.docs[0]

    def test_rebuilds_when_book_macros_change(self, db_conn, tmp_path):
        # Macros are a build input, so a macros change with an unchanged source
        # must miss the cache and rebuild — same bug class the pipeline version
        # guards against. The public source_hash stays the bare source digest.
        _add_question(db_conn)
        builder = _CountingBuilder()
        root = tmp_path / "p"
        first = preview.render_preview(db_conn, Q, cache_root=root, builder=builder)
        assert first.status == "built" and builder.calls == 1

        db_conn.execute(
            "UPDATE textbooks SET macros = ? WHERE book_xml_id = 'demo-book'",
            ("<macros>\\renewcommand{\\vec}{\\mathbf}</macros>",),
        )
        db_conn.commit()
        second = preview.render_preview(db_conn, Q, cache_root=root, builder=builder)
        assert second.status == "built"  # cache token changed -> rebuilt
        assert builder.calls == 2
        assert second.source_hash == preview.source_digest(SOURCE)  # unchanged


class TestRenderPreviewXrefResolution:
    """FU-2 end-to-end over the questions table: targets are looked up among the
    synced questions of the question's own book, ride the assembled document,
    and their hashes ride the cache key."""

    XSOURCE = (
        '<exercise xml:id="q1"><statement><p>See <xref ref="q2"/> first.</p>'
        "</statement></exercise>"
    )
    TARGET = (
        '<exercise xml:id="q2"><statement><p>TARGET-CONTENT</p></statement>'
        "<response/><solution><p>PULLED-SECRET</p></solution></exercise>"
    )

    def test_synced_target_is_resolved_into_the_assembled_doc(self, db_conn, tmp_path):
        tid = _add_question(db_conn, source_xml=self.XSOURCE)
        _add_synced_question(db_conn, tid, "q2", self.TARGET)
        cap = _CapturingBuilder()
        result = preview.render_preview(db_conn, Q, cache_root=tmp_path / "p", builder=cap)
        assert result.status == "built"
        doc = cap.docs[0]
        assert '<xref ref="q2"' in doc  # kept; resolves against the pulled copy
        assert "TARGET-CONTENT" in doc
        assert 'xml:id="preview-references"' in doc
        assert "PULLED-SECRET" not in doc  # referenced question's answer never leaks

    def test_non_question_target_gets_a_labeled_stub(self, db_conn, tmp_path):
        _add_question(
            db_conn,
            source_xml='<p>See <xref ref="fig-tree"/> here.</p>',
        )
        cap = _CapturingBuilder()
        preview.render_preview(db_conn, Q, cache_root=tmp_path / "p", builder=cap)
        doc = cap.docs[0]
        assert "<xref" not in doc
        assert "<em>[reference: fig-tree]</em>" in doc

    def test_target_in_another_book_is_not_resolved(self, db_conn, tmp_path):
        # The lookup is scoped to the question's own textbook: an id that is
        # only synced under a different book renders as a stub, never pulled in.
        _add_question(db_conn, source_xml=self.XSOURCE)
        other_tid = textbooks.add_textbook(
            db_conn, book_xml_id="other-book",
            git_repo_url="https://example.test/other.git", build_dir="/o",
        )
        _add_synced_question(db_conn, other_tid, "q2", self.TARGET, book="other-book")
        cap = _CapturingBuilder()
        preview.render_preview(db_conn, Q, cache_root=tmp_path / "p", builder=cap)
        doc = cap.docs[0]
        assert "TARGET-CONTENT" not in doc
        assert "[reference: q2]" in doc

    def test_target_edit_rebuilds_the_preview(self, db_conn, tmp_path):
        # The FU-2 cache requirement: the resolved targets' hashes ride the
        # cache key, so editing a *referenced* question rebuilds this preview
        # even though its own source is unchanged.
        tid = _add_question(db_conn, source_xml=self.XSOURCE)
        _add_synced_question(db_conn, tid, "q2", self.TARGET)
        builder = _CountingBuilder()
        root = tmp_path / "p"
        first = preview.render_preview(db_conn, Q, cache_root=root, builder=builder)
        second = preview.render_preview(db_conn, Q, cache_root=root, builder=builder)
        assert first.status == "built" and second.status == "cached"
        assert builder.calls == 1

        db_conn.execute(
            "UPDATE questions SET source_xml = ? WHERE question_id = ?",
            (self.TARGET.replace("TARGET-CONTENT", "EDITED"), "demo-book__q2"),
        )
        db_conn.commit()
        rebuilt = preview.render_preview(db_conn, Q, cache_root=root, builder=builder)
        assert rebuilt.status == "built"  # target hash changed -> cache miss
        assert builder.calls == 2
        # The public contract is untouched: source_hash stays the question's own.
        assert rebuilt.source_hash == preview.source_digest(self.XSOURCE)


class TestRenderAssembled:
    """The generic engine `render_preview` delegates to, also used by callers
    that build *synthesized* PreTeXt (e.g. the DnD legend, B4.15/LM-2)."""

    def test_builds_then_serves_from_cache_under_cache_name(self, tmp_path):
        builder = _CountingBuilder()
        root = tmp_path / "p"
        first = preview.render_assembled(
            "<p>hi</p>", book="demo-book", cache_name="demo-book__q1.legend",
            title="Legend", cache_root=root, builder=builder,
        )
        assert first.status == "built" and builder.calls == 1
        assert (root / "demo-book" / "demo-book__q1.legend.html").is_file()
        second = preview.render_assembled(
            "<p>hi</p>", book="demo-book", cache_name="demo-book__q1.legend",
            title="Legend", cache_root=root, builder=builder,
        )
        assert second.status == "cached" and builder.calls == 1
        assert second.question_id == "demo-book__q1.legend"

    def test_distinct_cache_name_does_not_collide(self, tmp_path):
        # Same book, different cache_name (a question and its legend) → separate
        # cache files; neither overwrites the other.
        root = tmp_path / "p"
        b = _CountingBuilder()
        preview.render_assembled(
            "<p>a</p>", book="demo-book", cache_name="q", title="Q",
            cache_root=root, builder=b,
        )
        preview.render_assembled(
            "<p>b</p>", book="demo-book", cache_name="q.legend", title="L",
            cache_root=root, builder=b,
        )
        assert (root / "demo-book" / "q.html").is_file()
        assert (root / "demo-book" / "q.legend.html").is_file()
        assert b.calls == 2

    def test_unavailable_without_a_builder(self, tmp_path):
        result = preview.render_assembled(
            "<p>x</p>", book="demo-book", cache_name="q.legend", title="L",
            cache_root=tmp_path, builder=None,
        )
        assert result.status == "unavailable"

    def test_error_when_build_raises(self, tmp_path):
        def boom(_doc):
            raise RuntimeError("nope")

        result = preview.render_assembled(
            "<p>x</p>", book="demo-book", cache_name="q.legend", title="L",
            cache_root=tmp_path, builder=boom,
        )
        assert result.status == "error" and "nope" in (result.error or "")

    def test_macros_injected_into_assembled_doc(self, tmp_path):
        cap = _CapturingBuilder()
        preview.render_assembled(
            r"<m>\vvec</m>", book="demo-book", cache_name="q.legend", title="L",
            macros=r"<macros>\newcommand{\vvec}{\vec}</macros>",
            cache_root=tmp_path, builder=cap,
        )
        assert r"\newcommand{\vvec}" in cap.docs[0]

    def test_rebuilds_when_title_changes_under_same_cache_name(self, tmp_path):
        # title shapes the wrapper, so a different title with the same source +
        # cache_name must rebuild, not serve the stale (old-title) HTML.
        root = tmp_path / "p"
        b = _CountingBuilder()
        preview.render_assembled(
            "<p>x</p>", book="demo-book", cache_name="q", title="A",
            cache_root=root, builder=b,
        )
        second = preview.render_assembled(
            "<p>x</p>", book="demo-book", cache_name="q", title="B",
            cache_root=root, builder=b,
        )
        assert second.status == "built" and b.calls == 2

    def test_rebuilds_when_wrap_option_changes_under_same_cache_name(self, tmp_path):
        # wrap_in_section changes the wrapper structure; same source + cache_name
        # with a different wrap must rebuild.
        root = tmp_path / "p"
        b = _CountingBuilder()
        preview.render_assembled(
            "<p>x</p>", book="demo-book", cache_name="q", title="T",
            wrap_in_section=True, cache_root=root, builder=b,
        )
        second = preview.render_assembled(
            "<p>x</p>", book="demo-book", cache_name="q", title="T",
            wrap_in_section=False, cache_root=root, builder=b,
        )
        assert second.status == "built" and b.calls == 2

    def test_rebuilds_when_an_xref_target_changes_under_same_cache_name(self, tmp_path):
        # FU-2: the resolved targets' hashes are cache-key inputs at the engine
        # level too — same source, same cache_name, an edited target rebuilds.
        src = '<p><xref ref="t"/></p>'
        root = tmp_path / "p"
        b = _CountingBuilder()
        preview.render_assembled(
            src, book="demo-book", cache_name="q", title="T",
            xref_targets={"t": '<exercise xml:id="t"><p>v1</p></exercise>'},
            cache_root=root, builder=b,
        )
        second = preview.render_assembled(
            src, book="demo-book", cache_name="q", title="T",
            xref_targets={"t": '<exercise xml:id="t"><p>v2</p></exercise>'},
            cache_root=root, builder=b,
        )
        assert second.status == "built" and b.calls == 2
