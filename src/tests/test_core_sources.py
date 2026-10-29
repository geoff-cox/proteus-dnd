"""
Tests for the core question-source sync (plan §2.6; the A1.6 slice that
B2.2 builds on).

The walk recognizes elements whose tags registered services claim
(question_types), names each question from the nearest identified ancestor
(xml:id, or Runestone label), writes the snippet cache and
questions.source_xml, and dispatches source handlers per claimed type.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from proteus.core import sources, textbooks
from proteus.core.registry import ServiceDescriptor

BOOK = "demo-book"
FIXTURE = "question_sources/sec-demo-shapes.ptx"


def _fake_service(name="fake", question_types=frozenset({"cardsort"}), handler=None):
    return ServiceDescriptor(
        name=name,
        title="Fake",
        selection_values=frozenset(),
        register_cli=lambda sub: None,
        status=lambda conn: {},
        question_types=question_types,
        source_handler=handler,
    )


@pytest.fixture
def textbook_id(db_conn):
    cur = db_conn.execute(
        "INSERT INTO textbooks (git_repo_url, git_ref, book_xml_id, build_dir) "
        "VALUES ('repo', 'ref', ?, 'build')",
        (BOOK,),
    )
    return cur.lastrowid


@pytest.fixture
def source_dir(tmp_path, fixtures_dir):
    src = tmp_path / "src"
    src.mkdir()
    shutil.copy(fixtures_dir / FIXTURE, src / "sec-demo-shapes.ptx")
    return src


class TestExtractFromFile:
    def test_finds_claimed_elements_with_unit_ids(self, source_dir):
        extracted, ok = sources.extract_from_file(
            source_dir / "sec-demo-shapes.ptx",
            frozenset({"cardsort", "shortanswer"}),
        )
        assert ok
        assert [(q.xml_id, q.question_type) for q in extracted] == [
            ("demo-shapes-q1", "cardsort"),
            ("demo-shapes-q2", "cardsort"),  # Runestone label fallback
            ("demo-shapes-sa1", "shortanswer"),  # synthetic <shortanswer> tag
            ("demo-shapes-sa2", "shortanswer"),  # real <exercise> + <response/>
        ]
        q1 = extracted[0]
        assert "<premise>equilateral triangle</premise>" in q1.source_xml
        assert q1.section_id == "sec-demo-shapes"
        assert q1.title == "Shape categories"

    def test_shortanswer_recognized_by_response_structure(self, source_dir):
        # PreTeXt has no <shortanswer> source tag: an <exercise> with a direct
        # <response/> child is the short-answer marker, and the exercise is the
        # unit. The cardsort exercises (whose <response>s nest inside <match>)
        # must NOT be picked up this way — even with cardsort unclaimed, so the
        # walk descends into them.
        extracted, ok = sources.extract_from_file(
            source_dir / "sec-demo-shapes.ptx", frozenset({"shortanswer"})
        )
        assert ok
        by_id = {q.xml_id: q for q in extracted}
        assert set(by_id) == {"demo-shapes-sa1", "demo-shapes-sa2"}
        assert all(q.question_type == "shortanswer" for q in extracted)
        # The exercise carries its statement + response as the cached snippet.
        sa2 = by_id["demo-shapes-sa2"]
        assert "<response" in sa2.source_xml
        assert "Explain why the system is consistent" in sa2.source_xml

    def test_unclaimed_types_are_invisible(self, source_dir):
        extracted, ok = sources.extract_from_file(
            source_dir / "sec-demo-shapes.ptx", frozenset({"cardsort"})
        )
        assert ok
        assert all(q.question_type == "cardsort" for q in extracted)

    def test_unparseable_file_is_reported_not_fatal(self, tmp_path):
        bad = tmp_path / "bad.ptx"
        bad.write_text("<section>&undefined-entity;</section>", encoding="utf-8")
        extracted, ok = sources.extract_from_file(bad, frozenset({"cardsort"}))
        assert extracted == []
        assert not ok


class TestTitleExtraction:
    """FU-3: the naming unit's <title> is stored as verbatim inner XML;
    NULL when the unit carries none."""

    def test_title_inner_xml_kept_verbatim(self, source_dir):
        extracted, ok = sources.extract_from_file(
            source_dir / "sec-demo-shapes.ptx", frozenset({"cardsort"})
        )
        assert ok
        by_id = {q.xml_id: q for q in extracted}
        assert by_id["demo-shapes-q1"].title == "Shape categories"
        # Math markup is kept as authored — inner XML, not stripped text.
        assert by_id["demo-shapes-q2"].title == r"Sorting <m>2 \times 2</m> matrices"

    def test_title_none_when_unit_has_none(self, source_dir):
        extracted, ok = sources.extract_from_file(
            source_dir / "sec-demo-shapes.ptx", frozenset({"shortanswer"})
        )
        assert ok
        # Neither sa1 nor sa2 authors a <title>.
        assert {q.xml_id: q.title for q in extracted} == {
            "demo-shapes-sa1": None,
            "demo-shapes-sa2": None,
        }

    def test_task_titles_match_the_synthesized_snippet(self, fixtures_dir):
        # A task's stored title is its own <title>, else the container's —
        # exactly the title _synthesize_task_question embeds in the snippet.
        # A cardsort task (tag path) has no fallback: its snippet is the raw
        # <task>, which carries no title.
        extracted, ok = sources.extract_from_file(
            fixtures_dir / "question_sources/sec-demo-tasks.ptx",
            frozenset({"cardsort", "shortanswer"}),
        )
        assert ok
        assert {q.xml_id: q.title for q in extracted} == {
            "demo-tasks-q1-part-a": "Pivots reading",
            "demo-tasks-q1-part-b": "Pivots reading",
            "demo-tasks-q2-sort": None,
            "demo-tasks-q2-explain": None,
            "demo-tasks-expl-a": "An exploration with tasks",
        }

    def test_empty_task_title_does_not_take_the_container_title(self, tmp_path):
        # An authored-but-empty task <title/> is *present*, so the synthesized
        # snippet embeds it rather than the container's title; the stored title
        # must mirror that — empty normalizes to None, never the container's
        # (PR #159 review: the fallback keys off the element being absent).
        src = tmp_path / "sec-empty-title.ptx"
        src.write_text(
            '<section xml:id="sec-empty-title">\n'
            '  <exercise label="et-q1">\n'
            "    <title>Container title</title>\n"
            '    <task label="et-q1-part-a">\n'
            "      <title/>\n"
            "      <statement><p>Explain.</p></statement>\n"
            "      <response/>\n"
            "    </task>\n"
            "  </exercise>\n"
            "</section>\n",
            encoding="utf-8",
        )
        extracted, ok = sources.extract_from_file(src, frozenset({"shortanswer"}))
        assert ok
        (q,) = extracted
        assert q.xml_id == "et-q1-part-a"
        assert q.title is None
        # The snippet embeds the task's own (empty) title, not the container's.
        assert "Container title" not in q.source_xml


class TestMultiTaskExercises:
    """A PreTeXt exercise with <task> children is several questions — Runestone
    logs each task under its own label — each named by its task and made
    self-contained by merging the parent <introduction>."""

    FIXTURE = "question_sources/sec-demo-tasks.ptx"

    def _extract(self, fixtures_dir, claimed):
        extracted, ok = sources.extract_from_file(
            fixtures_dir / self.FIXTURE, claimed
        )
        assert ok
        return extracted

    def test_each_task_is_its_own_question(self, fixtures_dir):
        extracted = self._extract(
            fixtures_dir, frozenset({"cardsort", "shortanswer"})
        )
        by_id = {q.xml_id: q.question_type for q in extracted}
        assert by_id == {
            "demo-tasks-q1-part-a": "shortanswer",
            "demo-tasks-q1-part-b": "shortanswer",
            "demo-tasks-q2-sort": "cardsort",  # mixed-type sibling, via the tag path
            "demo-tasks-q2-explain": "shortanswer",
            "demo-tasks-expl-a": "shortanswer",  # a <task> inside an <exploration>
        }

    def test_container_is_not_a_question(self, fixtures_dir):
        ids = {q.xml_id for q in self._extract(fixtures_dir, frozenset({"shortanswer"}))}
        # The <exercise> and <exploration> containers are not questions themselves.
        assert {"demo-tasks-q1", "demo-tasks-q2", "demo-tasks-expl"} & ids == set()

    def test_task_in_non_exercise_container_suppresses_answer(self, fixtures_dir):
        extracted = self._extract(fixtures_dir, frozenset({"shortanswer"}))
        expl_a = next(q for q in extracted if q.xml_id == "demo-tasks-expl-a")
        # Found inside an <exploration> (not an <exercise>); the statement is kept.
        assert "Find the pivot positions" in expl_a.source_xml
        # The authored solution/answer never reach the snippet a coder previews.
        assert "SECRET-SOLUTION" not in expl_a.source_xml
        assert "SECRET-ANSWER" not in expl_a.source_xml
        assert "<solution" not in expl_a.source_xml
        assert "<answer" not in expl_a.source_xml

    def test_introduction_merged_into_each_part(self, fixtures_dir):
        extracted = self._extract(fixtures_dir, frozenset({"shortanswer"}))
        part_a = next(q for q in extracted if q.xml_id == "demo-tasks-q1-part-a")
        # The shared introduction and the task's own statement are both present,
        # so the standalone snippet carries its context; named by the task.
        assert part_a.source_xml.strip().startswith("<exercise")
        assert 'label="demo-tasks-q1-part-a"' in part_a.source_xml
        assert "reduced row echelon form" in part_a.source_xml  # introduction
        assert "which unknowns are determined" in part_a.source_xml  # task statement
        assert "<response" in part_a.source_xml


class TestSyncQuestionSources:
    def test_sync_writes_rows_and_cache(
        self, db_conn, textbook_id, source_dir, tmp_path
    ):
        out = tmp_path / "cache"
        result = sources.sync_question_sources(
            db_conn, source_dir, textbook_id,
            output_dir=out, services=(_fake_service(),),
        )
        assert result.extracted == 2
        assert result.updated == 0
        assert result.skipped_files == ()

        row = db_conn.execute(
            "SELECT * FROM questions WHERE question_id = ?",
            (f"{BOOK}__demo-shapes-q1",),
        ).fetchone()
        assert row["question_type"] == "cardsort"
        assert row["xml_id"] == "demo-shapes-q1"
        assert "<cardsort>" in row["source_xml"]
        assert row["title"] == "Shape categories"
        assert '"question_type": "cardsort"' in row["metadata_json"]

        cache = out / BOOK / f"{BOOK}__demo-shapes-q1.ptx"
        assert cache.is_file()
        assert "<cardsort>" in cache.read_text(encoding="utf-8")

    def test_sync_finds_xml_extension_sources(
        self, db_conn, textbook_id, fixtures_dir, tmp_path
    ):
        # Some PreTeXt books ship source as the older `.xml` extension, not
        # `.ptx`. The walk must find both — otherwise such a book extracts zero
        # questions and the loop never runs (regression: PR #58, the `ula`
        # book's section*.xml were skipped, so no boards built and the B4.4
        # board-backed review could not run).
        src = tmp_path / "src"
        src.mkdir()
        content = (fixtures_dir / FIXTURE).read_text(encoding="utf-8")
        (src / "sec-demo-shapes.xml").write_text(content, encoding="utf-8")

        result = sources.sync_question_sources(
            db_conn, src, textbook_id,
            output_dir=tmp_path / "cache", services=(_fake_service(),),
        )
        assert result.extracted == 2  # both cardsort questions, from a .xml file
        # The cached snippet keeps the `.ptx` output extension regardless of the
        # input file's extension (the output is re-serialized PreTeXt).
        assert (tmp_path / "cache" / BOOK / f"{BOOK}__demo-shapes-q1.ptx").is_file()

    def test_resync_is_idempotent(self, db_conn, textbook_id, source_dir, tmp_path):
        kwargs = dict(output_dir=tmp_path / "cache", services=(_fake_service(),))
        sources.sync_question_sources(db_conn, source_dir, textbook_id, **kwargs)
        again = sources.sync_question_sources(
            db_conn, source_dir, textbook_id, **kwargs
        )
        assert again.extracted == 0
        assert again.updated == 0
        assert again.unchanged == 2

    def test_changed_source_is_updated(
        self, db_conn, textbook_id, source_dir, tmp_path
    ):
        kwargs = dict(output_dir=tmp_path / "cache", services=(_fake_service(),))
        sources.sync_question_sources(db_conn, source_dir, textbook_id, **kwargs)
        path = source_dir / "sec-demo-shapes.ptx"
        path.write_text(
            path.read_text(encoding="utf-8").replace("circle", "ellipse"),
            encoding="utf-8",
        )
        result = sources.sync_question_sources(
            db_conn, source_dir, textbook_id, **kwargs
        )
        assert result.updated == 1  # only q1 contains the circle premise
        assert result.unchanged == 1
        row = db_conn.execute(
            "SELECT source_xml FROM questions WHERE question_id = ?",
            (f"{BOOK}__demo-shapes-q1",),
        ).fetchone()
        assert "ellipse" in row["source_xml"]

    def test_resync_backfills_missing_titles(
        self, db_conn, textbook_id, source_dir, tmp_path
    ):
        # FU-3's backfill caveat: rows synced before migration 0014 have
        # title NULL under an unchanged snippet. A plain re-run of
        # sync-sources must repopulate them — and report it separately, not
        # as 'updated' (the snippet did not change) or 'unchanged'.
        kwargs = dict(output_dir=tmp_path / "cache", services=(_fake_service(),))
        sources.sync_question_sources(db_conn, source_dir, textbook_id, **kwargs)
        db_conn.execute("UPDATE questions SET title = NULL")
        db_conn.commit()

        again = sources.sync_question_sources(
            db_conn, source_dir, textbook_id, **kwargs
        )

        assert again.extracted == 0
        assert again.updated == 0
        assert again.unchanged == 0
        assert again.titles_backfilled == 2  # both cardsorts carry titles
        titles = {
            r["question_id"]: r["title"]
            for r in db_conn.execute("SELECT question_id, title FROM questions")
        }
        assert titles == {
            f"{BOOK}__demo-shapes-q1": "Shape categories",
            f"{BOOK}__demo-shapes-q2": r"Sorting <m>2 \times 2</m> matrices",
        }

    def test_cross_file_id_collision_keeps_first_and_warns(
        self, db_conn, textbook_id, tmp_path, caplog
    ):
        # BUG-03 (audit task A3): two files in one walk declaring the same
        # xml:id. Before the guard, last-file-wins silently overwrote the
        # first file's row and cache — and every re-sync churned the row back
        # and forth. The walk now keeps the first (sorted) file's content and
        # warns naming both files, mirroring the intra-file rule.
        import logging

        src = tmp_path / "src"
        src.mkdir()
        declaration = (
            '<section xml:id="sec-{sec}">'
            '<exercise xml:id="dup-q1"><title>{title}</title>'
            "<cardsort><match><premise>{premise}</premise>"
            "<response>A</response></match></cardsort>"
            "</exercise></section>"
        )
        (src / "a-first.ptx").write_text(
            declaration.format(sec="a", title="First declaration", premise="alpha"),
            encoding="utf-8",
        )
        (src / "b-second.ptx").write_text(
            declaration.format(sec="b", title="Second declaration", premise="beta"),
            encoding="utf-8",
        )

        out = tmp_path / "cache"
        kwargs = dict(output_dir=out, services=(_fake_service(),))
        with caplog.at_level(logging.WARNING, logger="proteus.core.sources"):
            result = sources.sync_question_sources(
                db_conn, src, textbook_id, **kwargs
            )

        # Only the first declaration was written; the collision warning names
        # the id and both files.
        assert result.extracted == 1
        collision = [
            r.message
            for r in caplog.records
            if "already extracted" in r.message
        ]
        assert len(collision) == 1
        assert "'dup-q1'" in collision[0]
        assert "a-first.ptx" in collision[0]
        assert "b-second.ptx" in collision[0]

        row = db_conn.execute(
            "SELECT source_xml, title FROM questions WHERE question_id = ?",
            (f"{BOOK}__dup-q1",),
        ).fetchone()
        assert "alpha" in row["source_xml"]
        assert "beta" not in row["source_xml"]
        assert row["title"] == "First declaration"
        cache = out / BOOK / f"{BOOK}__dup-q1.ptx"
        assert "alpha" in cache.read_text(encoding="utf-8")

        # A re-sync is idempotent (no update churn from the losing file) and
        # keeps warning while the collision exists in the source tree. Clear
        # the captured records first so the assertion reads the *second*
        # sync's warning, not a leftover of the first (PR #168 review).
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="proteus.core.sources"):
            again = sources.sync_question_sources(
                db_conn, src, textbook_id, **kwargs
            )
        assert again.updated == 0
        assert again.unchanged == 1
        recollision = [
            r.message
            for r in caplog.records
            if "already extracted" in r.message
        ]
        assert len(recollision) == 1
        assert "'dup-q1'" in recollision[0]

    def test_pre_title_column_database_is_refused(
        self, db_conn, textbook_id, source_dir, tmp_path
    ):
        # A database migrated only through 0013 lacks questions.title; sync
        # must fail fast with the migrate hint, not mid-walk with a bare
        # OperationalError.
        db_conn.execute("ALTER TABLE questions DROP COLUMN title")
        db_conn.commit()
        with pytest.raises(ValueError, match=r"questions\.title"):
            sources.sync_question_sources(
                db_conn, source_dir, textbook_id,
                output_dir=tmp_path / "cache", services=(_fake_service(),),
            )

    def test_cross_textbook_collision_refused(
        self, db_conn, textbook_id, source_dir, tmp_path
    ):
        other = db_conn.execute(
            "INSERT INTO textbooks (git_repo_url, git_ref, book_xml_id, build_dir) "
            "VALUES ('repo2', 'ref2', ?, 'build2')",
            (BOOK,),
        ).lastrowid
        kwargs = dict(output_dir=tmp_path / "cache", services=(_fake_service(),))
        sources.sync_question_sources(db_conn, source_dir, textbook_id, **kwargs)
        with pytest.raises(ValueError, match="refusing to silently re-associate"):
            sources.sync_question_sources(db_conn, source_dir, other, **kwargs)

    def test_adopts_unassociated_catalog_row(
        self, db_conn, textbook_id, source_dir, tmp_path
    ):
        # CD-2: a catalog row (textbook_id NULL, source='catalog', placeholder
        # xml_id) for a question the book contains must be adopted by sync, not
        # refused as a collision (SP-5 flip-to-analyzable).
        qid = f"{BOOK}__demo-shapes-q1"
        db_conn.execute(
            "INSERT INTO questions (question_id, textbook_id, xml_id, "
            "metadata_json, source, question_type) "
            "VALUES (?, NULL, ?, '{}', 'catalog', 'cardsort')",
            (qid, qid),  # xml_id placeholder == question_id, as catalog sets it
        )
        db_conn.commit()

        result = sources.sync_question_sources(
            db_conn, source_dir, textbook_id,
            output_dir=tmp_path / "cache", services=(_fake_service(),),
        )

        # q1 adopted (updated); q2 newly extracted.
        assert result.extracted == 1
        assert result.updated == 1
        row = db_conn.execute(
            "SELECT textbook_id, xml_id, section_id, source, source_xml, title "
            "FROM questions WHERE question_id = ?",
            (qid,),
        ).fetchone()
        assert row["textbook_id"] == textbook_id  # attached
        assert row["xml_id"] == "demo-shapes-q1"  # real xml_id, not placeholder
        assert row["section_id"] == "sec-demo-shapes"
        assert row["source"] == "auto_extracted"  # upgraded from 'catalog'
        assert "<premise>equilateral triangle</premise>" in row["source_xml"]
        assert row["title"] == "Shape categories"  # adoption fills the title too


class TestSyncBookSources:
    """SP-2: clone a registered book's public repo and extract its sources."""

    def test_clone_and_extract_from_file_repo(
        self, db_conn, fixtures_dir, tmp_path, make_git_repo
    ):
        content = (fixtures_dir / FIXTURE).read_text(encoding="utf-8")
        repo = make_git_repo(
            tmp_path / "repo", {"source/sec-demo-shapes.ptx": content}
        )
        textbooks.add_textbook(
            db_conn, book_xml_id=BOOK, git_repo_url=f"file://{repo}", build_dir="/b",
        )

        result = sources.sync_book_sources(
            db_conn, BOOK, clone_root=tmp_path / "clones",
            output_dir=tmp_path / "cache", services=(_fake_service(),),
        )

        assert result.extracted == 2  # two cardsort questions
        row = db_conn.execute(
            "SELECT source_xml FROM questions WHERE question_id = ?",
            (f"{BOOK}__demo-shapes-q1",),
        ).fetchone()
        assert "<premise>equilateral triangle</premise>" in row["source_xml"]
        assert (tmp_path / "cache" / BOOK / f"{BOOK}__demo-shapes-q1.ptx").is_file()
        # The local clone path is recorded on the registry row.
        assert textbooks.get_textbook(db_conn, BOOK)["source_dir"] == str(
            tmp_path / "clones" / BOOK
        )

    def test_unregistered_book_is_refused(self, db_conn, tmp_path):
        with pytest.raises(ValueError, match="not registered"):
            sources.sync_book_sources(
                db_conn, "ghost-book", clone_root=tmp_path / "c",
                output_dir=tmp_path / "o", services=(_fake_service(),),
            )

    def test_cloner_is_injectable(self, db_conn, fixtures_dir, tmp_path):
        # The extract path works with an injected cloner (no git invoked).
        content = (fixtures_dir / FIXTURE).read_text(encoding="utf-8")
        textbooks.add_textbook(
            db_conn, book_xml_id=BOOK,
            git_repo_url="https://example.test/x.git", build_dir="/b",
        )

        def fake_cloner(url, dest, *, git_ref=None):
            dest = Path(dest)
            (dest / "source").mkdir(parents=True, exist_ok=True)
            (dest / "source" / "sec.ptx").write_text(content, encoding="utf-8")
            return dest

        result = sources.sync_book_sources(
            db_conn, BOOK, clone_root=tmp_path / "clones",
            output_dir=tmp_path / "cache", services=(_fake_service(),),
            cloner=fake_cloner,
        )
        assert result.extracted == 2

    def test_captures_book_macros_from_main_file(
        self, db_conn, fixtures_dir, tmp_path
    ):
        # SP-2 follow-up (issue 2): sync stores the book's <docinfo><macros> on
        # the textbook row so the preview wrapper can inject it.
        content = (fixtures_dir / FIXTURE).read_text(encoding="utf-8")
        textbooks.add_textbook(
            db_conn, book_xml_id=BOOK,
            git_repo_url="https://example.test/x.git", build_dir="/b",
        )

        def fake_cloner(url, dest, *, git_ref=None):
            dest = Path(dest)
            (dest / "source").mkdir(parents=True, exist_ok=True)
            (dest / "source" / "sec.ptx").write_text(content, encoding="utf-8")
            (dest / "source" / "main.ptx").write_text(
                "<pretext>\n"
                "  <docinfo>\n"
                "    <macros>\\newcommand{\\RR}{\\mathbb{R}}</macros>\n"
                "  </docinfo>\n"
                '  <book xml:id="demo-book"><title>Demo</title></book>\n'
                "</pretext>\n",
                encoding="utf-8",
            )
            return dest

        sources.sync_book_sources(
            db_conn, BOOK, clone_root=tmp_path / "clones",
            output_dir=tmp_path / "cache", services=(_fake_service(),),
            cloner=fake_cloner,
        )
        macros = textbooks.get_textbook(db_conn, BOOK)["macros"]
        assert macros is not None
        assert "\\newcommand{\\RR}" in macros

    def test_macros_null_when_book_declares_none(
        self, db_conn, fixtures_dir, tmp_path
    ):
        # The main file is found (it has <docinfo>) but declares no macros, so the
        # column is left NULL rather than guessing from another file.
        content = (fixtures_dir / FIXTURE).read_text(encoding="utf-8")
        textbooks.add_textbook(
            db_conn, book_xml_id=BOOK,
            git_repo_url="https://example.test/x.git", build_dir="/b",
        )

        def fake_cloner(url, dest, *, git_ref=None):
            dest = Path(dest)
            (dest / "source").mkdir(parents=True, exist_ok=True)
            (dest / "source" / "sec.ptx").write_text(content, encoding="utf-8")
            (dest / "source" / "main.ptx").write_text(
                "<pretext>\n"
                "  <docinfo><document-id>demo</document-id></docinfo>\n"
                '  <book xml:id="demo-book"><title>Demo</title></book>\n'
                "</pretext>\n",
                encoding="utf-8",
            )
            return dest

        sources.sync_book_sources(
            db_conn, BOOK, clone_root=tmp_path / "clones",
            output_dir=tmp_path / "cache", services=(_fake_service(),),
            cloner=fake_cloner,
        )
        assert textbooks.get_textbook(db_conn, BOOK)["macros"] is None


class TestExtractBookMacros:
    """_extract_book_macros follows xi:include for both <docinfo> and <macros>."""

    def _write(self, d: Path, files: dict[str, str]) -> None:
        for rel, content in files.items():
            p = d / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")

    def test_literal_docinfo_and_macros(self, tmp_path):
        self._write(tmp_path, {
            "source/main.ptx":
                "<pretext>\n  <docinfo>\n"
                "    <macros>\\newcommand{\\RR}{\\mathbb{R}}</macros>\n"
                '  </docinfo>\n  <book xml:id="b"><title>T</title></book>\n</pretext>\n',
        })
        out = sources._extract_book_macros(tmp_path)
        assert out is not None and "\\newcommand{\\RR}" in out

    def test_docinfo_via_xi_include(self, tmp_path):
        # The owner's real layout: <docinfo> lives in bookinfo.ptx, pulled into
        # main.ptx via <xi:include> before <book>.
        self._write(tmp_path, {
            "source/main.ptx":
                '<pretext xmlns:xi="http://www.w3.org/2001/XInclude">\n'
                '  <xi:include href="bookinfo.ptx"/>\n'
                '  <book xml:id="b"><title>T</title></book>\n</pretext>\n',
            "source/bookinfo.ptx":
                "<docinfo>\n  <macros>\\newcommand{\\ZZ}{\\mathbb{Z}}</macros>\n</docinfo>\n",
        })
        out = sources._extract_book_macros(tmp_path)
        assert out is not None and "\\newcommand{\\ZZ}" in out

    def test_macros_via_xi_include_inside_docinfo(self, tmp_path):
        # <macros> resolved relative to the file that declared <docinfo>.
        self._write(tmp_path, {
            "source/main.ptx":
                '<pretext xmlns:xi="http://www.w3.org/2001/XInclude">\n'
                "  <docinfo>\n    <xi:include href=\"macros.ptx\"/>\n  </docinfo>\n"
                '  <book xml:id="b"><title>T</title></book>\n</pretext>\n',
            "source/macros.ptx": "<macros>\\newcommand{\\NN}{\\mathbb{N}}</macros>\n",
        })
        out = sources._extract_book_macros(tmp_path)
        assert out is not None and "\\newcommand{\\NN}" in out

    def test_none_when_docinfo_has_no_macros(self, tmp_path):
        self._write(tmp_path, {
            "source/main.ptx":
                "<pretext>\n  <docinfo><document-id>d</document-id></docinfo>\n"
                '  <book xml:id="b"><title>T</title></book>\n</pretext>\n',
        })
        assert sources._extract_book_macros(tmp_path) is None

    def test_none_when_include_is_unresolvable(self, tmp_path):
        # A missing include target degrades to None, never raises.
        self._write(tmp_path, {
            "source/main.ptx":
                '<pretext xmlns:xi="http://www.w3.org/2001/XInclude">\n'
                '  <xi:include href="missing.ptx"/>\n'
                '  <book xml:id="b"><title>T</title></book>\n</pretext>\n',
        })
        assert sources._extract_book_macros(tmp_path) is None


class TestHandlerDispatch:
    def test_handler_called_per_claimed_question(
        self, db_conn, textbook_id, source_dir, tmp_path
    ):
        seen: list[str] = []

        def handler(conn, question_row):
            seen.append(question_row["question_id"])
            return "created"

        result = sources.sync_question_sources(
            db_conn, source_dir, textbook_id,
            output_dir=tmp_path / "cache",
            services=(_fake_service(handler=handler),),
        )
        assert sorted(seen) == [
            f"{BOOK}__demo-shapes-q1",
            f"{BOOK}__demo-shapes-q2",
        ]
        assert result.handled == {"fake": {"created": 2}}

    def test_handler_exception_contained_per_question(
        self, db_conn, textbook_id, source_dir, tmp_path
    ):
        def handler(conn, question_row):
            if question_row["question_id"].endswith("q1"):
                raise RuntimeError("boom")
            return "created"

        result = sources.sync_question_sources(
            db_conn, source_dir, textbook_id,
            output_dir=tmp_path / "cache",
            services=(_fake_service(handler=handler),),
        )
        assert result.handled == {"fake": {"error": 1, "created": 1}}

    def test_service_without_handler_is_skipped(
        self, db_conn, textbook_id, source_dir, tmp_path
    ):
        result = sources.sync_question_sources(
            db_conn, source_dir, textbook_id,
            output_dir=tmp_path / "cache",
            services=(_fake_service(handler=None),),
        )
        assert result.handled == {}
