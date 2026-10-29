"""Tests for the `proteus textbook add` CLI subcommand (checklist item SP-1)."""

from __future__ import annotations

from pathlib import Path

from proteus import cli, db


def _init_db(db_path: Path) -> None:
    conn = db.connect(db_path)
    try:
        db.bootstrap(conn)
    finally:
        conn.close()


class TestCliTextbookAdd:
    def test_registers_a_textbook(self, tmp_path, capsys):
        db_path = tmp_path / "proteus.db"
        _init_db(db_path)

        rc = cli.main([
            "--db", str(db_path), "textbook", "add",
            "ula-proteus", "https://example.test/ula.git", "/build/ula",
        ])

        assert rc == 0
        assert "Registered textbook 'ula-proteus'" in capsys.readouterr().out

        conn = db.connect(db_path)
        try:
            row = conn.execute(
                "SELECT git_repo_url, build_dir, git_ref FROM textbooks "
                "WHERE book_xml_id = 'ula-proteus'"
            ).fetchone()
            assert row["git_repo_url"] == "https://example.test/ula.git"
            assert row["build_dir"] == "/build/ula"
            assert row["git_ref"] == "HEAD"
        finally:
            conn.close()

    def test_duplicate_is_clean_error(self, tmp_path, capsys):
        db_path = tmp_path / "proteus.db"
        _init_db(db_path)
        args = ["--db", str(db_path), "textbook", "add",
                "b", "https://x/y.git", "/b"]
        assert cli.main(args) == 0
        capsys.readouterr()

        assert cli.main(args) == 1
        assert "already registered" in capsys.readouterr().err

    def test_same_repo_ref_is_clean_error(self, tmp_path, capsys):
        # UNIQUE(git_repo_url, git_ref) must surface as a clean error, not a
        # traceback (exit 1).
        db_path = tmp_path / "proteus.db"
        _init_db(db_path)
        assert cli.main([
            "--db", str(db_path), "textbook", "add",
            "book-a", "https://x/shared.git", "/a",
        ]) == 0
        capsys.readouterr()

        rc = cli.main([
            "--db", str(db_path), "textbook", "add",
            "book-b", "https://x/shared.git", "/b",
        ])
        assert rc == 1
        assert "already registered for another book" in capsys.readouterr().err

    def test_credentialed_url_is_clean_error(self, tmp_path, capsys):
        db_path = tmp_path / "proteus.db"
        _init_db(db_path)
        rc = cli.main([
            "--db", str(db_path), "textbook", "add",
            "b", "git@github.com:x/y.git", "/b",
        ])
        assert rc == 1
        assert "public repos only" in capsys.readouterr().err

    def test_no_subcommand_is_clean_error(self, tmp_path, capsys):
        db_path = tmp_path / "proteus.db"
        _init_db(db_path)
        rc = cli.main(["--db", str(db_path), "textbook"])
        assert rc == 1
        assert "needs a subcommand" in capsys.readouterr().err

    def test_missing_database_is_clean_error(self, tmp_path, capsys):
        db_path = tmp_path / "nope.db"
        rc = cli.main([
            "--db", str(db_path), "textbook", "add", "b", "https://x/y.git", "/b",
        ])
        assert rc == 1
        assert "No database" in capsys.readouterr().err
