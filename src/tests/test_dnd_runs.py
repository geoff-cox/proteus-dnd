"""
Tests for DnD run-level provenance (checklist item F22).

Migration 0013 added ``pipeline_runs.stage``/``.config_json``; every DnD
derive/reconstruct/score/analyze pass records a run row carrying its
configuration: the stage's semantics version (the journal's "parser
version"), the idle-gap threshold and attempt-boundary rule, the
feedback-policy era, and the noop-move exclusion default
(subsec-provenance-and-reproducibility) (plus a result summary in notes).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from proteus import cli, db
from proteus.core import ingest as core_ingest
from proteus.services.dragndrop import process, reconstruct, scoring
from proteus.services.dragndrop.derive import derive_events

QUESTION = "demo-book__demo-shapes-q1"


def _dnd_runs(conn):
    return conn.execute(
        "SELECT * FROM pipeline_runs WHERE service = 'dnd' ORDER BY run_id"
    ).fetchall()


def _config(row) -> dict:
    return json.loads(row["config_json"])


@pytest.fixture
def full_pipeline(db_conn, golden_dnd_tab, four_shapes_board):
    """Ingest the golden fixture and run all four DnD stage passes."""
    core_ingest.ingest_file(db_conn, golden_dnd_tab)
    four_shapes_board(db_conn)
    results = (
        derive_events(db_conn),
        reconstruct.reconstruct_all(db_conn),
        scoring.score_checks(db_conn),
        process.analyze_all(db_conn),
    )
    return db_conn, results


class TestEveryStageRecordsARun:
    def test_one_completed_run_per_stage(self, full_pipeline):
        conn, _ = full_pipeline
        rows = _dnd_runs(conn)
        assert [r["stage"] for r in rows] == [
            "derive", "reconstruct", "score", "analyze",
        ]
        assert all(r["status"] == "completed" for r in rows)
        assert all(r["completed_at"] is not None for r in rows)

    def test_results_carry_their_run_id(self, full_pipeline):
        conn, results = full_pipeline
        rows = _dnd_runs(conn)
        assert [res.run_id for res in results] == [r["run_id"] for r in rows]

    def test_every_run_records_its_stage_version(self, full_pipeline):
        conn, _ = full_pipeline
        # analyze=7: time_to_first_check_s moved to the replayed clock
        # (A6/BUG-06); bump here is deliberate, values change on attempts
        # whose leading rows failed to replay. reconstruct=3: the frozen
        # anchor-eligibility definition (cc-task-anchor-eligibility, dnd/0012);
        # non-eligible checks are stored not comparable with a cause instead
        # of being compared on a best-effort decode. analyze=8: the F-NEW
        # phase-window features joined the row (dnd/0013); every earlier
        # column is computed exactly as before. reconstruct=4, score=3,
        # analyze=9: the question-identifier alias (dnd/0014); a retired
        # identifier binds to its successor's board in all three stages and
        # reconstruct records canonical_question_id; with no alias registered
        # every value is computed exactly as before.
        # reconstruct=5: the two structural anchor causes (dnd/0015); a
        # check of a resumed attempt or one logged before its import's
        # regime date is stored not comparable with its cause, every attempt
        # records prior_attempt_id, and the seeded-replay option and the
        # regime-set hash join the configuration; flags and readiness are
        # unchanged (the shadow comparison). Score and analyze do not read
        # the regime table and keep their versions (ruling 2026-10-06).
        # derive=2: the staff-account filter (dnd/0016, E-02 PR 1; ruling
        # 2026-10-19, R12); rows of an account ruled excluded are
        # withheld at derive and counted (`excluded`, kept apart from
        # `skipped`), the run configuration records roles_set_sha256 and
        # the notes the withheld counts; with an empty roles table every
        # row is derived exactly as before, so this fixture's values are
        # unchanged. Reconstruct, score and analyze do not read the
        # roles table and keep their versions.
        expected = {
            "derive": 2, "reconstruct": 5, "score": 3, "analyze": 9,
        }
        for row in _dnd_runs(conn):
            assert _config(row)["stage_version"] == expected[row["stage"]]

    def test_notes_summarize_the_pass(self, full_pipeline):
        conn, _ = full_pipeline
        notes = {r["stage"]: r["notes"] for r in _dnd_runs(conn)}
        # The golden fixture: 13 DnD-relevant events across both students.
        assert "seen=13" in notes["derive"]
        assert "attempts=" in notes["reconstruct"]
        assert "non_eligible=" in notes["reconstruct"]  # dnd/0012
        assert "scored=" in notes["score"]
        assert "attempts=" in notes["analyze"]


class TestRecordedConfiguration:
    def test_reconstruct_defaults(self, full_pipeline):
        conn, _ = full_pipeline
        (row,) = [r for r in _dnd_runs(conn) if r["stage"] == "reconstruct"]
        config = _config(row)
        # The conservative segmentation defaults (plan §7 OQ#4) and the
        # legacy feedback-policy era (FP-3): the journal's idle threshold
        # and attempt-boundary rule, now run-level provenance.
        assert config["gap_seconds"] == 1800.0
        assert config["split_on_session_change"] is True
        assert config["split_on_page_view"] is True
        assert config["split_on_reset"] is False
        assert config["feedback_policy"] == "legacy"
        # F9: the analytic idle threshold (distinct from gap_seconds) is
        # run-level provenance too (it drives long_idle_gap / active_work).
        assert config["idle_threshold_s"] == 300.0
        assert config["question_id"] is None

    def test_reconstruct_non_default_config_is_recorded(
        self, db_conn, golden_dnd_tab, four_shapes_board
    ):
        core_ingest.ingest_file(db_conn, golden_dnd_tab)
        four_shapes_board(db_conn)
        derive_events(db_conn)
        result = reconstruct.reconstruct_all(
            db_conn,
            question_id=QUESTION,
            config=reconstruct.SegmentationConfig(
                gap_seconds=60.0, split_on_reset=True
            ),
            feedback_policy="layered",
        )
        row = db_conn.execute(
            "SELECT * FROM pipeline_runs WHERE run_id = ?", (result.run_id,)
        ).fetchone()
        config = _config(row)
        assert config["gap_seconds"] == 60.0
        assert config["split_on_reset"] is True
        assert config["feedback_policy"] == "layered"
        assert config["question_id"] == QUESTION

    def test_analyze_records_exclusion_default(self, full_pipeline):
        conn, _ = full_pipeline
        (row,) = [r for r in _dnd_runs(conn) if r["stage"] == "analyze"]
        config = _config(row)
        assert config["cadence_threshold"] == 1
        assert config["exclude_noop_moves"] is True
        # F9: analyze reports the δ_max its active_time used.
        assert config["idle_threshold_s"] == 300.0

    def test_derive_and_score_record_their_filters(
        self, db_conn, golden_dnd_tab, four_shapes_board
    ):
        core_ingest.ingest_file(db_conn, golden_dnd_tab)
        four_shapes_board(db_conn)
        derived = derive_events(db_conn, question_id=QUESTION)
        reconstruct.reconstruct_all(db_conn, question_id=QUESTION)
        scored = scoring.score_checks(db_conn, question_id=QUESTION)
        for run_id in (derived.run_id, scored.run_id):
            row = db_conn.execute(
                "SELECT * FROM pipeline_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            assert _config(row)["question_id"] == QUESTION

    def test_sa_placeholders_and_stub_versions(self, full_pipeline):
        conn, _ = full_pipeline
        for row in _dnd_runs(conn):
            # The baseline's SA-specific NOT NULL columns carry the
            # documented placeholder; the stub vocab/codebook satisfy the
            # frozen FKs (the schema's recorded wart).
            assert row["prompt_version"] == "n/a"
            assert row["model_id"] == "n/a"


class TestFailureLeavesAFailedRow:
    def test_crashed_reconstruct_is_marked_failed(
        self, db_conn, golden_dnd_tab, four_shapes_board, monkeypatch
    ):
        # Reconstruct commits per question, so a crash can leave partial
        # data; the pass persists its 'running' row up front and marks it
        # failed so that data stays attributable to a run.
        core_ingest.ingest_file(db_conn, golden_dnd_tab)
        four_shapes_board(db_conn)
        derive_events(db_conn)

        def boom(*args, **kwargs):
            raise RuntimeError("mid-pass crash")

        monkeypatch.setattr(reconstruct, "reconstruct_question", boom)
        with pytest.raises(RuntimeError):
            reconstruct.reconstruct_all(db_conn)
        (row,) = [
            r for r in _dnd_runs(db_conn) if r["stage"] == "reconstruct"
        ]
        assert row["status"] == "failed"
        assert row["completed_at"] is not None


class TestCliSurface:
    def test_dnd_cli_stages_record_and_print_runs(
        self, tmp_path, capsys, golden_dnd_tab
    ):
        db_path: Path = tmp_path / "proteus.db"
        conn = db.connect(db_path)
        try:
            db.bootstrap(conn)
        finally:
            conn.close()
        assert cli.main(
            ["--db", str(db_path), "ingest", str(golden_dnd_tab)]
        ) == 0
        capsys.readouterr()
        assert cli.main(["--db", str(db_path), "dnd", "derive"]) == 0
        assert cli.main(["--db", str(db_path), "dnd", "reconstruct"]) == 0
        out = capsys.readouterr().out
        assert "run:" in out

        conn = db.connect(db_path)
        try:
            rows = _dnd_runs(conn)
        finally:
            conn.close()
        assert [(r["service"], r["stage"]) for r in rows] == [
            ("dnd", "derive"), ("dnd", "reconstruct"),
        ]
