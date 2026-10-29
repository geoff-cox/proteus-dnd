"""
Tests for the `proteus reingest-events` CLI subcommand (plan item A1.5).

The backfill path: a database already SA-imported (responses present, no
events) gets its events populated from the original export file, under the
existing import_id, without touching responses. A thin wrapper around
core.ingest.backfill_events.
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


def _sa_import(db_path: Path, tab: Path, legacy_ingest) -> None:
    """The legacy starting point: an SA-era import (responses, no events).
    ``legacy_ingest`` is the ``sa_legacy_ingest`` fixture, which skips the
    test in a tree without the SA service (R14)."""
    conn = db.connect(db_path)
    try:
        legacy_ingest(conn, tab)
        conn.commit()
    finally:
        conn.close()


class TestCliReingestEvents:
    def test_backfills_events_after_sa_import(
        self, tmp_path, capsys, golden_sa_tab, sa_legacy_ingest
    ):
        db_path = tmp_path / "proteus.db"
        _init_db(db_path)
        _sa_import(db_path, golden_sa_tab, sa_legacy_ingest)

        exit_code = cli.main(["--db", str(db_path), "reingest-events", str(golden_sa_tab)])

        assert exit_code == 0
        out = capsys.readouterr().out
        assert "Reingest-events complete:" in out
        assert "inserted:   33" in out
        # Per-selection breakdown surfaces previously-dropped rows.
        assert "shortanswer=22" in out
        assert "dragNdrop-drop=3" in out

        conn = db.connect(db_path)
        try:
            n_events = conn.execute("SELECT count(*) FROM events").fetchone()[0]
            n_responses = conn.execute("SELECT count(*) FROM responses").fetchone()[0]
            assert n_events == 33
            assert n_responses == 21  # untouched
        finally:
            conn.close()

    def test_not_yet_imported_is_clean_error(self, tmp_path, capsys, golden_core_tab):
        db_path = tmp_path / "proteus.db"
        _init_db(db_path)

        exit_code = cli.main(["--db", str(db_path), "reingest-events", str(golden_core_tab)])

        assert exit_code == 1
        err = capsys.readouterr().err
        assert "not yet imported" in err

    def test_idempotent_second_run(self, tmp_path, capsys, golden_sa_tab, sa_legacy_ingest):
        db_path = tmp_path / "proteus.db"
        _init_db(db_path)
        _sa_import(db_path, golden_sa_tab, sa_legacy_ingest)

        cli.main(["--db", str(db_path), "reingest-events", str(golden_sa_tab)])
        capsys.readouterr()  # drain
        exit_code = cli.main(["--db", str(db_path), "reingest-events", str(golden_sa_tab)])

        assert exit_code == 0
        out = capsys.readouterr().out
        assert "inserted:   0" in out
        assert "skipped:    33" in out

    def test_pre_migration_db_gets_clean_guard(
        self, tmp_path, capsys, golden_sa_tab, baseline_v3_script, sa_legacy_ingest
    ):
        # B2/ROB-02: the realistic backfill starting point is an upgraded
        # checkout whose database still lacks the events table — but the
        # migration must run first to create it. Without the guard this
        # died in a raw OperationalError traceback.
        db_path = tmp_path / "old.db"
        conn = db.connect(db_path)
        try:
            conn.executescript(baseline_v3_script)
            conn.commit()
        finally:
            conn.close()
        _sa_import(db_path, golden_sa_tab, sa_legacy_ingest)  # responses exist, events don't

        exit_code = cli.main(
            ["--db", str(db_path), "reingest-events", str(golden_sa_tab)]
        )

        assert exit_code == 1
        err = capsys.readouterr().err
        assert "No events table" in err
        assert "proteus migrate" in err

    def test_missing_database_is_clean_error(self, tmp_path, capsys, golden_core_tab):
        db_path = tmp_path / "nope.db"
        exit_code = cli.main(["--db", str(db_path), "reingest-events", str(golden_core_tab)])
        assert exit_code == 1
        err = capsys.readouterr().err
        assert "No database" in err
