"""
Error analysis inventory (plan item B3.4): how a check is *wrong*.

A configuration is a partition of the cards into zone-groups, so comparing an
answer to the key is comparing two partitions. Following the journal's
``subsec-zone-confusion-matrix`` (the object) and ch-group-features (its
grouping readout), everything here is read off the contingency table

    M[a][b] = #{ cards the student put at zone a that the key assigns to zone b }

over zones ``a, b ∈ {0, …, R}`` (0 = tray). Its diagonal is the score σ, its
tray row counts omissions, its tray column counts placed decoys, and its slot
block (rows/cols ≥ 1) is the confusion matrix ``N``
(``subsec-partition-confusion-table``) whose structure names the grouping
errors, **merges** (a student slot drawing from several key groups) and
**splits** (a key group scattered across student slots,
``sec-merge-and-split-features``), and feeds the two standard partition
indices.

``mislabel`` is the one label-aware mode: a perfect grouping filed under the
wrong slot names, the gap ``σ̃ - σ`` (here, the slot-block best assignment
minus its diagonal).

The pair-based indices (Rand, adjusted Rand, variation of information) are
computed but are **cohort-scale-only**: on a single answer (the journal's
worked check rests on four co-placed cards, six pairs) they are unstable and
merely illustrative of structure, they earn their keep pooled over many
cards and students (``subsec-pairwise-metric-choice``). All functions are
pure; nothing here writes to the database.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from proteus.services.dragndrop import scoring


def contingency_matrix(
    s: tuple[int, ...], key: tuple[int, ...], n_zones: int
) -> list[list[int]]:
    """
    The full ``(R+1)×(R+1)`` table M including the tray (zone 0):
    ``M[a][b]`` = cards the student put at zone ``a`` that the key keys to ``b``.

    Precondition: every zone in ``s`` and ``key`` lies in ``0..n_zones``. An
    out-of-range zone, e.g. a stale check referencing a slot the synced board
    no longer has (the B2.4 gap), is raised rather than silently dropped:
    dropping would make ``sum(M) < P`` and quietly corrupt every count derived
    from it. Callers pass board-valid configurations (``decode`` bounds the
    cards; ``validate`` flags off-board slots upstream).
    """
    size = n_zones + 1
    matrix = [[0] * size for _ in range(size)]
    for a, b in zip(s, key):
        if not (0 <= a <= n_zones) or not (0 <= b <= n_zones):
            raise ValueError(
                f"contingency_matrix: zone index out of range for an "
                f"{n_zones}-slot board (student zone {a}, key zone {b}); "
                f"bound the configuration to the board before tabulating"
            )
        matrix[a][b] += 1
    return matrix


def occupancy(s: tuple[int, ...], n_zones: int) -> tuple[int, ...]:
    """
    The occupancy vector **o**(X) = X·1 (g-occupancy-vector): entry ``i``
    counts the cards currently in zone ``z_i`` (index 0 = tray, so the
    entries sum to P). The key occupancy **o**★ is ``occupancy(key, R)``.
    """
    counts = [0] * (n_zones + 1)
    for zone in s:
        if not (0 <= zone <= n_zones):
            raise ValueError(
                f"occupancy: zone index {zone} out of range for an "
                f"{n_zones}-slot board"
            )
        counts[zone] += 1
    return tuple(counts)


def occupied_zones(s: tuple[int, ...], n_zones: int) -> int:
    """
    The occupied-zone count q(X) = #{i : o_i(X) > 0}
    (ch-state-model subsec-occupied-zones-rank), how spread out the cards
    are, tray included; not a correctness measure.
    """
    return sum(1 for o in occupancy(s, n_zones) if o > 0)


def occupancy_residual(
    s: tuple[int, ...], key: tuple[int, ...], n_zones: int
) -> tuple[int, ...]:
    """
    The occupancy residual r(X) = **o**(X) - **o**★
    (g-baseline-occupancy-residual): a positive entry is an overfilled zone,
    a negative one underfilled; the entries sum to zero. Zone-level only,
    it ignores *which* cards caused the imbalance.
    """
    return tuple(
        o - o_star
        for o, o_star in zip(occupancy(s, n_zones), occupancy(key, n_zones))
    )


def omissions(s: tuple[int, ...], key: tuple[int, ...]) -> int:
    """Cards left in the tray though the key places them (the tray row of M)."""
    return scoring.u(s, key)


def placed_decoys(s: tuple[int, ...], key: tuple[int, ...]) -> int:
    """Decoy cards the student dropped into a slot (the tray column of M)."""
    return scoring.decoy_source_leak(s, key)


def misplacements(
    s: tuple[int, ...], key: tuple[int, ...]
) -> tuple[tuple[int, int, int], ...]:
    """
    Cards placed in a slot other than their keyed slot, as
    ``(card_index, student_slot, key_slot)`` triples (both slots ≥ 1). Decoy
    cards dropped into a slot are *placed decoys*, not misplacements, and are
    excluded.
    """
    return tuple(
        (j + 1, a, b)
        for j, (a, b) in enumerate(zip(s, key))
        if a >= 1 and b >= 1 and a != b
    )


def merges(
    s: tuple[int, ...], key: tuple[int, ...], n_zones: int
) -> tuple[int, ...]:
    """Student slots (1..R) holding cards from more than one key group."""
    n = scoring.confusion_matrix(s, key, n_zones)
    return tuple(
        r + 1 for r, row in enumerate(n) if sum(1 for x in row if x) >= 2
    )


def splits(
    s: tuple[int, ...], key: tuple[int, ...], n_zones: int
) -> tuple[int, ...]:
    """Key groups (1..R) scattered across more than one student slot."""
    n = scoring.confusion_matrix(s, key, n_zones)
    return tuple(
        c + 1
        for c in range(n_zones)
        if sum(1 for row in n if row[c]) >= 2
    )


def merge_excess(s: tuple[int, ...], key: tuple[int, ...], n_zones: int) -> int:
    """
    The merge excess E_merge = Σ_a max{0, #{b : N_ab > 0} - 1}
    (``g-merge-excess``): key groups drawn on beyond the first, summed over
    the student slots. A slot holding cards from three key groups adds 2.
    A slot/group count, not a card count, and not a pair count.
    """
    n = scoring.confusion_matrix(s, key, n_zones)
    return sum(max(0, sum(1 for x in row if x) - 1) for row in n)


def split_excess(s: tuple[int, ...], key: tuple[int, ...], n_zones: int) -> int:
    """
    The split excess E_split = Σ_b max{0, #{a : N_ab > 0} - 1}
    (``g-split-excess``): student slots used beyond the first, summed over
    the key groups. A slot/group count, like ``merge_excess``; the merge/split
    counts N are ``len(merges(...))`` / ``len(splits(...))``.
    """
    n = scoring.confusion_matrix(s, key, n_zones)
    return sum(
        max(0, sum(1 for row in n if row[c]) - 1) for c in range(n_zones)
    )


def mislabels(s: tuple[int, ...], key: tuple[int, ...], n_zones: int) -> int:
    """
    Cards whose grouping is right but whose slot name is wrong: the slot-block
    best assignment minus its diagonal (``σ̃ - σ`` restricted to the slots).
    """
    n = scoring.confusion_matrix(s, key, n_zones)
    diagonal = sum(n[r][r] for r in range(n_zones))
    return scoring.max_assignment(n) - diagonal


def grouping_errors(s: tuple[int, ...], key: tuple[int, ...], n_zones: int) -> int:
    """Co-placed cards the best relabeling still cannot match (``T - σ̃``)."""
    n = scoring.confusion_matrix(s, key, n_zones)
    total = sum(sum(row) for row in n)
    return total - scoring.max_assignment(n)


# ---------------------------------------------------------------------------
# Pair-based grouping indices, cohort-scale-only (see the module note)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PairCounts:
    """Co-placed-card pair tallies underlying the Rand family."""

    n: int  # co-placed cards
    pairwise_agreement: int
    merge_pairs: int  # together for the student, apart for the key
    split_pairs: int  # apart for the student, together for the key

    @property
    def total(self) -> int:
        return self.n * (self.n - 1) // 2


def _choose2(x: int) -> int:
    return x * (x - 1) // 2


def pair_counts(
    s: tuple[int, ...], key: tuple[int, ...], n_zones: int
) -> PairCounts:
    """
    Tally pairwise-agreement / merge / split pairs among the co-placed cards from the
    slot confusion matrix ``N`` (cohort-scale-only; see the module note).
    """
    n_matrix = scoring.confusion_matrix(s, key, n_zones)
    row_sums = [sum(row) for row in n_matrix]
    col_sums = [sum(n_matrix[r][c] for r in range(n_zones)) for c in range(n_zones)]
    n = sum(row_sums)

    together_student = sum(_choose2(r) for r in row_sums)
    together_key = sum(_choose2(c) for c in col_sums)
    together_both = sum(_choose2(n_matrix[r][c]) for r in range(n_zones) for c in range(n_zones))
    total = _choose2(n)

    merge_pairs = together_student - together_both
    split_pairs = together_key - together_both
    pairwise_agreement = total - together_student - together_key + 2 * together_both
    return PairCounts(
        n=n, pairwise_agreement=pairwise_agreement, merge_pairs=merge_pairs, split_pairs=split_pairs
    )


def rand_index(
    s: tuple[int, ...], key: tuple[int, ...], n_zones: int
) -> float | None:
    """Concordant fraction of co-placed-card pairs; None when fewer than 2."""
    counts = pair_counts(s, key, n_zones)
    if counts.total == 0:
        return None
    return counts.pairwise_agreement / counts.total


def adjusted_rand_index(
    s: tuple[int, ...], key: tuple[int, ...], n_zones: int
) -> float | None:
    """
    Rand index corrected for chance (``subsec-adjusted-rand-index``); None
    when fewer than 2 co-placed cards. Cohort-scale-only: unstable on a
    single answer.
    """
    n_matrix = scoring.confusion_matrix(s, key, n_zones)
    row_sums = [sum(row) for row in n_matrix]
    col_sums = [sum(n_matrix[r][c] for r in range(n_zones)) for c in range(n_zones)]
    n = sum(row_sums)
    if n < 2:
        return None

    sum_cells = sum(_choose2(n_matrix[r][c]) for r in range(n_zones) for c in range(n_zones))
    sum_rows = sum(_choose2(r) for r in row_sums)
    sum_cols = sum(_choose2(c) for c in col_sums)
    expected = sum_rows * sum_cols / _choose2(n)
    maximum = (sum_rows + sum_cols) / 2
    if maximum == expected:
        # Both partitions trivial (one block), perfectly, vacuously agreed.
        return 1.0
    return (sum_cells - expected) / (maximum - expected)


def variation_of_information(
    s: tuple[int, ...], key: tuple[int, ...], n_zones: int
) -> float | None:
    """
    Variation of information between the student and key groupings of the
    co-placed cards (a true metric on partitions, 0 iff identical); None when
    there are none. Cohort-scale-only (see the module note).
    """
    n_matrix = scoring.confusion_matrix(s, key, n_zones)
    row_sums = [sum(row) for row in n_matrix]
    col_sums = [sum(n_matrix[r][c] for r in range(n_zones)) for c in range(n_zones)]
    n = sum(row_sums)
    if n == 0:
        return None

    def entropy(sizes: list[int]) -> float:
        return -sum((x / n) * math.log(x / n) for x in sizes if x)

    mutual = 0.0
    for r in range(n_zones):
        for c in range(n_zones):
            joint = n_matrix[r][c]
            if joint:
                mutual += (joint / n) * math.log(n * joint / (row_sums[r] * col_sums[c]))
    return entropy(row_sums) + entropy(col_sums) - 2 * mutual


# ---------------------------------------------------------------------------
# The reportable error profile, the five-count mixed set of the journal's
# error-profile displays (subsec-error-profile-bars), which
# subsec-grouping-versus-baseline-profile distinguishes from the
# four-component baseline ε of subsec-error-profile.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ErrorProfile:
    """A short, additive per-check profile: how the answer is wrong."""

    omissions: int  # cards treated as decoys that are not
    placed_decoys: int  # decoy cards dropped into a slot
    merge_pairs: int  # grouping: together for student, apart for key
    split_pairs: int  # grouping: apart for student, together for key
    mislabels: int  # right grouping, wrong slot name (label-aware)


def error_profile(
    s: tuple[int, ...], key: tuple[int, ...], n_zones: int
) -> ErrorProfile:
    """The two boundary counts and two grouping counts, plus the mislabel gap."""
    counts = pair_counts(s, key, n_zones)
    return ErrorProfile(
        omissions=omissions(s, key),
        placed_decoys=placed_decoys(s, key),
        merge_pairs=counts.merge_pairs,
        split_pairs=counts.split_pairs,
        mislabels=mislabels(s, key, n_zones),
    )
