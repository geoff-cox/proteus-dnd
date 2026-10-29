"""
Tests for the `proteus sync-questions` CLI subcommand (item 18, Phase 3.5).

These verify the CLI is a thin wrapper around `sync_questions`:
- a successful run prints a summary line and exits 0;
- an answered-but-not-catalogued response surfaces as a WARNING
  log record (the orchestrator emits it; the CLI must not swallow it).
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from proteus import cli, db
from proteus.core import registry

# Service-conditional cases (cc-task-public-export PR 1, ruling R14): keyed on
# registry discovery, so the same file runs in the private tree (sa + dnd) and
# in the public export (dnd only).
SA = "sa" in {s.name for s in registry.available_services()}
requires_sa = pytest.mark.skipif(
    not SA, reason="the Short Answer service is not in this tree (R14)"
)


BOOK = "testbook"

SECTION_HTML = """<!DOCTYPE html>
<html>
<head><title>Section A</title></head>
<body class="pretext">
<div id="latex-macros">\\(\\amp\\)</div>
<article class="exercise-like reading-question" id="ex1">
  <h2 class="heading">Exercise 1</h2>
  <div data-component="shortanswer" id="testbook__ex1">Question one.</div>
</article>
<article class="exercise-like reading-question" id="ex2">
  <h2 class="heading">Exercise 2</h2>
  <div data-component="shortanswer" id="testbook__ex2">Question two.</div>
</article>
</body>
</html>
"""


def _write_build_dir(build_dir: Path) -> None:
    build_dir.mkdir(parents=True, exist_ok=True)
    (build_dir / "sec-a.html").write_text(SECTION_HTML, encoding="utf-8")


def _bootstrap_db(db_path: Path) -> int:
    conn = db.connect(db_path)
    try:
        db.bootstrap(conn)
        cur = conn.execute(
            "INSERT INTO textbooks (git_repo_url, git_ref, book_xml_id, build_dir) "
            "VALUES (?, ?, ?, ?)",
            ("https://example.test/repo", "test-ref", BOOK, "/tmp/build"),
        )
        textbook_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    return textbook_id


def _seed_response(db_path: Path, question_id: str, response_key: str) -> None:
    conn = db.connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO raw_imports (file_path, file_hash, file_format) "
            "VALUES (?, ?, ?)",
            (f"/tmp/{response_key}.tab", f"hash-{response_key}", "datashop_tab"),
        )
        import_id = cur.lastrowid
        conn.execute(
            "INSERT INTO responses (response_id, import_id, student_id, "
            "question_id, response_text, submitted_at) VALUES (?, ?, ?, ?, ?, ?)",
            (response_key, import_id, "stu1", question_id, "answer",
             "2024-01-01T00:00:00+00:00"),
        )
        conn.commit()
    finally:
        conn.close()


class TestSyncQuestionsCLI:
    def test_cli_prints_summary_and_returns_zero(self, tmp_path, capsys):
        db_path = tmp_path / "proteus.db"
        textbook_id = _bootstrap_db(db_path)
        build_dir = tmp_path / "build"
        output_dir = tmp_path / "previews"
        _write_build_dir(build_dir)

        exit_code = cli.main([
            "--db", str(db_path),
            "sync-questions",
            "--build-dir", str(build_dir),
            "--output-dir", str(output_dir),
            "--textbook-id", str(textbook_id),
        ])

        assert exit_code == 0
        out = capsys.readouterr().out
        assert "Sync complete:" in out
        assert f"textbook_id:                  {textbook_id}" in out
        assert "extracted:                    2" in out
        assert "updated:                      0" in out
        assert "unchanged:                    0" in out
        assert "answered_but_not_catalogued:  0" in out
        assert "not_yet_answered:             2" in out

        # And the files actually landed where the summary implies.
        written = sorted((output_dir / str(textbook_id)).glob("*.html"))
        assert {f.name for f in written} == {"ex1.html", "ex2.html"}

    @requires_sa  # seeds the SA service's responses table
    def test_cli_emits_warning_for_uncatalogued_response(self, tmp_path, caplog):
        db_path = tmp_path / "proteus.db"
        textbook_id = _bootstrap_db(db_path)
        build_dir = tmp_path / "build"
        output_dir = tmp_path / "previews"
        _write_build_dir(build_dir)

        uncatalogued = f"{BOOK}__nonexistent"
        _seed_response(db_path, uncatalogued, "r1")

        with caplog.at_level(logging.WARNING, logger="proteus.sync.questions"):
            exit_code = cli.main([
                "--db", str(db_path),
                "sync-questions",
                "--build-dir", str(build_dir),
                "--output-dir", str(output_dir),
                "--textbook-id", str(textbook_id),
            ])

        assert exit_code == 0
        warnings = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING
            and r.name == "proteus.sync.questions"
        ]
        assert warnings, (
            "Expected at least one WARNING from proteus.sync.questions when "
            "the DB contains an answered-but-not-catalogued question; got none."
        )
        assert any(uncatalogued in r.getMessage() for r in warnings), (
            f"Expected uncatalogued question_id {uncatalogued!r} in a WARNING "
            f"message; got: {[r.getMessage() for r in warnings]}"
        )
