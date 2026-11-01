"""
Tests for the RQ3 contrast runner (expansion memo E-15;
cc-task-frozen-run-tooling). The statistics are pinned by hand: medians and
interquartile ranges by linear interpolation, the difference of medians, and
a student-clustered bootstrap interval on a two-student population whose
resampling distribution is enumerable.
"""

from __future__ import annotations

import csv
import json
import random

import pytest

from proteus import cli, db
from proteus.core import ingest as core_ingest
from proteus.services.dragndrop import contrast, process
from proteus.services.dragndrop.derive import derive_events
from proteus.services.dragndrop.reconstruct import reconstruct_all
from proteus.services.dragndrop.scoring import score_checks


def _row(attempt_id, student, group, readiness="ready", flags=("replay_ok",), **features):
    question_id = features.pop("question_id", "q1")
    base = {f: None for f in contrast.BLOCK1 + contrast.BLOCK2 + contrast.BLOCK3 + contrast.STRUCTURAL}
    base.update(features)
    return contrast.AttemptRow(
        attempt_id=attempt_id, student_id=student, question_id=question_id,
        readiness=readiness, flags=frozenset(flags), group=group, features=base,
    )


def _population_two_students():
    """Student A: first-check-correct attempt with 3 effective moves before
    the first check, first-check-incorrect attempt with 8; student B: 5 and
    6. Pooled medians 4 (correct) and 7 (incorrect): difference -3; IQRs by
    linear interpolation (3.5, 4.5) and (6.5, 7.5). Resampling the two
    students with replacement gives AA → 3 - 8 = -5, AB → 4 - 7 = -3,
    BB → 5 - 6 = -1 (probabilities ¼, ½, ¼), so the 2.5th / 97.5th
    percentile interval over any large number of replicates is [-5, -1]."""
    return [
        _row(1, "A", contrast.GROUP_CORRECT, n_effective_moves_pre=3, active_time_pre_s=10.0,
             n_excess_moves_pre=0, n_takebacks_pre=0, n_resets_pre=0, n_moves=5, n_checks=1,
             active_time_s=20.0),
        _row(2, "A", contrast.GROUP_INCORRECT, n_effective_moves_pre=8, active_time_pre_s=40.0,
             n_excess_moves_pre=2, n_takebacks_pre=1, n_resets_pre=0, first_check_distance=2,
             first_check_omissions=1, first_check_wrong_slot=1, first_check_decoy_card_placements=0,
             first_check_decoy_slot_placements=0, n_effective_moves_repair=2, n_takebacks_repair=0,
             n_resets_repair=0, n_checks_repair=1, active_time_repair_s=15.0, n_fix_moves_repair=2,
             n_moves=10, n_checks=2, active_time_s=60.0),
        _row(3, "B", contrast.GROUP_CORRECT, n_effective_moves_pre=5, active_time_pre_s=30.0,
             n_excess_moves_pre=1, n_takebacks_pre=0, n_resets_pre=0, n_moves=6, n_checks=1,
             active_time_s=35.0),
        _row(4, "B", contrast.GROUP_INCORRECT, n_effective_moves_pre=6, active_time_pre_s=20.0,
             n_excess_moves_pre=0, n_takebacks_pre=0, n_resets_pre=1, first_check_distance=1,
             first_check_omissions=0, first_check_wrong_slot=0, first_check_decoy_card_placements=1,
             first_check_decoy_slot_placements=1, n_effective_moves_repair=1, n_takebacks_repair=1,
             n_resets_repair=0, n_checks_repair=1, active_time_repair_s=5.0, n_fix_moves_repair=1,
             n_moves=7, n_checks=2, active_time_s=30.0),
    ]


class TestPureStatistics:
    def test_percentile_linear_interpolation(self):
        assert contrast.percentile([3, 5], 25) == 3.5
        assert contrast.percentile([3, 5], 75) == 4.5
        assert contrast.percentile([1, 2, 3, 4], 50) == 2.5
        assert contrast.percentile([7], 97.5) == 7.0
        assert contrast.percentile([], 50) is None

    def test_bootstrap_interval_is_enumerable_on_two_students(self):
        samples = [("A", contrast.GROUP_CORRECT, 3.0), ("A", contrast.GROUP_INCORRECT, 8.0),
                   ("B", contrast.GROUP_CORRECT, 5.0), ("B", contrast.GROUP_INCORRECT, 6.0)]
        lo, hi, n = contrast.bootstrap_difference(samples, 2000, random.Random(7))
        assert (lo, hi) == (-5.0, -1.0)
        assert n == 2000

    def test_bootstrap_is_seeded_and_skips_empty_groups(self):
        samples = [("A", contrast.GROUP_CORRECT, 3.0), ("B", contrast.GROUP_INCORRECT, 8.0)]
        a = contrast.bootstrap_difference(samples, 50, random.Random(3))
        b = contrast.bootstrap_difference(samples, 50, random.Random(3))
        assert a == b
        # Resamples drawing one student twice leave a group empty and are
        # skipped: only AB-type replicates (difference -5) are valid.
        assert a[0] == a[1] == -5.0
        assert 0 < a[2] < 50

    def test_bootstrap_without_a_valid_replicate(self):
        samples = [("A", contrast.GROUP_CORRECT, 3.0)]
        assert contrast.bootstrap_difference(samples, 10, random.Random(1)) == (None, None, 0)


class TestCounts:
    def test_groups_arms_and_reporting_level(self):
        pop = _population_two_students()
        counts = contrast.count_groups(pop)
        by = {g.group: g for g in counts.groups}
        assert by[contrast.GROUP_CORRECT].attempts == 2
        assert by[contrast.GROUP_CORRECT].students == 2
        assert by[contrast.GROUP_INCORRECT].items == 1
        assert counts.arms[0].n_first_check_correct == 2
        assert counts.arms[0].clears_floor is False  # floor is 5 per arm
        assert counts.n_items_clearing_floor == 0
        assert counts.reporting_level == "pooled_only"
        assert counts.floor_per_arm == 5 and counts.min_items_for_per_item == 3

    def test_per_item_level_needs_three_items_clearing_the_floor(self):
        pop = []
        aid = 0
        for item in ("q1", "q2", "q3"):
            for k in range(5):
                for group in contrast.GROUPS:
                    aid += 1
                    pop.append(_row(aid, f"s{aid}", group, question_id=item, n_effective_moves_pre=k))
        assert contrast.count_groups(pop).reporting_level == "per_item_and_pooled"
        assert contrast.count_groups(pop[:-2]).n_items_clearing_floor == 2
        assert contrast.count_groups(pop[:-2]).reporting_level == "pooled_only"


class TestEstimate:
    def test_block1_by_hand(self):
        pop = _population_two_students()
        est = contrast.estimate(pop, contrast.ContrastConfig(seed=11, B=2000))
        row = next(r for r in est.rows if r.feature == "n_effective_moves_pre")
        assert row.block == "pre_first_check"
        assert (row.n_first_check_correct, row.n_first_check_incorrect_eventually_correct) == (2, 2)
        assert row.median_first_check_correct == 4.0
        assert row.iqr_first_check_correct == (3.5, 4.5)
        assert row.median_first_check_incorrect_eventually_correct == 7.0
        assert row.iqr_first_check_incorrect_eventually_correct == (6.5, 7.5)
        assert row.difference_in_medians == -3.0
        assert (row.bootstrap_lo, row.bootstrap_hi) == (-5.0, -1.0)
        assert row.n_valid_replicates == 2000

    def test_row_order_follows_tab_process_results(self):
        est = contrast.estimate(_population_two_students(), contrast.ContrastConfig(seed=1, B=10))
        assert [r.feature for r in est.rows] == list(
            contrast.BLOCK1 + contrast.BLOCK2 + contrast.BLOCK3 + contrast.STRUCTURAL
        )
        blocks = [r.block for r in est.rows]
        assert blocks[:5] == ["pre_first_check"] * 5
        assert blocks[5:10] == ["first_checked_state"] * 5
        assert blocks[10:16] == ["repair"] * 6
        assert blocks[16:] == ["whole_attempt"] * 3

    def test_blocks_two_and_three_describe_the_incorrect_group_only(self):
        est = contrast.estimate(_population_two_students(), contrast.ContrastConfig(seed=1, B=10))
        dist = next(r for r in est.rows if r.feature == "first_check_distance")
        # Incorrect group: distances 2 and 1 → median 1.5; nothing for the
        # correct group (fixed at zero by construction) and no contrast.
        assert dist.n_first_check_correct == 0
        assert dist.median_first_check_incorrect_eventually_correct == 1.5
        assert dist.difference_in_medians is None
        repair = next(r for r in est.rows if r.feature == "n_effective_moves_repair")
        assert repair.median_first_check_incorrect_eventually_correct == 1.5
        assert repair.bootstrap_lo is None

    def test_structural_descriptors_have_no_interval(self):
        est = contrast.estimate(_population_two_students(), contrast.ContrastConfig(seed=1, B=10))
        moves = next(r for r in est.rows if r.feature == "n_moves")
        assert moves.block == "whole_attempt"
        assert moves.median_first_check_correct == 5.5  # {5, 6}
        assert moves.median_first_check_incorrect_eventually_correct == 8.5  # {10, 7}
        assert moves.difference_in_medians is None

    def test_estimate_requires_a_seed(self):
        with pytest.raises(ValueError):
            contrast.estimate(_population_two_students(), contrast.ContrastConfig())

    def test_primary_difference_matches_block1(self):
        pop = _population_two_students()
        row = contrast._feature_row(
            "pre_first_check", contrast.PRIMARY_FEATURE, pop, contrast.ContrastConfig(), None,
            contrast=True,
        )
        assert row.difference_in_medians == -3.0


class TestFeatureMatrix:
    def test_limited_attempt_contributes_only_permitted_families(self):
        mismatch = _row(1, "A", contrast.GROUP_INCORRECT, readiness="limited",
                        flags=("check_mismatch",), n_effective_moves_pre=4,
                        first_check_distance=2, active_time_pre_s=9.0)
        assert mismatch.value("n_effective_moves_pre") is None  # movement not permitted
        assert mismatch.value("first_check_distance") == 2.0  # endpoint permitted
        assert mismatch.value("active_time_pre_s") is None
        assert mismatch.value("n_effective_moves_pre", apply_matrix=False) == 4.0
        idle = _row(2, "B", contrast.GROUP_CORRECT, readiness="limited",
                    flags=("long_idle_gap",), n_effective_moves_pre=4, active_time_pre_s=9.0)
        assert idle.value("n_effective_moves_pre") == 4.0  # movement permitted
        assert idle.value("active_time_pre_s") == 9.0  # active time permitted
        ambiguous = _row(3, "C", contrast.GROUP_CORRECT, readiness="limited",
                         flags=("row_order_ambiguous",), n_effective_moves_pre=4,
                         first_check_distance=0, first_check_omissions=0)
        assert ambiguous.value("n_effective_moves_pre") is None
        assert ambiguous.value("first_check_distance") is None  # conservative
        assert ambiguous.value("first_check_omissions") == 0.0

    def test_ready_attempts_are_never_filtered(self):
        ready = _row(1, "A", contrast.GROUP_CORRECT, flags=("replay_ok", "noop_move"),
                     n_effective_moves_pre=4)
        assert ready.value("n_effective_moves_pre") == 4.0

    def test_every_flag_in_the_vocabulary_has_a_row(self):
        from proteus.services.dragndrop import qc

        assert set(contrast.PERMITTED) == set(qc.FLAG_ORDER)
        assert set(contrast.FAMILY) == set(
            contrast.BLOCK1 + contrast.BLOCK2 + contrast.BLOCK3 + contrast.STRUCTURAL
        )


class TestRepresentatives:
    def test_nearest_to_group_median_standardized_within_the_busiest_item(self):
        # Three first-check-correct attempts on q1 and one on q2 (q1 is the
        # item with most attempts). Block-1 vectors on q1 differ only in
        # effective moves: 2, 4, 9 → median 4 → attempt 2 is nearest
        # (distance 0); standardization cannot change that here.
        pop = [
            _row(1, "A", contrast.GROUP_CORRECT, n_effective_moves_pre=2, n_excess_moves_pre=0,
                 n_takebacks_pre=0, n_resets_pre=0, active_time_pre_s=10.0),
            _row(2, "B", contrast.GROUP_CORRECT, n_effective_moves_pre=4, n_excess_moves_pre=0,
                 n_takebacks_pre=0, n_resets_pre=0, active_time_pre_s=10.0),
            _row(3, "C", contrast.GROUP_CORRECT, n_effective_moves_pre=9, n_excess_moves_pre=0,
                 n_takebacks_pre=0, n_resets_pre=0, active_time_pre_s=10.0),
            _row(4, "D", contrast.GROUP_CORRECT, question_id="q2", n_effective_moves_pre=4,
                 n_excess_moves_pre=0, n_takebacks_pre=0, n_resets_pre=0, active_time_pre_s=10.0),
        ]
        reps, item = contrast.representatives(pop, contrast.ContrastConfig())
        assert item == "q1"
        assert [(r.group, r.attempt_id, r.distance) for r in reps] == [
            (contrast.GROUP_CORRECT, 2, 0.0)
        ]

    def test_standardization_changes_the_metric(self):
        # Without standardization active time (seconds) dominates; with it
        # both coordinates are unit-scaled. Moves 1, 5, 9 and times 100,
        # 150, 130: the medians are 5 and 130. Raw distances 30.3, 20, 4 →
        # attempt 3. Standardized (pstdev 3.27 moves, 20.55 s): z-moves
        # -1.22, 0, 1.22 and z-time -1.30, 1.14, 0.16 with medians 0 and
        # 0.16, so the distances are 1.91, 0.97, 1.22 → attempt 2.
        pop = [
            _row(1, "A", contrast.GROUP_CORRECT, n_effective_moves_pre=1, n_excess_moves_pre=0,
                 n_takebacks_pre=0, n_resets_pre=0, active_time_pre_s=100.0),
            _row(2, "B", contrast.GROUP_CORRECT, n_effective_moves_pre=5, n_excess_moves_pre=0,
                 n_takebacks_pre=0, n_resets_pre=0, active_time_pre_s=150.0),
            _row(3, "C", contrast.GROUP_CORRECT, n_effective_moves_pre=9, n_excess_moves_pre=0,
                 n_takebacks_pre=0, n_resets_pre=0, active_time_pre_s=130.0),
        ]
        raw, _ = contrast.representatives(
            pop, contrast.ContrastConfig(representative_standardize=False)
        )
        std, _ = contrast.representatives(pop, contrast.ContrastConfig())
        assert raw[0].attempt_id == 3
        assert std[0].attempt_id == 2

    def test_skips_attempts_with_an_undefined_block1_feature(self):
        pop = [_row(1, "A", contrast.GROUP_CORRECT, n_effective_moves_pre=2)]  # the rest None
        # The item restriction is still reported: q1 was chosen and no
        # candidate in it is complete. An empty population names no item.
        assert contrast.representatives(pop, contrast.ContrastConfig()) == ([], "q1")
        assert contrast.representatives([], contrast.ContrastConfig()) == ([], None)

    def test_busiest_item_is_counted_over_the_whole_admitted_population(self):
        # q1 contributes four admitted attempts, one with an undefined block-1
        # feature; q2 contributes three complete ones. The rule names the item
        # with most attempts (q1) not the item with most complete attempts;
        # the incomplete attempt is then dropped as a candidate only. Among
        # q1's complete candidates (2, 4, 9 moves) the median is attempt 2.
        def complete(aid, student, moves, question_id):
            return _row(aid, student, contrast.GROUP_CORRECT, question_id=question_id,
                        n_effective_moves_pre=moves, n_excess_moves_pre=0,
                        n_takebacks_pre=0, n_resets_pre=0, active_time_pre_s=10.0)

        pop = [
            complete(1, "A", 2, "q1"), complete(2, "B", 4, "q1"), complete(3, "C", 9, "q1"),
            _row(4, "D", contrast.GROUP_CORRECT, question_id="q1", n_effective_moves_pre=4),
            complete(5, "E", 1, "q2"), complete(6, "F", 1, "q2"), complete(7, "G", 1, "q2"),
        ]
        reps, item = contrast.representatives(pop, contrast.ContrastConfig())
        assert item == "q1"
        assert [(r.question_id, r.attempt_id) for r in reps] == [("q1", 2)]

    def test_frozen_rule_is_the_default_and_recorded(self, golden_run, tmp_path):
        # Stage 0.9 (owner, 2026-10-07, rulings R1a R2a R3a): both halves of
        # the rule confirmed. The defaults are the frozen values and
        # provenance.json says so; a run that flips either half is marked as
        # deviating rather than described as frozen.
        cfg = contrast.ContrastConfig()
        assert cfg.representative_standardize is True
        assert cfg.representative_within_item_with_most_attempts is True
        assert contrast.REPRESENTATIVE_RULE_FROZEN == "2026-10-07"
        assert contrast.FROZEN_REPRESENTATIVE_RULE == {
            "standardize": True, "within_item_with_most_attempts": True,
        }
        conn = db.connect(golden_run)
        try:
            contrast.run(
                conn, tmp_path / "frozen", contrast.ContrastConfig(seed=1, B=5),
                estimate_mode=True,
            )
            contrast.run(
                conn, tmp_path / "deviating",
                contrast.ContrastConfig(seed=1, B=5, representative_standardize=False),
                estimate_mode=True,
            )
        finally:
            conn.close()
        frozen = json.loads((tmp_path / "frozen" / "provenance.json").read_text())
        rule = frozen["representative_rule"]
        assert rule["standardize"] is True
        assert rule["within_item_with_most_attempts"] is True
        assert rule["frozen"] == "2026-10-07"
        assert rule["status"].startswith("frozen 2026-10-07")
        assert "pending" not in rule["status"]
        deviating = json.loads((tmp_path / "deviating" / "provenance.json").read_text())
        assert deviating["representative_rule"]["status"] == (
            "deviates from the rule frozen 2026-10-07: standardize=False"
        )


@pytest.fixture
def golden_run(tmp_path, golden_dnd_tab, four_shapes_board):
    path = tmp_path / "run.sqlite"
    conn = db.connect(path)
    db.bootstrap(conn)
    core_ingest.ingest_file(conn, golden_dnd_tab)
    derive_events(conn)
    four_shapes_board(conn)
    reconstruct_all(conn)
    score_checks(conn)
    process.analyze_all(conn)
    conn.commit()
    conn.close()
    return path


class TestEndToEnd:
    def test_population_on_the_golden_fixture(self, golden_run):
        conn = db.connect(golden_run)
        try:
            pop = contrast.read_population(conn)
        finally:
            conn.close()
        # DemoStudent1 is ready, first check incorrect, eventually correct;
        # DemoStudent2 is excluded (never correct, readiness exclude).
        assert [(r.group, r.readiness) for r in pop] == [
            (contrast.GROUP_INCORRECT, "ready")
        ]
        r = pop[0]
        assert r.features["n_effective_moves_pre"] == 4
        assert r.features["first_check_distance"] == 1
        # The first checked state (1,1,2,3): the decoy placed in the decoy
        # slot. One decoy-card placement, one decoy-slot placement, no
        # omission, no wrong slot.
        assert r.features["first_check_omissions"] == 0
        assert r.features["first_check_wrong_slot"] == 0
        assert r.features["first_check_decoy_card_placements"] == 1
        assert r.features["first_check_decoy_slot_placements"] == 1
        assert r.features["n_effective_moves_repair"] == 1

    def test_counts_only_files(self, golden_run, tmp_path):
        conn = db.connect(golden_run)
        try:
            _, est = contrast.run(conn, tmp_path / "out")
        finally:
            conn.close()
        assert est is None
        assert {p.name for p in (tmp_path / "out").iterdir()} == {
            "counts.json", "arms.csv", "provenance.json"
        }
        data = json.loads((tmp_path / "out" / "counts.json").read_text())
        assert data["reporting_level"] == "pooled_only"
        assert data["groups"][1] == {
            "group": contrast.GROUP_INCORRECT, "attempts": 1, "students": 1, "items": 1,
        }
        prov = json.loads((tmp_path / "out" / "provenance.json").read_text())
        assert prov["mode"] == "counts_only"
        assert prov["seed"] is None
        assert [r["stage"] for r in prov["stage_runs"]] == ["derive", "reconstruct", "score", "analyze"]

    def test_counts_only_rerun_removes_stale_estimate_files(self, golden_run, tmp_path):
        conn = db.connect(golden_run)
        try:
            contrast.run(
                conn, tmp_path / "out", contrast.ContrastConfig(seed=5, B=20), estimate_mode=True
            )
            assert (tmp_path / "out" / "tab-process-results.csv").exists()
            contrast.run(conn, tmp_path / "out")
        finally:
            conn.close()
        # The directory never contradicts provenance.json.
        assert {p.name for p in (tmp_path / "out").iterdir()} == {
            "counts.json", "arms.csv", "provenance.json"
        }
        prov = json.loads((tmp_path / "out" / "provenance.json").read_text())
        assert prov["mode"] == "counts_only"

    def test_estimate_files_and_headers(self, golden_run, tmp_path):
        conn = db.connect(golden_run)
        try:
            contrast.run(
                conn, tmp_path / "out", contrast.ContrastConfig(seed=5, B=20), estimate_mode=True
            )
        finally:
            conn.close()
        text = (tmp_path / "out" / "tab-process-results.csv").read_text()
        head = [line for line in text.splitlines() if line.startswith("#")]
        assert any("P5 frozen 2026-09-27" in line for line in head)
        assert any("representative rule 2026-10-07" in line for line in head)
        assert any("seed=5 B=20" in line for line in head)
        rows = list(csv.DictReader(line for line in text.splitlines() if not line.startswith("#")))
        assert [r["feature"] for r in rows][:5] == list(contrast.BLOCK1)
        first = rows[0]
        assert first["n_first_check_correct"] == "0"
        assert first["median_first_check_incorrect_eventually_correct"] == "4"
        assert first["difference_in_medians"] == ""  # one group only
        reps = (tmp_path / "out" / "representatives.csv").read_text()
        assert "student" not in reps
        assert "DemoStudent1" not in reps
        assert reps.splitlines()[0] == "group,question_id,attempt_id,distance"
        for name in ("block1_per_item.csv", "provenance.json", "counts.json", "arms.csv"):
            assert (tmp_path / "out" / name).exists()

    def test_group_names_are_structural_everywhere(self, golden_run, tmp_path):
        conn = db.connect(golden_run)
        try:
            contrast.run(conn, tmp_path / "out", contrast.ContrastConfig(seed=1, B=5), estimate_mode=True)
        finally:
            conn.close()
        blob = " ".join(p.read_text() for p in (tmp_path / "out").iterdir())
        for forbidden in ("direct", "revised"):
            assert f'"{forbidden}"' not in blob and f",{forbidden}," not in blob
        assert contrast.GROUP_INCORRECT in blob

    def test_cli_counts_only_and_estimate(self, golden_run, tmp_path, capsys):
        out = tmp_path / "cli"
        assert cli.main(["--db", str(golden_run), "dnd", "contrast", "--out", str(out)]) == 0
        printed = capsys.readouterr().out
        assert "counts only" in printed
        assert f"{contrast.GROUP_INCORRECT}: 1 attempts" in printed
        assert "reporting level: pooled_only" in printed
        assert cli.main(["--db", str(golden_run), "dnd", "contrast", "--out", str(out), "--estimate"]) == 1
        assert "needs --seed" in capsys.readouterr().err
        assert cli.main([
            "--db", str(golden_run), "dnd", "contrast", "--out", str(out),
            "--estimate", "--seed", "3", "--B", "10", "--include-limited",
            "--exclude-flag", "row_order_ambiguous",
        ]) == 0
        prov = json.loads((out / "provenance.json").read_text())
        assert prov["readiness"] == ["ready", "limited"]
        assert prov["exclude_flags"] == ["row_order_ambiguous"]
        assert prov["B"] == 10 and prov["seed"] == 3
