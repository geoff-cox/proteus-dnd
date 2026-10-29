"""
Tests for the `proteus sync-catalog` CLI subcommand (checklist item CD-2).

A thin wrapper around core.catalog.sync_catalog: it reconciles the question
catalog from events and prints a summary. Ingest already runs the same reconcile,
so an explicit sync afterward is idempotent (creates nothing new).
"""

from __future__ import annotations

from pathlib import Path

from proteus import cli, db


def _init_db(db_path: Path) -> None:
    conn = db.connect(db_path)
    try:
        db.bootstrap(conn)
    finally:
        conn.close()


class TestCliSyncCatalog:
    def test_syncs_catalog_and_is_idempotent_after_ingest(
        self, tmp_path, capsys, golden_core_tab
    ):
        db_path = tmp_path / "proteus.db"
        _init_db(db_path)
        # Ingest runs the catalog reconcile as a side effect.
        assert cli.main(["--db", str(db_path), "ingest", str(golden_core_tab)]) == 0
        capsys.readouterr()  # drain

        exit_code = cli.main(["--db", str(db_path), "sync-catalog"])

        assert exit_code == 0
        out = capsys.readouterr().out
        assert "Catalog sync complete:" in out
        assert "shortanswer=" in out  # a serviced type from the fixture

        conn = db.connect(db_path)
        try:
            n_questions = conn.execute(
                "SELECT count(*) FROM questions"
            ).fetchone()[0]
            expected = conn.execute(
                "SELECT COUNT(DISTINCT problem_name) FROM events "
                "WHERE selection <> 'page' AND problem_name <> ''"
            ).fetchone()[0]
            assert n_questions == expected > 0
        finally:
            conn.close()

    def test_no_database_is_clean_error(self, tmp_path, capsys):
        db_path = tmp_path / "nope.db"
        exit_code = cli.main(["--db", str(db_path), "sync-catalog"])
        assert exit_code == 1
        err = capsys.readouterr().err
        assert "No database" in err
