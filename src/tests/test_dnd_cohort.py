"""
Cohort aggregation smoke + view-bound tests (build item EX-1).

EX-1 "Done when": ``cohort.cohort_data`` is callable from a plain pytest with no
Streamlit and returns the data for all eight v1 charts. This pins the bundle on
the single-student worked attempt (the retired ``sec-worked-example`` walk,
see test_dnd_worked_example). Two checks, the first σ=2 (Incorrect, both
decoys leaked) and the second the key (σ=4). So every field group has a
hand-checkable value, and it proves the ``FilterSpec`` narrows the read as a
view bound (rule 7) rather than re-scoring.

The four-student cohort numbers (heatmap X̄, mean 2.75, difficulty, mislabel
gap. The retired ``subsec-we-cohort`` example) are pinned separately as the
EX-2 golden test.
"""

from __future__ import annotations

import pytest

from proteus.core import ingest as core_ingest
from proteus.core.filters import FilterSpec
from proteus.services.dragndrop import cohort
from proteus.services.dragndrop.derive import derive_events
from proteus.services.dragndrop.reconstruct import reconstruct_all
from proteus.services.dragndrop.scoring import score_checks

Q = "demo-book__demo-shapes-q1"
KEY = (1, 1, 2, 0)
R = 3


@pytest.fixture
def cohort_db(db_conn, golden_dnd_worked_example_tab, four_shapes_board):
    """The worked example flowed through ingest → derive → board → reconstruct
    → score, leaving ``dnd_check_scores`` + ``dnd_configurations`` populated."""
    core_ingest.ingest_file(db_conn, golden_dnd_worked_example_tab)
    derive_events(db_conn)
    four_shapes_board(db_conn)
    reconstruct_all(db_conn)
    score_checks(db_conn)
    return db_conn


class TestCardEntropy:
    """F11: the pure H_j (g-baseline-card-entropy), natural log, 0·log 0 = 0."""

    def test_journal_tallies_column(self):
        # tab-cheat-tallies, card c2 over N = 30 final checks: counts
        # (2, 16, 12, 0) → X̄ column (1/15, 8/15, 2/5, 0).
        import math

        column = [2 / 30, 16 / 30, 12 / 30, 0.0]
        expected = -sum(p * math.log(p) for p in column if p)
        assert cohort.card_entropy(column) == pytest.approx(expected)
        assert cohort.card_entropy(column) == pytest.approx(0.8823, abs=5e-5)

    def test_consensus_column_is_zero(self):
        # Everyone agreed (rightly or wrongly): no dispersion.
        assert cohort.card_entropy([0.0, 1.0, 0.0, 0.0]) == 0.0

    def test_zero_column_uses_the_convention(self):
        # An empty sample's all-zero column stays 0 by 0·log 0 = 0.
        assert cohort.card_entropy([0.0, 0.0, 0.0, 0.0]) == 0.0

    def test_uniform_column_is_the_maximum(self):
        import math

        assert cohort.card_entropy([0.25] * 4) == pytest.approx(math.log(4))


class TestBundleShape:
    def test_callable_returns_all_eight_chart_datasets(self, cohort_db):
        data = cohort.cohort_data(cohort_db, Q, unit="final")
        # Board shape echoed for the chart axes.
        assert data.n_cards == 4
        assert data.n_zones == R
        assert data.key == KEY
        assert data.card_labels == ["c1", "c2", "c3", "c4"]
        # Pentagon (slot 3) is the decoy zone → starred in the axis labels.
        assert data.zone_labels == ["tray z0", "z1", "z2", "z3*"]
        # Two checks in scope; the final unit picks one per attempt.
        assert data.n_checks == 2
        assert data.n_unit == 1
        # All eight chart datasets are present and shaped.
        assert len(data.sigma_all) == 2 and len(data.sigma_unit) == 1
        assert [r["error_type"] for r in data.error_totals] == [
            "omissions", "placed decoys", "merge pairs", "split pairs", "mislabels"
        ]
        assert len(data.gap_rows) == 2
        assert len(data.heat_count) == R + 1 and len(data.heat_count[0]) == 4
        assert len(data.heat_frac) == R + 1
        assert len(data.difficulty) == 4
        assert data.conf_key_labels and data.conf_rows
        assert data.lc_rows and data.cock_rows


class TestScoreDistribution:
    def test_sigma_persisted_all_and_unit(self, cohort_db):
        data = cohort.cohort_data(cohort_db, Q, unit="final")
        # All checks in (attempt, time) order: Incorrect σ=2 then Correct σ=4.
        assert data.sigma_all == [2, 4]
        # The final check is the key.
        assert data.sigma_unit == [4]


class TestPlacement:
    def test_heatmap_mean_and_difficulty_on_final(self, cohort_db):
        data = cohort.cohort_data(cohort_db, Q, unit="final")
        # The single final state is the key (1,1,2,0): each card sits home.
        assert data.heat_count[1][0] == 1  # c1 → z1
        assert data.heat_count[1][1] == 1  # c2 → z1
        assert data.heat_count[2][2] == 1  # c3 → z2
        assert data.heat_count[0][3] == 1  # c4 (decoy) → tray
        assert data.heat_frac[1][0] == pytest.approx(1.0)
        # mean score ⟨X̄,X*⟩ = Σ_j X̄[key_j, j] = 4 (the only final check is the key).
        mean_score = sum(
            data.heat_count[KEY[j - 1]][j - 1] / data.n_unit for j in range(1, 5)
        )
        assert mean_score == pytest.approx(4.0)
        # Nobody misplaces anything in the (single, perfect) final sample.
        assert [d["difficulty"] for d in data.difficulty] == [0.0, 0.0, 0.0, 0.0]

    def test_unit_all_aggregates_every_check(self, cohort_db):
        data = cohort.cohort_data(cohort_db, Q, unit="all")
        assert data.n_unit == 2
        assert data.sigma_unit == [2, 4]
        # c4 appears once in the empty-slot decoy (z3, from the first check) and
        # once back in the tray (the key, second check).
        assert data.heat_count[3][3] == 1
        assert data.heat_count[0][3] == 1

    def test_unit_first_correct_picks_the_solving_check(self, cohort_db):
        # F16/F26: the first exactly-correct check. Here the second check
        # (the key), not the σ=2 first press.
        data = cohort.cohort_data(cohort_db, Q, unit="first_correct")
        assert data.n_unit == 1
        assert data.sigma_unit == [4]
        assert data.heat_count[0][3] == 1  # the decoy back in the tray
        # The all-checks series is unit-independent.
        assert data.sigma_all == [2, 4]


class TestErrorAndGap:
    def test_error_totals_over_all_checks(self, cohort_db):
        data = cohort.cohort_data(cohort_db, Q, unit="final")
        totals = {r["error_type"]: r["total"] for r in data.error_totals}
        # First check (1,2,2,3): one placed decoy, one merge, one split; the
        # second check is the key (no errors). The five-count profile of
        # subsec-error-profile-bars, on the retired subsec-we-errors numbers.
        assert totals == {
            "omissions": 0, "placed decoys": 1, "merge pairs": 1,
            "split pairs": 1, "mislabels": 0,
        }

    def test_merge_split_totals_over_all_checks(self, cohort_db):
        data = cohort.cohort_data(cohort_db, Q, unit="final")
        # F13: first check (1,2,2,3) has one merge slot and one split group,
        # each with excess 1; the second check is the key. Slot/group counts,
        # riding beside error_totals rather than in it (different units).
        assert data.merge_split_totals == [
            {"summary": "merge slots", "total": 1},
            {"summary": "merge excess", "total": 1},
            {"summary": "split groups", "total": 1},
            {"summary": "split excess", "total": 1},
        ]

    def test_pair_index_gate_shares_one_n(self):
        # The singleton state (one co-placed card) defines VI (trivially 0)
        # but not Rand/ARI. The shared >= 2 co-placed gate must drop it from
        # all three, or VI's mean is pulled toward agreement on a check the
        # others skip. With it gated out, the means are exactly the two
        # remaining states' averages and every n agrees.
        singleton = (1, 0, 0, 0)
        worked = (1, 2, 2, 3)     # Rand 1/3, ARI -1/2, VI ≈ 0.9242
        the_key = KEY             # Rand 1, ARI 1, VI 0
        rows = cohort.pair_index_means([singleton, worked, the_key], KEY, R)
        means = {r["index"]: r for r in rows}
        assert all(r["n"] == 2 for r in rows)
        assert means["rand"]["mean"] == pytest.approx((1 / 3 + 1.0) / 2)
        assert means["adjusted_rand"]["mean"] == pytest.approx(0.25)
        assert means["variation_of_information"]["mean"] == pytest.approx(
            0.9242 / 2, abs=1e-3
        )

    def test_pair_index_means_empty_when_nothing_qualifies(self):
        # Only vacuous states in scope (empty board, a lone co-placed card):
        # no index is reported, rather than VI alone reporting 0.
        rows = cohort.pair_index_means([(0, 0, 0, 0), (1, 0, 0, 0)], KEY, R)
        assert all(r["mean"] is None and r["n"] == 0 for r in rows)

    def test_pair_index_means_over_all_checks(self, cohort_db):
        data = cohort.cohort_data(cohort_db, Q, unit="final")
        # F14: mean of the per-check indices. First check (1,2,2,3) is the
        # worked example (Rand 1/3, ARI -1/2, VI ≈ 0.9242, pinned in
        # test_dnd_errors); the second check is the key (1, 1, 0 resp.).
        means = {r["index"]: r for r in data.pair_index_means}
        assert [r["index"] for r in data.pair_index_means] == [
            "rand", "adjusted_rand", "variation_of_information",
        ]
        assert means["rand"]["n"] == 2
        assert means["rand"]["mean"] == pytest.approx((1 / 3 + 1.0) / 2)
        assert means["adjusted_rand"]["n"] == 2
        assert means["adjusted_rand"]["mean"] == pytest.approx(0.25)
        assert means["variation_of_information"]["n"] == 2
        assert means["variation_of_information"]["mean"] == pytest.approx(
            0.9242 / 2, abs=1e-3
        )

    def test_mislabel_gap_rows(self, cohort_db):
        data = cohort.cohort_data(cohort_db, Q, unit="final")
        # Neither check is a pure relabel (σ̃ = σ), so both gaps are 0; the first
        # has grouping errors, the second (the key) is perfect.
        assert data.gap_rows == [
            {"gap": 0, "perfect": False},
            {"gap": 0, "perfect": True},
        ]


class TestConfusion:
    def test_confusion_on_final_is_the_key(self, cohort_db):
        data = cohort.cohort_data(cohort_db, Q, unit="final")
        assert data.conf_key_labels == ["z1", "z2"]
        assert data.conf_student_labels == ["tray z0", "z1", "z2", "z3"]
        rows = {(r["key_zone"], r["student_zone"]): r for r in data.conf_rows}
        # The final state is the key: every key group's cards sit in their slot.
        assert rows[("z1", "z1")]["frac"] == pytest.approx(1.0)
        assert rows[("z1", "z1")]["count"] == 2  # both triangles key to z1
        assert rows[("z2", "z2")]["frac"] == pytest.approx(1.0)
        assert rows[("z2", "z2")]["count"] == 1


class TestByCheckOrdinal:
    def test_learning_curve_and_cock(self, cohort_db):
        data = cohort.cohort_data(cohort_db, Q, unit="final")
        assert data.lc_rows == [
            {"k": 1, "mean_sigma": 2.0, "n": 1},
            {"k": 2, "mean_sigma": 4.0, "n": 1},
        ]
        assert data.cock_rows == [
            {"k": 1, "correct": 0, "reached": 1, "rate": 0.0},
            {"k": 2, "correct": 1, "reached": 1, "rate": 1.0},
        ]


class TestFilterViewBound:
    def test_permissive_filter_keeps_every_check(self, cohort_db):
        # class_id "1" is the only class in the fixture (selects everything).
        data = cohort.cohort_data(
            cohort_db, Q, unit="all", filter_spec=FilterSpec(class_id="1")
        )
        assert data.n_checks == 2

    def test_nonmatching_class_narrows_to_empty(self, cohort_db):
        data = cohort.cohort_data(
            cohort_db, Q, unit="all", filter_spec=FilterSpec(class_id="no-such-class")
        )
        assert data.n_checks == 0
        assert data.n_unit == 0
        # Per-check datasets go empty; the board-shaped scaffolds stay (all-zero)
        # so the gallery can still lay out empty charts.
        assert data.sigma_all == [] and data.sigma_unit == []
        assert data.gap_rows == [] and data.lc_rows == [] and data.cock_rows == []
        assert all(r["count"] == 0 for r in data.conf_rows)
        assert all(t["total"] == 0 for t in data.error_totals)
        assert all(t["total"] == 0 for t in data.merge_split_totals)
        assert all(
            r["mean"] is None and r["n"] == 0 for r in data.pair_index_means
        )
        assert all(cell == 0 for row in data.heat_count for cell in row)

    def test_future_date_floor_narrows_to_empty(self, cohort_db):
        # The fixture's checks are in 2026-03; a 9999 floor excludes them all,
        # proving the date bound is applied as a view, not ignored.
        data = cohort.cohort_data(
            cohort_db, Q, filter_spec=FilterSpec(date_from="9999-12-31")
        )
        assert data.n_checks == 0


class TestEmptyUnitSample:
    """BUG-04 (audit task A4): the unit sample can be empty while checks are
    in scope. ``first_correct`` on a never-solved question. The sample-derived
    diagnostics must read as "no data" (None), never as real zeros: difficulty
    0.0 says "everyone gets it right", entropy 0.0 says "perfect consensus",
    and an all-zero ō with residual -o★ says "underfilled"."""

    def test_never_solved_first_correct_yields_none_not_zero(self, cohort_db):
        # Simulate the never-solved cohort through the persisted read path:
        # both scored checks stay in scope, none all-or-nothing correct.
        cohort_db.execute(
            "UPDATE dnd_check_scores SET all_or_nothing = 0 "
            "WHERE question_id = ?",
            (Q,),
        )
        cohort_db.commit()
        data = cohort.cohort_data(cohort_db, Q, unit="first_correct")
        assert data.n_checks == 2  # the checks are still in scope…
        assert data.n_unit == 0    # …but no attempt reached the analysis point
        for row in data.difficulty:
            assert row["difficulty"] is None
            assert row["entropy"] is None
        for row in data.occupancy_rows:
            assert row["mean_occupancy"] is None
            assert row["mean_residual"] is None
            assert row["key_occupancy"] is not None  # board-derived, stays
        # The existing empty-sample None convention these fields now join.
        assert data.mean_entropy is None and data.max_entropy is None
        assert data.mean_occupied_zones is None

    def test_populated_unit_sample_keeps_real_values(self, cohort_db):
        # Guard the guard: with the worked example's solving check present,
        # first_correct still carries real numbers (no accidental None leak).
        data = cohort.cohort_data(cohort_db, Q, unit="first_correct")
        assert data.n_unit == 1
        for row in data.difficulty:
            assert isinstance(row["difficulty"], float)
            assert isinstance(row["entropy"], float)
        for row in data.occupancy_rows:
            assert isinstance(row["mean_occupancy"], float)
            assert isinstance(row["mean_residual"], float)


class TestGuards:
    def test_unknown_unit_raises(self, cohort_db):
        with pytest.raises(ValueError, match="unit must be one of"):
            cohort.cohort_data(cohort_db, Q, unit="median")

    def test_unsynced_board_raises(self, db_conn):
        with pytest.raises(ValueError, match="no synced board"):
            cohort.cohort_data(db_conn, Q)


# The four-shape board re-authored without the Pentagon decoy slot: still
# P=4 cards and key (1,1,2,0), but R drops 3 → 2, so zone 3 no longer exists.
THREE_MATCH_XML = """
<cardsort>
  <match><premise>equilateral triangle</premise><premise>right triangle</premise><response>Triangle</response></match>
  <match><premise>square</premise><response>Quadrilateral</response></match>
  <match><premise>circle</premise></match>
</cardsort>
"""


class TestStaleBoardGuard:
    """F17a: a board re-synced to fewer slots leaves stale ``dnd_configurations``
    whose zone indices exceed the smaller R (the B2.4 gap). The cohort read must
    skip-and-count those checks (never reach ``errors.contingency_matrix`` and
    crash the Explore page)."""

    def test_resynced_smaller_board_skips_and_counts(self, cohort_db):
        from proteus.services.dragndrop import board

        spec = board.board_from_source(Q, THREE_MATCH_XML)
        board.write_board(cohort_db, spec, "resynced-hash")
        # Loads cleanly. The first check (1,2,2,3) references the dropped z3
        # → excluded; the second (the key, (1,1,2,0)) still fits → kept.
        data = cohort.cohort_data(cohort_db, Q, unit="final")
        assert data.n_zones == 2
        assert data.n_checks == 1
        assert data.n_excluded == 1
        assert data.sigma_all == [4]
        # The excluded check is gone from every derived dataset, not zeroed in.
        assert all(t["total"] == 0 for t in data.error_totals)
        assert data.lc_rows == [{"k": 1, "mean_sigma": 4.0, "n": 1}]

    def test_current_board_excludes_nothing(self, cohort_db):
        data = cohort.cohort_data(cohort_db, Q, unit="final")
        assert data.n_excluded == 0
        assert data.n_checks == 2
