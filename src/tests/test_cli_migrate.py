"""
Tests for the `proteus migrate` CLI subcommand (plan item A0.2).

Verify the CLI is a thin wrapper around `core.migrate`:
- on a fresh database there is nothing to do;
- `--dry-run` previews pending migrations without applying them;
- a real upgrade applies the pending migrations and reports the new version;
- a missing database is a clean error, not a traceback.

A "previous-release" database is simulated by applying the frozen v3 baseline
alone (the `baseline_v3_script` fixture: core + service schema files, without
any 0004+ migrations) — exactly what an existing database from before the
multi-service refactor looks like.
"""

from __future__ import annotations

from pathlib import Path

from proteus import cli, db
from proteus.core import migrate


def _head_version() -> int:
    return max(m.version for m in migrate.discover_migrations())


def _fresh_db(db_path: Path) -> None:
    conn = db.connect(db_path)
    try:
        db.bootstrap(conn)
    finally:
        conn.close()


def _v3_baseline_db(db_path: Path, baseline_script: str) -> None:
    """A database at the frozen v3 baseline, no migrations run."""
    conn = db.connect(db_path)
    try:
        conn.executescript(baseline_script)
        conn.commit()
    finally:
        conn.close()


class TestCliMigrate:
    def test_fresh_database_reports_up_to_date(self, tmp_path, capsys):
        db_path = tmp_path / "proteus.db"
        _fresh_db(db_path)

        exit_code = cli.main(["--db", str(db_path), "migrate"])

        assert exit_code == 0
        out = capsys.readouterr().out
        assert "already up to date" in out
        assert f"version {_head_version()}" in out

    def test_dry_run_on_fresh_database_reports_none_pending(self, tmp_path, capsys):
        db_path = tmp_path / "proteus.db"
        _fresh_db(db_path)

        exit_code = cli.main(["--db", str(db_path), "migrate", "--dry-run"])

        assert exit_code == 0
        out = capsys.readouterr().out
        assert "no migrations pending" in out

    def test_dry_run_lists_pending_without_applying(
        self, tmp_path, capsys, baseline_v3_script
    ):
        db_path = tmp_path / "proteus.db"
        _v3_baseline_db(db_path, baseline_v3_script)

        exit_code = cli.main(["--db", str(db_path), "migrate", "--dry-run"])

        assert exit_code == 0
        out = capsys.readouterr().out
        assert "pending migrations:" in out
        assert "0004_events" in out

        # Still at the baseline: dry-run applied nothing.
        conn = db.connect(db_path)
        try:
            head = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
            assert head == 3
        finally:
            conn.close()

    def test_applies_pending_migrations(self, tmp_path, capsys, baseline_v3_script):
        db_path = tmp_path / "proteus.db"
        _v3_baseline_db(db_path, baseline_v3_script)

        exit_code = cli.main(["--db", str(db_path), "migrate"])

        assert exit_code == 0
        out = capsys.readouterr().out
        assert "Applied" in out
        assert "0004_events" in out
        assert f"schema version {_head_version()}" in out

        conn = db.connect(db_path)
        try:
            head = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
            assert head == _head_version()
        finally:
            conn.close()

    def test_missing_database_is_clean_error(self, tmp_path, capsys):
        db_path = tmp_path / "nope.db"
        exit_code = cli.main(["--db", str(db_path), "migrate"])
        assert exit_code == 1
        err = capsys.readouterr().err
        assert "No database" in err
