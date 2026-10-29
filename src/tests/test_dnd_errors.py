"""
Tests for the DnD error inventory (plan item B3.4).

Two contrasting reference configurations (the journal's retired worked
example; the objects they exercise live in ``ch-group-features``):

* the worked first check ``s4 = (1, 2, 2, 3)`` — a genuine *grouping* error
  (one merge, one split; ``sec-merge-and-split-features``), whose contingency
  table the retired ``subsec-we-errors`` printed;
* the swapped-names student ``s = (2, 2, 1, 0)`` — a *perfect* grouping
  filed under swapped slot names, so no merges/splits but a mislabel gap
  (worked live in ``subsec-label-free-example``).

Both make the pair indices (ARI/VI) hand-checkable, and they sit at opposite
ends (ARI = −1/2 vs 1, VI ≈ 0.924 vs 0) — exactly the structure these
cohort-scale indices are meant to surface.
"""

from __future__ import annotations

import pytest

from proteus.services.dragndrop import errors

KEY = (1, 1, 2, 0)
R = 3

S4 = (1, 2, 2, 3)  # worked-example first check
MISLABEL = (2, 2, 1, 0)  # cohort student 2: groups right, names swapped


class TestOccupancy:
    """F8b: the zone-level occupancy summaries (sec-occupancy-and-rank)."""

    def test_running_example_residual(self):
        # ch-baseline-features' running example: the first checked state
        # (1,1,2,3) against key (1,1,2,0) has r(X) = (−1, 0, 0, 1) — the
        # tray one card short, the Pentagon slot one card over.
        state = (1, 1, 2, 3)
        assert errors.occupancy(state, R) == (0, 2, 1, 1)
        assert errors.occupancy(KEY, R) == (1, 2, 1, 0)  # o★
        assert errors.occupancy_residual(state, KEY, R) == (-1, 0, 0, 1)

    def test_residual_entries_sum_to_zero(self):
        for state in (S4, MISLABEL, (0, 0, 0, 0)):
            assert sum(errors.occupancy_residual(state, KEY, R)) == 0

    def test_occupied_zones(self):
        # q counts occupied zones, tray included; X0 occupies only the tray.
        assert errors.occupied_zones((1, 1, 2, 3), R) == 3
        assert errors.occupied_zones((0, 0, 0, 0), R) == 1
        assert errors.occupied_zones(KEY, R) == 3  # tray (decoy), z1, z2

    def test_out_of_range_zone_fails_fast(self):
        with pytest.raises(ValueError, match="out of range"):
            errors.occupancy((1, 7, 2, 0), R)


class TestContingencyTable:
    def test_matches_the_reference_table(self):
        # The retired subsec-we-errors printed exactly this M — the object of
        # subsec-zone-confusion-matrix (rows/cols = tray, z1, z2, z3).
        assert errors.contingency_matrix(S4, KEY, R) == [
            [0, 0, 0, 0],
            [0, 1, 0, 0],
            [0, 1, 1, 0],
            [1, 0, 0, 0],
        ]

    def test_boundary_counts(self):
        assert errors.omissions(S4, KEY) == 0
        assert errors.placed_decoys(S4, KEY) == 1  # the circle in z3

    def test_out_of_range_zone_fails_fast(self):
        # A stale check naming a slot the R=3 board no longer has must raise,
        # not silently undercount M (which would corrupt downstream counts).
        with pytest.raises(ValueError, match="out of range"):
            errors.contingency_matrix((1, 7, 2, 0), KEY, R)


class TestGroupingStructure:
    def test_misplacements(self):
        # The single misplacement: the right triangle (card 2) in z2, keyed z1.
        assert errors.misplacements(S4, KEY) == ((2, 2, 1),)

    def test_merge_and_split(self):
        # Student slot z2 merges a triangle with the square; key group z1 is
        # split across z1 and z2.
        assert errors.merges(S4, KEY, R) == (2,)
        assert errors.splits(S4, KEY, R) == (1,)

    def test_grouping_not_yet_correct_so_no_mislabel(self):
        assert errors.mislabels(S4, KEY, R) == 0
        assert errors.grouping_errors(S4, KEY, R) == 1


class TestMergeSplitExcess:
    """F13: the four summaries of ``sec-merge-and-split-features``, pinned to
    ``subsec-merge-split-example`` (same key s★ = (1,1,2,0))."""

    def test_pure_merge(self):
        # s = (1,1,1,0) piles the square in with the two triangles: slot z1
        # draws on two key groups, and no key group is split.
        s = (1, 1, 1, 0)
        assert len(errors.merges(s, KEY, R)) == 1   # N_merge
        assert errors.merge_excess(s, KEY, R) == 1  # E_merge
        assert errors.splits(s, KEY, R) == ()
        assert errors.split_excess(s, KEY, R) == 0

    def test_forced_merge_split_pair(self):
        # s = (1,2,2,0): the two-card triangle group splits across z1/z2, and
        # z2 merges the stray triangle with the square — the forced pairing
        # on a two-card key group.
        s = (1, 2, 2, 0)
        assert errors.merge_excess(s, KEY, R) == 1
        assert errors.split_excess(s, KEY, R) == 1

    def test_diagonal_slot_block_has_neither(self):
        # The running attempt's first checked state errs only at the tray
        # boundary; its slot block is diagonal.
        s = (1, 1, 2, 3)
        assert errors.merges(s, KEY, R) == ()
        assert errors.merge_excess(s, KEY, R) == 0
        assert errors.splits(s, KEY, R) == ()
        assert errors.split_excess(s, KEY, R) == 0

    def test_three_group_slot_has_excess_two(self):
        # g-merge-excess' own example: a slot holding three key cards from
        # three different key groups has an excess of 2.
        assert errors.merge_excess((1, 1, 1, 0), (1, 2, 3, 0), 3) == 2
        assert errors.split_excess((1, 1, 1, 0), (1, 2, 3, 0), 3) == 0

    def test_worked_example_and_mislabel(self):
        # S4: one merge slot and one split group, each with excess 1. The
        # mislabel student's grouping is perfect, so all four vanish.
        assert errors.merge_excess(S4, KEY, R) == 1
        assert errors.split_excess(S4, KEY, R) == 1
        assert errors.merge_excess(MISLABEL, KEY, R) == 0
        assert errors.split_excess(MISLABEL, KEY, R) == 0


class TestMislabelStudent:
    def test_perfect_grouping_has_no_merges_or_splits(self):
        assert errors.merges(MISLABEL, KEY, R) == ()
        assert errors.splits(MISLABEL, KEY, R) == ()

    def test_mislabel_gap(self):
        # The grouping is perfect; only the names are wrong, gap = 3.
        assert errors.mislabels(MISLABEL, KEY, R) == 3
        assert errors.grouping_errors(MISLABEL, KEY, R) == 0


class TestPairIndices:
    def test_pair_counts_worked_example(self):
        counts = errors.pair_counts(S4, KEY, R)
        assert counts.n == 3
        assert (counts.pairwise_agreement, counts.merge_pairs, counts.split_pairs) == (1, 1, 1)
        assert errors.rand_index(S4, KEY, R) == pytest.approx(1 / 3)

    def test_adjusted_rand_and_vi_worked_example(self):
        assert errors.adjusted_rand_index(S4, KEY, R) == pytest.approx(-0.5)
        assert errors.variation_of_information(S4, KEY, R) == pytest.approx(
            0.9242, abs=1e-3
        )

    def test_mislabel_student_groups_identically(self):
        # Same grouping, different names: Rand and ARI saturate, VI vanishes.
        assert errors.rand_index(MISLABEL, KEY, R) == pytest.approx(1.0)
        assert errors.adjusted_rand_index(MISLABEL, KEY, R) == pytest.approx(1.0)
        assert errors.variation_of_information(MISLABEL, KEY, R) == pytest.approx(0.0)

    def test_indices_none_when_too_few_co_placed_cards(self):
        # Empty board: no co-placed cards, the indices are undefined.
        assert errors.adjusted_rand_index((0, 0, 0, 0), KEY, R) is None
        assert errors.variation_of_information((0, 0, 0, 0), KEY, R) is None

    def test_singleton_defines_vi_but_not_rand_or_ari(self):
        # Exactly one co-placed card: zero pairs, so Rand and ARI are None —
        # but the one-card partitions are trivially identical, so VI is 0.0,
        # not None. This asymmetry is why cohort.pair_index_means gates all
        # three on >= 2 co-placed cards rather than on each index's own
        # None: counting the trivial VI would bias its mean toward agreement
        # on checks the other two skip.
        s = (1, 0, 0, 0)
        assert errors.pair_counts(s, KEY, R).n == 1
        assert errors.rand_index(s, KEY, R) is None
        assert errors.adjusted_rand_index(s, KEY, R) is None
        assert errors.variation_of_information(s, KEY, R) == pytest.approx(0.0)


class TestErrorProfile:
    def test_worked_example_profile(self):
        # The five-count profile (subsec-error-profile-bars): no omissions,
        # one misplaced decoy, one merge pair, one split pair, no mislabel.
        profile = errors.error_profile(S4, KEY, R)
        assert profile == errors.ErrorProfile(
            omissions=0, placed_decoys=1, merge_pairs=1, split_pairs=1, mislabels=0
        )

    def test_mislabel_profile_isolates_the_naming_error(self):
        profile = errors.error_profile(MISLABEL, KEY, R)
        assert profile == errors.ErrorProfile(
            omissions=0, placed_decoys=0, merge_pairs=0, split_pairs=0, mislabels=3
        )
