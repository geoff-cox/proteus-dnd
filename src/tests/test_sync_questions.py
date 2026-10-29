"""
Tests for the sync_questions orchestrator (item 17, Phase 3.5).

Covers, against tmp_path:
(a) first run writes N files and N rows;
(b) immediate re-run reports all unchanged and rewrites no files (mtimes unchanged);
(c) mutating one parent statement invalidates exactly that parent's child tasks;
(d) a responses row whose question_id is absent from the build appears in the
    answered-but-not-catalogued count;
(e) a catalogued question with no response appears only in the informational
    count, never as a warning.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest

from proteus import db
from proteus.sync.questions import extract_from_section, sync_questions
from proteus.core import registry

# Service-conditional cases (cc-task-public-export PR 1, ruling R14): keyed on
# registry discovery, so the same file runs in the private tree (sa + dnd) and
# in the public export (dnd only).
SA = "sa" in {s.name for s in registry.available_services()}
requires_sa = pytest.mark.skipif(
    not SA, reason="the Short Answer service is not in this tree (R14)"
)


BOOK = "testbook"

# A self-contained PreTeXt-shaped section with one parent exercise (two task
# children, both short-answers) and one standalone exercise (also a short-
# answer). The parent_statement placeholder lets test (c) mutate the parent's
# shared context.
SECTION_A_TEMPLATE = """<!DOCTYPE html>
<html>
<head><title>Section A</title></head>
<body class="pretext">
<div id="latex-macros">\\(\\amp\\)</div>
<article class="exercise-like reading-question" id="ex1">
  <h2 class="heading">Exercise 1</h2>
  <p class="parent-statement">{parent_statement}</p>
  <article class="exercise-like task" id="ex1-a">
    <p>Part a</p>
    <div data-component="shortanswer" id="{book}__ex1-a">What is the rank?</div>
  </article>
  <article class="exercise-like task" id="ex1-b">
    <p>Part b</p>
    <div data-component="shortanswer" id="{book}__ex1-b">What is the nullity?</div>
  </article>
</article>
<article class="exercise-like reading-question" id="ex2">
  <h2 class="heading">Exercise 2</h2>
  <div data-component="shortanswer" id="{book}__ex2">Standalone question.</div>
</article>
</body>
</html>
"""

SECTION_B_TEMPLATE = """<!DOCTYPE html>
<html>
<head><title>Section B</title></head>
<body class="pretext">
<div id="latex-macros">\\(\\amp\\)</div>
<article class="exercise-like reading-question" id="ex3">
  <h2 class="heading">Exercise 3</h2>
  <div data-component="shortanswer" id="{book}__ex3">Section B question.</div>
</article>
</body>
</html>
"""

# A PreTeXt-generated auxiliary preview file: same `<body class="pretext">`
# shape and a matching shortanswer (collides with ex1-a from section A),
# but no `<div id="latex-macros">` block. Sort-order matters: `_preview_*`
# sorts before `sec-*` under rglob, so without the filename skip these
# files "win" the source_hash race.
SECTION_PREVIEW_TEMPLATE = """<!DOCTYPE html>
<html>
<head><title>Auxiliary preview</title></head>
<body class="pretext">
<article class="exercise-like task" id="ex1-a">
  <p>Part a</p>
  <div data-component="shortanswer" id="{book}__ex1-a">What is the rank?</div>
</article>
</body>
</html>
"""

# A file that doesn't match the `_preview_` filename filter but otherwise
# looks auxiliary: `<body class="pretext">` and a shortanswer, but no
# latex-macros block. Stands in for PreTeXt's knowl/index helpers,
# which were the second class of auxiliary file discovered during
# manual verification. Under the positive-filter logic this is skipped
# silently (DEBUG, no WARNING) — the skip is expected, not a tripwire.
SECTION_UNKNOWN_AUX_TEMPLATE = """<!DOCTYPE html>
<html>
<head><title>Unknown auxiliary</title></head>
<body class="pretext">
<article class="exercise-like reading-question" id="ex9-aux">
  <h2 class="heading">Aux Exercise</h2>
  <div data-component="shortanswer" id="{book}__ex9-aux">Auxiliary question.</div>
</article>
</body>
</html>
"""

# A file that has the latex-macros block but lacks the `pretext` body
# class. The positive filter must reject this too — both signals are
# required. Mirrors a hypothetical theme variant or stripped/transformed
# section page.
SECTION_NO_PRETEXT_CLASS_TEMPLATE = """<!DOCTYPE html>
<html>
<head><title>No pretext class</title></head>
<body>
<div id="latex-macros">\\(\\amp\\)</div>
<article class="exercise-like reading-question" id="ex8-noclass">
  <h2 class="heading">No-class Exercise</h2>
  <div data-component="shortanswer" id="{book}__ex8-noclass">Question.</div>
</article>
</body>
</html>
"""

# A PreTeXt <exploration> activity: its child <task>s render without an
# enclosing `<article class="exercise-like">`, so _find_parent_exercise
# returns None and they render standalone. The exploration wrapper itself
# carries `class="exploration"` (no `exercise-like`), which the ancestor
# walk records as the containing environment.
SECTION_EXPLORATION_TEMPLATE = """<!DOCTYPE html>
<html>
<head><title>Section E</title></head>
<body class="pretext">
<div id="latex-macros">\\(\\amp\\)</div>
<article class="exploration" id="expl1">
  <h2 class="heading">Exploration 1</h2>
  <article class="exercise-like task" id="expl1-a">
    <p>Part a</p>
    <div data-component="shortanswer" id="{book}__expl1-a">Explore the rank.</div>
  </article>
  <article class="exercise-like task" id="expl1-b">
    <p>Part b</p>
    <div data-component="shortanswer" id="{book}__expl1-b">Explore the nullity.</div>
  </article>
</article>
</body>
</html>
"""

# A task short-answer with no recognized PreTeXt environment ancestor: the
# task article sits directly under <body>. This shouldn't appear in real
# builds; it documents the 'unknown' fallback of the ancestor walk.
SECTION_BARE_TASK_TEMPLATE = """<!DOCTYPE html>
<html>
<head><title>Bare task</title></head>
<body class="pretext">
<div id="latex-macros">\\(\\amp\\)</div>
<article class="exercise-like task" id="bare-task">
  <p>Orphan task</p>
  <div data-component="shortanswer" id="{book}__bare-task">No recognized ancestor.</div>
</article>
</body>
</html>
"""

EXPECTED_QUESTION_IDS = {
    f"{BOOK}__ex1-a",
    f"{BOOK}__ex1-b",
    f"{BOOK}__ex2",
    f"{BOOK}__ex3",
}


def _write_fixture(build_dir: Path, parent_statement: str = "Consider matrix M.") -> None:
    build_dir.mkdir(parents=True, exist_ok=True)
    (build_dir / "sec-a.html").write_text(
        SECTION_A_TEMPLATE.format(book=BOOK, parent_statement=parent_statement),
        encoding="utf-8",
    )
    (build_dir / "sec-b.html").write_text(
        SECTION_B_TEMPLATE.format(book=BOOK),
        encoding="utf-8",
    )
    # PreTeXt builds include `_preview_*.html` auxiliary files that look
    # like section pages but lack the latex-macros block. They sort before
    # `sec-*` and would otherwise "win" the source_hash race against the
    # real section's good extraction. The filename filter in
    # extract_from_section must skip them; including one in the default
    # fixture ensures every test sees that filter exercised.
    (build_dir / "_preview_ex1-a.html").write_text(
        SECTION_PREVIEW_TEMPLATE.format(book=BOOK),
        encoding="utf-8",
    )


def _setup(tmp_path: Path) -> tuple[sqlite3.Connection, Path, Path, int]:
    """Build a fresh DB + fixture tree and return (conn, build_dir, output_dir, textbook_id)."""
    db_path = tmp_path / "proteus.db"
    conn = db.connect(db_path)
    db.bootstrap(conn)
    cur = conn.execute(
        "INSERT INTO textbooks (git_repo_url, git_ref, book_xml_id, build_dir) "
        "VALUES (?, ?, ?, ?)",
        ("https://example.test/repo", "test-ref", BOOK, "/tmp/testbook-build"),
    )
    textbook_id = cur.lastrowid
    conn.commit()

    build_dir = tmp_path / "build"
    output_dir = tmp_path / "previews"
    _write_fixture(build_dir)
    return conn, build_dir, output_dir, textbook_id


def _seed_response(conn: sqlite3.Connection, question_id: str, response_key: str) -> None:
    """Insert one responses row (and a raw_imports parent) for a given question_id."""
    file_hash = f"hash-{response_key}"
    cur = conn.execute(
        "INSERT INTO raw_imports (file_path, file_hash, file_format) "
        "VALUES (?, ?, ?)",
        (f"/tmp/{response_key}.tab", file_hash, "datashop_tab"),
    )
    import_id = cur.lastrowid
    conn.execute(
        "INSERT INTO responses (response_id, import_id, student_id, "
        "question_id, response_text, submitted_at) VALUES (?, ?, ?, ?, ?, ?)",
        (response_key, import_id, "stu1", question_id, "answer text",
         "2024-01-01T00:00:00+00:00"),
    )
    conn.commit()


class TestSyncQuestions:
    def test_first_run_writes_n_files_and_n_rows(self, tmp_path):
        conn, build_dir, output_dir, textbook_id = _setup(tmp_path)
        try:
            result = sync_questions(conn, build_dir, output_dir, textbook_id)

            assert result.extracted == 4
            assert result.updated == 0
            assert result.unchanged == 0

            files = sorted((output_dir / str(textbook_id)).glob("*.html"))
            assert {f.name for f in files} == {
                "ex1-a.html", "ex1-b.html", "ex2.html", "ex3.html",
            }

            rows = conn.execute(
                "SELECT question_id FROM questions WHERE textbook_id = ?",
                (textbook_id,),
            ).fetchall()
            assert {r["question_id"] for r in rows} == EXPECTED_QUESTION_IDS
        finally:
            conn.close()

    def test_adopts_unassociated_catalog_row(self, tmp_path):
        # CD-2: a catalog row (textbook_id NULL, source='catalog') for a
        # question the build contains must be adopted by sync, not refused as a
        # cross-textbook collision.
        conn, build_dir, output_dir, textbook_id = _setup(tmp_path)
        try:
            qid = f"{BOOK}__ex2"
            conn.execute(
                "INSERT INTO questions (question_id, textbook_id, xml_id, "
                "metadata_json, source) VALUES (?, NULL, ?, '{}', 'catalog')",
                (qid, qid),
            )
            conn.commit()

            result = sync_questions(conn, build_dir, output_dir, textbook_id)

            # ex2 adopted (updated); the other three newly extracted.
            assert result.extracted == 3
            assert result.updated == 1
            row = conn.execute(
                "SELECT textbook_id, xml_id, preview_path, source "
                "FROM questions WHERE question_id = ?",
                (qid,),
            ).fetchone()
            assert row["textbook_id"] == textbook_id  # attached
            assert row["xml_id"] == "ex2"  # real xml_id, not the placeholder
            assert row["preview_path"] == f"{textbook_id}/ex2.html"
            assert row["source"] == "auto_extracted"  # upgraded from 'catalog'
            assert (output_dir / str(textbook_id) / "ex2.html").is_file()
        finally:
            conn.close()

    def test_immediate_rerun_reports_unchanged_and_rewrites_no_files(self, tmp_path):
        conn, build_dir, output_dir, textbook_id = _setup(tmp_path)
        try:
            sync_questions(conn, build_dir, output_dir, textbook_id)
            files = sorted((output_dir / str(textbook_id)).glob("*.html"))
            mtimes_before = {f.name: f.stat().st_mtime_ns for f in files}

            result = sync_questions(conn, build_dir, output_dir, textbook_id)
            assert result.extracted == 0
            assert result.updated == 0
            assert result.unchanged == 4

            mtimes_after = {f.name: f.stat().st_mtime_ns for f in files}
            assert mtimes_after == mtimes_before, (
                "Unchanged questions must not rewrite their preview files."
            )
        finally:
            conn.close()

    def test_unchanged_branch_rewrites_missing_file(self, tmp_path):
        """If a preview file is deleted from disk while its DB row (and
        matching source_hash) survive, the unchanged branch must fall
        through and rewrite the file rather than silently report it
        unchanged. The rewrite is counted as `updated`."""
        conn, build_dir, output_dir, textbook_id = _setup(tmp_path)
        try:
            sync_questions(conn, build_dir, output_dir, textbook_id)

            target = output_dir / str(textbook_id) / "ex2.html"
            original_content = target.read_text(encoding="utf-8")
            target.unlink()
            assert not target.exists()

            result = sync_questions(conn, build_dir, output_dir, textbook_id)

            # (a) the deleted file is back on disk
            assert target.exists()
            # (b) its content equals the original extraction
            assert target.read_text(encoding="utf-8") == original_content
            # (c) the rewrite is counted as `updated`; the three files that
            # were left on disk (hash matching) stay `unchanged`.
            assert result.updated == 1
            assert result.unchanged == 3
            assert result.extracted == 0
        finally:
            conn.close()

    def test_unchanged_branch_skips_when_file_exists(self, tmp_path):
        """Existing behavior is preserved: when the preview file is still
        on disk and the hash matches, no rewrite happens (mtime unchanged)."""
        conn, build_dir, output_dir, textbook_id = _setup(tmp_path)
        try:
            sync_questions(conn, build_dir, output_dir, textbook_id)
            files = sorted((output_dir / str(textbook_id)).glob("*.html"))
            mtimes_before = {f.name: f.stat().st_mtime_ns for f in files}

            result = sync_questions(conn, build_dir, output_dir, textbook_id)
            assert result.unchanged == 4
            assert result.updated == 0
            assert result.extracted == 0

            mtimes_after = {f.name: f.stat().st_mtime_ns for f in files}
            assert mtimes_after == mtimes_before, (
                "A preview that still exists with a matching hash must not "
                "be rewritten."
            )
        finally:
            conn.close()

    def test_exploration_task_records_exploration_environment(self, tmp_path):
        """A task inside a PreTeXt <exploration> has no exercise-like
        parent; the ancestor walk records the exploration wrapper as the
        containing environment."""
        section = tmp_path / "sec-exploration.html"
        section.write_text(
            SECTION_EXPLORATION_TEMPLATE.format(book=BOOK), encoding="utf-8"
        )
        extractions = list(extract_from_section(section))

        assert {e.xml_id for e in extractions} == {"expl1-a", "expl1-b"}
        for e in extractions:
            assert e.metadata["containing_environment"] == "exploration", (
                f"{e.xml_id} should record its exploration ancestor"
            )

    def test_real_exercise_records_exercise_environment(self, tmp_path):
        """Tasks with a real exercise-like parent record that parent's
        inferred type as the containing environment. Section A's parent
        ex1 is `exercise-like reading-question`, so its child tasks report
        `reading-question`."""
        section = tmp_path / "sec-a.html"
        section.write_text(
            SECTION_A_TEMPLATE.format(book=BOOK, parent_statement="Consider matrix M."),
            encoding="utf-8",
        )
        extractions = {e.xml_id: e for e in extract_from_section(section)}

        assert extractions["ex1-a"].metadata["containing_environment"] == "reading-question"
        assert extractions["ex1-b"].metadata["containing_environment"] == "reading-question"

    def test_standalone_exercise_records_own_environment(self, tmp_path):
        """A standalone (non-task) exercise has no parent; its containing
        environment is its own inferred type, not the 'unknown' ancestor-walk
        fallback used for parentless tasks. Section A's ex2 is a standalone
        `exercise-like reading-question`."""
        section = tmp_path / "sec-a.html"
        section.write_text(
            SECTION_A_TEMPLATE.format(book=BOOK, parent_statement="Consider matrix M."),
            encoding="utf-8",
        )
        extractions = {e.xml_id: e for e in extract_from_section(section)}

        assert extractions["ex2"].metadata["containing_environment"] == "reading-question"

    def test_unknown_containing_environment_when_no_recognized_ancestor(self, tmp_path):
        """A task directly under <body> with no recognized PreTeXt
        environment ancestor falls back to 'unknown'."""
        section = tmp_path / "sec-bare.html"
        section.write_text(
            SECTION_BARE_TASK_TEMPLATE.format(book=BOOK), encoding="utf-8"
        )
        extractions = list(extract_from_section(section))

        assert len(extractions) == 1
        assert extractions[0].metadata["containing_environment"] == "unknown"

    def test_mutating_parent_invalidates_only_child_tasks(self, tmp_path):
        conn, build_dir, output_dir, textbook_id = _setup(tmp_path)
        try:
            sync_questions(conn, build_dir, output_dir, textbook_id)

            before_hashes = {
                row["question_id"]: row["source_hash"]
                for row in conn.execute(
                    "SELECT question_id, source_hash FROM questions "
                    "WHERE textbook_id = ?",
                    (textbook_id,),
                )
            }
            files = {
                f.name: f for f in (output_dir / str(textbook_id)).glob("*.html")
            }
            mtimes_before = {name: f.stat().st_mtime_ns for name, f in files.items()}

            # Mutate the parent's shared statement in section A. Its two child
            # tasks share that statement in their render input; the standalone
            # exercise (ex2) in the same section does not; section B is
            # untouched.
            _write_fixture(build_dir, parent_statement="A DIFFERENT statement.")

            result = sync_questions(conn, build_dir, output_dir, textbook_id)
            assert result.extracted == 0
            assert result.updated == 2
            assert result.unchanged == 2

            after_hashes = {
                row["question_id"]: row["source_hash"]
                for row in conn.execute(
                    "SELECT question_id, source_hash FROM questions "
                    "WHERE textbook_id = ?",
                    (textbook_id,),
                )
            }
            changed = {
                qid for qid in before_hashes
                if before_hashes[qid] != after_hashes[qid]
            }
            assert changed == {f"{BOOK}__ex1-a", f"{BOOK}__ex1-b"}

            mtimes_after = {name: f.stat().st_mtime_ns for name, f in files.items()}
            for unchanged_file in ("ex2.html", "ex3.html"):
                assert mtimes_after[unchanged_file] == mtimes_before[unchanged_file], (
                    f"{unchanged_file} should not have been rewritten."
                )
        finally:
            conn.close()

    @requires_sa  # seeds the SA service's responses table
    def test_answered_but_not_catalogued_appears_in_missing(self, tmp_path):
        conn, build_dir, output_dir, textbook_id = _setup(tmp_path)
        try:
            # A response for a question_id that does NOT exist in the build.
            uncatalogued = f"{BOOK}__nonexistent"
            _seed_response(conn, uncatalogued, "r1")
            # Plus a response for a catalogued question, to make sure that one
            # is NOT in `missing`.
            _seed_response(conn, f"{BOOK}__ex2", "r2")

            result = sync_questions(conn, build_dir, output_dir, textbook_id)

            assert uncatalogued in result.missing
            assert f"{BOOK}__ex2" not in result.missing
            assert result.missing == [uncatalogued]
        finally:
            conn.close()

    @requires_sa  # seeds the SA service's responses table
    def test_catalogued_with_no_response_only_in_informational_count(self, tmp_path, caplog):
        conn, build_dir, output_dir, textbook_id = _setup(tmp_path)
        try:
            # One response, for ex2 only. The remaining three catalogued
            # questions have no responses and should land in `orphan`.
            _seed_response(conn, f"{BOOK}__ex2", "r1")

            with caplog.at_level(logging.DEBUG, logger="proteus.sync.questions"):
                result = sync_questions(conn, build_dir, output_dir, textbook_id)

            expected_orphans = {
                f"{BOOK}__ex1-a", f"{BOOK}__ex1-b", f"{BOOK}__ex3",
            }
            assert set(result.orphan) == expected_orphans
            assert result.missing == []

            # Scoped to orphan-related warnings: this test asserts the
            # "no warning for orphans" property, not "no warning ever".
            # Other warnings (e.g., missing _static/) are out of scope.
            orphan_warnings = [
                r for r in caplog.records
                if r.levelno >= logging.WARNING and "no response" in r.getMessage()
            ]
            assert orphan_warnings == [], (
                "Catalogued-but-not-answered questions must never produce a "
                f"WARNING; got: {[r.getMessage() for r in orphan_warnings]}"
            )
        finally:
            conn.close()

    def test_mirrors_static_assets_when_present(self, tmp_path):
        """If build_dir/_static exists, sync_questions copies it alongside
        previews so the cloned <head>'s relative URLs resolve. The
        operation is idempotent across re-runs."""
        conn, build_dir, output_dir, textbook_id = _setup(tmp_path)
        try:
            src_static = build_dir / "_static"
            (src_static / "css").mkdir(parents=True)
            (src_static / "css" / "pretext.css").write_text("body{}", encoding="utf-8")
            (src_static / "mathjax").mkdir()
            (src_static / "mathjax" / "MathJax.js").write_text("/*mj*/", encoding="utf-8")

            sync_questions(conn, build_dir, output_dir, textbook_id)

            dst_static = output_dir / str(textbook_id) / "_static"
            assert (dst_static / "css" / "pretext.css").read_text(encoding="utf-8") == "body{}"
            assert (dst_static / "mathjax" / "MathJax.js").read_text(encoding="utf-8") == "/*mj*/"

            # Re-running with the same inputs must not raise (dirs_exist_ok)
            # and must not corrupt the mirror.
            sync_questions(conn, build_dir, output_dir, textbook_id)
            assert (dst_static / "css" / "pretext.css").read_text(encoding="utf-8") == "body{}"
        finally:
            conn.close()

    def test_auxiliary_files_missing_latex_macros_are_skipped(self, tmp_path, caplog):
        """The positive filter requires both `<body class="pretext">` AND
        a `<div id="latex-macros">` block. Files with the pretext body
        class but no macros block — covering both `_preview_*.html` and
        non-`_preview_` shapes (knowl/index helpers, etc.) — must be
        skipped silently at DEBUG. Filename no longer matters.

        Default fixture already seeds `_preview_ex1-a.html`; this test
        also adds an `unknown-aux.html` to confirm the filename prefix
        is not load-bearing."""
        conn, build_dir, output_dir, textbook_id = _setup(tmp_path)
        try:
            (build_dir / "unknown-aux.html").write_text(
                SECTION_UNKNOWN_AUX_TEMPLATE.format(book=BOOK),
                encoding="utf-8",
            )

            with caplog.at_level(logging.DEBUG, logger="proteus.sync.questions"):
                result = sync_questions(conn, build_dir, output_dir, textbook_id)

            # Real section's macros block makes it to disk; the aux
            # files don't displace it.
            ex1a_html = (output_dir / str(textbook_id) / "ex1-a.html").read_text(
                encoding="utf-8"
            )
            assert 'id="latex-macros"' in ex1a_html

            # No double-counting: the aux files contributed nothing.
            assert result.extracted == 4

            # Skip is DEBUG, not WARNING — auxiliary files are expected
            # now, not a tripwire.
            warnings_about_aux = [
                r.getMessage() for r in caplog.records
                if r.levelno >= logging.WARNING
                and ("_preview_ex1-a.html" in r.getMessage()
                     or "unknown-aux.html" in r.getMessage())
            ]
            assert warnings_about_aux == [], (
                f"Auxiliary files must not produce WARNINGs; got: {warnings_about_aux}"
            )

            skip_messages = [
                r.getMessage() for r in caplog.records
                if r.levelno == logging.DEBUG
                and "not a real PreTeXt section page" in r.getMessage()
            ]
            assert any("_preview_ex1-a.html" in m for m in skip_messages), (
                f"Expected DEBUG skip for _preview_ex1-a.html; got: {skip_messages}"
            )
            assert any("unknown-aux.html" in m for m in skip_messages), (
                f"Expected DEBUG skip for unknown-aux.html (no-`_preview_`-prefix "
                f"shape); got: {skip_messages}"
            )
        finally:
            conn.close()

    def test_file_with_macros_but_no_pretext_body_class_is_skipped(self, tmp_path, caplog):
        """Both signals are required. A file with the latex-macros block
        but a `<body>` without `class="pretext"` must be skipped — the
        positive filter is conjunctive."""
        conn, build_dir, output_dir, textbook_id = _setup(tmp_path)
        try:
            # 'z' > 's': sorts after sec-* so real extractions land first.
            (build_dir / "z-no-pretext-class.html").write_text(
                SECTION_NO_PRETEXT_CLASS_TEMPLATE.format(book=BOOK),
                encoding="utf-8",
            )

            with caplog.at_level(logging.DEBUG, logger="proteus.sync.questions"):
                result = sync_questions(conn, build_dir, output_dir, textbook_id)

            # The no-class file's question (ex8-noclass) must not be in
            # the catalog.
            rows = conn.execute(
                "SELECT question_id FROM questions WHERE textbook_id = ?",
                (textbook_id,),
            ).fetchall()
            assert f"{BOOK}__ex8-noclass" not in {r["question_id"] for r in rows}
            assert result.extracted == 4

            skip_messages = [
                r.getMessage() for r in caplog.records
                if r.levelno == logging.DEBUG
                and "not a real PreTeXt section page" in r.getMessage()
                and "z-no-pretext-class.html" in r.getMessage()
            ]
            assert skip_messages, (
                "Expected DEBUG skip for the file with latex-macros but no "
                "`pretext` body class."
            )
        finally:
            conn.close()

    def test_copytree_failure_warns_but_returns_result(self, tmp_path, caplog, monkeypatch):
        """A copytree OSError (permissions, disk full, transient I/O)
        must not invalidate the otherwise-successful sync. DB rows and
        preview HTML are already committed/written before the mirror;
        the mirror is auxiliary and will catch up on the next run."""
        conn, build_dir, output_dir, textbook_id = _setup(tmp_path)
        try:
            (build_dir / "_static").mkdir()
            (build_dir / "_static" / "marker.css").write_text("x", encoding="utf-8")

            def boom(src, dst, dirs_exist_ok=False):
                raise OSError("simulated permission denied")

            monkeypatch.setattr("proteus.sync.questions.shutil.copytree", boom)

            with caplog.at_level(logging.WARNING, logger="proteus.sync.questions"):
                result = sync_questions(conn, build_dir, output_dir, textbook_id)

            # Catalog + previews intact.
            assert result.extracted == 4
            files = sorted((output_dir / str(textbook_id)).glob("*.html"))
            assert len(files) == 4

            assert any(
                "Failed to mirror" in r.getMessage() and "simulated permission denied" in r.getMessage()
                for r in caplog.records
            ), f"Expected mirror-failure WARNING; got: {[r.getMessage() for r in caplog.records]}"
        finally:
            conn.close()

    def test_missing_static_dir_warns_but_does_not_fail(self, tmp_path, caplog):
        """A build_dir without _static/ is unusual but should not break
        sync — preview HTML still gets written; a WARNING flags the
        missing assets."""
        conn, build_dir, output_dir, textbook_id = _setup(tmp_path)
        try:
            assert not (build_dir / "_static").exists()
            with caplog.at_level(logging.WARNING, logger="proteus.sync.questions"):
                result = sync_questions(conn, build_dir, output_dir, textbook_id)

            assert result.extracted == 4
            assert not (output_dir / str(textbook_id) / "_static").exists()
            assert any(
                "No _static/" in r.getMessage() for r in caplog.records
            ), "Missing _static/ should produce a WARNING for the maintainer."
        finally:
            conn.close()
