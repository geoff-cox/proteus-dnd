"""
Process and trajectory analysis (plan item B3.5): how a student worked.

Where scoring (B3.3) weighs a single board, this watches the sequence of
boards in an attempt. Following the journal's movement and segment feature
families (``sec-trajectory-features``, ``sec-check-and-feedback-features``),
each attempt's reconstructed configurations give:

* the **convergence curve** ``k ↦ d(s_k)`` — the distance to the key after
  every state-changing action, starting at ``d(s0) = P − o`` and reaching 0
  when the board matches the key; a reset jumps it back to ``d(s0)``;
* the **move tallies** — ``n_moves`` (drops, takebacks included),
  ``n_takebacks`` (``b = 0``), ``n_resets``, ``n_checks``. Move counts follow
  the journal's processing default (subsec-reconstruction-rules): rows the
  replay flagged ``noop_move`` (``is_noop = 1`` — a drop whose destination
  equals the card's current zone) are excluded, so the tallies, the cadence,
  and the convergence curve count state changes rather than raw drop rows;
  the excluded count is kept beside them as ``n_noop_moves``. Two D3.2
  sensitivity alternatives live on :class:`ProcessConfig`:
  ``count_noop_moves`` (knob 4) counts the flagged rows as moves, and
  ``truncate_at_first_correct`` (knob 3, read-time option (i)) drops the
  rows after the first correct scored check before any feature is
  computed, leaving attempts, flags, readiness and segments untouched;
* the **move mix** (F6) — every ordinary move classified by its distance
  change ``Δd ∈ {−1, 0, +1}`` as **fixing**, **lateral**, or **breaking**
  (subsec-move-classification), counted within reset-free segments (a reset is
  not an ordinary move and is never classified), plus the two inefficiency
  measures of subsec-excess-moves: ``excess_moves`` ``E_move = n − (F − B) =
  2B + L`` and ``path_excess`` ``E_path = Σ n_seg − d_X(start, end)`` per
  segment, where the graph distance ``d_X`` is the number of cards whose zones
  differ. ``E_path`` needs no key, so it is computed even for board-less
  attempts; the classification and ``E_move`` need the key and are None
  without one;
* the **check-structure features** (F7, sec-check-and-feedback-features) —
  the full check
  cadence ``n0, …, n(q−1)`` (subsec-check-cadence: ordinary moves before
  the first check, then between each consecutive pair; every logged check
  counts, a layered-policy *refused* press included), the post-check gains
  ``G_i = d★(X_{k_i}) − d★(X_{k_{i+1}})`` between consecutive checked states
  (subsec-post-check-gain), and the responsiveness
  ``ρ_i = |H_i ∩ M_i| / |H_i|`` (subsec-responsiveness-baseline) — the fraction
  of the cards highlighted at check ``i`` that were moved before check
  ``i+1``. The highlighted set is the wrongly-placed set, reconstructed from
  the check's state and the key, but it counts as *seen* only when the check
  disclosed at the ``highlight`` layer (FP-3): every legacy check, and a
  layered attempt's third graded check on. Elsewhere ρ is undefined (null),
  never zero;
* **time to first check** — the gap from the attempt's first action to its
  first check;
* the **time features** (F9, tab-basic-time-features) — ``total_time`` (first
  to last replayed action), ``time_to_first_move`` (to the first *ordinary*
  move; a flagged noop drop is not one), ``median_move_gap`` (consecutive
  ordinary moves), ``max_action_gap`` (consecutive logged actions of any
  kind — noop drops and checks included, they are activity), and
  ``active_time`` — total time after *excluding* action gaps larger than the
  analytic idle threshold ``δ_max`` (subsec-action-gaps-processing; the
  journal's other option, truncation, is not taken). ``δ_max`` is recorded
  beside the values (``idle_threshold_s``) and is deliberately distinct from
  the attempt-segmentation gap; :mod:`qc` owns the shared default, which
  also drives reconstruct's ``long_idle_gap`` flag and ``active_work``
  segments. The clock is the reconstructed attempt: rows that never
  replayed have no configuration and contribute no timestamps;
* the **outcome features** (F10, tab-check-outcome-features) — the modeling
  chapter's outcome variables, materialized from the persisted outcome layer
  rather than recomputed: ``first_check_distance`` / ``final_check_distance``
  (``d★`` at the first / last retained check), ``first_check_correct``,
  ``eventual_correct`` (1 iff *any* retained check was correct — the
  journal's ``max_i`` indicator, so a later broken board does not undo it),
  and ``correct_check_ordinal`` (``min{i : d★ = 0}``, NULL when never
  correct). One indexed read of the attempt's ``dnd_check_scores`` rows in
  replay order per attempt; the scores are the authoritative record (each
  check's logged Input, rule 6), so a check whose Input never parsed is not
  retained and does not advance the ordinal. Run ``dnd score`` before
  ``dnd analyze`` — with no scored checks (no board, or score not yet run)
  the five columns stay NULL;
* the **phase-window features** (F-NEW, Paper 1 decision 2026-09-28; P5
  blocks 1 and 3) — the movement and time families restricted to two
  windows of the attempt, delimited by the F10 scored checks: the **pre**
  window (rows strictly before the first check; NULL without a scored
  check) and the **repair** window (after the first check up to and
  including the first correct check; defined only when the first check was
  incorrect and the attempt is eventually correct). Per window: effective
  moves, takebacks, resets, and active time under the same ``δ_max``; plus
  the excess before the first check (effective moves minus the
  configuration-graph distance from ``X0`` to the replayed state at the
  first check, floored at 0), and in repair the check count (the first
  correct check included) and the fixing moves. See
  :func:`phase_window_features` for the exact window and gap rules; and
* the **grader-as-oracle probe** ``checked_every_move`` — set when the student
  checked after almost every move (a short cadence), the "grader as oracle"
  pattern ``subsec-check-policy`` names.

Metrics are computed over the *reconstructed* attempt, so the move tallies
count replayed actions; rows that could not be replayed (malformed or
off-board) are surfaced separately by ``reconstruct`` and excluded here. The
results land in ``dnd_attempt_metrics``.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from proteus.core import runs
from proteus.core.db import transaction
from proteus.core.ingest.normalize import parse_timestamp
from proteus.services.dragndrop import aliases, qc, scoring
from proteus.services.dragndrop.board import load_board

log = logging.getLogger(__name__)

# Version of this stage's process-metric semantics, recorded on each pass's
# pipeline_runs provenance row (F22). Bump when a metric or an exclusion
# default changes meaning. v2: the F6 move mix (F/L/B, excess_moves,
# path_excess) joined the vocabulary. v3: the F7 check-structure features
# (cadence sequence, post-check gains, responsiveness) joined it. v4: the F9
# time features (total/active time, move/action gaps) joined it. v5: the F10
# outcome features (first/final check distance, correctness, ordinal). v6: the
# F8 repeated-state counts (V(Γ), R_state over the retained walk). v7:
# time_to_first_check_s moved from the logged-segment clock (started_at) to
# the replayed clock every other F9 feature uses (A6/BUG-06) — values change
# on attempts whose leading rows failed to replay (all readiness-limited).
# v8: the F-NEW phase-window features (the pre / repair restrictions of the
# movement and time families, dnd/0013) joined the vocabulary; every
# pre-existing column is computed exactly as before.
# 9: question-identifier aliases (dnd/0014) — an attempt logged under a
# retired identifier is analyzed against its successor's key (convergence,
# outcomes and the key-based features exist for it); the run configuration
# records the alias-set hash. Every other attempt is computed as before.
STAGE_VERSION = 9

# The cadence at or below which checking counts as "after every small change"
# (the grader-as-oracle probe). 1 move between checks is the strict, sensitive
# default; what a short cadence *means* depends on the feedback policy, which
# the journal parks (plan §7 OQ#6 / subsec-check-cadence: "its interpretation
# depends on what the check returns"), so it is configuration.
# TODO(plan §7 OQ#6): revisit against the feedback-policy work.
_DEFAULT_CADENCE_THRESHOLD = 1


@dataclass(frozen=True)
class ProcessConfig:
    """Knobs for the process metrics (B3.5)."""

    cadence_threshold: int = _DEFAULT_CADENCE_THRESHOLD
    # The analytic idle threshold δ_max for `active_time` (F9) — the shared
    # default lives in qc.py so this stays in step with reconstruct's
    # long_idle_gap flag and active_work segments. Recorded per pass (F22)
    # and per metrics row (`idle_threshold_s`).
    idle_threshold_s: float = qc.DEFAULT_IDLE_THRESHOLD_S
    # D3.2 knob 4 alternative (paper1-decisions.md; `app-sensitivity`):
    # count the replay-flagged no-op drops as moves. Off, the journal's
    # processing default excludes them from every move count; on, a flagged
    # row is an ordinary move that left the board unchanged — a lateral
    # move, one more step of the walk, a repeated state — and
    # `n_noop_moves` still reports how many rows were flagged.
    count_noop_moves: bool = False
    # D3.2 knob 3 alternative, read-time option (i) (owner decision,
    # 2026-10-04): drop an attempt's replayed rows after its first correct
    # scored check before computing every feature. Attempts, QC flags,
    # readiness and the stored segments are untouched, so only the
    # features and the clock change; an attempt never correct is left whole.
    truncate_at_first_correct: bool = False


@dataclass(frozen=True)
class MoveMix:
    """The F6 move classification and excess measures for one attempt.

    ``n_fixing``/``n_lateral``/``n_breaking`` and ``excess_moves`` need the
    board key and are None without one; ``path_excess`` compares each
    reset-free segment's walk to its own endpoints, so it is always computed.
    """

    n_fixing: int | None
    n_lateral: int | None
    n_breaking: int | None
    excess_moves: int | None  # E_move = n − (F − B) = 2B + L
    path_excess: int  # E_path = Σ_seg n_seg − d_X(start, end)


@dataclass(frozen=True)
class TimeFeatures:
    """The F9 basic timing summaries for one attempt, in seconds.

    ``None`` means undefined for the attempt — no ordinary move for
    ``time_to_first_move_s``, fewer than two moves/actions for the gap
    summaries, no timestamped action at all for the totals — never zero.
    The one degenerate-but-defined case: a single-action attempt has
    genuinely zero elapsed and active time, so its totals are ``0.0``.
    """

    total_time_s: float | None  # first to last replayed action
    time_to_first_move_s: float | None  # first action → first ordinary move
    median_move_gap_s: float | None  # consecutive ordinary moves
    max_action_gap_s: float | None  # consecutive logged actions, any kind
    active_time_s: float | None  # total after excluding gaps > δ_max


@dataclass(frozen=True)
class OutcomeFeatures:
    """The F10 attempt-level outcomes (tab-check-outcome-features).

    All five are None when the attempt has no scored checks — no board, no
    parseable check Input, or ``dnd score`` has not run yet.
    """

    first_check_distance: int | None  # d★ at the first scored check
    final_check_distance: int | None  # d★ at the last retained check
    first_check_correct: int | None  # 1 iff d★ = 0 at the first check
    eventual_correct: int | None  # 1 iff any retained check had d★ = 0
    correct_check_ordinal: int | None  # min{i: d★ = 0}; None if never


@dataclass(frozen=True)
class PhaseWindowFeatures:
    """The F-NEW phase-window features for one attempt (P5 blocks 1 and 3).

    The five ``_pre`` fields are None when the attempt has no scored check;
    the six ``_repair`` fields are None unless the first scored check was
    incorrect and the attempt is eventually correct. The two time fields
    are also None when no replayed row is on the clock. Within a defined
    window a count with nothing to count is 0, never None.
    """

    n_effective_moves_pre: int | None
    n_excess_moves_pre: int | None
    n_takebacks_pre: int | None
    n_resets_pre: int | None
    active_time_pre_s: float | None
    n_effective_moves_repair: int | None
    n_takebacks_repair: int | None
    n_resets_repair: int | None
    n_checks_repair: int | None
    active_time_repair_s: float | None
    n_fix_moves_repair: int | None


@dataclass(frozen=True)
class ReplayedRow:
    """One replayed configuration row, the unit :func:`phase_window_features`
    reads: ``seq`` 0 is ``X0`` (``kind`` None, no timestamp); every later
    row is a replayed event — a ``move`` / ``takeback`` (``is_noop`` when the
    drop re-targeted the card's current zone), a ``reset``, or a ``check``
    — carrying the board state at that point and its parsed timestamp
    (None when absent or unparseable: the row is then off the clock)."""

    seq: int
    kind: str | None
    is_check: bool
    is_noop: bool
    state: tuple[int, ...]
    ts: datetime | None


@dataclass(frozen=True)
class AttemptMetrics:
    """The process metrics for one attempt."""

    n_moves: int  # state-changing moves only (noop_move rows excluded)
    n_takebacks: int
    n_resets: int
    n_checks: int
    n_noop_moves: int  # flagged rows excluded from the tallies above
    time_to_first_check_s: float | None
    convergence: tuple[int, ...] | None  # d(s_k); None without a board key
    checked_every_move: int
    move_mix: MoveMix
    cadence: tuple[int, ...]  # n0..n(q−1); empty for a checkless attempt
    post_check_gains: tuple[int, ...] | None  # G_i; None without a board key
    # ρ_i per consecutive check pair; None entries where ρ is undefined
    # (non-highlight disclosure, empty highlighted set, unclassified layer);
    # None as a whole without a board key.
    responsiveness: tuple[float | None, ...] | None
    times: TimeFeatures
    idle_threshold_s: float  # the δ_max active_time_s was computed under
    outcomes: OutcomeFeatures
    # F8 (subsec-repeat-and-cycle-features), over the retained walk X_0..X_n:
    n_distinct_states: int  # V(Γ) = #{X_k}
    n_repeated_states: int  # R_state(Γ) = (n+1) − V(Γ)
    phases: PhaseWindowFeatures  # F-NEW: the pre / repair window restrictions


@dataclass(frozen=True)
class AnalyzeResult:
    """Summary of one analyze pass."""

    attempts: int
    with_convergence: int  # attempts whose question had a board key
    run_id: int  # the pass's pipeline_runs provenance row (F22)


def convergence_curve(
    states: list[tuple[int, ...]], key: tuple[int, ...]
) -> tuple[int, ...]:
    """The distance-to-key after each state-changing action (pure)."""
    return tuple(scoring.distance(s, key) for s in states)


def graph_distance(a: tuple[int, ...], b: tuple[int, ...]) -> int:
    """
    The configuration-graph distance ``d_X(a, b)``: the number of cards whose
    zones differ. One ordinary move re-zones exactly one card and zones have
    no capacity bound, so the shortest walk between two states moves each
    differing card once (subsec-excess-moves). The states must describe the
    same board (equal card counts) — strict zip fails loudly rather than
    silently undercounting a truncated comparison.
    """
    return sum(1 for x, y in zip(a, b, strict=True) if x != y)


def move_mix(
    trace: list[tuple[str | None, tuple[int, ...]]],
    key: tuple[int, ...] | None,
) -> MoveMix:
    """
    Classify an attempt's ordinary moves and total its excess measures (pure).

    `trace` is the replayed state-changing walk: ``(kind, state-after)`` per
    configuration, starting with ``(None, X0)`` and containing only ordinary
    moves (``move``/``takeback``; noop rows are excluded upstream, the F1
    processing default) and ``reset`` rows. Checks do not change state and do
    not appear.

    Each ordinary move's distance change ``Δd = d★(X_k) − d★(X_{k−1})`` is
    ``−1`` (fixing), ``0`` (lateral), or ``+1`` (breaking)
    (subsec-move-classification). A reset is not an ordinary move: it is never
    classified, and it closes the current reset-free segment — the scope on
    which the excess measures are defined (the same spans F3 materializes as
    ``reset`` segments). ``E_move`` telescopes, so its per-segment sum equals
    ``n − (F − B)`` on attempt totals; ``E_path`` compares each segment's walk
    to its own endpoints and is summed explicitly.

    An empty trace (an attempt with no replayed configurations — unreachable
    from ``analyze_all``, which always sees the X0 row) is the zero-length
    walk: every count and excess is zero.
    """
    if not trace:
        if key is None:
            return MoveMix(None, None, None, None, 0)
        return MoveMix(0, 0, 0, 0, 0)
    n = n_fixing = n_lateral = n_breaking = 0
    path_excess = 0
    seg_start = trace[0][1]  # state at the segment's opening boundary
    seg_n = 0
    prev_state = trace[0][1]
    prev_d = scoring.distance(prev_state, key) if key is not None else None

    for kind, state in trace[1:]:
        if kind == "reset":
            path_excess += seg_n - graph_distance(seg_start, prev_state)
            seg_start = state
            seg_n = 0
            prev_state = state
            if key is not None:
                prev_d = scoring.distance(state, key)
            continue
        n += 1
        seg_n += 1
        if key is not None:
            d = scoring.distance(state, key)
            delta = d - prev_d
            if delta < 0:
                n_fixing += 1
            elif delta == 0:
                n_lateral += 1
            else:
                n_breaking += 1
            prev_d = d
        prev_state = state
    path_excess += seg_n - graph_distance(seg_start, prev_state)

    if key is None:
        return MoveMix(None, None, None, None, path_excess)
    return MoveMix(
        n_fixing=n_fixing,
        n_lateral=n_lateral,
        n_breaking=n_breaking,
        excess_moves=n - (n_fixing - n_breaking),
        path_excess=path_excess,
    )


def cadence_gaps(check_after_moves: list[int]) -> list[int]:
    """
    Moves between consecutive checks, given the running move count at each
    check. The list ``[m1, m2, …]`` of "moves so far" at each check yields the
    inter-check gaps ``[m2 − m1, …]``.
    """
    return [b - a for a, b in zip(check_after_moves, check_after_moves[1:])]


def cadence_sequence(check_after_moves: list[int]) -> list[int]:
    """
    The check cadence ``n0, …, n(q−1)`` (g-baseline-check-cadence, F7): the
    move count before the first check (``n0``, defined whenever the student
    checked at all), then between each consecutive check pair — one entry per
    check. A checkless attempt has an empty cadence.
    """
    if not check_after_moves:
        return []
    return [check_after_moves[0], *cadence_gaps(check_after_moves)]


def post_check_gains(
    check_states: list[tuple[int, ...]], key: tuple[int, ...]
) -> list[int]:
    """
    The post-check gains ``G_i = d★(X_{k_i}) − d★(X_{k_{i+1}})`` between
    consecutive checked states (g-baseline-post-check-gain, F7): positive
    when the next checked state is closer to the key. ``q − 1`` entries for
    ``q`` checks.

    Basis (A10/BUG-10, owner decision 2026-08-25 — documented, not
    changed): d★ here is the distance of the *replayed* state at each
    check, the same walk every other F6–F8 feature reads — not the
    check's own logged Input, which the F10 outcome layer
    (``dnd_check_scores``) scores. On a ``check_mismatch`` attempt (a
    move failed to replay, so the board the grader saw disagrees with
    the replayed one) the two bases diverge and these gains can
    contradict the F10 distances for the same checks. Kept on the
    replayed basis deliberately: scoring from Input would make the
    metric depend on whether ``dnd score`` ran before ``dnd analyze`` —
    same database, different stored values by stage order — a worse
    property than a divergence bounded to flagged, readiness-limited
    attempts. The journal states the same basis (the computation note in
    ``subsec-post-check-gain`` and the checked-state discussion in the
    trajectory-model chapter), so the user-facing contract matches this
    code. The regression test pinning the divergence is
    ``test_dnd_process.py::TestReplayedBasisDivergence``.
    """
    distances = [scoring.distance(s, key) for s in check_states]
    return [a - b for a, b in zip(distances, distances[1:])]


def highlighted_cards(
    s: tuple[int, ...], key: tuple[int, ...]
) -> frozenset[int]:
    """
    The 0-based positions of the wrongly-*placed* cards — the set ``H`` a
    highlight-layer check reddens (subsec-platform-feedback-policies): placed
    somewhere, but not in the keyed zone. Cards still in the tray are "left
    to place", never highlighted.
    """
    return frozenset(
        j for j, (a, b) in enumerate(zip(s, key)) if a != 0 and a != b
    )


def responsiveness(
    checks: list[tuple[tuple[int, ...], str | None]],
    moved_between: list[set[int]],
    key: tuple[int, ...],
) -> list[float | None]:
    """
    The responsiveness ``ρ_i = |H_i ∩ M_i| / |H_i|``
    (g-baseline-responsiveness, F7) per consecutive check pair: how much of
    the set highlighted at check ``i`` was moved before check ``i+1``.

    ``checks`` pairs each check's replayed state with its FP-3 disclosure
    layer; ``moved_between[i]`` is the 0-based card positions moved between
    checks ``i+1`` and ``i+2`` (ordinary moves only — noop rows are excluded
    upstream and a reset is not an ordinary move). ``q − 1`` entries for
    ``q`` checks; an entry is ``None`` — undefined, never zero
    (subsec-responsiveness-baseline) — when the check did not disclose at the
    ``highlight`` layer (a layered attempt's refused/count checks, or an
    unclassified row from an older reconstruct) or nothing was highlighted.
    Legacy checks always disclose at ``highlight`` (the owner-confirmed
    single-layer policy), so ρ is computable for both eras.
    """
    out: list[float | None] = []
    for i, (state, layer) in enumerate(checks[:-1]):
        if layer != "highlight":
            out.append(None)
            continue
        highlighted = highlighted_cards(state, key)
        if not highlighted:
            out.append(None)
            continue
        moved = moved_between[i] if i < len(moved_between) else set()
        out.append(len(highlighted & moved) / len(highlighted))
    return out


def time_features(
    action_times: list[datetime],
    move_times: list[datetime],
    idle_threshold_s: float,
) -> TimeFeatures:
    """
    The F9 basic timing summaries (tab-basic-time-features), pure.

    ``action_times`` is every timestamped replayed action in order (moves,
    takebacks, noop drops, resets, checks — all logged activity);
    ``move_times`` the ordinary moves among them (noop drops excluded, the
    F1 default). ``active_time`` sums the action gaps at or below the
    analytic ``δ_max``, i.e. long gaps are *excluded*, not truncated to the
    threshold (subsec-action-gaps-processing offers both; exclusion is the
    conservative default). Undefined values are None, never zero — except a
    single-action attempt, whose elapsed and active time are genuinely 0.
    """
    action_gaps = [
        (b - a).total_seconds() for a, b in zip(action_times, action_times[1:])
    ]
    move_gaps = [
        (b - a).total_seconds() for a, b in zip(move_times, move_times[1:])
    ]
    if not action_times:
        total = active = None
    else:
        total = (action_times[-1] - action_times[0]).total_seconds()
        active = sum(g for g in action_gaps if g <= idle_threshold_s)
    return TimeFeatures(
        total_time_s=total,
        time_to_first_move_s=(
            (move_times[0] - action_times[0]).total_seconds()
            if move_times else None
        ),
        median_move_gap_s=statistics.median(move_gaps) if move_gaps else None,
        max_action_gap_s=max(action_gaps) if action_gaps else None,
        active_time_s=active,
    )


def outcome_features(check_distances: list[int]) -> OutcomeFeatures:
    """
    The F10 outcomes from an attempt's scored-check distances, in check
    order (pure). Correctness is ``d★ = 0`` — exactly the persisted
    ``all_or_nothing`` bit. ``eventual_correct`` is the journal's ``max_i``
    indicator: any correct check sets it, even if a later retained check is
    wrong again (the final distance still records that).
    """
    if not check_distances:
        return OutcomeFeatures(None, None, None, None, None)
    ordinal = next(
        (i for i, d in enumerate(check_distances, start=1) if d == 0), None
    )
    return OutcomeFeatures(
        first_check_distance=check_distances[0],
        final_check_distance=check_distances[-1],
        first_check_correct=1 if check_distances[0] == 0 else 0,
        eventual_correct=0 if ordinal is None else 1,
        correct_check_ordinal=ordinal,
    )


_NO_PHASES = PhaseWindowFeatures(*([None] * 11))


def phase_window_features(
    rows: Sequence[ReplayedRow],
    scored_check_seqs: Sequence[int],
    outcomes: OutcomeFeatures,
    key: tuple[int, ...] | None,
    idle_threshold_s: float,
) -> PhaseWindowFeatures:
    """
    The F-NEW phase-window features (pure): the movement and time families
    of one attempt restricted to the window *before the first check* (P5
    block 1) and to the *repair* window from the first check to the first
    correct check (block 3).

    ``rows`` is the attempt's replayed configuration sequence in ``seq``
    order, ``X0`` first; ``scored_check_seqs`` the ``seq`` of each scored
    check in the same order as the distances behind ``outcomes`` (the F10
    outcome layer, ``dnd_check_scores``). The windows are delimited by the
    *scored* checks, so they are consistent with the grouping the F10
    outcomes define: the first check is the first scored check and the
    first correct check the scored check at ``correct_check_ordinal``. A
    replayed check row that was never scored (an unparseable payload) sits
    inside whichever window its position falls in, as a row on the clock;
    on a ``ready`` attempt every replayed check is scored, so there the
    first check is also the first replayed check row.

    * **pre** — rows with ``0 < seq < first_seq``; defined whenever the
      attempt has a scored check. Its counts are 0 when nothing precedes
      the first check.
    * **repair** — rows with ``first_seq < seq <= correct_seq``; defined
      only when ``first_check_correct = 0`` and ``eventual_correct = 1``
      (so ``correct_check_ordinal >= 2``).

    Counts follow the whole-attempt rules: effective moves are ordinary
    moves that changed the state (noop drops excluded — the F1 default),
    takebacks are the effective moves whose destination is the tray, resets
    are replayed reset rows, and ``n_checks_repair`` counts the replayed
    check rows in repair — the first check itself excluded, the first
    correct check included. ``n_excess_moves_pre`` is the pre effective
    moves minus the configuration-graph distance :func:`graph_distance`
    from ``X0`` to the replayed state at the first check — the minimum
    number of single-card moves that reach the state actually checked, not
    the key — floored at 0 (the floor cannot bind on the replayed walk; it
    is kept as the brief states it). No excess is defined for repair.
    ``n_fix_moves_repair`` is the repair effective moves whose key distance
    fell by one (the F6 fixing class, within the window); it needs the key.

    Active time per window uses the same gaps as the whole-attempt
    ``active_time_s``: consecutive rows on the replayed clock, gaps above
    ``idle_threshold_s`` excluded. Each gap belongs to the window of its
    *later* row, with the first check closing pre and the first correct
    check closing repair: the gap ending at the first check is pre (the
    window runs *to* the first check, as ``time_to_first_check_s`` does),
    the gap starting at it — the feedback-reading pause — and any gap that
    spans it (an off-clock first check) are repair. A defined window with
    no gap has active time 0.0; both time fields are None when no row is
    on the clock at all, matching the whole-attempt feature.
    """
    if not scored_check_seqs:
        return _NO_PHASES
    first_seq = scored_check_seqs[0]
    repair_defined = (
        outcomes.first_check_correct == 0 and outcomes.eventual_correct == 1
    )
    correct_seq = (
        scored_check_seqs[outcomes.correct_check_ordinal - 1]
        if repair_defined else None
    )

    def window(seq: int) -> str | None:
        if seq < first_seq:
            return "pre"
        if repair_defined and first_seq < seq <= correct_seq:
            return "repair"
        return None

    counts: dict[str, dict[str, int]] = {
        w: {"moves": 0, "takebacks": 0, "resets": 0, "checks": 0, "fix": 0}
        for w in ("pre", "repair")
    }
    first_state: tuple[int, ...] | None = None
    prev_state = rows[0].state  # X0: the board before the first event
    for row in rows[1:]:
        if row.seq == first_seq:
            first_state = row.state
        w = window(row.seq)
        if w is not None:
            c = counts[w]
            effective = row.kind in ("move", "takeback") and not row.is_noop
            if effective:
                c["moves"] += 1
                if row.kind == "takeback":
                    c["takebacks"] += 1
                if key is not None and (
                    scoring.distance(row.state, key)
                    == scoring.distance(prev_state, key) - 1
                ):
                    c["fix"] += 1
            elif row.kind == "reset":
                c["resets"] += 1
            elif row.is_check:
                c["checks"] += 1
        prev_state = row.state
    if first_state is None:  # the scored first check is not among the rows
        return _NO_PHASES

    clock = [r for r in rows if r.ts is not None]
    pre_time: float | None = None
    repair_time: float | None = None
    if clock:
        pre_time = 0.0
        repair_time = 0.0 if repair_defined else None
        for a, b in zip(clock, clock[1:]):
            gap = (b.ts - a.ts).total_seconds()
            if gap > idle_threshold_s:
                continue
            if b.seq <= first_seq:
                pre_time += gap
            elif repair_defined and b.seq <= correct_seq:
                repair_time += gap

    pre = counts["pre"]
    rep = counts["repair"]
    return PhaseWindowFeatures(
        n_effective_moves_pre=pre["moves"],
        n_excess_moves_pre=max(
            0, pre["moves"] - graph_distance(rows[0].state, first_state)
        ),
        n_takebacks_pre=pre["takebacks"],
        n_resets_pre=pre["resets"],
        active_time_pre_s=pre_time,
        n_effective_moves_repair=rep["moves"] if repair_defined else None,
        n_takebacks_repair=rep["takebacks"] if repair_defined else None,
        n_resets_repair=rep["resets"] if repair_defined else None,
        n_checks_repair=rep["checks"] if repair_defined else None,
        active_time_repair_s=repair_time,
        n_fix_moves_repair=(
            rep["fix"] if repair_defined and key is not None else None
        ),
    )


def analyze_all(
    conn: sqlite3.Connection,
    question_id: str | None = None,
    config: ProcessConfig | None = None,
) -> AnalyzeResult:
    """
    Compute and store ``dnd_attempt_metrics`` for every attempt (or just one
    question's). Idempotent: each attempt's metrics row is replaced.

    Every pass records a ``pipeline_runs`` row carrying its configuration —
    the cadence threshold and the noop-move exclusion default (F22); the pass
    is a single transaction, so the run row commits with the metrics it
    describes.
    """
    config = config or ProcessConfig()
    sql = "SELECT attempt_id, question_id FROM dnd_attempts"
    params: list[object] = []
    if question_id is not None:
        sql += " WHERE question_id = ?"
        params.append(question_id)
    sql += " ORDER BY attempt_id"
    attempts = conn.execute(sql, params).fetchall()

    keys: dict[str, tuple[tuple[int, ...], int] | None] = {}
    n_attempts = with_convergence = 0
    with transaction(conn):
        run_id = runs.start_stage_run(
            conn,
            service="dnd",
            stage="analyze",
            config={
                "stage_version": STAGE_VERSION,
                "question_id": question_id,
                "cadence_threshold": config.cadence_threshold,
                # The analytic idle threshold δ_max behind active_time (F9);
                # the journal insists the value in force be reported.
                "idle_threshold_s": config.idle_threshold_s,
                # The journal's processing default (subsec-reconstruction-
                # rules): rows flagged noop_move are excluded from the move
                # tallies, the cadence, and the convergence curve; the D3.2
                # knob-4 alternative (`count_noop_moves`) counts them.
                "exclude_noop_moves": not config.count_noop_moves,
                # The D3.2 knob-3 alternative, read-time option (i): rows
                # after the first correct scored check are dropped before
                # the features are computed (dnd_attempts untouched).
                "truncate_at_first_correct": config.truncate_at_first_correct,
                # dnd/0014: the identifier-alias set the boards were bound
                # through (None when no alias is registered).
                "alias_set_sha256": aliases.alias_set_sha256(conn),
            },
        )
        for attempt in attempts:
            q = attempt["question_id"]
            if q not in keys:
                board = load_board(conn, q)
                keys[q] = (board.key, board.n_zones) if board else None
            key = keys[q]
            metrics = _analyze_attempt(
                conn, attempt["attempt_id"], key, config
            )
            _write_metrics(conn, attempt["attempt_id"], metrics)
            n_attempts += 1
            if metrics.convergence is not None:
                with_convergence += 1
        runs.complete_run(
            conn, run_id,
            notes=f"attempts={n_attempts} with_convergence={with_convergence}",
        )

    log.info(
        "DnD analyze: %d attempt(s) (%d with a convergence curve)",
        n_attempts, with_convergence,
    )
    return AnalyzeResult(
        attempts=n_attempts, with_convergence=with_convergence, run_id=run_id
    )


def _analyze_attempt(
    conn: sqlite3.Connection,
    attempt_id: int,
    key: tuple[tuple[int, ...], int] | None,
    config: ProcessConfig,
) -> AttemptMetrics:
    """Compute one attempt's metrics from its reconstructed configurations."""
    rows = conn.execute(
        "SELECT c.seq, c.is_check, c.is_noop, c.s_json, c.disclosure_layer, "
        "d.kind, e.timestamp "
        "FROM dnd_configurations c "
        "LEFT JOIN dnd_events d ON d.event_id = c.event_id "
        "LEFT JOIN events e ON e.event_id = c.event_id "
        "WHERE c.attempt_id = ? ORDER BY c.seq",
        (attempt_id,),
    ).fetchall()

    # F10: the attempt's scored checks in replay order — the persisted
    # outcome layer (each check's logged Input, rule 6), not the replayed
    # states, so a check whose Input never parsed is not retained here.
    # Empty until `dnd score` has run (or without a board): outcomes NULL.
    # Each scored check's seq rides beside its distance: the F-NEW phase
    # windows are delimited by the scored checks, in this same order, and
    # the knob-3 alternative cuts the attempt at the first correct one.
    scored_checks = [
        (row["seq"], row["distance"])
        for row in conn.execute(
            "SELECT c.seq, s.distance FROM dnd_configurations c "
            "JOIN dnd_check_scores s ON s.event_id = c.event_id "
            "WHERE c.attempt_id = ? AND c.is_check = 1 ORDER BY c.seq",
            (attempt_id,),
        )
        if row["distance"] is not None
    ]
    if config.truncate_at_first_correct:
        correct_seq = next((seq for seq, d in scored_checks if d == 0), None)
        if correct_seq is not None:
            rows = [row for row in rows if row["seq"] <= correct_seq]
            scored_checks = [
                (seq, d) for seq, d in scored_checks if seq <= correct_seq
            ]

    n_moves = n_takebacks = n_resets = n_checks = n_noop_moves = 0
    # The state-changing walk, (kind, state-after) per config: X0 (kind None),
    # ordinary moves, and resets — the shape `move_mix` classifies (F6).
    trace: list[tuple[str | None, tuple[int, ...]]] = []
    # A6/BUG-06: the first check's parsed timestamp, on the replayed clock —
    # time_to_first_check_s measures from action_times[0], not the attempt's
    # started_at, so it can never contradict the other F9 features when a
    # leading row failed to replay (started_at includes such rows; the
    # replayed clock does not).
    first_check_dt: datetime | None = None
    first_check_seen = False
    moves_so_far = 0
    check_after_moves: list[int] = []
    # Every check's replayed state and FP-3 disclosure layer, in check order
    # (a layered-policy refused press is still a logged check and stays in
    # the sequence), and the cards moved per inter-check window (F7). A new
    # window opens at every check, so with the journal's 1-based check
    # ordinals: moved_windows[0] holds the moves before the first check,
    # moved_windows[i] the moves between the i-th and (i+1)-th checks — the
    # window after `checks[i - 1]` in 0-based list terms — and the last
    # window is the tail after the final check.
    checks: list[tuple[tuple[int, ...], str | None]] = []
    moved_windows: list[set[int]] = [set()]
    # The F9 clocks: every timestamped replayed action, and the ordinary
    # moves among them. X0 has no event and no timestamp; a row whose
    # timestamp does not parse contributes none (its neighbors' gap then
    # spans it).
    action_times: list[datetime] = []
    move_times: list[datetime] = []
    # Every replayed row with its state and clock position, in seq order —
    # the sequence the F-NEW phase windows are cut from.
    replayed: list[ReplayedRow] = []

    for row in rows:
        ts = _parse_ts(row["timestamp"])
        if ts is not None:
            action_times.append(ts)
        state = tuple(json.loads(row["s_json"]))
        # The flagged count is informational either way; whether a flagged
        # row is *excluded* from the move counts is the knob-4 setting.
        if row["is_noop"]:
            n_noop_moves += 1
        is_noop = bool(row["is_noop"]) and not config.count_noop_moves
        replayed.append(
            ReplayedRow(
                seq=row["seq"],
                kind=row["kind"],
                is_check=bool(row["is_check"]),
                is_noop=is_noop,
                state=state,
                ts=ts,
            )
        )
        if row["is_check"]:
            n_checks += 1
            if not first_check_seen:
                first_check_seen = True
                first_check_dt = ts  # None if its timestamp does not parse
            check_after_moves.append(moves_so_far)
            checks.append((state, row["disclosure_layer"]))
            moved_windows.append(set())
            continue
        if is_noop:
            # The journal's processing default (subsec-reconstruction-rules):
            # rows flagged noop_move are excluded from the move tallies, the
            # cadence, the convergence curve, and the move classification —
            # the board did not change. It stays in `action_times`: the drop
            # happened, so for the clock it is activity. Under the knob-4
            # alternative the row falls through as an ordinary move whose
            # state equals its predecessor's.
            continue
        # A non-check config: X0 (seq 0, no kind) or a state-changing action.
        kind = row["kind"]
        if kind in ("move", "takeback"):
            n_moves += 1
            # The cadence counts *moves* between checks (subsec-check-cadence).
            # A reset is a distinguished jump, not a move
            # (sec-resets-as-distinguished-jumps), tracked separately
            # via n_resets, so it does not advance the move count.
            moves_so_far += 1
            if ts is not None:
                move_times.append(ts)
            if kind == "takeback":
                n_takebacks += 1
            # The moved card is the one whose zone changed (exactly one per
            # ordinary move); its window feeds M_i in the responsiveness. A
            # reset is not an ordinary move and never enters M_i.
            if trace:
                previous = trace[-1][1]
                moved_windows[-1].update(
                    j for j, (a, b) in enumerate(zip(previous, state))
                    if a != b
                )
        elif kind == "reset":
            n_resets += 1
        trace.append((kind, state))

    gaps = cadence_gaps(check_after_moves)
    checked_every_move = (
        1 if gaps and max(gaps) <= config.cadence_threshold else 0
    )

    convergence = None
    gains = None
    rho = None
    if key is not None:
        convergence = convergence_curve([s for _, s in trace], key[0])
        gains = tuple(post_check_gains([s for s, _ in checks], key[0]))
        # Dropping the pre-first-check window aligns the slice 0-based with
        # `checks`: moved_windows[1:][k] holds the moves after `checks[k]` —
        # the M for the pair (checks[k], checks[k+1]). The tail window past
        # the last check pairs with no following check and is never read.
        rho = tuple(responsiveness(checks, moved_windows[1:], key[0]))

    check_distances = [d for _, d in scored_checks]
    outcomes = outcome_features(check_distances)

    # F8: the walk is `trace` (X0 first), so n+1 = len(trace) and V(Γ)
    # counts its distinct states — key-free, defined for every attempt.
    n_distinct_states = len({s for _, s in trace})

    return AttemptMetrics(
        n_moves=n_moves,
        n_takebacks=n_takebacks,
        n_resets=n_resets,
        n_checks=n_checks,
        n_noop_moves=n_noop_moves,
        time_to_first_check_s=(
            (first_check_dt - action_times[0]).total_seconds()
            if first_check_dt is not None and action_times
            else None
        ),
        convergence=convergence,
        checked_every_move=checked_every_move,
        move_mix=move_mix(trace, key[0] if key is not None else None),
        cadence=tuple(cadence_sequence(check_after_moves)),
        post_check_gains=gains,
        responsiveness=rho,
        times=time_features(action_times, move_times, config.idle_threshold_s),
        idle_threshold_s=config.idle_threshold_s,
        outcomes=outcomes,
        n_distinct_states=n_distinct_states,
        n_repeated_states=len(trace) - n_distinct_states,
        phases=phase_window_features(
            replayed,
            [seq for seq, _ in scored_checks],
            outcomes,
            key[0] if key is not None else None,
            config.idle_threshold_s,
        ),
    )


def _parse_ts(value: str | None) -> datetime | None:
    """A parsed timestamp, or None when absent/unparseable (defensive)."""
    if not value:
        return None
    try:
        return parse_timestamp(value)
    except ValueError:
        return None


def _write_metrics(
    conn: sqlite3.Connection, attempt_id: int, metrics: AttemptMetrics
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO dnd_attempt_metrics (attempt_id, n_moves, "
        "n_takebacks, n_resets, n_checks, n_noop_moves, time_to_first_check_s, "
        "convergence_json, checked_every_move, n_fixing, n_lateral, "
        "n_breaking, excess_moves, path_excess, cadence_json, "
        "post_check_gains_json, responsiveness_json, total_time_s, "
        "time_to_first_move_s, median_move_gap_s, max_action_gap_s, "
        "active_time_s, idle_threshold_s, first_check_distance, "
        "final_check_distance, first_check_correct, eventual_correct, "
        "correct_check_ordinal, n_distinct_states, n_repeated_states, "
        "n_effective_moves_pre, n_excess_moves_pre, n_takebacks_pre, "
        "n_resets_pre, active_time_pre_s, n_effective_moves_repair, "
        "n_takebacks_repair, n_resets_repair, n_checks_repair, "
        "active_time_repair_s, n_fix_moves_repair) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
        "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
        "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            attempt_id,
            metrics.n_moves,
            metrics.n_takebacks,
            metrics.n_resets,
            metrics.n_checks,
            metrics.n_noop_moves,
            metrics.time_to_first_check_s,
            None if metrics.convergence is None else json.dumps(list(metrics.convergence)),
            metrics.checked_every_move,
            metrics.move_mix.n_fixing,
            metrics.move_mix.n_lateral,
            metrics.move_mix.n_breaking,
            metrics.move_mix.excess_moves,
            metrics.move_mix.path_excess,
            json.dumps(list(metrics.cadence)),
            None if metrics.post_check_gains is None
            else json.dumps(list(metrics.post_check_gains)),
            # None entries serialize as JSON null: ρ undefined, never zero.
            None if metrics.responsiveness is None
            else json.dumps(list(metrics.responsiveness)),
            metrics.times.total_time_s,
            metrics.times.time_to_first_move_s,
            metrics.times.median_move_gap_s,
            metrics.times.max_action_gap_s,
            metrics.times.active_time_s,
            metrics.idle_threshold_s,
            metrics.outcomes.first_check_distance,
            metrics.outcomes.final_check_distance,
            metrics.outcomes.first_check_correct,
            metrics.outcomes.eventual_correct,
            metrics.outcomes.correct_check_ordinal,
            metrics.n_distinct_states,
            metrics.n_repeated_states,
            metrics.phases.n_effective_moves_pre,
            metrics.phases.n_excess_moves_pre,
            metrics.phases.n_takebacks_pre,
            metrics.phases.n_resets_pre,
            metrics.phases.active_time_pre_s,
            metrics.phases.n_effective_moves_repair,
            metrics.phases.n_takebacks_repair,
            metrics.phases.n_resets_repair,
            metrics.phases.n_checks_repair,
            metrics.phases.active_time_repair_s,
            metrics.phases.n_fix_moves_repair,
        ),
    )
