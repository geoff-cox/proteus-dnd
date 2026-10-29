"""
Tests for the sensitivity harness (expansion memo E-01;
cc-task-frozen-run-tooling).

The knob table is pinned to D3.2; the harness's own stage invocation under
the primary values reproduces a plain `reconstruct → score → analyze` run
bit for bit on both golden fixtures; each knob is exercised on a synthetic
trace built to move exactly the quantity the knob is meant to move; an
off-table knob is labelled post_hoc by the API and refused by the CLI; the
canonical database is refused; the materiality rule is pinned on hand-made
summaries; the evidence report counts the golden fixture by hand.
"""

from __future__ import annotations

import csv
import io
import json

import pytest

from proteus import cli, db
from proteus.core import ingest as core_ingest
from proteus.services.dragndrop import export, process, sensitivity
from proteus.services.dragndrop.derive import derive_events
from proteus.services.dragndrop.reconstruct import reconstruct_all
from proteus.services.dragndrop.scoring import score_checks

Q = "demo-book__demo-shapes-q1"


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------
def _move(card, zone):
    target = f"{Q}_drop{zone}" if zone else "dragzone"
    return "dragNdrop-drop", f"{Q}_drag{card} -> {target}"


def _check(**zones):
    return "dragNdrop", json.dumps({
        f"{Q}_drop{z}": [f"{Q}_drag{c}" for c in cards] for z, cards in zones.items()
    })


RESET = ("dragNdrop-reset", "reset")
KEY_PAYLOAD = _check(**{"1": [1, 2], "2": [3]})


def _ts(seconds: float) -> str:
    minutes, sec = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"2026-02-06 {10 + hours:02d}:{minutes:02d}:{sec:02d}.000000"


def _seed(conn, rows):
    """Raw `events` rows → derive. Each row: (eid, (selection, action),
    seconds[, student[, problem_view]])."""
    import_id = conn.execute(
        "INSERT INTO raw_imports (file_path, file_hash, file_format, row_count) "
        "VALUES ('synthetic.tab', ?, 'datashop_tab', ?)",
        (f"h-{id(rows)}", len(rows)),
    ).lastrowid
    for i, row in enumerate(rows):
        eid, (selection, action), secs = row[:3]
        student = row[3] if len(row) > 3 else "S"
        problem_view = row[4] if len(row) > 4 else str(i + 1)
        conn.execute(
            "INSERT INTO events (event_id, import_id, student_id, session_id, "
            "problem_name, selection, action, input, feedback_text, "
            "feedback_classification, problem_view, timestamp, course_id) "
            "VALUES (?, ?, ?, '100', ?, ?, ?, ?, '', 'Incorrect', ?, ?, 'course')",
            (eid, import_id, student, Q, selection, action, action, problem_view, _ts(secs)),
        )
    conn.commit()
    derive_events(conn)


def _primary_db(tmp_path, four_shapes_board, rows, name="primary.sqlite"):
    """A file database holding the frozen primary run of a synthetic trace."""
    path = tmp_path / name
    conn = db.connect(path)
    db.bootstrap(conn)
    _seed(conn, rows)
    four_shapes_board(conn)
    reconstruct_all(conn)
    score_checks(conn)
    process.analyze_all(conn)
    conn.commit()
    conn.close()
    return path


def _golden_db(tmp_path, fixture, four_shapes_board, name):
    path = tmp_path / name
    conn = db.connect(path)
    db.bootstrap(conn)
    core_ingest.ingest_file(conn, fixture)
    derive_events(conn)
    four_shapes_board(conn)
    reconstruct_all(conn)
    score_checks(conn)
    process.analyze_all(conn)
    conn.commit()
    conn.close()
    return path


def _exports(path, names=("checks", "attempt_metrics", "modeling_table", "configurations", "segments")):
    conn = db.connect(path)
    try:
        out = {}
        for name in names:
            buf = io.StringIO()
            export.export_dataset(conn, name, buf)
            out[name] = buf.getvalue()
        return out
    finally:
        conn.close()


def _run_one(primary, out, knob, **kw):
    return sensitivity.run_configuration(primary, out, knob, **kw)


def _scratch_metrics(out, key):
    conn = db.connect(out / key / "scratch.sqlite")
    try:
        return conn.execute(
            "SELECT a.student_id, a.readiness, a.qc_flags_json, m.* FROM dnd_attempts a "
            "LEFT JOIN dnd_attempt_metrics m USING (attempt_id) ORDER BY a.attempt_id"
        ).fetchall()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# The frozen table
# ---------------------------------------------------------------------------
class TestKnobTable:
    def test_primary_plus_ten_from_d32(self):
        # D3.2 frozen 2026-07-17 (nine), amended 2026-10-05: knob 10 "seeded
        # replay" joins the reconstruction group in table order (1–3, 10)
        # without renumbering 1–9.
        table = sensitivity.KNOB_TABLE
        assert len(table) == 10
        assert [k.number for k in table] == [1, 1, 2, 3, 10, 4, 5, 6, 6, 7]
        assert [k.group for k in table[:5]] == [sensitivity.RECONSTRUCTION] * 5
        assert [k.group for k in table[5:]] == [sensitivity.FEATURES_INCLUSION] * 5
        by = {k.key: k for k in table}
        assert by["k10-seeded-replay"].reconstruct == {"seed_resumed_attempts": True}
        assert by["k10-seeded-replay"].seeds is True
        assert not any(k.seeds for k in table if k.key != "k10-seeded-replay")
        assert by["k1-gap-600"].segmentation == {"gap_seconds": 600.0}
        assert by["k1-gap-3600"].segmentation == {"gap_seconds": 3600.0}
        assert by["k2-split-on-reset"].segmentation == {"split_on_reset": True}
        assert by["k3-truncate-at-first-correct"].process == {"truncate_at_first_correct": True}
        assert by["k3-truncate-at-first-correct"].truncate_agreement is True
        assert by["k4-count-noop-moves"].process == {"count_noop_moves": True}
        assert by["k5-exclude-order-ambiguous"].exclude_flags == ("row_order_ambiguous",)
        assert by["k6-idle-120"].idle_threshold_s == 120.0
        assert by["k6-idle-600"].idle_threshold_s == 600.0
        assert by["k7-ready-and-limited"].readiness == ("ready", "limited")
        # The primary values the alternatives bracket are the code defaults.
        assert sensitivity._PRIMARY_RECONSTRUCT["gap_seconds"] == 1800.0
        assert sensitivity._PRIMARY_RECONSTRUCT["idle_threshold_s"] == 300.0
        assert sensitivity._PRIMARY_RECONSTRUCT["seed_resumed_attempts"] is False

    def test_stages_each_knob_reruns(self):
        stages = {k.key: k.stages for k in sensitivity.KNOB_TABLE}
        all3 = ("reconstruct", "score", "analyze")
        assert stages["k1-gap-600"] == all3
        assert stages["k2-split-on-reset"] == all3
        assert stages["k6-idle-120"] == all3  # option (a): both stages at δ
        assert stages["k10-seeded-replay"] == all3  # reconstruct (seeded) → score → analyze
        assert stages["k3-truncate-at-first-correct"] == ("analyze",)
        assert stages["k4-count-noop-moves"] == ("analyze",)
        assert stages["k5-exclude-order-ambiguous"] == ()
        assert stages["k7-ready-and-limited"] == ()
        assert sensitivity.PRIMARY.stages == ()

    def test_labels_match_the_results_table(self):
        labels = [k.label for k in sensitivity.KNOB_TABLE]
        assert labels == [
            "Attempt gap 10 min (30 min)",
            "Attempt gap 60 min (30 min)",
            "Reset opens a new attempt (reset within attempt)",
            "Truncate at first correct check (retain)",
            "Seeded replay: resumed attempt starts at the prior attempt's last "
            "decodable checked configuration, s_0 if none (s_0)",
            "No-op drops counted as moves (flagged, excluded)",
            "Exclude order-ambiguous attempts (retain, flagged)",
            "Idle threshold 120 s (300 s)",
            "Idle threshold 600 s (300 s)",
            "Ready and limited attempts (ready only)",
        ]

    def test_thresholds_are_the_p6_constants(self):
        assert sensitivity.MATERIAL_AGREEMENT_PP == 2.0
        assert sensitivity.MATERIAL_RQ3_RELATIVE == 0.25
        assert sensitivity.MATERIAL_RQ3_FLOOR_MOVES == 1.0
        assert sensitivity.MATERIAL_ATTEMPTS_RELATIVE == 0.10
        assert sensitivity.MATERIAL_READY_SHARE_RELATIVE == 0.10
        assert sensitivity.P6_SIGN_CLAUSE_DROPPED == "2026-10-03"

    def test_unknown_key_is_refused(self):
        with pytest.raises(sensitivity.SensitivityError):
            sensitivity.knob_by_key("k9-anything")


# ---------------------------------------------------------------------------
# Bit-identity of the primary configuration
# ---------------------------------------------------------------------------
class TestPrimaryReproducesAPlainRun:
    @pytest.mark.parametrize("fixture_name", ["golden_dnd_tab", "golden_dnd_worked_example_tab"])
    def test_harness_stages_match_plain_stages_byte_for_byte(
        self, request, tmp_path, four_shapes_board, fixture_name
    ):
        fixture = request.getfixturevalue(fixture_name)
        plain = _golden_db(tmp_path, fixture, four_shapes_board, "plain.sqlite")
        # The harness's own invocation: a knob carrying the primary values
        # explicitly, so every stage re-runs through `run_stages`.
        harness_db = tmp_path / "harness.sqlite"
        conn = db.connect(harness_db)
        db.bootstrap(conn)
        core_ingest.ingest_file(conn, fixture)
        derive_events(conn)
        four_shapes_board(conn)
        knob = sensitivity.Knob(
            "primary-explicit", 0, "primary", "explicit primary values",
            segmentation={"gap_seconds": 1800.0, "split_on_reset": False},
            idle_threshold_s=300.0,
            process={"count_noop_moves": False, "truncate_at_first_correct": False},
        )
        assert knob.stages == ("reconstruct", "score", "analyze")
        sensitivity.run_stages(conn, knob)
        conn.commit()
        conn.close()
        a, b = _exports(plain), _exports(harness_db)
        for name in a:
            assert a[name] == b[name], name

    def test_run_configuration_primary_is_a_faithful_clone(self, tmp_path, golden_dnd_tab, four_shapes_board):
        primary = _golden_db(tmp_path, golden_dnd_tab, four_shapes_board, "primary.sqlite")
        out = tmp_path / "out"
        summary = _run_one(primary, out, sensitivity.PRIMARY)
        assert summary.key == "primary" and not summary.post_hoc
        assert (out / "primary" / "scratch.sqlite").exists()
        for name in ("checks", "attempt_metrics", "modeling_table"):
            assert (out / "primary" / f"{name}.csv").read_text() == _exports(primary)[name]
        config = json.loads((out / "primary" / "config.json").read_text())
        assert config["label"] == "primary"
        assert config["stages_rerun"] == []
        # Provenance names every stage's effective run — all inherited here.
        assert [(r["stage"], r["source"]) for r in config["pipeline_runs"]] == [
            ("derive", "inherited"), ("reconstruct", "inherited"),
            ("score", "inherited"), ("analyze", "inherited"),
        ]
        assert all(r["status"] == "completed" and r["config"] for r in config["pipeline_runs"])
        assert config["stage_versions"]["analyze"] == process.STAGE_VERSION
        assert len(config["primary_db_sha256"]) == 64 and len(config["scratch_db_sha256"]) == 64
        # DemoStudent1 ready with two agreeing anchors; DemoStudent2 excluded.
        assert (summary.n_reconstructed, summary.n_ready, summary.n_attempts) == (2, 1, 1)
        assert summary.ready_share == 0.5
        assert (summary.n_anchors, summary.n_agree, summary.agreement) == (2, 2, 1.0)
        assert summary.primary_rq3 is None

    def test_non_primary_input_is_refused(self, tmp_path, golden_dnd_tab, four_shapes_board):
        path = tmp_path / "nonprimary.sqlite"
        conn = db.connect(path)
        db.bootstrap(conn)
        core_ingest.ingest_file(conn, golden_dnd_tab)
        derive_events(conn)
        four_shapes_board(conn)
        reconstruct_all(conn, config=process.reconstruct_mod.SegmentationConfig(gap_seconds=600.0)
                        if hasattr(process, "reconstruct_mod") else
                        __import__("proteus.services.dragndrop.reconstruct", fromlist=["x"]).SegmentationConfig(gap_seconds=600.0))
        score_checks(conn)
        process.analyze_all(conn)
        conn.commit()
        conn.close()
        with pytest.raises(sensitivity.SensitivityError, match="gap_seconds=600.0") as exc:
            _run_one(path, tmp_path / "out", sensitivity.PRIMARY)
        # Only the keys that differ are named: the alias set is unchanged here.
        assert "alias_set_sha256" not in str(exc.value)

    def test_newest_failed_reconstruct_run_is_refused(self, tmp_path, golden_dnd_tab, four_shapes_board):
        # reconstruct commits per question and marks a crashed pass failed:
        # a newer failed row means part of the data may be that pass's.
        primary = _golden_db(tmp_path, golden_dnd_tab, four_shapes_board, "primary.sqlite")
        conn = db.connect(primary)
        conn.execute(
            "INSERT INTO pipeline_runs (status, prompt_version, model_id, service, stage, config_json) "
            "SELECT 'failed', prompt_version, model_id, service, stage, config_json "
            "FROM pipeline_runs WHERE stage = 'reconstruct' ORDER BY run_id DESC LIMIT 1"
        )
        conn.commit()
        conn.close()
        with pytest.raises(sensitivity.SensitivityError, match="'failed', not completed"):
            _run_one(primary, tmp_path / "out", sensitivity.PRIMARY)

    def test_newest_question_scoped_rerun_is_refused(self, tmp_path, golden_dnd_tab, four_shapes_board):
        # A full run followed by a question-scoped analyze pass: the newest
        # row is scoped, so the database is not certified as the primary.
        primary = _golden_db(tmp_path, golden_dnd_tab, four_shapes_board, "primary.sqlite")
        conn = db.connect(primary)
        q = conn.execute("SELECT question_id FROM dnd_attempts LIMIT 1").fetchone()["question_id"]
        process.analyze_all(conn, question_id=q)
        conn.commit()
        conn.close()
        with pytest.raises(sensitivity.SensitivityError, match="question_id="):
            _run_one(primary, tmp_path / "out", sensitivity.PRIMARY)

    def test_other_stage_version_is_refused(self, tmp_path, golden_dnd_tab, four_shapes_board):
        primary = _golden_db(tmp_path, golden_dnd_tab, four_shapes_board, "primary.sqlite")
        conn = db.connect(primary)
        row = conn.execute(
            "SELECT run_id, config_json FROM pipeline_runs WHERE stage = 'score' "
            "ORDER BY run_id DESC LIMIT 1"
        ).fetchone()
        config = json.loads(row["config_json"])
        config["stage_version"] += 1
        conn.execute(
            "UPDATE pipeline_runs SET config_json = ? WHERE run_id = ?",
            (json.dumps(config, sort_keys=True), row["run_id"]),
        )
        conn.commit()
        conn.close()
        with pytest.raises(sensitivity.SensitivityError, match="score.*stage_version="):
            _run_one(primary, tmp_path / "out", sensitivity.PRIMARY)

    def test_other_feedback_policy_is_refused(self, tmp_path, golden_dnd_tab, four_shapes_board):
        primary = _golden_db(tmp_path, golden_dnd_tab, four_shapes_board, "primary.sqlite")
        conn = db.connect(primary)
        reconstruct_all(conn, feedback_policy="layered")
        conn.commit()
        conn.close()
        with pytest.raises(sensitivity.SensitivityError, match="feedback_policy='layered'"):
            _run_one(primary, tmp_path / "out", sensitivity.PRIMARY)


# ---------------------------------------------------------------------------
# One targeted trace per knob
# ---------------------------------------------------------------------------
class TestEachKnob:
    def test_knob1_gap_600_splits_a_fifteen_minute_pause(self, tmp_path, four_shapes_board):
        """The key placed (0–4 s) and checked Correct (5 s); a 900 s pause;
        reset (905 s), drag2→drop1 (910 s), Incorrect check (915 s; its
        payload {drop1:[drag2]} matches the replay after the reset in both
        regimes). Primary (gap 1800 s): one attempt, and because the pause
        also exceeds δ_max = 300 s it is flagged long_idle_gap → limited: 0
        attempts in the primary analyses, ready share 0. Gap 600 s: the
        pause is an attempt boundary, so two attempts, neither with a long
        gap, both ready → Δ attempts +2, Δ ready share +1.0; agreement 1.0
        in both regimes (every anchor agrees)."""
        rows = [
            ("m1", _move(1, 1), 0), ("m2", _move(2, 1), 2), ("m3", _move(3, 2), 4),
            ("c1", KEY_PAYLOAD, 5),
            ("r1", RESET, 905), ("m4", _move(2, 1), 910), ("c2", _check(**{"1": [2]}), 915),
        ]
        primary = _primary_db(tmp_path, four_shapes_board, rows)
        out = tmp_path / "out"
        p = _run_one(primary, out, sensitivity.PRIMARY)
        a = _run_one(primary, out, sensitivity.knob_by_key("k1-gap-600"))
        assert (p.n_reconstructed, p.n_attempts, p.ready_share) == (1, 0, 0.0)
        [pm] = _scratch_metrics(out, "primary")
        assert "long_idle_gap" in json.loads(pm["qc_flags_json"])
        assert (a.n_reconstructed, a.n_attempts, a.ready_share) == (2, 2, 1.0)
        assert p.agreement == a.agreement == 1.0
        [d] = sensitivity.deltas(p, [(sensitivity.knob_by_key("k1-gap-600"), a)])
        assert (d.delta_attempts, d.delta_ready_share) == (2, 1.0)
        assert d.material == "yes" and d.material_reasons == ("attempts", "ready_share")
        config = json.loads((out / "k1-gap-600" / "config.json").read_text())
        assert config["label"] == "frozen_alternative"
        assert config["stages_rerun"] == ["reconstruct", "score", "analyze"]
        recon = next(r for r in config["pipeline_runs"] if r["stage"] == "reconstruct")
        assert recon["config"]["gap_seconds"] == 600.0
        assert recon["config"]["idle_threshold_s"] == 300.0

    def test_knob2_split_on_reset_opens_a_second_attempt(self, tmp_path, four_shapes_board):
        """Key placed and checked (0–5 s), reset (10 s), drag2→drop1
        (15 s), Incorrect check (20 s). Primary: one attempt. Reset opening a
        new attempt: two, both ready → Δ attempts +1."""
        rows = [
            ("m1", _move(1, 1), 0), ("m2", _move(2, 1), 2), ("m3", _move(3, 2), 4),
            ("c1", KEY_PAYLOAD, 5),
            ("r1", RESET, 10), ("m4", _move(2, 1), 15), ("c2", _check(**{"1": [2]}), 20),
        ]
        primary = _primary_db(tmp_path, four_shapes_board, rows)
        out = tmp_path / "out"
        p = _run_one(primary, out, sensitivity.PRIMARY)
        a = _run_one(primary, out, sensitivity.knob_by_key("k2-split-on-reset"))
        assert (p.n_reconstructed, a.n_reconstructed) == (1, 2)
        assert (p.n_attempts, a.n_attempts) == (1, 2)
        rules = {r["readiness"] for r in _scratch_metrics(out, "k2-split-on-reset")}
        assert rules == {"ready"}

    def test_knob3_truncation_moves_only_the_agreement_denominator(self, tmp_path, four_shapes_board):
        """Key placed and checked (Correct at 5 s), then a move that cannot
        replay (garbage, 10 s) and a check whose payload shows the board the
        grader saw (15 s): an eligible anchor that disagrees. Primary:
        agreement 1/2, the attempt limited (check_mismatch). Truncated at the
        first correct check: the disagreeing anchor leaves the denominator →
        agreement 1/1, Δ +50 pp; attempts, readiness and the pre features
        unchanged by construction."""
        rows = [
            ("m1", _move(1, 1), 0), ("m2", _move(2, 1), 2), ("m3", _move(3, 2), 4),
            ("c1", KEY_PAYLOAD, 5),
            ("bad", ("dragNdrop-drop", "garbage-action"), 10),
            ("c2", _check(**{"1": [1, 2], "2": [3], "3": [4]}), 15),
        ]
        primary = _primary_db(tmp_path, four_shapes_board, rows)
        out = tmp_path / "out"
        knob = sensitivity.knob_by_key("k3-truncate-at-first-correct")
        p = _run_one(primary, out, sensitivity.PRIMARY)
        a = _run_one(primary, out, knob)
        assert (p.n_anchors, p.n_agree) == (2, 1)
        assert (a.n_anchors, a.n_agree) == (1, 1)
        assert (p.n_attempts, a.n_attempts) == (0, 0)  # limited in both regimes
        assert p.ready_share == a.ready_share
        [d] = sensitivity.deltas(p, [(knob, a)])
        assert d.delta_attempts == 0 and d.delta_ready_share == 0.0
        assert d.delta_agreement_pp == 50.0
        assert d.material == "yes" and d.material_reasons == ("agreement",)
        [m] = _scratch_metrics(out, knob.key)
        assert m["n_checks"] == 1 and m["final_check_distance"] == 0
        assert m["n_effective_moves_pre"] == 3
        config = json.loads((out / knob.key / "config.json").read_text())
        assert config["stages_rerun"] == ["analyze"]
        # Every stage's effective run is recorded: the inherited primary
        # rows for derive / reconstruct / score, the new analyze run.
        assert [(r["stage"], r["source"]) for r in config["pipeline_runs"]] == [
            ("derive", "inherited"), ("reconstruct", "inherited"),
            ("score", "inherited"), ("analyze", "rerun"),
        ]
        recon = next(r for r in config["pipeline_runs"] if r["stage"] == "reconstruct")
        assert recon["config"]["gap_seconds"] == 1800.0  # the primary's
        analyze = next(r for r in config["pipeline_runs"] if r["stage"] == "analyze")
        assert analyze["config"]["truncate_at_first_correct"] is True

    def test_knob4_counting_noops_changes_the_move_counts_only(self, tmp_path, four_shapes_board):
        """drag1→drop1 (0 s), the same drop again (2 s; a no-op), drag2→drop1
        (4 s), drag3→drop2 (6 s), Correct check (8 s). Primary: 3 effective
        moves before the first check. Counted: 4."""
        rows = [
            ("m1", _move(1, 1), 0), ("m2", _move(1, 1), 2), ("m3", _move(2, 1), 4),
            ("m4", _move(3, 2), 6), ("c1", KEY_PAYLOAD, 8),
        ]
        primary = _primary_db(tmp_path, four_shapes_board, rows)
        out = tmp_path / "out"
        knob = sensitivity.knob_by_key("k4-count-noop-moves")
        p = _run_one(primary, out, sensitivity.PRIMARY)
        a = _run_one(primary, out, knob)
        [pm] = _scratch_metrics(out, "primary")
        [am] = _scratch_metrics(out, knob.key)
        assert (pm["n_moves"], pm["n_effective_moves_pre"]) == (3, 3)
        assert (am["n_moves"], am["n_effective_moves_pre"]) == (4, 4)
        assert am["n_noop_moves"] == 1
        assert (p.n_attempts, a.n_attempts, p.agreement, a.agreement) == (1, 1, 1.0, 1.0)
        analyze = next(
            r for r in json.loads((out / knob.key / "config.json").read_text())["pipeline_runs"]
            if r["stage"] == "analyze"
        )
        assert analyze["config"]["exclude_noop_moves"] is False

    def test_knob5_excludes_the_order_ambiguous_attempt(self, tmp_path, four_shapes_board):
        """Student T's two moves tie on timestamp and problem view (file
        order decided the replay), then a Correct check: flagged
        row_order_ambiguous, so limited. Student S is a clean ready attempt.
        Primary: 2 reconstructed, 1 ready (share 0.5), 1 analysed. Knob 5
        drops T: 1 reconstructed, share 1.0, attempts unchanged at 1."""
        rows = [
            ("s1", _move(1, 1), 0, "S"), ("s2", _move(2, 1), 2, "S"), ("s3", _move(3, 2), 4, "S"),
            ("sc", KEY_PAYLOAD, 6, "S"),
            ("t1", _move(1, 1), 100, "T", "7"), ("t2", _move(2, 1), 100, "T", "7"),
            ("t3", _move(3, 2), 104, "T", "8"), ("tc", KEY_PAYLOAD, 106, "T", "9"),
        ]
        primary = _primary_db(tmp_path, four_shapes_board, rows)
        out = tmp_path / "out"
        knob = sensitivity.knob_by_key("k5-exclude-order-ambiguous")
        p = _run_one(primary, out, sensitivity.PRIMARY)
        a = _run_one(primary, out, knob)
        flags = {r["student_id"]: json.loads(r["qc_flags_json"]) for r in _scratch_metrics(out, "primary")}
        assert "row_order_ambiguous" in flags["T"]
        assert (p.n_reconstructed, p.n_ready, p.n_attempts, p.ready_share) == (2, 1, 1, 0.5)
        assert (a.n_reconstructed, a.n_ready, a.n_attempts, a.ready_share) == (1, 1, 1, 1.0)
        [d] = sensitivity.deltas(p, [(knob, a)])
        assert d.delta_attempts == 0
        assert d.delta_ready_share == 0.5
        assert "ready_share" in d.material_reasons

    def test_knob6_idle_120_flags_a_200_second_pause(self, tmp_path, four_shapes_board):
        """Two placements, a 200 s pause, the third placement and a Correct
        check. δ_max 300: no flag, ready. δ_max 120: long_idle_gap, limited
        → Δ attempts −1, Δ ready share −1.0; active time drops by the 200 s
        gap in the scratch metrics; 600 s: unchanged."""
        rows = [
            ("m1", _move(1, 1), 0), ("m2", _move(2, 1), 5), ("m3", _move(3, 2), 205),
            ("c1", KEY_PAYLOAD, 210),
        ]
        primary = _primary_db(tmp_path, four_shapes_board, rows)
        out = tmp_path / "out"
        p = _run_one(primary, out, sensitivity.PRIMARY)
        a120 = _run_one(primary, out, sensitivity.knob_by_key("k6-idle-120"))
        a600 = _run_one(primary, out, sensitivity.knob_by_key("k6-idle-600"))
        assert (p.n_attempts, p.ready_share) == (1, 1.0)
        assert (a120.n_attempts, a120.ready_share) == (0, 0.0)
        assert (a600.n_attempts, a600.ready_share) == (1, 1.0)
        [pm] = _scratch_metrics(out, "primary")
        [m120] = _scratch_metrics(out, "k6-idle-120")
        assert (pm["active_time_s"], pm["idle_threshold_s"]) == (210.0, 300.0)
        assert (m120["active_time_s"], m120["idle_threshold_s"]) == (10.0, 120.0)
        assert m120["readiness"] == "limited"
        config = json.loads((out / "k6-idle-120" / "config.json").read_text())
        assert config["stages_rerun"] == ["reconstruct", "score", "analyze"]
        for stage in ("reconstruct", "analyze"):
            run = next(r for r in config["pipeline_runs"] if r["stage"] == stage)
            assert run["config"]["idle_threshold_s"] == 120.0
        d = sensitivity.deltas(p, [(sensitivity.knob_by_key("k6-idle-120"), a120)])[0]
        assert d.delta_attempts == -1 and d.delta_ready_share == -1.0
        assert d.material == "yes"

    def test_knob7_admits_the_limited_attempt(self, tmp_path, four_shapes_board):
        """Student S: a clean ready attempt. Student L: a 400 s pause before
        the key is completed — long_idle_gap, limited. Primary analyses: 1
        attempt; ready + limited: 2 → Δ attempts +1; ready share and
        agreement unchanged (both are computed before readiness)."""
        rows = [
            ("s1", _move(1, 1), 0, "S"), ("s2", _move(2, 1), 2, "S"), ("s3", _move(3, 2), 4, "S"),
            ("sc", KEY_PAYLOAD, 6, "S"),
            ("l1", _move(1, 1), 0, "L"), ("l2", _move(2, 1), 2, "L"), ("l3", _move(3, 2), 402, "L"),
            ("lc", KEY_PAYLOAD, 404, "L"),
        ]
        primary = _primary_db(tmp_path, four_shapes_board, rows)
        out = tmp_path / "out"
        knob = sensitivity.knob_by_key("k7-ready-and-limited")
        p = _run_one(primary, out, sensitivity.PRIMARY)
        a = _run_one(primary, out, knob)
        assert (p.n_reconstructed, p.n_ready, p.n_attempts) == (2, 1, 1)
        assert (a.n_reconstructed, a.n_ready, a.n_attempts) == (2, 1, 2)
        assert p.ready_share == a.ready_share == 0.5
        assert p.agreement == a.agreement == 1.0
        [d] = sensitivity.deltas(p, [(knob, a)])
        assert d.delta_attempts == 1 and d.delta_ready_share == 0.0
        assert d.material_reasons == ("attempts",)


class TestEstimateColumn:
    def test_rq3_column_empty_by_default_and_filled_with_estimate(self, tmp_path, four_shapes_board):
        """Two students, one item. A: the key placed in 3 moves and checked
        (first check correct). B: drag1→drop2 (wrong), drag1→drop1,
        drag2→drop1, Incorrect check (d=1), drag3→drop2, Correct check:
        first check incorrect, eventually correct, 3 effective moves before
        the first check. Difference in medians 3 − 3 = 0. Knob 3 cannot move
        it (the pre window precedes the truncation point); knob 4 cannot
        either here (no no-op)."""
        rows = [
            ("a1", _move(1, 1), 0, "A"), ("a2", _move(2, 1), 2, "A"), ("a3", _move(3, 2), 4, "A"),
            ("ac", KEY_PAYLOAD, 6, "A"),
            ("b1", _move(1, 2), 0, "B"), ("b2", _move(1, 1), 2, "B"), ("b3", _move(2, 1), 4, "B"),
            ("bc1", _check(**{"1": [1, 2]}), 6, "B"), ("b4", _move(3, 2), 8, "B"),
            ("bc2", KEY_PAYLOAD, 10, "B"),
        ]
        primary = _primary_db(tmp_path, four_shapes_board, rows)
        plain, rows_plain = sensitivity.run_all(primary, tmp_path / "plain", [sensitivity.knob_by_key("k3-truncate-at-first-correct")])
        assert plain.primary_rq3 is None
        assert rows_plain[0].delta_primary_rq3 is None
        assert rows_plain[0].material == "pending"
        est, rows_est = sensitivity.run_all(
            primary, tmp_path / "est",
            [sensitivity.knob_by_key("k3-truncate-at-first-correct"), sensitivity.knob_by_key("k4-count-noop-moves")],
            estimate=True,
        )
        assert est.primary_rq3 == 0.0
        assert (est.rq3_n_first_check_correct, est.rq3_n_first_check_incorrect_eventually_correct) == (1, 1)
        for d in rows_est:
            assert d.delta_primary_rq3 == 0.0
            assert d.material == "no"
        meta = json.loads((tmp_path / "est" / "deltas.json").read_text())
        assert meta["rq3_estimated"] is True
        assert meta["thresholds"]["sign_clause_dropped"] == "2026-10-03"


class TestMaterialityRule:
    def _summary(self, **kw):
        base = {
            "key": "x", "label": "x", "group": "g", "post_hoc": False,
            "n_reconstructed": 100, "n_ready": 80, "n_attempts": 80, "ready_share": 0.8,
            "n_anchors": 200, "n_agree": 190, "agreement": 0.95, "primary_rq3": 4.0,
            "rq3_n_first_check_correct": 10,
            "rq3_n_first_check_incorrect_eventually_correct": 70,
        }
        base.update(kw)
        return sensitivity.Summary(**base)

    def test_no_change_is_not_material(self):
        p = self._summary()
        assert sensitivity.material(p, self._summary()) == ("no", ())

    def test_each_threshold(self):
        p = self._summary()
        assert sensitivity.material(p, self._summary(n_attempts=89)) == ("yes", ("attempts",))
        assert sensitivity.material(p, self._summary(n_attempts=88)) == ("no", ())
        assert sensitivity.material(p, self._summary(ready_share=0.7)) == ("yes", ("ready_share",))
        assert sensitivity.material(p, self._summary(agreement=0.929)) == ("yes", ("agreement",))
        assert sensitivity.material(p, self._summary(agreement=0.931)) == ("no", ())
        # RQ3: the larger of 25 % of 4.0 (= 1.0) and one move → 1.0.
        assert sensitivity.material(p, self._summary(primary_rq3=5.05)) == ("yes", ("primary_rq3",))
        assert sensitivity.material(p, self._summary(primary_rq3=4.9)) == ("no", ())
        small = self._summary(primary_rq3=0.5)
        assert sensitivity.material(small, self._summary(primary_rq3=1.4)) == ("no", ())  # floor 1 move
        assert sensitivity.material(small, self._summary(primary_rq3=1.6)) == ("yes", ("primary_rq3",))
        # A sign change alone is not material (the clause was dropped 2026-10-03).
        assert sensitivity.material(self._summary(primary_rq3=0.4), self._summary(primary_rq3=-0.4)) == ("no", ())

    def test_pending_without_an_estimate(self):
        p = self._summary(primary_rq3=None)
        assert sensitivity.material(p, self._summary(primary_rq3=None)) == ("pending", ())
        assert sensitivity.material(p, self._summary(primary_rq3=None, n_attempts=50)) == ("yes", ("attempts",))


class TestOutputs:
    def test_deltas_csv_column_order_and_grouping(self, tmp_path, golden_dnd_tab, four_shapes_board):
        primary = _golden_db(tmp_path, golden_dnd_tab, four_shapes_board, "primary.sqlite")
        out = tmp_path / "out"
        _, rows = sensitivity.run_all(primary, out)
        assert len(rows) == 10
        text = (out / "deltas.csv").read_text()
        header = text.splitlines()[0].split(",")
        assert header[:6] == [
            "alternative", "delta_attempts", "delta_ready_share", "delta_agreement_pp",
            "delta_primary_rq3_moves", "material",
        ]
        records = list(csv.DictReader(io.StringIO(text)))
        assert [r["group"] for r in records] == ["reconstruction"] * 5 + ["features_inclusion"] * 5
        assert [r["knob"] for r in records] == ["1", "1", "2", "3", "10", "4", "5", "6", "6", "7"]
        assert all(r["delta_primary_rq3_moves"] == "" for r in records)
        assert all(r["material"] == "pending" for r in records)
        assert {p.name for p in out.iterdir()} >= {"deltas.csv", "deltas.json", "primary"} | {k.key for k in sensitivity.KNOB_TABLE}
        for k in sensitivity.KNOB_TABLE:
            expected = {
                "scratch.sqlite", "config.json", "checks.csv", "attempt_metrics.csv", "modeling_table.csv",
            }
            if k.seeds:
                expected.add("seeded_strata.csv")  # the knob-10 run's counts-only file
            assert {q.name for q in (out / k.key).iterdir()} == expected
        meta = json.loads((out / "deltas.json").read_text())
        assert meta["groups"] == {"reconstruction": "knobs 1-3, 10", "features_inclusion": "knobs 4-7"}

    def test_off_table_knob_is_labelled_post_hoc(self, tmp_path, golden_dnd_tab, four_shapes_board):
        primary = _golden_db(tmp_path, golden_dnd_tab, four_shapes_board, "primary.sqlite")
        knob = sensitivity.Knob("gap-900", 1, sensitivity.RECONSTRUCTION, "gap 15 min",
                                segmentation={"gap_seconds": 900.0})
        s = _run_one(primary, tmp_path / "out", knob)
        assert s.post_hoc is True
        config = json.loads((tmp_path / "out" / "gap-900" / "config.json").read_text())
        assert config["label"] == "post_hoc"

    def test_canonical_database_is_refused(self, tmp_path, monkeypatch):
        canonical = tmp_path / "data" / "proteus.db"
        monkeypatch.setattr(db, "default_db_path", lambda **kw: canonical)
        monkeypatch.setattr(sensitivity.db, "default_db_path", lambda **kw: canonical)
        with pytest.raises(sensitivity.SensitivityError, match="canonical"):
            sensitivity.refuse_canonical(canonical)
        sensitivity.refuse_canonical(canonical, allow_canonical=True)
        sensitivity.refuse_canonical(tmp_path / "scratch.sqlite")


class TestEvidence:
    def test_golden_fixture_counts_by_hand(self, tmp_path, golden_dnd_tab, four_shapes_board):
        """DemoStudent1: eight non-page events at 5, 11, 16, 36, 40, 56, 60,
        90 s (seven gaps: 6, 5, 20, 4, 16, 4, 30) with one reset;
        DemoStudent2: four events at 0, 10, 20, 30 s (three gaps of 10).
        Ten gaps, median 10, max 30, none above 120 s; one session each; no
        page view between events (the page view precedes DemoStudent1's
        first drop); two attempts, one with a reset."""
        primary = _golden_db(tmp_path, golden_dnd_tab, four_shapes_board, "primary.sqlite")
        conn = db.connect(primary)
        try:
            report = sensitivity.evidence(conn)
        finally:
            conn.close()
        assert (report.n_timelines, report.n_events, report.n_gaps) == (2, 12, 10)
        assert report.quantiles_s["p50"] == 10.0
        assert report.max_gap_s == 30.0
        assert report.above == {"gt_120s": 0, "gt_300s": 0, "gt_600s": 0, "gt_1800s": 0, "gt_3600s": 0}
        assert (report.n_session_changes, report.n_page_views_between) == (0, 0)
        assert (report.n_reset_events, report.n_timelines_with_reset) == (1, 1)
        assert (report.n_attempts, report.n_attempts_with_reset) == (2, 1)
        text = sensitivity.render_evidence(report)
        assert "counts only" in text and "No outcome column was read" in text


class TestCli:
    def test_run_and_evidence(self, tmp_path, golden_dnd_tab, four_shapes_board, capsys):
        primary = _golden_db(tmp_path, golden_dnd_tab, four_shapes_board, "primary.sqlite")
        out = tmp_path / "out"
        assert cli.main([
            "dnd", "sensitivity", "run", "--primary-db", str(primary), "--out", str(out),
            "--knob", "k2-split-on-reset", "--knob", "k7-ready-and-limited",
        ]) == 0
        printed = capsys.readouterr().out
        assert "alternatives:   2" in printed
        assert "empty (run with --estimate)" in printed
        assert (out / "deltas.csv").exists()
        with pytest.raises(SystemExit):
            cli.main(["dnd", "sensitivity", "run", "--primary-db", str(primary),
                      "--out", str(out), "--knob", "k9-not-frozen"])
        capsys.readouterr()
        assert cli.main(["--db", str(primary), "dnd", "sensitivity", "evidence"]) == 0
        assert "# Sensitivity brackets" in capsys.readouterr().out
        report = tmp_path / "evidence.md"
        assert cli.main(["--db", str(primary), "dnd", "sensitivity", "evidence", "-o", str(report)]) == 0
        assert report.read_text().startswith("# Sensitivity brackets")

    def test_missing_primary(self, tmp_path, capsys):
        assert cli.main(["dnd", "sensitivity", "run", "--primary-db", str(tmp_path / "x.sqlite"),
                         "--out", str(tmp_path / "o")]) == 1
