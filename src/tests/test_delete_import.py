"""
Import-delete cascade tests (checklist item B4.14).

Deleting an import must remove the ``raw_imports`` row, its ``events``, and
**every** service's derived rows for those events (atomically, with a
blast-radius report that matches what is actually removed, and without touching
any other import). The cascade is registry-driven (core never names a service
table), so these tests exercise it end-to-end through a real DnD + SA pipeline.
"""

from __future__ import annotations

import pytest

from proteus.core import imports as imports_mod
from proteus.core import ingest as core_ingest
from proteus.services.dragndrop import process, reconstruct, scoring
from proteus.services.dragndrop import derive as dnd_derive
from proteus.core import registry

# Service-conditional cases (cc-task-public-export PR 1, ruling R14): keyed on
# registry discovery, so the same file runs in the private tree (sa + dnd) and
# in the public export (dnd only).
SA = "sa" in {s.name for s in registry.available_services()}
requires_sa = pytest.mark.skipif(
    not SA, reason="the Short Answer service is not in this tree (R14)"
)
if SA:
    from proteus.services.shortanswer import derive as sa_derive


def _count(conn, table: str, where: str = "", params=()) -> int:
    sql = f"SELECT COUNT(*) FROM {table}"
    if where:
        sql += f" WHERE {where}"
    return conn.execute(sql, params).fetchone()[0]


@pytest.fixture
def populated_import(db_conn, four_shapes_board, golden_dnd_tab):
    """Ingest the DnD golden file and run the full pipeline for both services.

    Returns the import_id. After this the database carries events, dnd_events,
    a reconstructed attempt layer (attempts/configs/scores/metrics), and the
    single short-answer response the file also contains.
    """
    four_shapes_board(db_conn)  # board for demo-book__demo-shapes-q1
    result = core_ingest.ingest_file(db_conn, file_path=golden_dnd_tab)
    import_id = result.import_id

    # Every service's derive, then the DnD reconstruction + scoring + metrics.
    if SA:
        sa_derive.derive_responses(db_conn, import_id=import_id)
    dnd_derive.derive_events(db_conn, import_id=import_id)
    reconstruct.reconstruct_all(db_conn)
    scoring.score_checks(db_conn)
    process.analyze_all(db_conn)
    db_conn.commit()
    return import_id


class TestPopulated:
    def test_pipeline_actually_populated(self, db_conn, populated_import):
        # Guard: the fixture really did fill every layer, so the delete test
        # below is meaningful (deleting from empty tables proves nothing).
        assert _count(db_conn, "events") > 0
        assert _count(db_conn, "dnd_events") > 0
        assert _count(db_conn, "dnd_attempts") > 0
        assert _count(db_conn, "dnd_configurations") > 0
        assert _count(db_conn, "dnd_check_scores") > 0
        if SA:
            assert _count(db_conn, "responses") > 0


class TestBlastRadius:
    def test_counts_match_reality(self, db_conn, populated_import):
        radius = imports_mod.blast_radius(db_conn, populated_import)
        assert radius.exists is True

        # Core: every event for this import, plus the one raw_imports row.
        assert radius.core_counts["events"] == _count(
            db_conn, "events", "import_id = ?", (populated_import,)
        )
        assert radius.core_counts["raw_imports"] == 1

        svc = {s.name: s for s in radius.services}
        # DnD reports its derived layer; counts equal the live tables (this
        # import is the only one, so question-scope == import-scope here).
        assert svc["dnd"].counts["dnd_events"] == _count(db_conn, "dnd_events")
        assert svc["dnd"].counts["dnd_check_scores"] == _count(
            db_conn, "dnd_check_scores"
        )
        assert svc["dnd"].counts["dnd_attempts"] == _count(db_conn, "dnd_attempts")
        if SA:
            # SA reports its responses (the canonical tables are always present).
            assert svc["sa"].counts["responses"] == _count(db_conn, "responses")
            assert "screenings" in svc["sa"].counts  # canonical, even at zero

    def test_blast_radius_is_read_only(self, db_conn, populated_import):
        def snapshot():
            counts = [_count(db_conn, "events"), _count(db_conn, "dnd_events")]
            if SA:
                counts.append(_count(db_conn, "responses"))
            return counts

        before = snapshot()
        imports_mod.blast_radius(db_conn, populated_import)
        assert snapshot() == before

    def test_total_sums_everything(self, db_conn, populated_import):
        radius = imports_mod.blast_radius(db_conn, populated_import)
        expected = sum(radius.core_counts.values()) + sum(
            s.total for s in radius.services
        )
        assert radius.total == expected
        assert radius.total > 0


class TestDelete:
    def test_delete_removes_the_whole_cascade(self, db_conn, populated_import):
        result = imports_mod.delete_import(db_conn, populated_import)
        assert result.exists is True
        assert result.total > 0

        # Nothing left for this import (across core and every service).
        for table in (
            "events",
            "dnd_events",
            "dnd_attempts",
            "dnd_configurations",
            "dnd_segments",
            "dnd_check_scores",
            "dnd_attempt_metrics",
            *(("responses",) if SA else ()),
        ):
            assert _count(db_conn, table) == 0, f"{table} not cleared"
        assert _count(db_conn, "raw_imports", "import_id = ?", (populated_import,)) == 0

    def test_delete_returns_what_it_removed(self, db_conn, populated_import):
        # The realized radius should equal the dry-run radius taken just before.
        before = imports_mod.blast_radius(db_conn, populated_import)
        result = imports_mod.delete_import(db_conn, populated_import)
        assert result.total == before.total
        assert result.core_counts["events"] == before.core_counts["events"]

    def test_no_dangling_foreign_keys(self, db_conn, populated_import):
        imports_mod.delete_import(db_conn, populated_import)
        # PRAGMA foreign_key_check returns one row per violation; expect none.
        assert db_conn.execute("PRAGMA foreign_key_check").fetchall() == []

    def test_delete_is_idempotent_second_call_noop(self, db_conn, populated_import):
        imports_mod.delete_import(db_conn, populated_import)
        again = imports_mod.delete_import(db_conn, populated_import)
        assert again.exists is False
        assert again.total == 0

    def test_missing_import_is_noop(self, db_conn):
        radius = imports_mod.delete_import(db_conn, 9999)
        assert radius.exists is False
        assert radius.total == 0


class TestIsolation:
    @requires_sa
    def test_other_imports_untouched(
        self, db_conn, four_shapes_board, golden_dnd_tab, golden_sa_tab
    ):
        # Import A: the DnD golden (full pipeline). Import B: the SA golden.
        four_shapes_board(db_conn)
        a = core_ingest.ingest_file(db_conn, file_path=golden_dnd_tab).import_id
        dnd_derive.derive_events(db_conn, import_id=a)
        sa_derive.derive_responses(db_conn, import_id=a)
        reconstruct.reconstruct_all(db_conn)
        scoring.score_checks(db_conn)

        b = core_ingest.ingest_file(db_conn, file_path=golden_sa_tab).import_id
        sa_derive.derive_responses(db_conn, import_id=b)
        db_conn.commit()

        b_events = _count(db_conn, "events", "import_id = ?", (b,))
        b_responses = _count(db_conn, "responses", "import_id = ?", (b,))
        assert b_events > 0 and b_responses > 0

        imports_mod.delete_import(db_conn, a)

        # Import A is gone; import B is exactly as it was.
        assert _count(db_conn, "raw_imports", "import_id = ?", (a,)) == 0
        assert _count(db_conn, "events", "import_id = ?", (a,)) == 0
        assert _count(db_conn, "raw_imports", "import_id = ?", (b,)) == 1
        assert _count(db_conn, "events", "import_id = ?", (b,)) == b_events
        assert _count(db_conn, "responses", "import_id = ?", (b,)) == b_responses
        assert db_conn.execute("PRAGMA foreign_key_check").fetchall() == []
