"""
Unit tests for the per-service import-delete handlers (item B4.14).

Each service's ``delete_import_rows(conn, import_id, *, dry_run)`` counts the
rows it would remove for an import (``dry_run=True``, the confirmation-dialog
path) or removes them and returns the same counts (``dry_run=False``). These
tests seed rows directly so the handler contracts are pinned independently of
the full pipeline.
"""

from __future__ import annotations

import pytest

from proteus.core.ingest.imports import register_import
from proteus.services.dragndrop import cleanup as dnd_cleanup
from proteus.core import registry

# Service-conditional cases (cc-task-public-export PR 1, ruling R14): keyed on
# registry discovery, so the same file runs in the private tree (sa + dnd) and
# in the public export (dnd only).
SA = "sa" in {s.name for s in registry.available_services()}
requires_sa = pytest.mark.skipif(
    not SA, reason="the Short Answer service is not in this tree (R14)"
)


@pytest.fixture
def sa_cleanup():
    from proteus.services.shortanswer import cleanup

    return cleanup


def _import(conn, tmp_path, name: str) -> int:
    f = tmp_path / f"{name}.tab"
    f.write_text(name)
    return register_import(conn, file_path=f, file_format="datashop_tab", row_count=1)


def _event(conn, import_id, event_id, *, problem="Q1", selection="dragNdrop-drop"):
    conn.execute(
        "INSERT INTO events (event_id, import_id, student_id, problem_name, "
        "selection) VALUES (?, ?, 's1', ?, ?)",
        (event_id, import_id, problem, selection),
    )


# ---------------------------------------------------------------------------
# DnD handler
# ---------------------------------------------------------------------------

class TestDndCleanup:
    def _seed(self, conn, import_id, question="Q1"):
        """A minimal DnD layer for one import: one check event with a score,
        one attempt with a config and metrics."""
        _event(conn, import_id, "ev_move")
        _event(conn, import_id, "ev_check", selection="dragNdrop")
        conn.execute(
            "INSERT INTO dnd_events (event_id, kind) VALUES ('ev_move', 'move')"
        )
        conn.execute(
            "INSERT INTO dnd_events (event_id, kind) VALUES ('ev_check', 'check')"
        )
        conn.execute(
            "INSERT INTO dnd_attempts (attempt_id, student_id, question_id, "
            "attempt_index) VALUES (1, 's1', ?, 1)",
            (question,),
        )
        conn.execute(
            "INSERT INTO dnd_configurations (attempt_id, seq, event_id, s_json) "
            "VALUES (1, 0, NULL, '[0]')"
        )
        conn.execute(
            "INSERT INTO dnd_attempt_metrics (attempt_id) VALUES (1)"
        )
        conn.execute(
            "INSERT INTO dnd_check_scores (event_id, attempt_id, question_id, "
            "sigma) VALUES ('ev_check', 1, ?, 3)",
            (question,),
        )
        conn.commit()

    def test_dry_run_counts_without_deleting(self, db_conn, tmp_path):
        imp = _import(db_conn, tmp_path, "dnd")
        self._seed(db_conn, imp)

        counts = dnd_cleanup.delete_import_rows(db_conn, imp, dry_run=True)
        assert counts["dnd_events"] == 2
        assert counts["dnd_attempts"] == 1
        assert counts["dnd_configurations"] == 1
        assert counts["dnd_check_scores"] == 1
        assert counts["dnd_attempt_metrics"] == 1
        # Nothing actually removed.
        assert db_conn.execute("SELECT COUNT(*) FROM dnd_events").fetchone()[0] == 2

    def test_delete_removes_rows(self, db_conn, tmp_path):
        imp = _import(db_conn, tmp_path, "dnd")
        self._seed(db_conn, imp)

        counts = dnd_cleanup.delete_import_rows(db_conn, imp, dry_run=False)
        assert counts["dnd_events"] == 2
        for table in (
            "dnd_events", "dnd_attempts", "dnd_configurations",
            "dnd_check_scores", "dnd_attempt_metrics",
        ):
            assert db_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0

    def test_question_scoped_clear_of_reconstruction(self, db_conn, tmp_path):
        """The reconstruction layer is question-scoped: deleting an import
        clears the whole affected question even if some derived rows came from
        a second import of the same question."""
        a = _import(db_conn, tmp_path, "dnd_a")
        b = _import(db_conn, tmp_path, "dnd_b")
        # Import A seeds the question's attempt/score and one of its events.
        self._seed(db_conn, a, question="Q1")
        # Import B contributes a second event to the SAME question Q1.
        _event(db_conn, b, "ev_move_b")
        db_conn.execute(
            "INSERT INTO dnd_events (event_id, kind) VALUES ('ev_move_b', 'move')"
        )
        db_conn.commit()

        # Deleting A clears Q1's reconstruction (attempts/scores) and A's own
        # dnd_events, but leaves B's dnd_event (kept until B is deleted).
        dnd_cleanup.delete_import_rows(db_conn, a, dry_run=False)
        assert db_conn.execute("SELECT COUNT(*) FROM dnd_attempts").fetchone()[0] == 0
        assert (
            db_conn.execute("SELECT COUNT(*) FROM dnd_check_scores").fetchone()[0] == 0
        )
        # B's raw dnd_event survives (deleted only when B itself is deleted).
        surviving = db_conn.execute(
            "SELECT event_id FROM dnd_events"
        ).fetchall()
        assert [r["event_id"] for r in surviving] == ["ev_move_b"]

    def test_empty_import_returns_zeros(self, db_conn, tmp_path):
        imp = _import(db_conn, tmp_path, "empty")
        counts = dnd_cleanup.delete_import_rows(db_conn, imp, dry_run=True)
        assert counts["dnd_events"] == 0
        assert counts["dnd_attempts"] == 0


# ---------------------------------------------------------------------------
# SA handler
# ---------------------------------------------------------------------------

@requires_sa
class TestSaCleanup:
    def _seed_response(self, conn, import_id, rid="r1", student="s1", q="Q1"):
        conn.execute(
            "INSERT INTO responses (response_id, import_id, student_id, "
            "question_id, response_text, submitted_at) "
            "VALUES (?, ?, ?, ?, 'text', '2026-01-01 00:00:00')",
            (rid, import_id, student, q),
        )

    def test_canonical_tables_always_present(self, db_conn, tmp_path, sa_cleanup):
        imp = _import(db_conn, tmp_path, "sa")
        self._seed_response(db_conn, imp)
        db_conn.commit()

        counts = sa_cleanup.delete_import_rows(db_conn, imp, dry_run=True)
        # Every canonical table is reported, even the ones with zero rows.
        for table in ("responses", "screenings", "chains", "chain_steps",
                      "matches", "dispositions"):
            assert table in counts
        assert counts["responses"] == 1
        assert counts["screenings"] == 0
        # The review family is omitted when empty.
        assert "reviews" not in counts

    def test_delete_cascades_response_subtree(self, db_conn, tmp_path, sa_cleanup):
        from proteus import runs

        imp = _import(db_conn, tmp_path, "sa")
        self._seed_response(db_conn, imp, rid="r1")
        run_id = runs.start_run(
            db_conn, prompt_version="t-v1", model_id="test-model"
        )
        db_conn.execute(
            "INSERT INTO screenings (response_id, run_id, label, confidence) "
            "VALUES ('r1', ?, 'INCLUDE', 0.9)",
            (run_id,),
        )
        cur = db_conn.execute(
            "INSERT INTO chains (response_id, run_id) VALUES ('r1', ?)",
            (run_id,),
        )
        chain_id = cur.lastrowid
        db_conn.execute(
            "INSERT INTO chain_steps (chain_id, step_index, conclusion) "
            "VALUES (?, 1, 'c')",
            (chain_id,),
        )
        db_conn.execute(
            "INSERT INTO dispositions (response_id, run_id, disposition) "
            "VALUES ('r1', ?, 'auto_accept')",
            (run_id,),
        )
        db_conn.commit()

        counts = sa_cleanup.delete_import_rows(db_conn, imp, dry_run=False)
        assert counts["responses"] == 1
        assert counts["screenings"] == 1
        assert counts["chains"] == 1
        assert counts["chain_steps"] == 1
        assert counts["dispositions"] == 1
        for table in ("responses", "screenings", "chains", "chain_steps",
                      "dispositions"):
            assert db_conn.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0] == 0

    def test_only_targets_the_named_import(self, db_conn, tmp_path, sa_cleanup):
        a = _import(db_conn, tmp_path, "sa_a")
        b = _import(db_conn, tmp_path, "sa_b")
        self._seed_response(db_conn, a, rid="ra", student="sa")
        self._seed_response(db_conn, b, rid="rb", student="sb")
        db_conn.commit()

        sa_cleanup.delete_import_rows(db_conn, a, dry_run=False)
        remaining = [r["response_id"] for r in db_conn.execute(
            "SELECT response_id FROM responses"
        )]
        assert remaining == ["rb"]
