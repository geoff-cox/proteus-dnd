"""
Tests for `proteus sync-sources --book`, the SP-2 clone+extract path.

Uses a real ``file://`` clone (no network). The local `--source-dir` mode is
exercised elsewhere (test_cli_dnd); here we cover the clone mode and the
mode-validation / error handling.
"""

from __future__ import annotations

from pathlib import Path

from proteus import cli, db
from proteus.core import registry

BOOK = "demo-book"

# The two short answers in the fixture are extracted only when the SA service
# is there to claim them (cc-task-public-export PR 1, ruling R14).
SA = "sa" in {s.name for s in registry.available_services()}


def _init_db(db_path: Path) -> None:
    conn = db.connect(db_path)
    try:
        db.bootstrap(conn)
    finally:
        conn.close()


class TestCliSyncSourcesBook:
    def test_clones_and_extracts(self, tmp_path, capsys, fixtures_dir, make_git_repo):
        content = (fixtures_dir / "question_sources" / "sec-demo-shapes.ptx").read_text(
            encoding="utf-8"
        )
        repo = make_git_repo(
            tmp_path / "repo", {"source/sec-demo-shapes.ptx": content}
        )
        db_path = tmp_path / "proteus.db"
        _init_db(db_path)
        assert cli.main([
            "--db", str(db_path), "textbook", "add",
            BOOK, f"file://{repo}", "/build",
        ]) == 0
        capsys.readouterr()

        rc = cli.main([
            "--db", str(db_path), "sync-sources", "--book", BOOK,
            "--clone-root", str(tmp_path / "clones"),
            "--output-dir", str(tmp_path / "cache"),
        ])

        assert rc == 0
        out = capsys.readouterr().out
        assert f"book:           {BOOK}" in out
        # Real services: two cardsorts + two short answers (a synthetic
        # <shortanswer> and a real <exercise>+<response/>); DnD builds two boards.
        assert f"extracted:     {4 if SA else 2}" in out
        assert "dnd source handler: created=2" in out
        assert (tmp_path / "cache" / BOOK / f"{BOOK}__demo-shapes-q1.ptx").is_file()

    def test_title_backfill_reported_in_summary(self, tmp_path, capsys, fixtures_dir):
        # FU-3's backfill caveat, at the CLI surface: rows synced before
        # migration 0014 (title NULL, snippet unchanged) are repopulated by a
        # plain re-run, and the summary says so. Uses --source-dir (no clone).
        db_path = tmp_path / "proteus.db"
        _init_db(db_path)
        src = tmp_path / "src"
        src.mkdir()
        content = (fixtures_dir / "question_sources" / "sec-demo-shapes.ptx").read_text(
            encoding="utf-8"
        )
        (src / "sec-demo-shapes.ptx").write_text(content, encoding="utf-8")
        conn = db.connect(db_path)
        # The retired baseline seed row (migration 0010) consumed AUTOINCREMENT
        # id 1, so read the real id back rather than assuming it.
        tid = conn.execute(
            "INSERT INTO textbooks (git_repo_url, git_ref, book_xml_id, build_dir) "
            f"VALUES ('repo', 'ref', '{BOOK}', 'build')"
        ).lastrowid
        conn.commit()

        args = [
            "--db", str(db_path), "sync-sources",
            "--source-dir", str(src), "--textbook-id", str(tid),
            "--output-dir", str(tmp_path / "cache"),
        ]
        assert cli.main(args) == 0
        # A fresh sync has nothing to backfill; the line stays out of the summary.
        assert "titles backfilled" not in capsys.readouterr().out

        # Simulate rows synced before the questions.title column existed.
        conn.execute("UPDATE questions SET title = NULL")
        conn.commit()
        conn.close()

        assert cli.main(args) == 0
        out = capsys.readouterr().out
        # The two cardsorts carry titles; the two short answers author none
        # (and are not catalogued at all without the SA service).
        assert "titles backfilled: 2" in out
        assert f"unchanged:     {2 if SA else 0}" in out

    def test_unregistered_book_is_clean_error(self, tmp_path, capsys):
        db_path = tmp_path / "proteus.db"
        _init_db(db_path)
        rc = cli.main(["--db", str(db_path), "sync-sources", "--book", "ghost"])
        assert rc == 1
        assert "not registered" in capsys.readouterr().err

    def test_requires_exactly_one_mode(self, tmp_path, capsys):
        db_path = tmp_path / "proteus.db"
        _init_db(db_path)
        # neither --book nor --source-dir
        assert cli.main(["--db", str(db_path), "sync-sources"]) == 1
        assert "exactly one" in capsys.readouterr().err
        # both
        assert cli.main([
            "--db", str(db_path), "sync-sources",
            "--book", "b", "--source-dir", str(tmp_path),
        ]) == 1
        assert "exactly one" in capsys.readouterr().err

    def test_source_dir_requires_textbook_id(self, tmp_path, capsys):
        db_path = tmp_path / "proteus.db"
        _init_db(db_path)
        rc = cli.main([
            "--db", str(db_path), "sync-sources", "--source-dir", str(tmp_path),
        ])
        assert rc == 1
        assert "requires --textbook-id" in capsys.readouterr().err
