"""
Cohort aggregation for the Explore surface (build item EX-1).

A pure, read-only aggregator that turns one cardsort question's *persisted*
results into the chart-ready bundle the Explore gallery renders. It began as
the backend analog of the dev ``dnd_visualize`` / ``poc_dnd_metrics``
recompute; EX-8 retired that parallel path, and those harnesses now drive
this module (plus :mod:`~proteus.services.dragndrop.trajectory`) instead of
recomputing. No ``altair``, no ``streamlit``, no
``pandas`` — only stdlib + the same-service pure metric modules
(:mod:`~proteus.services.dragndrop.scoring`,
:mod:`~proteus.services.dragndrop.errors`,
:mod:`~proteus.services.dragndrop.board`).

Two data sources, by chart (the journal's deliberate split, EX checklist #3):

* **Score-distribution charts** (``score_hist``, ``mislabel_gap``) read the
  *persisted* ``dnd_check_scores`` columns (``sigma`` / ``sigma_tilde`` /
  ``all_or_nothing``) so they cannot disagree with the Scoring page.
* **Placement / grouping charts** (``heatmap``, ``difficulty``, ``confusion``)
  and the per-card ``error_profile`` have no persisted form, so they aggregate
  the reconstructed state vector ``dnd_configurations.s_json`` against the board
  key at read time.

Both come from a single per-check read that anchors on ``dnd_check_scores`` (the
authoritative scored-checks set) and joins each check's reconstructed
``is_check`` configuration for ``s`` and the core ``events`` row for the filter.

**The filter is a view bound, never a re-score** (rule 7 / CD-3): every read is
narrowed by ``events_conditions(filter_spec)`` exactly the way
``filters.filtered_aggregates`` does — the frozen B3 scoring is not re-run over
the subset. Because the unit of analysis (a check) carries its own ``event_id``
in ``dnd_check_scores``, the events join is the *direct* linkage on
``cs.event_id`` (no per-move trajectory is read, so v1 never touches the
``has_trajectories`` gate).

The single entry point is :func:`cohort_data`; everything it returns lives on the
frozen :class:`CohortData` bundle, one field group per v1 chart.
"""

from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
from collections import Counter, OrderedDict
from dataclasses import dataclass

from proteus.core.filters import FilterSpec, events_conditions
from proteus.services.dragndrop import aliases, errors
from proteus.services.dragndrop.board import BoardSpec, load_board

log = logging.getLogger(__name__)

__all__ = ["CohortData", "card_entropy", "cohort_data", "VALID_UNITS"]

# Aggregation unit of analysis (EX checklist #5, extended by F16/F26): each
# attempt's final checked configuration (the cheatsheet's X̄ default), every
# check, the first check, or the first correct check
# (subsec-heatmap-definition's analysis points; time-normalized stays
# deferred). An attempt with no state at the chosen point contributes
# nothing — a never-correct attempt is absent from `first_correct` exactly
# as a checkless attempt is absent from `first`/`final`.
VALID_UNITS = ("final", "all", "first", "first_correct")


def card_entropy(column: list[float]) -> float:
    """
    The card placement entropy H_j of one X̄ column
    (g-baseline-card-entropy): −Σ_i X̄_ij·ln(X̄_ij), natural log, with the
    0·log 0 = 0 convention. Low = the cohort agreed where the card goes
    (rightly or wrongly); high = placements spread across zones.
    """
    return -sum(p * math.log(p) for p in column if p > 0)

# Error-profile bars, in the fixed display order the dev gallery uses.
_ERROR_ORDER = ("omissions", "placed decoys", "merge pairs", "split pairs", "mislabels")
# F13: the four merge/split summaries, in the journal's presentation order
# (N_merge, E_merge, N_split, E_split — sec-merge-and-split-features).
_MERGE_SPLIT_ORDER = ("merge slots", "merge excess", "split groups", "split excess")
# F14: the pair-based indices, in the journal's presentation order
# (sec-pairwise-agreement-metrics). Cohort-scale-only per
# subsec-pairwise-metric-choice — aggregated here, never read singly.
_PAIR_INDEX_FNS = (
    ("rand", errors.rand_index),
    ("adjusted_rand", errors.adjusted_rand_index),
    ("variation_of_information", errors.variation_of_information),
)
_PAIR_INDEX_ORDER = tuple(name for name, _ in _PAIR_INDEX_FNS)


def pair_index_means(
    states: list[tuple[int, ...]], key: tuple[int, ...], n_zones: int
) -> list[dict]:
    """
    F14: the mean per-check pair index over the states where the pair
    machinery is non-vacuous — at least 2 co-placed cards. The gate is shared
    by all three indices: Rand and ARI are undefined below it anyway, but VI
    alone is defined at one co-placed card (the one-card partitions are
    trivially identical, VI = 0), and counting that check would pull VI's
    mean toward agreement on checks the other two skip. Gating the triple
    together keeps the indices comparable, so n is the same for all three;
    mean is None when no state qualifies.
    """
    sums: Counter = Counter()
    n_defined = 0
    for s in states:
        if errors.pair_counts(s, key, n_zones).n < 2:
            continue
        n_defined += 1
        for index_name, index_fn in _PAIR_INDEX_FNS:
            value = index_fn(s, key, n_zones)
            # All three are defined at >= 2 co-placed cards.
            sums[index_name] += value
    return [
        {
            "index": k,
            "mean": (sums[k] / n_defined) if n_defined else None,
            "n": n_defined,
        }
        for k in _PAIR_INDEX_ORDER
    ]


@dataclass(frozen=True)
class CohortData:
    """
    Chart-ready cohort aggregates for one question under one filter + unit.

    Field groups map one-to-one to the v1 gallery charts. ``sigma_all`` /
    ``error_totals`` / ``gap_rows`` / ``lc_rows`` / ``cock_rows`` aggregate
    **every** check in scope; ``heat_*`` / ``difficulty`` / ``conf_*`` /
    ``sigma_unit`` aggregate the **unit** sample (final / all / first).
    """

    question_id: str
    unit: str
    # Board shape (P cards, R slots; the tray is zone 0).
    n_cards: int
    n_zones: int
    key: tuple[int, ...]
    card_labels: list[str]      # ["c1", …, "cP"]
    card_content: list[str]     # short authored content per card (tooltips)
    zone_labels: list[str]      # ["tray z0", "z1", …, "zR"] ("*" on decoy slots)
    # Scope sizes.
    n_checks: int               # all checks in scope (post-filter)
    n_unit: int                 # size of the unit-of-analysis sample
    # Board-invalid checks the read skipped (F17a): reconstructed states whose
    # card count or zone indices no longer fit the current board — stale rows
    # from a re-synced source. Surfaced so the page can say what was excluded;
    # per-check flag persistence rides F17b.
    n_excluded: int
    # score_hist (persisted σ): unit sample vs all checks.
    sigma_unit: list[int]
    sigma_all: list[int]
    # error_profile (s-derived, over all checks): [{error_type, total}].
    error_totals: list[dict]
    # F13 merge/split summaries (s-derived, over all checks): the four
    # sec-merge-and-split-features totals ΣN_merge / ΣE_merge / ΣN_split /
    # ΣE_split. Slot/group counts — a third unit family beside error_totals'
    # card counts and pair counts, so they ride beside the error-profile
    # bars, never in them (subsec-error-profile-bars' labeling condition;
    # the display question is open-todos item 23).
    merge_split_totals: list[dict]  # [{summary, total}]
    # F14 pair-based indices (s-derived, over all checks): unweighted mean of
    # the per-check Rand / adjusted Rand / VI over the n checks with at least
    # 2 co-placed cards — one shared gate for the triple (see
    # pair_index_means: VI alone is defined at a single co-placed card, and
    # counting it would bias VI toward agreement on checks the others skip),
    # so n is identical across the three rows; mean is None when n = 0. The
    # journal makes these cohort-scale-only and pooled from per-student
    # configurations (subsec-pairwise-metric-choice); the aggregation shape —
    # mean of per-check indices vs a pooled-tally micro-average — is the
    # rule-11 pick, open-todos item 24.
    pair_index_means: list[dict]  # [{index, mean, n}]
    # mislabel_gap (σ̃−σ persisted; grouping flag s-derived), over all checks.
    gap_rows: list[dict]        # [{gap, perfect}]
    # heatmap (unit sample): counts[zone][card] and the fraction X̄.
    heat_count: list[list[int]]
    heat_frac: list[list[float]]
    # difficulty (unit sample): [{card, content, difficulty, entropy}] — the
    # F11 placement entropy H_j rides beside each card's difficulty, matching
    # the journal's diagnostic pairing (subsec-card-entropy-diagnostic: the
    # heatmap column tells a consensus-right card from a consensus-wrong one).
    # Both values are None on an empty unit sample (BUG-04: no data is not
    # difficulty 0.0 / perfect-consensus entropy 0.0).
    difficulty: list[dict]
    # F11 dispersion summaries over the unit sample (the F20 item-summary
    # fields); None when the sample is empty.
    mean_entropy: float | None
    max_entropy: float | None
    # confusion (unit sample): key group → where the cohort placed it.
    conf_rows: list[dict]       # [{key_zone, student_zone, frac, count}]
    conf_key_labels: list[str]
    conf_student_labels: list[str]
    # learning_curve (all checks, per attempt by check ordinal k).
    lc_rows: list[dict]         # [{k, mean_sigma, n}]
    # cock — correct-on-check-k (all checks, per attempt by k).
    cock_rows: list[dict]       # [{k, correct, reached, rate}]
    # F8b zone-level occupancy diagnostics (unit sample): per zone, the mean
    # occupancy ō_i, the key occupancy o★_i, and the mean residual r̄_i =
    # ō_i − o★_i (subsec-occupancy-residual; positive = overfilled). The
    # mean occupancies are the heatmap's row sums, so these cannot disagree
    # with the placement chart. mean_occupancy/mean_residual are None on an
    # empty unit sample (BUG-04: no data is not "every zone empty" and a
    # −o★ residual); key_occupancy is board-derived and always present.
    occupancy_rows: list[dict]  # [{zone, mean_occupancy, key_occupancy, mean_residual}]
    mean_occupied_zones: float | None  # mean q(X) over the unit sample


@dataclass(frozen=True)
class _Check:
    """One scored, reconstructed check in scope (the per-check read row)."""

    attempt_id: int
    sigma: int                  # persisted
    sigma_tilde: int            # persisted
    all_or_nothing: int         # persisted
    s: tuple[int, ...]          # reconstructed state vector (dnd_configurations)


def cohort_data(
    conn: sqlite3.Connection,
    question_id: str,
    *,
    unit: str = "final",
    filter_spec: FilterSpec | None = None,
) -> CohortData:
    """
    Aggregate one question's scored checks into the v1 cohort-chart bundle.

    ``unit`` selects the cohort unit of analysis for the placement charts
    (``final`` / ``all`` / ``first``; default ``final``). ``filter_spec`` is the
    read-time row filter (course / class / week / date) applied as a view bound;
    ``None`` means no filter. The question is scoped on ``dnd_check_scores``
    directly; ``filter_spec.question_id`` is ignored in favour of the explicit
    ``question_id`` argument.

    Raises ``ValueError`` if ``unit`` is unknown or the question has no synced
    board. Returns a valid, all-empty bundle when no check is in scope (a fresh
    upload or a filter that selects nothing) so the page can degrade cleanly.
    Checks whose reconstructed state no longer fits the board (stale rows left
    by a re-synced source) are skipped, never raised, and counted in
    ``n_excluded`` (F17a).
    """
    if unit not in VALID_UNITS:
        raise ValueError(f"unit must be one of {VALID_UNITS}, got {unit!r}")
    board = load_board(conn, question_id)
    if board is None:
        raise ValueError(f"no synced board for question {question_id!r}")
    spec = filter_spec or FilterSpec()

    checks, n_excluded = _read_checks(
        conn, question_id, spec, board.n_cards, board.n_zones
    )
    sample = _unit_sample(checks, unit)
    return _assemble(board, question_id, unit, checks, sample, n_excluded)


# ---------------------------------------------------------------------------
# Read (the view bound)
# ---------------------------------------------------------------------------
def _read_checks(
    conn: sqlite3.Connection,
    question_id: str,
    spec: FilterSpec,
    n_cards: int,
    n_zones: int,
) -> tuple[list[_Check], int]:
    """
    Every scored check for the question, narrowed by ``spec`` and carrying its
    reconstructed state vector, ordered by (attempt, check time) — plus the
    count of board-invalid checks skipped along the way.

    Anchors on ``dnd_check_scores`` (so the set matches the Scoring page) and
    joins each check's ``is_check`` configuration for ``s`` and ``events`` for
    the filter — the direct ``cs.event_id`` linkage. ``events`` is always joined
    (for the per-attempt check ordering), and ``events_conditions`` contributes
    the row-filter predicates only when the spec constrains them.

    A state whose card count or zone indices don't fit the current board — the
    B2.4 gap: a source re-synced to a smaller board leaves ``dnd_configurations``
    rows keyed to the old shape — is **skipped and counted, never raised**
    (F17a): downstream, ``errors.contingency_matrix`` raises on an out-of-range
    zone, which would take down the whole Explore page for the question.
    """
    # dnd/0014: the item's logged identifiers — the board's own and its
    # aliases when `question_id` is canonical; just itself when it is an
    # alias (a view of one logged identifier against the canonical board).
    ids = aliases.item_ids(conn, question_id)
    conditions = [f"cs.question_id IN ({', '.join('?' * len(ids))})"]
    params: list[str] = list(ids)
    ev_conditions, ev_params = events_conditions(spec, "e")
    conditions.extend(ev_conditions)
    params.extend(ev_params)
    sql = (
        "SELECT c.attempt_id AS attempt_id, cs.sigma AS sigma, "
        "cs.sigma_tilde AS sigma_tilde, cs.all_or_nothing AS all_or_nothing, "
        "c.s_json AS s_json "
        "FROM dnd_check_scores cs "
        "JOIN dnd_configurations c "
        "  ON c.event_id = cs.event_id AND c.is_check = 1 "
        "JOIN events e ON e.event_id = cs.event_id "
        "WHERE " + " AND ".join(conditions) + " "
        # The F2 sort key: numeric-cast problem_view breaks timestamp ties
        # before the event-id fallback (the check ordinal k depends on this).
        "ORDER BY c.attempt_id, e.timestamp, "
        "CAST(e.problem_view AS INTEGER), cs.event_id"
    )
    checks: list[_Check] = []
    n_excluded = 0
    for row in conn.execute(sql, params):
        s = tuple(json.loads(row["s_json"]))
        # A board-invalid state would corrupt every count (and an out-of-range
        # zone raises in errors.contingency_matrix): skip and count.
        if len(s) != n_cards or any(not (0 <= z <= n_zones) for z in s):
            n_excluded += 1
            continue
        checks.append(
            _Check(
                attempt_id=row["attempt_id"],
                sigma=row["sigma"],
                sigma_tilde=row["sigma_tilde"],
                all_or_nothing=row["all_or_nothing"],
                s=s,
            )
        )
    if n_excluded:
        # TODO(F17b): persist this as a per-check QC flag instead of only
        # logging/counting (dev/proteus-dnd-alignment-build-checklist.md,
        # Phase 2).
        log.warning(
            "%s: excluded %d stale check(s) whose reconstructed state does not "
            "fit the current board (P=%d, R=%d) — likely a re-synced source",
            question_id, n_excluded, n_cards, n_zones,
        )
    return checks, n_excluded


def _unit_sample(checks: list[_Check], unit: str) -> list[_Check]:
    """The unit-of-analysis subset: every check, each attempt's first/last,
    or each attempt's first exactly-correct check (F16 — an attempt that
    never solves has no state at that analysis point and is absent)."""
    if unit == "all":
        return list(checks)
    groups: "OrderedDict[int, list[_Check]]" = OrderedDict()
    for ck in checks:
        groups.setdefault(ck.attempt_id, []).append(ck)
    if unit == "first_correct":
        sample = []
        for grp in groups.values():
            first_correct = next((ck for ck in grp if ck.all_or_nothing), None)
            if first_correct is not None:
                sample.append(first_correct)
        return sample
    idx = 0 if unit == "first" else -1
    return [grp[idx] for grp in groups.values()]


# ---------------------------------------------------------------------------
# Assemble (the chart-ready bundle)
# ---------------------------------------------------------------------------
def _assemble(
    board: BoardSpec,
    question_id: str,
    unit: str,
    checks: list[_Check],
    sample: list[_Check],
    n_excluded: int,
) -> CohortData:
    P, R, key = board.n_cards, board.n_zones, board.key
    decoy_zones = set(board.decoy_zones)
    card_labels = [f"c{j}" for j in range(1, P + 1)]
    zone_labels = [
        ("tray z0" if i == 0 else f"z{i}") + ("*" if i in decoy_zones else "")
        for i in range(R + 1)
    ]
    card_content = [_short(c, 28) for c in board.cards]

    n_checks = len(checks)
    n_unit = len(sample)

    # score distribution (persisted σ).
    sigma_all = [ck.sigma for ck in checks]
    sigma_unit = [ck.sigma for ck in sample]

    # error profile (s-derived) + mislabel gap (σ̃−σ persisted, flag s-derived),
    # both over all checks. One ErrorProfile per check feeds both.
    totals: Counter = Counter()
    ms_totals: Counter = Counter()
    gap_rows: list[dict] = []
    for ck in checks:
        ep = errors.error_profile(ck.s, key, R)
        totals["omissions"] += ep.omissions
        totals["placed decoys"] += ep.placed_decoys
        totals["merge pairs"] += ep.merge_pairs
        totals["split pairs"] += ep.split_pairs
        totals["mislabels"] += ep.mislabels
        ms_totals["merge slots"] += len(errors.merges(ck.s, key, R))
        ms_totals["merge excess"] += errors.merge_excess(ck.s, key, R)
        ms_totals["split groups"] += len(errors.splits(ck.s, key, R))
        ms_totals["split excess"] += errors.split_excess(ck.s, key, R)
        gap_rows.append(
            {
                "gap": ck.sigma_tilde - ck.sigma,
                "perfect": ep.merge_pairs == 0 and ep.split_pairs == 0,
            }
        )
    error_totals = [{"error_type": k, "total": totals[k]} for k in _ERROR_ORDER]
    merge_split_totals = [
        {"summary": k, "total": ms_totals[k]} for k in _MERGE_SPLIT_ORDER
    ]

    # placement heatmap X̄ + per-card difficulty (unit sample).
    heat_count = [[0] * P for _ in range(R + 1)]
    for ck in sample:
        for j, z in enumerate(ck.s):
            heat_count[z][j] += 1
    heat_frac = [
        [(heat_count[i][j] / n_unit if n_unit else 0.0) for j in range(P)]
        for i in range(R + 1)
    ]
    # F11: H_j from each heatmap column — one line from heat_frac, so the
    # entropy can never disagree with the placement chart it reads beside.
    entropies = [
        card_entropy([heat_frac[i][j] for i in range(R + 1)]) for j in range(P)
    ]
    # BUG-04: on an empty unit sample the per-card values are None ("no
    # data"), joining the mean_entropy/max_entropy convention below — 0.0
    # would read as "everyone gets it right" / "perfect consensus".
    difficulty = [
        {
            "card": f"c{j}",
            "content": card_content[j - 1],
            "difficulty": (1 - heat_count[key[j - 1]][j - 1] / n_unit) if n_unit else None,
            "entropy": entropies[j - 1] if n_unit else None,
        }
        for j in range(1, P + 1)
    ]
    mean_entropy = sum(entropies) / P if n_unit else None
    max_entropy = max(entropies) if n_unit else None

    conf_rows, conf_key_labels, conf_student_labels = _confusion(sample, key, R)
    lc_rows, cock_rows = _by_check_ordinal(checks)

    # F8b: zone-level occupancy diagnostics over the unit sample. Mean
    # occupancy per zone is the heatmap row sum (ō_i = Σ_j X̄[i][j]); the key
    # occupancy and residual come straight from the journal's definitions.
    key_occ = errors.occupancy(key, R)
    mean_occ = [sum(row) for row in heat_frac]
    # BUG-04: same None convention — an all-zero ō with residual −o★ would
    # read as real underfill data; the key occupancy is board-derived and
    # meaningful regardless of the sample.
    occupancy_rows = [
        {
            "zone": zone_labels[i],
            "mean_occupancy": mean_occ[i] if n_unit else None,
            "key_occupancy": key_occ[i],
            "mean_residual": (mean_occ[i] - key_occ[i]) if n_unit else None,
        }
        for i in range(R + 1)
    ]
    mean_occupied_zones = (
        sum(errors.occupied_zones(ck.s, R) for ck in sample) / n_unit
        if n_unit
        else None
    )

    return CohortData(
        question_id=question_id,
        unit=unit,
        n_cards=P,
        n_zones=R,
        key=key,
        card_labels=card_labels,
        card_content=card_content,
        zone_labels=zone_labels,
        n_checks=n_checks,
        n_unit=n_unit,
        n_excluded=n_excluded,
        sigma_unit=sigma_unit,
        sigma_all=sigma_all,
        error_totals=error_totals,
        merge_split_totals=merge_split_totals,
        pair_index_means=pair_index_means([ck.s for ck in checks], key, R),
        gap_rows=gap_rows,
        heat_count=heat_count,
        heat_frac=heat_frac,
        difficulty=difficulty,
        mean_entropy=mean_entropy,
        max_entropy=max_entropy,
        conf_rows=conf_rows,
        conf_key_labels=conf_key_labels,
        conf_student_labels=conf_student_labels,
        lc_rows=lc_rows,
        cock_rows=cock_rows,
        occupancy_rows=occupancy_rows,
        mean_occupied_zones=mean_occupied_zones,
    )


def _confusion(
    sample: list[_Check], key: tuple[int, ...], R: int
) -> tuple[list[dict], list[str], list[str]]:
    """
    Grouping confusion over the unit sample: for each key group ``b`` (a real
    slot, decoy cards excluded), the row-normalized fraction of the cohort's
    cards that landed in each student zone ``a`` (tray included). Rows sum to 1.
    """
    conf_count: Counter = Counter()
    for ck in sample:
        for j, a in enumerate(ck.s):
            b = key[j]
            if b == 0:
                continue  # a decoy card belongs to no real key group
            conf_count[(b, a)] += 1
    key_set = set(key)
    conf_key_labels = [f"z{b}" for b in range(1, R + 1) if b in key_set]
    conf_student_labels = ["tray z0"] + [f"z{a}" for a in range(1, R + 1)]
    conf_rows: list[dict] = []
    for b in range(1, R + 1):
        if b not in key_set:
            continue  # a decoy slot is not a key group (no member cards)
        tot_b = sum(conf_count.get((b, a), 0) for a in range(R + 1))
        for a in range(R + 1):
            c = conf_count.get((b, a), 0)
            conf_rows.append(
                {
                    "key_zone": f"z{b}",
                    "student_zone": "tray z0" if a == 0 else f"z{a}",
                    "frac": (c / tot_b) if tot_b else 0.0,
                    "count": c,
                }
            )
    return conf_rows, conf_key_labels, conf_student_labels


def _by_check_ordinal(checks: list[_Check]) -> tuple[list[dict], list[dict]]:
    """
    Learning curve and correct-on-check-k: checks grouped per attempt and
    indexed by their 1-based check ordinal ``k``. Mean score uses the persisted
    ``sigma``; "correct" is the persisted ``all_or_nothing`` (the configuration
    equals the key — how a cardsort is graded).
    """
    groups: "OrderedDict[int, list[_Check]]" = OrderedDict()
    for ck in checks:
        groups.setdefault(ck.attempt_id, []).append(ck)
    reached: Counter = Counter()
    correct: Counter = Counter()
    sig_by_k: dict[int, list[int]] = {}
    for grp in groups.values():
        for k, ck in enumerate(grp, start=1):
            reached[k] += 1
            correct[k] += 1 if ck.all_or_nothing else 0
            sig_by_k.setdefault(k, []).append(ck.sigma)
    cock_rows = [
        {"k": k, "correct": correct[k], "reached": reached[k],
         "rate": correct[k] / reached[k]}
        for k in sorted(reached)
    ]
    lc_rows = [
        {"k": k, "mean_sigma": sum(v) / len(v), "n": len(v)}
        for k, v in sorted(sig_by_k.items())
    ]
    return lc_rows, cock_rows


def _short(content: str | None, n: int = 28) -> str:
    """Authored content with tags stripped and whitespace collapsed, truncated."""
    txt = re.sub(r"<[^>]+>", "", content or "").strip()
    txt = re.sub(r"\s+", " ", txt)
    return txt if len(txt) <= n else txt[: n - 1] + "…"
