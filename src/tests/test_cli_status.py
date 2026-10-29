"""
Tests for the aggregated `proteus status` (plan item A2.4).

Core counts (imports, events, runs) print first; each available service adds
its own block via descriptor.status(). Degradation cases: a pre-migration
database (no events table / no pipeline_runs.service column) gets a hint, not
a crash; a broken service gets an UNAVAILABLE line while core still prints.
"""

from __future__ import annotations

import importlib
import types
from pathlib import Path

import pytest

from proteus import cli, db
from proteus.core import ingest as core_ingest
from proteus.core.ingest import backfill_events
from proteus.core import registry

# Service-conditional cases (cc-task-public-export PR 1, ruling R14): keyed on
# registry discovery, so the same file runs in the private tree (sa + dnd) and
# in the public export (dnd only).
SA = "sa" in {s.name for s in registry.available_services()}
requires_sa = pytest.mark.skipif(
    not SA, reason="the Short Answer service is not in this tree (R14)"
)


def _seeded_db(tmp_path: Path, tab: Path, legacy_ingest=None) -> Path:
    """Bootstrapped db with ``tab`` ingested: through the core ingest, or —
    given the SA-era ``ingest_file`` — SA-ingested and then events-backfilled
    (the legacy starting point)."""
    db_path = tmp_path / "proteus.db"
    conn = db.connect(db_path)
    try:
        db.bootstrap(conn)
        if legacy_ingest is None:
            core_ingest.ingest_file(conn, tab)
        else:
            legacy_ingest(conn, tab)
            backfill_events(conn, tab)
        conn.commit()
    finally:
        conn.close()
    return db_path


def _v3_baseline_db(tmp_path: Path, baseline_script: str) -> Path:
    """A previous-release database: the v3 baseline alone, no migrations."""
    db_path = tmp_path / "old.db"
    conn = db.connect(db_path)
    try:
        conn.executescript(baseline_script)
        conn.commit()
    finally:
        conn.close()
    return db_path


class TestAggregatedStatus:
    @requires_sa
    def test_core_and_sa_blocks(self, tmp_path, capsys, golden_sa_tab, sa_legacy_ingest):
        db_path = _seeded_db(tmp_path, golden_sa_tab, sa_legacy_ingest)
        assert cli.main(["--db", str(db_path), "sa", "screen"]) == 0
        capsys.readouterr()  # drain screen output

        exit_code = cli.main(["--db", str(db_path), "status"])

        assert exit_code == 0
        out = capsys.readouterr().out
        # Core block.
        assert "imports:   1" in out
        assert "events:    33" in out
        # 2 sa rows: the shared screen run plus its F22 stage-provenance
        # row (A8) — the runs line counts every provenance row, exactly as
        # it already did for DnD's derive/reconstruct stage rows.
        assert "runs:      2 (sa=2)" in out
        # SA contribution.
        assert "Short Answer (sa):" in out
        assert "responses:" in out and "21" in out
        assert "screened:" in out
        assert "INCLUDE=" in out

    def test_fresh_empty_database(self, tmp_path, capsys):
        db_path = tmp_path / "fresh.db"
        conn = db.connect(db_path)
        try:
            db.bootstrap(conn)
        finally:
            conn.close()

        assert cli.main(["--db", str(db_path), "status"]) == 0
        out = capsys.readouterr().out
        assert "imports:   0" in out
        assert "events:    0" in out
        # Every service this tree carries contributes its block.
        for svc in registry.available_services():
            assert f"{svc.title} ({svc.name}):" in out
        # No dispositions yet -> no disposed line (matches old behavior).
        assert "disposed" not in out

    def test_old_database_without_events_gets_hint_not_crash(
        self, tmp_path, capsys, baseline_v3_script
    ):
        db_path = _v3_baseline_db(tmp_path, baseline_v3_script)

        exit_code = cli.main(["--db", str(db_path), "status"])

        assert exit_code == 0
        out = capsys.readouterr().out
        assert "events table missing" in out
        assert "proteus migrate" in out
        # runs falls back to the plain count (no service column pre-0005).
        assert "runs:      0" in out
        if SA:  # the SA block still prints (its tables exist at v3).
            assert "Short Answer (sa):" in out

    def test_broken_service_yields_unavailable_line(
        self, tmp_path, capsys, monkeypatch, golden_core_tab
    ):
        db_path = _seeded_db(tmp_path, golden_core_tab)

        # Break the first service the scan finds (R14: whichever this tree
        # carries), remembering its block title before it is unimportable.
        victim = registry.KNOWN_SERVICE_PACKAGES[0]
        descriptor = importlib.import_module(f"proteus.services.{victim}.service").DESCRIPTOR
        victim_block = f"{descriptor.title} ({descriptor.name}):"
        real_import = registry.importlib.import_module

        def boom(name, *args, **kwargs):
            if name == f"proteus.services.{victim}.service":
                raise RuntimeError("synthetic service breakage")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(
            registry, "importlib", types.SimpleNamespace(import_module=boom)
        )

        exit_code = cli.main(["--db", str(db_path), "status"])

        assert exit_code == 0
        captured = capsys.readouterr()
        # Core block intact.
        assert "imports:   1" in captured.out
        assert "events:    33" in captured.out
        # Service reported unavailable, not crashed.
        assert "UNAVAILABLE" in captured.out
        assert victim_block not in captured.out
