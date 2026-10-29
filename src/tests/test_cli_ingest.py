"""
Tests for the A3.3 CLI surface:

- `proteus ingest` now runs the shared event ingest (every row → `events`,
  per-selection counts printed) and no longer writes `responses` itself;
- `proteus ingest --derive` chains every available service's derive over the
  new import, restoring the old one-command UX;
- `proteus sa derive` materializes responses from already-ingested events,
  with `--question` / `--import-id` filters.
"""

from __future__ import annotations

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


def _init_db(db_path: Path) -> None:
    conn = db.connect(db_path)
    try:
        db.bootstrap(conn)
    finally:
        conn.close()


def _count(db_path: Path, table: str, where: str = "1=1") -> int:
    conn = db.connect(db_path)
    try:
        return conn.execute(f"SELECT count(*) FROM {table} WHERE {where}").fetchone()[0]
    finally:
        conn.close()


def _golden_rows(db_path: Path) -> list[dict]:
    conn = db.connect(db_path)
    try:
        cur = conn.execute(
            "SELECT response_id, import_id, student_id, question_id, "
            "response_text, submitted_at, course_id "
            "FROM responses ORDER BY response_id"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, tuple(r))) for r in cur.fetchall()]
    finally:
        conn.close()


class TestCliIngest:
    def test_ingest_writes_events_not_responses(self, tmp_path, capsys, golden_core_tab):
        db_path = tmp_path / "proteus.db"
        _init_db(db_path)

        exit_code = cli.main(["--db", str(db_path), "ingest", str(golden_core_tab)])

        assert exit_code == 0
        out = capsys.readouterr().out
        assert "Ingest complete:" in out
        assert "inserted:   33" in out
        assert "shortanswer=22" in out
        assert "dragNdrop-drop=3" in out
        assert _count(db_path, "events") == 33
        if SA:  # the responses table exists only with the SA service
            assert _count(db_path, "responses") == 0

    @requires_sa
    def test_ingest_derive_reproduces_golden_baseline(
        self, tmp_path, capsys, golden_sa_tab, golden_sa_responses
    ):
        db_path = tmp_path / "proteus.db"
        _init_db(db_path)

        exit_code = cli.main(
            ["--db", str(db_path), "ingest", "--derive", str(golden_sa_tab)]
        )

        assert exit_code == 0
        out = capsys.readouterr().out
        assert "sa derive:" in out
        assert _golden_rows(db_path) == golden_sa_responses

    @requires_sa
    def test_ingest_derive_question_filter(
        self, tmp_path, golden_sa_tab, golden_sa_responses
    ):
        db_path = tmp_path / "proteus.db"
        _init_db(db_path)
        target = golden_sa_responses[0]["question_id"]
        expected = [r for r in golden_sa_responses if r["question_id"] == target]

        exit_code = cli.main(
            [
                "--db", str(db_path), "ingest", "--derive",
                "--question", target, str(golden_sa_tab),
            ]
        )

        assert exit_code == 0
        # Events still capture the whole file; only the derive was filtered.
        assert _count(db_path, "events") == 33
        assert _golden_rows(db_path) == expected

    def test_question_without_derive_is_an_error(self, tmp_path, capsys, golden_core_tab):
        db_path = tmp_path / "proteus.db"
        _init_db(db_path)

        exit_code = cli.main(
            ["--db", str(db_path), "ingest", "--question", "q1", str(golden_core_tab)]
        )

        assert exit_code == 1
        assert "--derive" in capsys.readouterr().err
        assert _count(db_path, "events") == 0

    def test_pre_migration_db_gets_clean_guard(
        self, tmp_path, capsys, golden_core_tab, baseline_v3_script
    ):
        # B2/ROB-02: a previous-release (v3-baseline) database has no
        # events table; ingest must answer with the migrate hint and exit
        # 1 — never a raw OperationalError traceback — and register no
        # raw_imports row.
        db_path = tmp_path / "old.db"
        conn = db.connect(db_path)
        try:
            conn.executescript(baseline_v3_script)
            conn.commit()
        finally:
            conn.close()

        exit_code = cli.main(
            ["--db", str(db_path), "ingest", str(golden_core_tab)]
        )

        assert exit_code == 1
        err = capsys.readouterr().err
        assert "No events table" in err
        assert "proteus migrate" in err
        assert _count(db_path, "raw_imports") == 0

    def test_failing_service_derive_is_isolated(
        self, tmp_path, capsys, golden_core_tab, monkeypatch
    ):
        # B1/ROB-01: one service's derive failure must not take the others
        # down. The first service with a derive blows up here; every other
        # service still derives, a FAILED line prints instead of a traceback,
        # the exit code is non-zero, and the events ingest itself stays
        # committed. Keyed on discovery (R14): in a one-service tree the
        # "others" are none, and the rest of the contract still holds.
        import dataclasses

        db_path = tmp_path / "proteus.db"
        _init_db(db_path)

        services = [s for s in registry.available_services() if s.derive is not None]
        victim, others = services[0], services[1:]

        def boom(conn, *, import_id, question_id=None):
            raise RuntimeError("synthetic derive breakage")

        patched = [
            dataclasses.replace(svc, derive=boom) if svc.name == victim.name else svc
            for svc in registry.available_services()
        ]
        monkeypatch.setattr(registry, "available_services", lambda: patched)

        exit_code = cli.main([
            "--db", str(db_path), "ingest", str(golden_core_tab), "--derive",
        ])

        assert exit_code == 1
        captured = capsys.readouterr()
        assert f"FAILED ({victim.name}): synthetic derive breakage" in captured.err
        assert "derive step(s) failed" in captured.err
        # Every other service's derive still ran and reported.
        for other in others:
            assert f"{other.name} derive:" in captured.out
        # The shared event ingest is committed; only the victim's rows are
        # absent (its own blast-radius count says so), the others' present.
        assert _count(db_path, "events") == 33
        conn = db.connect(db_path)
        try:
            assert not any(victim.delete_import_rows(conn, 1, dry_run=True).values())
            for other in others:
                assert any(other.delete_import_rows(conn, 1, dry_run=True).values())
        finally:
            conn.close()

    def test_reingest_same_file_refused(self, tmp_path, capsys, golden_core_tab):
        db_path = tmp_path / "proteus.db"
        _init_db(db_path)
        assert cli.main(["--db", str(db_path), "ingest", str(golden_core_tab)]) == 0
        capsys.readouterr()

        exit_code = cli.main(["--db", str(db_path), "ingest", str(golden_core_tab)])

        assert exit_code == 2
        assert "already imported" in capsys.readouterr().err

    def test_ingest_runestone_csv(self, tmp_path, capsys, sample_csv):
        db_path = tmp_path / "proteus.db"
        _init_db(db_path)

        exit_code = cli.main(
            ["--db", str(db_path), "ingest", "--derive", str(sample_csv)]
        )

        assert exit_code == 0
        out = capsys.readouterr().out
        assert "shortanswer=3" in out
        assert "page=2" in out
        assert _count(db_path, "events") == 5
        if SA:  # --derive ran the SA derive over the three shortanswer rows
            assert _count(db_path, "responses") == 3


@requires_sa
class TestCliSaDerive:
    def _ingested_db(self, tmp_path, tab) -> Path:
        db_path = tmp_path / "proteus.db"
        _init_db(db_path)
        assert cli.main(["--db", str(db_path), "ingest", str(tab)]) == 0
        return db_path

    def test_sa_derive_end_to_end(
        self, tmp_path, capsys, golden_sa_tab, golden_sa_responses
    ):
        db_path = self._ingested_db(tmp_path, golden_sa_tab)
        capsys.readouterr()

        exit_code = cli.main(["--db", str(db_path), "sa", "derive"])

        assert exit_code == 0
        out = capsys.readouterr().out
        assert "Derive complete:" in out
        assert "inserted:  21" in out
        assert "malformed: 1" in out
        assert _golden_rows(db_path) == golden_sa_responses

    def test_sa_derive_is_idempotent(self, tmp_path, capsys, golden_sa_tab):
        db_path = self._ingested_db(tmp_path, golden_sa_tab)
        assert cli.main(["--db", str(db_path), "sa", "derive"]) == 0
        capsys.readouterr()

        assert cli.main(["--db", str(db_path), "sa", "derive"]) == 0
        out = capsys.readouterr().out
        assert "inserted:  0" in out
        assert "skipped:   21" in out

    def test_sa_derive_question_filter(
        self, tmp_path, golden_sa_tab, golden_sa_responses
    ):
        db_path = self._ingested_db(tmp_path, golden_sa_tab)
        target = golden_sa_responses[0]["question_id"]
        expected = [r for r in golden_sa_responses if r["question_id"] == target]

        assert cli.main(
            ["--db", str(db_path), "sa", "derive", "--question", target]
        ) == 0
        assert _golden_rows(db_path) == expected

    def test_sa_derive_import_id_filter(self, tmp_path, golden_sa_tab, sample_csv):
        db_path = self._ingested_db(tmp_path, golden_sa_tab)
        assert cli.main(["--db", str(db_path), "ingest", str(sample_csv)]) == 0

        # Derive only the second import (the CSV: 3 shortanswer rows).
        assert cli.main(
            ["--db", str(db_path), "sa", "derive", "--import-id", "2"]
        ) == 0
        assert _count(db_path, "responses") == 3
        assert _count(db_path, "responses", "import_id = 2") == 3

    def test_sa_derive_requires_database(self, tmp_path, capsys):
        exit_code = cli.main(
            ["--db", str(tmp_path / "missing.db"), "sa", "derive"]
        )
        assert exit_code == 1
        assert "run 'init' first" in capsys.readouterr().err
