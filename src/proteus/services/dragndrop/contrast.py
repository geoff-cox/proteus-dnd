"""
RQ3 endpoint-equivalent process-contrast runner (expansion memo E-15;
``cc-task-frozen-run-tooling``, owner decision 2026-10-04: build).

The frozen P5 analysis (``paper1-decisions.md`` 2026-09-27, superseded
2026-09-28 by the three-block structure) as a command. Among attempts that
reach a correct check, the groups are structural and named as such
everywhere — in code, in every output file and in the docs:

* ``first_check_correct`` — the first scored check matched the key;
* ``first_check_incorrect_eventually_correct`` — it did not, a later one did.

Never "direct", "revised" or "repair" as a group name; the *repair window*
is a feature name (``_repair`` columns, F-NEW) and stays.

Two modes, the default deliberately blind to any process feature:

* **counts-only** (the default) — the two group sizes, students and items
  per group, the per-item arm sizes, the count of items with at least
  ``FLOOR_PER_ARM`` attempts in each arm, and the mechanical reporting-level
  verdict (per item and pooled when at least ``MIN_ITEMS_FOR_PER_ITEM``
  items clear the floor, else pooled only). This is what Stage 4.1 of the
  frozen-run procedure reports *first*, before any estimate is seen.
* ``--estimate`` — block 1 (before the first check: the between-group
  contrast) as medians, interquartile ranges and the difference in medians
  with a student-clustered bootstrap percentile interval (students resampled
  with replacement, the difference of group medians recomputed); blocks 2
  and 3 (the first checked state; the repair window) as descriptors of the
  first-check-incorrect group only; whole-attempt structural descriptors per
  group without intervals; per-item block-1 rows for every item clearing the
  floor when the verdict is per item; and the representative attempts. The
  primary quantity for the P6 materiality rule is the difference in group
  medians of ``n_effective_moves_pre``.

Inputs are the persisted service tables only — ``dnd_attempts``,
``dnd_attempt_metrics``, the first scored check's ``dnd_check_scores`` row
(the first checked state) and, to find that check in replay order,
``dnd_configurations`` (attempt id, check flag and sequence number; nothing
else is read from the reconstruction layer) — never ``events`` or
``dnd_events``. Items are canonical identifiers: an attempt logged under a
retired identifier (dnd/0014) counts toward its successor's arms, per-item
rows and representatives. The population is the attempts whose readiness is admitted
(``ready`` in the primary configuration; ``ready`` and ``limited`` under D3.2
knob 7), with at least one scored check and ``eventual_correct = 1``. Under
knob 7 a limited attempt contributes a feature only when every warning flag
it carries permits that feature's family — the flag-by-feature matrix of the
QC rulebook (``app-qc-rules``, PR #216 state), encoded in :data:`PERMITTED`.

Output is one versioned result file in the row order of
``tab-process-results`` plus the counts, the per-item rows, the
representatives (attempt ids only — never a student id) and a provenance
record carrying the seed, B, the freeze dates, the stage run configurations
and the package version. Every file is deterministic for a given seed.
"""

from __future__ import annotations

import csv
import json
import logging
import math
import random
import sqlite3
import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

__all__ = [
    "P5_FROZEN",
    "P5_SUPERSEDED",
    "REPRESENTATIVE_RULE_FROZEN",
    "FROZEN_REPRESENTATIVE_RULE",
    "CONTRAST_VERSION",
    "FLOOR_PER_ARM",
    "MIN_ITEMS_FOR_PER_ITEM",
    "DEFAULT_B",
    "GROUP_CORRECT",
    "GROUP_INCORRECT",
    "GROUPS",
    "BLOCK1",
    "BLOCK2",
    "BLOCK3",
    "STRUCTURAL",
    "PRIMARY_FEATURE",
    "PERMITTED",
    "FAMILY",
    "AttemptRow",
    "ContrastConfig",
    "Counts",
    "FeatureRow",
    "Representative",
    "Estimates",
    "PrimaryDifference",
    "read_population",
    "count_groups",
    "estimate",
    "primary_difference",
    "representatives",
    "percentile",
    "bootstrap_difference",
    "write_results",
    "run",
]

# The P5 freeze: groups and floor (2026-09-27), the three-block structure and
# the primary quantity (2026-09-28), the representative rule in full
# (2026-10-07, Stage 0.9 of the frozen-run procedure). Written into every
# result file header.
P5_FROZEN = "2026-09-27"
P5_SUPERSEDED = "2026-09-28"
REPRESENTATIVE_RULE_FROZEN = "2026-10-07"
# The representative rule as frozen (paper1-decisions.md 2026-10-07 entry;
# owner rulings R1a, R2a, R3a of the Stage 0.9 questionnaire): standardized
# coordinates; candidates from the item contributing most attempts. A run
# whose configuration differs is marked as deviating in provenance.json.
FROZEN_REPRESENTATIVE_RULE: Mapping[str, bool] = {
    "standardize": True,
    "within_item_with_most_attempts": True,
}
# Version of this runner's result-file semantics; bump when a column, a
# block, the bootstrap or the representative rule changes meaning.
CONTRAST_VERSION = 1

FLOOR_PER_ARM = 5
MIN_ITEMS_FOR_PER_ITEM = 3
DEFAULT_B = 2000
PERCENTILE_INTERVAL = (2.5, 97.5)

GROUP_CORRECT = "first_check_correct"
GROUP_INCORRECT = "first_check_incorrect_eventually_correct"
GROUPS: tuple[str, str] = (GROUP_CORRECT, GROUP_INCORRECT)

# Block 1 (between-group contrast), in the row order of tab-process-results.
BLOCK1: tuple[str, ...] = (
    "n_effective_moves_pre",
    "n_excess_moves_pre",
    "n_takebacks_pre",
    "n_resets_pre",
    "active_time_pre_s",
)
PRIMARY_FEATURE = "n_effective_moves_pre"
# Block 2 (the first checked state; first-check-incorrect group only). The
# decomposition d* = omissions + wrong_slot + decoy-card placements (L_P,
# stored decoy_source_leak) is exact; the decoy-slot count (L_R, stored
# decoy_target_leak) overlaps it and rides as a supplementary row.
BLOCK2: tuple[str, ...] = (
    "first_check_distance",
    "first_check_omissions",
    "first_check_wrong_slot",
    "first_check_decoy_card_placements",
    "first_check_decoy_slot_placements",
)
# Block 3 (the repair window; same group only).
BLOCK3: tuple[str, ...] = (
    "n_effective_moves_repair",
    "n_takebacks_repair",
    "n_resets_repair",
    "n_checks_repair",
    "active_time_repair_s",
    "n_fix_moves_repair",
)
# Whole-attempt structural descriptors (per group, never contrasts).
STRUCTURAL: tuple[str, ...] = ("n_moves", "n_checks", "active_time_s")

# Feature families, the unit of the flag-by-feature matrix.
_MOVEMENT = "movement"
_CHECKING = "checking"
_ACTIVE_TIME = "active_time"
_CHECKED_STATE = "checked_state"
_ENDPOINT = "endpoint"
FAMILY: Mapping[str, str] = {
    "n_effective_moves_pre": _MOVEMENT,
    "n_excess_moves_pre": _MOVEMENT,
    "n_takebacks_pre": _MOVEMENT,
    "n_resets_pre": _MOVEMENT,
    "active_time_pre_s": _ACTIVE_TIME,
    "first_check_distance": _ENDPOINT,
    "first_check_omissions": _CHECKED_STATE,
    "first_check_wrong_slot": _CHECKED_STATE,
    "first_check_decoy_card_placements": _CHECKED_STATE,
    "first_check_decoy_slot_placements": _CHECKED_STATE,
    "n_effective_moves_repair": _MOVEMENT,
    "n_takebacks_repair": _MOVEMENT,
    "n_resets_repair": _MOVEMENT,
    "n_checks_repair": _CHECKING,
    "active_time_repair_s": _ACTIVE_TIME,
    "n_fix_moves_repair": _MOVEMENT,
    "n_moves": _MOVEMENT,
    "n_checks": _CHECKING,
    "active_time_s": _ACTIVE_TIME,
}
_ALL_FAMILIES = frozenset(
    {_MOVEMENT, _CHECKING, _ACTIVE_TIME, _CHECKED_STATE, _ENDPOINT}
)
# The flag-by-feature matrix (app-qc-rules, tab-qc-rulebook parts 1–3 as of
# PR #216): the families a limited attempt carrying the flag may still
# contribute. Where the rulebook's permission depends on something the flag
# set does not record (a parse_error on a move versus a check; a tie that
# includes a check), the conservative reading is encoded. The excluding
# flags permit nothing (such attempts are never admitted).
PERMITTED: Mapping[str, frozenset[str]] = {
    "replay_ok": _ALL_FAMILIES,
    "noop_move": _ALL_FAMILIES,
    "check_mismatch": frozenset({_CHECKED_STATE, _ENDPOINT}),
    "parse_error": frozenset({_CHECKED_STATE, _ENDPOINT}),
    "unknown_event_type": frozenset(),
    "row_order_ambiguous": frozenset({_CHECKED_STATE}),
    "duplicate_row": frozenset({_CHECKED_STATE, _ENDPOINT}),
    "session_break": frozenset({_MOVEMENT, _CHECKING, _CHECKED_STATE, _ENDPOINT}),
    "long_idle_gap": frozenset(
        {_MOVEMENT, _CHECKING, _CHECKED_STATE, _ENDPOINT, _ACTIVE_TIME}
    ),
    "missing_source_map": frozenset(),
    "invalid_state": frozenset(),
}


@dataclass(frozen=True)
class ContrastConfig:
    """How the population is admitted and the estimates are computed."""

    readiness: tuple[str, ...] = ("ready",)
    exclude_flags: tuple[str, ...] = ()
    # Apply the flag-by-feature matrix to admitted non-ready attempts.
    apply_feature_matrix: bool = True
    seed: int | None = None
    B: int = DEFAULT_B
    # The representative-trajectory rule (P5), frozen in full on 2026-10-07
    # (paper1-decisions.md 2026-10-07 entry; Stage 0.9 of the frozen-run
    # procedure, owner rulings R1a R2a R3a): nearest-to-group-median on the
    # *standardized* block-1 feature vector, within each group, *within the
    # item contributing most attempts* to the admitted population. The
    # defaults are the frozen values (FROZEN_REPRESENTATIVE_RULE); the rule
    # stays configuration, not a D3.2 knob, and no CLI flag changes it.
    representative_standardize: bool = True
    representative_within_item_with_most_attempts: bool = True


@dataclass(frozen=True)
class AttemptRow:
    """One admitted attempt with its group and the features the blocks read."""

    attempt_id: int
    student_id: str
    question_id: str
    readiness: str
    flags: frozenset[str]
    group: str
    features: Mapping[str, float | int | None]

    def value(self, feature: str, apply_matrix: bool = True) -> float | None:
        """The feature's value, or None when undefined or not permitted for
        this attempt's flags (the matrix applies to non-ready attempts)."""
        v = self.features.get(feature)
        if v is None:
            return None
        if apply_matrix and self.readiness != "ready" and not self.permits(feature):
            return None
        return float(v)

    def permits(self, feature: str) -> bool:
        family = FAMILY[feature]
        return all(family in PERMITTED.get(flag, frozenset()) for flag in self.flags)


@dataclass(frozen=True)
class GroupCounts:
    group: str
    attempts: int
    students: int
    items: int


@dataclass(frozen=True)
class ItemArms:
    question_id: str
    n_first_check_correct: int
    n_first_check_incorrect_eventually_correct: int

    @property
    def clears_floor(self) -> bool:
        return (
            self.n_first_check_correct >= FLOOR_PER_ARM
            and self.n_first_check_incorrect_eventually_correct >= FLOOR_PER_ARM
        )


@dataclass(frozen=True)
class Counts:
    """The counts-only output: Stage 4.1 of the frozen-run procedure."""

    groups: tuple[GroupCounts, ...]
    arms: tuple[ItemArms, ...]
    n_items_clearing_floor: int
    reporting_level: str  # "per_item_and_pooled" | "pooled_only"
    readiness: tuple[str, ...]
    exclude_flags: tuple[str, ...]
    floor_per_arm: int = FLOOR_PER_ARM
    min_items_for_per_item: int = MIN_ITEMS_FOR_PER_ITEM


@dataclass(frozen=True)
class FeatureRow:
    """One row of the result file: a feature's summary per group."""

    block: str
    feature: str
    question_id: str | None  # None for the pooled rows
    n_first_check_correct: int
    median_first_check_correct: float | None
    iqr_first_check_correct: tuple[float, float] | None
    n_first_check_incorrect_eventually_correct: int
    median_first_check_incorrect_eventually_correct: float | None
    iqr_first_check_incorrect_eventually_correct: tuple[float, float] | None
    difference_in_medians: float | None  # correct minus incorrect
    bootstrap_lo: float | None
    bootstrap_hi: float | None
    n_valid_replicates: int | None


@dataclass(frozen=True)
class Representative:
    group: str
    question_id: str
    attempt_id: int
    distance: float


@dataclass(frozen=True)
class Estimates:
    rows: tuple[FeatureRow, ...]  # pooled, in tab-process-results order
    per_item: tuple[FeatureRow, ...]  # block 1 per item clearing the floor
    representatives: tuple[Representative, ...]
    representative_item: str | None
    seed: int
    B: int


@dataclass(frozen=True)
class PrimaryDifference:
    """The primary RQ3 quantity, for the sensitivity harness's Δ column."""

    difference: float | None
    n_first_check_correct: int
    n_first_check_incorrect_eventually_correct: int


# ---------------------------------------------------------------------------
# Population
# ---------------------------------------------------------------------------
# The first scored check of an attempt is located through dnd_configurations
# (attempt id, is_check, seq): dnd_check_scores is keyed by event id and
# carries no replay order. Nothing else is read from the reconstruction layer.
_POPULATION_SQL = (
    "WITH first_scored AS ("
    "  SELECT c.attempt_id, c.event_id, "
    "         ROW_NUMBER() OVER (PARTITION BY c.attempt_id ORDER BY c.seq) AS rn "
    "  FROM dnd_configurations c "
    "  JOIN dnd_check_scores s ON s.event_id = c.event_id "
    "  WHERE c.is_check = 1) "
    # dnd/0014: items are canonical identifiers — an attempt logged under a
    # retired identifier counts toward its successor's arms and rows.
    "SELECT a.attempt_id, a.student_id, "
    "COALESCE(a.canonical_question_id, a.question_id) AS question_id, a.readiness, "
    "a.qc_flags_json, m.first_check_correct, "
    "m.n_effective_moves_pre, m.n_excess_moves_pre, m.n_takebacks_pre, "
    "m.n_resets_pre, m.active_time_pre_s, "
    "m.first_check_distance, "
    "s.omissions AS first_check_omissions, "
    "s.wrong_slot AS first_check_wrong_slot, "
    "s.decoy_source_leak AS first_check_decoy_card_placements, "
    "s.decoy_target_leak AS first_check_decoy_slot_placements, "
    "m.n_effective_moves_repair, m.n_takebacks_repair, m.n_resets_repair, "
    "m.n_checks_repair, m.active_time_repair_s, m.n_fix_moves_repair, "
    "m.n_moves, m.n_checks, m.active_time_s "
    "FROM dnd_attempts a "
    "JOIN dnd_attempt_metrics m ON m.attempt_id = a.attempt_id "
    "LEFT JOIN first_scored fc ON fc.attempt_id = a.attempt_id AND fc.rn = 1 "
    "LEFT JOIN dnd_check_scores s ON s.event_id = fc.event_id "
    "WHERE m.eventual_correct = 1 AND m.first_check_correct IS NOT NULL "
    "ORDER BY a.attempt_id"
)
_FEATURES = BLOCK1 + BLOCK2 + BLOCK3 + STRUCTURAL


def read_population(
    conn: sqlite3.Connection, config: ContrastConfig | None = None
) -> list[AttemptRow]:
    """
    The admitted attempts: readiness in ``config.readiness``, no flag in
    ``config.exclude_flags``, at least one scored check (``first_check_correct``
    not NULL) and ``eventual_correct = 1``; each with its structural group.
    """
    config = config or ContrastConfig()
    admitted = set(config.readiness)
    excluded = set(config.exclude_flags)
    rows: list[AttemptRow] = []
    for r in conn.execute(_POPULATION_SQL):
        if r["readiness"] not in admitted:
            continue
        try:
            flags = frozenset(json.loads(r["qc_flags_json"] or "[]"))
        except (TypeError, ValueError):
            flags = frozenset()
        if flags & excluded:
            continue
        rows.append(
            AttemptRow(
                attempt_id=r["attempt_id"],
                student_id=r["student_id"],
                question_id=r["question_id"],
                readiness=r["readiness"],
                flags=flags,
                group=GROUP_CORRECT if r["first_check_correct"] == 1 else GROUP_INCORRECT,
                features={f: r[f] for f in _FEATURES},
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Counts-only (the default mode)
# ---------------------------------------------------------------------------
def count_groups(population: Sequence[AttemptRow], config: ContrastConfig | None = None) -> Counts:
    config = config or ContrastConfig()
    groups = []
    for g in GROUPS:
        rows = [r for r in population if r.group == g]
        groups.append(
            GroupCounts(
                group=g,
                attempts=len(rows),
                students=len({r.student_id for r in rows}),
                items=len({r.question_id for r in rows}),
            )
        )
    by_item: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in population:
        by_item[r.question_id][0 if r.group == GROUP_CORRECT else 1] += 1
    arms = tuple(
        ItemArms(q, n[0], n[1]) for q, n in sorted(by_item.items())
    )
    n_clear = sum(1 for a in arms if a.clears_floor)
    level = (
        "per_item_and_pooled" if n_clear >= MIN_ITEMS_FOR_PER_ITEM else "pooled_only"
    )
    return Counts(
        groups=tuple(groups),
        arms=arms,
        n_items_clearing_floor=n_clear,
        reporting_level=level,
        readiness=tuple(config.readiness),
        exclude_flags=tuple(config.exclude_flags),
    )


# ---------------------------------------------------------------------------
# Pure statistics
# ---------------------------------------------------------------------------
def percentile(sorted_values: Sequence[float], p: float) -> float | None:
    """The p-th percentile (0–100) by linear interpolation between order
    statistics — numpy's default — on an ascending sequence; None when
    empty."""
    n = len(sorted_values)
    if n == 0:
        return None
    if n == 1:
        return float(sorted_values[0])
    k = (n - 1) * p / 100.0
    f = math.floor(k)
    c = min(f + 1, n - 1)
    return float(sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f))


def _median_iqr(values: Sequence[float]) -> tuple[float | None, tuple[float, float] | None]:
    if not values:
        return None, None
    s = sorted(values)
    return float(statistics.median(s)), (percentile(s, 25.0), percentile(s, 75.0))


def bootstrap_difference(
    samples: Sequence[tuple[str, str, float]],
    B: int,
    rng: random.Random,
) -> tuple[float | None, float | None, int]:
    """
    Student-clustered bootstrap percentile interval for the difference of
    group medians (``GROUP_CORRECT`` minus ``GROUP_INCORRECT``).

    ``samples`` are ``(student_id, group, value)`` triples. Each replicate
    resamples the students with replacement (as many as there are), takes
    every attempt of each sampled student as many times as the student was
    drawn, recomputes the two medians and their difference, and is skipped
    when either group is empty. Returns ``(lo, hi, n_valid)`` at the
    :data:`PERCENTILE_INTERVAL` percentiles of the valid replicates by linear
    interpolation; ``(None, None, 0)`` with no valid replicate.
    """
    by_student: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for student, group, value in samples:
        by_student[student].append((group, value))
    students = sorted(by_student)
    diffs: list[float] = []
    for _ in range(B):
        drawn = [rng.choice(students) for _ in students]
        correct: list[float] = []
        incorrect: list[float] = []
        for student in drawn:
            for group, value in by_student[student]:
                (correct if group == GROUP_CORRECT else incorrect).append(value)
        if not correct or not incorrect:
            continue
        diffs.append(statistics.median(correct) - statistics.median(incorrect))
    if not diffs:
        return None, None, 0
    diffs.sort()
    lo, hi = PERCENTILE_INTERVAL
    return percentile(diffs, lo), percentile(diffs, hi), len(diffs)


# ---------------------------------------------------------------------------
# Estimates (--estimate)
# ---------------------------------------------------------------------------
def _feature_row(
    block: str,
    feature: str,
    rows: Sequence[AttemptRow],
    config: ContrastConfig,
    rng: random.Random | None,
    *,
    question_id: str | None = None,
    contrast: bool,
    groups: Iterable[str] = GROUPS,
) -> FeatureRow:
    apply = config.apply_feature_matrix
    per_group: dict[str, list[tuple[str, float]]] = {g: [] for g in GROUPS}
    for r in rows:
        if r.group not in groups:
            continue
        v = r.value(feature, apply)
        if v is not None:
            per_group[r.group].append((r.student_id, v))
    m_c, iqr_c = _median_iqr([v for _, v in per_group[GROUP_CORRECT]])
    m_i, iqr_i = _median_iqr([v for _, v in per_group[GROUP_INCORRECT]])
    diff = lo = hi = None
    n_valid: int | None = None
    if contrast and m_c is not None and m_i is not None:
        diff = m_c - m_i
        if rng is not None:
            samples = [
                (s, g, v) for g in GROUPS for s, v in per_group[g]
            ]
            lo, hi, n_valid = bootstrap_difference(samples, config.B, rng)
    return FeatureRow(
        block=block,
        feature=feature,
        question_id=question_id,
        n_first_check_correct=len(per_group[GROUP_CORRECT]),
        median_first_check_correct=m_c,
        iqr_first_check_correct=iqr_c,
        n_first_check_incorrect_eventually_correct=len(per_group[GROUP_INCORRECT]),
        median_first_check_incorrect_eventually_correct=m_i,
        iqr_first_check_incorrect_eventually_correct=iqr_i,
        difference_in_medians=diff,
        bootstrap_lo=lo,
        bootstrap_hi=hi,
        n_valid_replicates=n_valid,
    )


def estimate(
    population: Sequence[AttemptRow], config: ContrastConfig
) -> Estimates:
    """
    The three blocks and the structural descriptors (pooled), the per-item
    block-1 rows when the counts verdict is per item, and the representative
    attempts. ``config.seed`` is required: the bootstrap is seeded so the
    result file is reproducible.
    """
    if config.seed is None:
        raise ValueError("--estimate needs a seed (the bootstrap is seeded)")
    if config.B < 1:
        raise ValueError("B must be at least 1")
    rng = random.Random(config.seed)
    rows: list[FeatureRow] = []
    for f in BLOCK1:
        rows.append(_feature_row("pre_first_check", f, population, config, rng, contrast=True))
    for f in BLOCK2:
        rows.append(
            _feature_row(
                "first_checked_state", f, population, config, None,
                contrast=False, groups=(GROUP_INCORRECT,),
            )
        )
    for f in BLOCK3:
        rows.append(
            _feature_row(
                "repair", f, population, config, None,
                contrast=False, groups=(GROUP_INCORRECT,),
            )
        )
    for f in STRUCTURAL:
        rows.append(_feature_row("whole_attempt", f, population, config, None, contrast=False))

    counts = count_groups(population, config)
    per_item: list[FeatureRow] = []
    if counts.reporting_level == "per_item_and_pooled":
        for arm in counts.arms:
            if not arm.clears_floor:
                continue
            item_rows = [r for r in population if r.question_id == arm.question_id]
            for f in BLOCK1:
                per_item.append(
                    _feature_row(
                        "pre_first_check", f, item_rows, config, rng,
                        question_id=arm.question_id, contrast=True,
                    )
                )

    reps, rep_item = representatives(population, config)
    return Estimates(
        rows=tuple(rows),
        per_item=tuple(per_item),
        representatives=tuple(reps),
        representative_item=rep_item,
        seed=config.seed,
        B=config.B,
    )


def primary_difference(
    conn: sqlite3.Connection, config: ContrastConfig | None = None
) -> PrimaryDifference:
    """The primary RQ3 quantity (difference in group medians of
    ``n_effective_moves_pre``, first-check-correct minus first-check-
    incorrect) on the admitted population — no bootstrap, for the
    sensitivity harness's Δ column."""
    config = config or ContrastConfig()
    population = read_population(conn, config)
    row = _feature_row("pre_first_check", PRIMARY_FEATURE, population, config, None, contrast=True)
    return PrimaryDifference(
        difference=row.difference_in_medians,
        n_first_check_correct=row.n_first_check_correct,
        n_first_check_incorrect_eventually_correct=row.n_first_check_incorrect_eventually_correct,
    )


# ---------------------------------------------------------------------------
# Representative attempts
# ---------------------------------------------------------------------------
def representatives(
    population: Sequence[AttemptRow], config: ContrastConfig
) -> tuple[list[Representative], str | None]:
    """
    Nearest-to-group-median on the block-1 feature vector, per group (the
    P5 rule, frozen in full on 2026-10-07 — Stage 0.9 — with both options on):
    within the item contributing most attempts to the admitted population
    (counted over every admitted attempt, before any candidate is dropped;
    ties by the smallest question id) when
    ``config.representative_within_item_with_most_attempts``, else over
    every item; coordinates standardized by the pooled mean and standard
    deviation of the candidate attempts (a zero-variance feature contributes
    0) when ``config.representative_standardize``. Euclidean distance; ties
    by the lowest attempt id. An attempt with an undefined block-1 feature is
    not a candidate (it still counts toward its item's attempts). Returns
    the representatives and the item restriction used — the item even when
    no candidate in it is complete.
    """
    if not population:
        return [], None
    item: str | None = None
    candidates: Sequence[AttemptRow] = population
    if config.representative_within_item_with_most_attempts:
        counts: dict[str, int] = defaultdict(int)
        for r in population:
            counts[r.question_id] += 1
        item = min(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        candidates = [r for r in population if r.question_id == item]
    rows = [
        r for r in candidates
        if all(r.value(f, config.apply_feature_matrix) is not None for f in BLOCK1)
    ]
    if not rows:
        return [], item

    vectors = {
        r.attempt_id: [r.value(f, config.apply_feature_matrix) for f in BLOCK1]
        for r in rows
    }
    if config.representative_standardize:
        means = []
        sds = []
        for j in range(len(BLOCK1)):
            column = [v[j] for v in vectors.values()]
            mean = sum(column) / len(column)
            sd = statistics.pstdev(column) if len(column) > 1 else 0.0
            means.append(mean)
            sds.append(sd)
        vectors = {
            a: [
                ((v[j] - means[j]) / sds[j]) if sds[j] > 0 else 0.0
                for j in range(len(BLOCK1))
            ]
            for a, v in vectors.items()
        }

    out: list[Representative] = []
    for g in GROUPS:
        members = [r for r in rows if r.group == g]
        if not members:
            continue
        center = [
            statistics.median([vectors[r.attempt_id][j] for r in members])
            for j in range(len(BLOCK1))
        ]
        best = min(
            members,
            key=lambda r: (
                math.dist(vectors[r.attempt_id], center),
                r.attempt_id,
            ),
        )
        out.append(
            Representative(
                group=g,
                question_id=best.question_id,
                attempt_id=best.attempt_id,
                distance=math.dist(vectors[best.attempt_id], center),
            )
        )
    return out, item


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------
# Written in estimate mode only; a counts-only run removes stale copies.
_ESTIMATE_FILES = ("tab-process-results.csv", "block1_per_item.csv", "representatives.csv")
_RESULT_COLUMNS = (
    "block", "feature", "question_id",
    "n_first_check_correct",
    "median_first_check_correct",
    "iqr_lo_first_check_correct", "iqr_hi_first_check_correct",
    "n_first_check_incorrect_eventually_correct",
    "median_first_check_incorrect_eventually_correct",
    "iqr_lo_first_check_incorrect_eventually_correct",
    "iqr_hi_first_check_incorrect_eventually_correct",
    "difference_in_medians", "bootstrap_lo", "bootstrap_hi",
    "n_valid_replicates",
)


def _fmt(v: object) -> object:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.6g}"
    return v


def _row_record(r: FeatureRow) -> dict[str, object]:
    iqr_c = r.iqr_first_check_correct or (None, None)
    iqr_i = r.iqr_first_check_incorrect_eventually_correct or (None, None)
    return {
        "block": r.block,
        "feature": r.feature,
        "question_id": r.question_id or "",
        "n_first_check_correct": r.n_first_check_correct,
        "median_first_check_correct": _fmt(r.median_first_check_correct),
        "iqr_lo_first_check_correct": _fmt(iqr_c[0]),
        "iqr_hi_first_check_correct": _fmt(iqr_c[1]),
        "n_first_check_incorrect_eventually_correct": r.n_first_check_incorrect_eventually_correct,
        "median_first_check_incorrect_eventually_correct": _fmt(
            r.median_first_check_incorrect_eventually_correct
        ),
        "iqr_lo_first_check_incorrect_eventually_correct": _fmt(iqr_i[0]),
        "iqr_hi_first_check_incorrect_eventually_correct": _fmt(iqr_i[1]),
        "difference_in_medians": _fmt(r.difference_in_medians),
        "bootstrap_lo": _fmt(r.bootstrap_lo),
        "bootstrap_hi": _fmt(r.bootstrap_hi),
        "n_valid_replicates": _fmt(r.n_valid_replicates),
    }


def _header_lines(provenance: Mapping[str, object]) -> list[str]:
    """The result file's header: comment lines a reader skips (the planning
    scripts' ``load_map`` idiom) carrying what makes the file citable."""
    return [
        (
            f"# dnd contrast result v{CONTRAST_VERSION}; "
            f"P5 frozen {P5_FROZEN}, superseded {P5_SUPERSEDED} (three blocks), "
            f"representative rule {REPRESENTATIVE_RULE_FROZEN}"
        ),
        (
            f"# groups: {GROUP_CORRECT} vs {GROUP_INCORRECT}; "
            "difference = first_check_correct minus first_check_incorrect_eventually_correct"
        ),
        (
            f"# seed={provenance.get('seed')} B={provenance.get('B')} "
            f"interval=percentile {PERCENTILE_INTERVAL[0]}-{PERCENTILE_INTERVAL[1]} "
            "(student-clustered)"
        ),
        (
            f"# readiness={list(provenance.get('readiness', ()))} "
            f"exclude_flags={list(provenance.get('exclude_flags', ()))} "
            f"feature_matrix={provenance.get('apply_feature_matrix')}"
        ),
        (
            f"# package={provenance.get('package_version')} "
            f"generated_at={provenance.get('generated_at')}"
        ),
    ]


def _write_csv(path: Path, columns: Sequence[str], records: Iterable[Mapping[str, object]],
               header: Sequence[str] = ()) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        for line in header:
            f.write(line + "\n")
        w = csv.DictWriter(f, fieldnames=list(columns), lineterminator="\n")
        w.writeheader()
        for rec in records:
            w.writerow(rec)


def _package_version() -> str:
    try:
        from importlib.metadata import version

        return version("proteus-rs-pipeline")
    except Exception:  # pragma: no cover - fallback outside an installed env
        import proteus

        return getattr(proteus, "__version__", "unknown")


def _stage_runs(conn: sqlite3.Connection) -> list[dict[str, object]]:
    """The latest DnD stage run per stage: the F22 provenance the results
    were computed from."""
    out = []
    try:
        rows = conn.execute(
            "SELECT run_id, stage, status, config_json FROM pipeline_runs "
            "WHERE service = 'dnd' AND stage IN ('derive', 'reconstruct', "
            "'score', 'analyze') AND run_id IN ("
            "  SELECT MAX(run_id) FROM pipeline_runs WHERE service = 'dnd' "
            "  GROUP BY stage) ORDER BY run_id"
        ).fetchall()
    except sqlite3.OperationalError:
        return out
    for r in rows:
        try:
            config = json.loads(r["config_json"]) if r["config_json"] else None
        except ValueError:
            config = None
        out.append(
            {"run_id": r["run_id"], "stage": r["stage"], "status": r["status"],
             "config": config}
        )
    return out


def write_results(
    out_dir: Path,
    counts: Counts,
    estimates: Estimates | None,
    config: ContrastConfig,
    conn: sqlite3.Connection,
) -> dict[str, object]:
    """Write ``counts.json``, ``arms.csv``, and — with estimates —
    ``tab-process-results.csv``, ``block1_per_item.csv``,
    ``representatives.csv``; always ``provenance.json``. A counts-only run
    removes those three estimate files when an earlier run left them in
    ``out_dir``, so the directory never contradicts its provenance record.
    Returns the provenance record."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rule = {
        "standardize": config.representative_standardize,
        "within_item_with_most_attempts": config.representative_within_item_with_most_attempts,
    }
    if rule == FROZEN_REPRESENTATIVE_RULE:
        rule_status = (
            f"frozen {REPRESENTATIVE_RULE_FROZEN} "
            f"(paper1-decisions.md {REPRESENTATIVE_RULE_FROZEN} entry; Stage 0.9)"
        )
    else:
        deviations = ", ".join(
            f"{k}={v}" for k, v in rule.items() if v != FROZEN_REPRESENTATIVE_RULE[k]
        )
        rule_status = f"deviates from the rule frozen {REPRESENTATIVE_RULE_FROZEN}: {deviations}"
    provenance: dict[str, object] = {
        "contrast_version": CONTRAST_VERSION,
        "p5_frozen": P5_FROZEN,
        "p5_superseded": P5_SUPERSEDED,
        "groups": list(GROUPS),
        "primary_feature": PRIMARY_FEATURE,
        "readiness": list(config.readiness),
        "exclude_flags": list(config.exclude_flags),
        "apply_feature_matrix": config.apply_feature_matrix,
        "mode": "estimate" if estimates is not None else "counts_only",
        "seed": estimates.seed if estimates else None,
        "B": estimates.B if estimates else None,
        "representative_rule": {
            **rule,
            "item": estimates.representative_item if estimates else None,
            "frozen": REPRESENTATIVE_RULE_FROZEN,
            "status": rule_status,
        },
        "package_version": _package_version(),
        "stage_runs": _stage_runs(conn),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    (out_dir / "counts.json").write_text(
        json.dumps(
            {
                "groups": [asdict(g) for g in counts.groups],
                "n_items_clearing_floor": counts.n_items_clearing_floor,
                "reporting_level": counts.reporting_level,
                "floor_per_arm": counts.floor_per_arm,
                "min_items_for_per_item": counts.min_items_for_per_item,
                "readiness": list(counts.readiness),
                "exclude_flags": list(counts.exclude_flags),
                "p5_frozen": P5_FROZEN,
            },
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    _write_csv(
        out_dir / "arms.csv",
        ("question_id", "n_first_check_correct",
         "n_first_check_incorrect_eventually_correct", "clears_floor"),
        (
            {
                "question_id": a.question_id,
                "n_first_check_correct": a.n_first_check_correct,
                "n_first_check_incorrect_eventually_correct": a.n_first_check_incorrect_eventually_correct,
                "clears_floor": int(a.clears_floor),
            }
            for a in counts.arms
        ),
    )
    if estimates is not None:
        header = _header_lines(provenance)
        _write_csv(
            out_dir / "tab-process-results.csv", _RESULT_COLUMNS,
            (_row_record(r) for r in estimates.rows), header,
        )
        _write_csv(
            out_dir / "block1_per_item.csv", _RESULT_COLUMNS,
            (_row_record(r) for r in estimates.per_item), header,
        )
        _write_csv(
            out_dir / "representatives.csv",
            ("group", "question_id", "attempt_id", "distance"),
            (
                {"group": r.group, "question_id": r.question_id,
                 "attempt_id": r.attempt_id, "distance": _fmt(r.distance)}
                for r in estimates.representatives
            ),
        )
    else:
        for name in _ESTIMATE_FILES:
            stale = out_dir / name
            if stale.exists():
                stale.unlink()
                log.warning(
                    "DnD contrast: removed %s left by an earlier --estimate run "
                    "(this run is counts-only)", stale.name,
                )
    (out_dir / "provenance.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return provenance


def run(
    conn: sqlite3.Connection,
    out_dir: Path,
    config: ContrastConfig | None = None,
    *,
    estimate_mode: bool = False,
) -> tuple[Counts, Estimates | None]:
    """Counts (always) and, in estimate mode, the estimates; files written
    under ``out_dir``."""
    config = config or ContrastConfig()
    population = read_population(conn, config)
    counts = count_groups(population, config)
    est = estimate(population, config) if estimate_mode else None
    write_results(out_dir, counts, est, config, conn)
    log.info(
        "DnD contrast: %s; %d / %d attempts; %d item(s) clear the floor; %s",
        "estimate" if estimate_mode else "counts-only",
        counts.groups[0].attempts, counts.groups[1].attempts,
        counts.n_items_clearing_floor, counts.reporting_level,
    )
    return counts, est
