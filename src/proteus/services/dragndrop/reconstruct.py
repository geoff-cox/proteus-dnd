"""
Reconstruction layer (plan items B3.1/B3.2): replay moves → configurations.

A check row is the only row that records a whole configuration; every move
row records a *step*, not a state (reference page ``sec-raw-data``). So the
state after each action is rebuilt by replaying the move list from the
initial board ``X0`` — all cards in the dragzone, ``s0 = (0, …, 0)`` — exactly
as ``sec-moves-as-state-transitions`` describes:
``X_k = X_{k-1} + (e_{b_k}-e_{a_k}) e_{j_k}^T``,
i.e. moving card ``j`` to zone ``b`` just rewrites ``s(j) = b``. A reset jumps
the board back to ``X0`` in one action.

Two things come out of the replay:

* **dnd_attempts / dnd_configurations** — the segmented attempts (one per
  student × question × sitting) and the configuration after every action
  (seq 0 = ``X0``). Checks get a row too (``is_check = 1``) carrying the
  reconstructed state at the moment of the check. A drop whose destination
  equals the card's current zone (``b = a``) is a **no-op**: the row is still
  written (so the event stays traceable) but flagged ``is_noop = 1`` — the
  journal's ``noop_move`` flag — and the process metrics exclude it from move
  counts by default, so features count state changes, not raw drop rows.
* **the cross-check (B3.2)** — at each *eligible* check the reconstructed
  state must equal the configuration the check row logged in its ``Input``
  column (decoded to ``s``). A mismatch is the strongest available
  data-integrity signal — it means a move row was dropped or merged — and is
  logged with both states and recorded as ``matches_input = 0``. A check is
  an eligible anchor when its payload decodes to a unique, board-valid
  configuration (paper1-decisions.md, 2026-09-28) **and** the replay it
  would be compared against is a measurement (the 2026-10-05 amendment, two
  tiers); a malformed payload or an invalid decode (one card in two zones,
  an unknown card or zone label, a slot outside 1..R) is never compared —
  ``matches_input`` stays NULL and the cause is stored in ``anchor_cause``
  (migrations ``dnd/0012`` and ``dnd/0015``; the vocabulary lives in
  :mod:`~proteus.services.dragndrop.qc`) — so agreement rates have the
  eligible anchors as their denominator and the non-eligible checks are
  reported by cause. Two *structural* causes precede the decode causes:
  ``no_move_logging`` (the check's calendar date precedes its import's
  ``move_logging_from`` in ``dnd_logging_regimes`` — the replay is ``s_0``
  by construction, :mod:`~proteus.services.dragndrop.logging_regimes`) and
  ``resumed_attempt`` (the attempt has a prior attempt by the same student
  on the same logged identifier — the platform restored the last checked
  board without logging a move, so the replay never saw it). Such a check is
  stored not comparable like any other, but its **shadow comparison** — the
  replay-versus-payload test made exactly as before, and the decode outcome
  — still feeds the attempt's flag rollup, so ``replay_ok``,
  ``check_mismatch``, ``parse_error``, ``invalid_state`` and readiness are
  byte-identical to what they were before the two causes existed (owner
  ruling 2026-10-06, the code brief's Checkpoint 1 C).
* **resumption and seeding (P8, D3.2 knob 10)** — every attempt records
  ``prior_attempt_id`` (dnd/0015): the same student's previous attempt on
  the same logged identifier under the segmentation in force, NULL for a
  first attempt. Under ``seed_resumed_attempts`` ("seeded replay", the
  knob-10 configuration) a resumed attempt's replay starts at the prior
  attempt's last checked configuration whose payload decodes (tier (a)
  only, the memo's own simulation; ``s_0`` when the prior attempt has
  none), its checks are compared, and ``seeded`` marks every configuration
  (1 seeded, 0 the fallback or a first attempt; NULL when the option is
  off); the ``resumed_attempt`` cause is then not applied,
  ``no_move_logging`` still is. Resumption, the regime date and the seed
  are computed from student, question, time and the configuration sequence
  — never from correctness, score, feedback or readiness.
* **the QC rollup (F17b)** — each attempt's per-row signals (parse failures,
  cross-check results, no-op drops, replay skips, order ties, long idle
  gaps, …) roll up into the journal's quality-control flags and the
  three-level feature readiness, persisted on ``dnd_attempts.qc_flags_json``
  / ``.readiness`` (:mod:`~proteus.services.dragndrop.qc` is the
  vocabulary's single home). The ``long_idle_gap`` scan (F9) uses the
  *analytic* idle threshold ``δ_max`` — deliberately distinct from the
  segmentation gap: a pause can be too long to count as active work yet too
  short to end the sitting.
* **the segments (F3/F9)** — the journal's first-class analysis units,
  materialized in ``dnd_segments`` as inclusive ``[start_seq, end_seq]``
  spans of the attempt's configurations: **check** segments (boundary after
  each check; the tail past the last check is the post-check-revision
  span), **reset** segments (same rule with resets), **session** segments
  (maximal single-session runs), and — since F9 delivered ``δ_max`` —
  **active_work** segments (maximal runs not interrupted by a long idle
  gap, the clock-based type of subsec-segment-tables).
* **the feedback-policy classification (FP-3)** — each check's disclosure
  layer and its graded ordinal under the attempt's ``feedback_policy`` era,
  persisted on ``dnd_configurations.disclosure_layer`` / ``.graded_ordinal``
  and ``dnd_attempts.feedback_policy``
  (:mod:`~proteus.services.dragndrop.policy` is the vocabulary's home).

Reconstruction needs the card count ``P`` (to size and bound the state
vector) but **not** the key, so it runs whether or not a board has been
synced: with a board, ``P = n_cards``; without one, ``P`` is inferred from
the highest ``drag`` index seen in the question's events (the same fallback
``validate`` uses). Scoring (B3.3) is what needs the key, and runs after.
A question logged under a *retired* identifier binds to its successor's
board through the identifier alias (dnd/0014, resolved inside
``load_board``); its attempts record the canonical identifier in
``dnd_attempts.canonical_question_id`` and are otherwise replayed exactly
as any other — labels parsed under the logged identifier, flags and
readiness unchanged.

Re-running is idempotent: it clears and rebuilds the reconstruction layer for
each question in scope. Because the scores and attempt metrics of B3.3/B3.5
are built on these attempt ids, rebuilding the reconstruction also clears
those downstream rows for the same questions — re-run ``dnd score`` /
``dnd analyze`` after.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass

from proteus.core import runs
from proteus.core.db import transaction
from proteus.core.ingest.normalize import parse_timestamp
from proteus.services.dragndrop import aliases, decode, logging_regimes, policy, qc
from proteus.services.dragndrop.board import load_board

log = logging.getLogger(__name__)

# Version of this stage's replay/segmentation semantics, recorded on each
# pass's pipeline_runs provenance row (F22). Bump when the replay, the
# cross-check, the QC rollup, or the segment rules change meaning. v2: the
# F9 analytic idle threshold joined — the long_idle_gap flag is emitted and
# active_work segments are materialized. v3: the frozen anchor-eligibility
# definition — a non-eligible check is stored not comparable with its cause
# instead of being compared on a best-effort decode, and an off-board slot
# in a check payload now flags invalid_state.
# 4: question-identifier aliases (dnd/0014) — a retired identifier binds to
# its successor's board (P, R, ℓ₀ and the slot bound come from it, so
# `missing_source_map` no longer fires on it) and every attempt records
# `canonical_question_id`; the run configuration records the alias-set hash.
# 5: the two structural anchor causes (dnd/0015) — a check of a resumed
# attempt (`resumed_attempt`) or one logged before its import's regime date
# (`no_move_logging`) is stored not comparable with its cause and leaves the
# agreement denominator; every attempt records `prior_attempt_id`; the
# seeded-replay option (D3.2 knob 10) and the regime-set hash join the run
# configuration. Flags and readiness are unchanged (the shadow comparison).
STAGE_VERSION = 5


# Attempt-segmentation thresholds are a design question the journal parks
# (plan §7 Open Questions #4, "Unit of analysis"): the pilot is a single
# sitting, and the rule for splitting one student's work on one question into
# separate attempts is not settled. Per CLAUDE.md rule 7 they are
# configuration with conservative defaults — split only on strong sitting
# boundaries (a new session, a page (re)view, or a long idle gap), never on a
# reset alone (a reset is a within-attempt restart; the convergence curve
# jumps back to d(s0) and process analysis counts resets per attempt). The
# rule that fired is recorded on each attempt row.
# TODO(plan §7 OQ#4): revisit these once cohort data and the consent/anonymization
# story land; the defaults below are deliberately coarse (few, large attempts).
_DEFAULT_GAP_SECONDS = 1800.0  # 30 minutes idle ⇒ a new sitting


@dataclass(frozen=True)
class SegmentationConfig:
    """How one student × question timeline is split into attempts (B3.1)."""

    gap_seconds: float = _DEFAULT_GAP_SECONDS
    split_on_session_change: bool = True
    split_on_page_view: bool = True
    # A reset restarts the board but, conservatively, not the attempt; flip
    # this on to treat every reset as an attempt boundary too.
    split_on_reset: bool = False

    def describe(self) -> str:
        """A compact, auditable record of the policy (stored per attempt)."""
        bits = [f"gap>{self.gap_seconds:g}s"]
        if self.split_on_session_change:
            bits.append("session")
        if self.split_on_page_view:
            bits.append("page")
        if self.split_on_reset:
            bits.append("reset")
        return "split_on:" + ",".join(bits)


@dataclass(frozen=True)
class ReconstructResult:
    """Summary of one reconstruction pass."""

    questions: int
    attempts: int
    configurations: int
    checks: int
    mismatches: int  # eligible checks whose reconstructed state ≠ logged Input (B3.2)
    non_eligible: int  # checks stored not comparable with an `anchor_cause` (never compared)
    noop_moves: int  # drops flagged `noop_move` (destination = current zone)
    skipped_actions: int  # moves that could not be replayed (malformed/off-board)
    # Traces (student × question timelines) where consecutive rows still tie
    # on (timestamp, problem_view) after the F2 sort, so file order (rowid)
    # decided their replay order — the journal's `row_order_ambiguous` case.
    # TODO(F17b): persist as a per-attempt QC flag; until then log and count
    # (dev/proteus-dnd-alignment-build-checklist.md).
    ambiguous_traces: int
    run_id: int  # the pass's pipeline_runs provenance row (F22)
    # dnd/0015: attempts with a prior attempt (`prior_attempt_id` set); of
    # them, the ones whose replay began at the prior attempt's last decodable
    # checked configuration (only under `seed_resumed_attempts`); and the
    # checks stored with each structural cause (never compared).
    resumed_attempts: int = 0
    seeded_attempts: int = 0
    no_move_logging_checks: int = 0
    resumed_attempt_checks: int = 0


# Kinds that advance the board state vs. the bookkeeping kinds.
_STATE_MOVES = frozenset({"move", "takeback"})

# _apply_move outcomes: the drop rewrote the state, left it unchanged (a
# no-op, the journal's `noop_move` flag), or could not be replayed at all.
_APPLIED = "applied"
_NOOP = "noop"
_SKIPPED = "skipped"


def reconstruct_all(
    conn: sqlite3.Connection,
    question_id: str | None = None,
    config: SegmentationConfig | None = None,
    *,
    feedback_policy: str = policy.LEGACY,
    idle_threshold_s: float = qc.DEFAULT_IDLE_THRESHOLD_S,
    seed_resumed_attempts: bool = False,
) -> ReconstructResult:
    """
    Reconstruct every question that has derived (non-page) DnD events, or just
    `question_id` when given. Returns aggregate counts across questions.

    ``seed_resumed_attempts`` (D3.2 knob 10, "seeded replay"; CLI
    ``--seed-resumed``) starts a resumed attempt at the prior attempt's last
    decodable checked configuration and compares its checks instead of
    storing them ``resumed_attempt``; off by default (the primary). Recorded
    in the pass's run configuration, as is the logging-regime set's hash.

    Before anything is written the logging-regime tripwire runs
    (:func:`logging_regimes.assert_data_consistent`): an import whose move,
    takeback or reset rows precede its ``move_logging_from`` date refuses
    the whole pass with :class:`logging_regimes.RegimeError` — no run row is
    recorded, nothing changes.

    ``feedback_policy`` (FP-3) is the platform era each check is classified
    under — ``legacy`` (the conservative default; every collected export
    predates the mid-2026 change) or ``layered``. It is recorded per attempt
    and drives the per-check disclosure-layer classification.

    ``idle_threshold_s`` (F9) is the analytic idle threshold ``δ_max`` —
    distinct from ``config.gap_seconds``, the attempt boundary. It drives the
    ``long_idle_gap`` QC flag and delimits the ``active_work`` segments, and
    is recorded in the pass's run config. ``dnd analyze`` computes
    ``active_time`` under the same default (:mod:`qc` owns it).

    Every pass records a ``pipeline_runs`` row carrying its configuration —
    the idle-gap threshold, the attempt-boundary rule, the policy era (F22).
    Unlike the single-transaction stages, this pass commits per question, so
    the 'running' row is committed up front and marked failed on an
    exception: partial data left by a crash stays attributable to its run.

    Contract: this function commits ``conn`` as it goes (it always has — one
    transaction per question). Do not call it with a transaction open on
    ``conn``; pending caller writes would be committed with the first
    question (and now with the run row).
    """
    if feedback_policy not in policy.VALID_POLICIES:
        raise ValueError(
            f"feedback_policy must be one of {policy.VALID_POLICIES}, "
            f"got {feedback_policy!r}"
        )
    config = config or SegmentationConfig()
    # dnd/0015: the tripwire first — a contradicted regime row refuses the
    # pass before a run row exists; the map it returns is applied below.
    regimes = logging_regimes.assert_data_consistent(conn)
    if question_id is not None:
        questions = [question_id]
    else:
        questions = [
            row["problem_name"]
            for row in conn.execute(
                "SELECT DISTINCT e.problem_name FROM dnd_events d "
                "JOIN events e USING (event_id) WHERE d.kind != 'page_view' "
                "ORDER BY e.problem_name"
            )
        ]

    run_id = runs.start_stage_run(
        conn,
        service="dnd",
        stage="reconstruct",
        config={
            "stage_version": STAGE_VERSION,
            "question_id": question_id,
            "gap_seconds": config.gap_seconds,
            "split_on_session_change": config.split_on_session_change,
            "split_on_page_view": config.split_on_page_view,
            "split_on_reset": config.split_on_reset,
            "feedback_policy": feedback_policy,
            "idle_threshold_s": idle_threshold_s,
            # dnd/0014: the identifier-alias set the boards were bound
            # through (None when no alias is registered).
            "alias_set_sha256": aliases.alias_set_sha256(conn),
            # dnd/0015: the seeded-replay option (D3.2 knob 10) and the
            # logging-regime set the structural causes were read under
            # (None when no regime is recorded).
            "seed_resumed_attempts": seed_resumed_attempts,
            "regime_set_sha256": logging_regimes.regime_set_sha256(conn),
        },
    )
    conn.commit()  # persist the 'running' row before the per-question passes

    # attempts, configs, checks, mismatches, non_eligible, noops, skipped,
    # ambiguous, resumed, seeded, no_move_logging, resumed_attempt
    totals = [0] * 12
    n_questions = 0
    try:
        for q in questions:
            counts = reconstruct_question(
                conn, q, config, feedback_policy, idle_threshold_s,
                seed_resumed_attempts=seed_resumed_attempts, regimes=regimes,
            )
            if counts is None:
                continue  # nothing reconstructable for this question
            n_questions += 1
            for i, value in enumerate(counts):
                totals[i] += value
    except BaseException:
        runs.complete_run(conn, run_id, status="failed")
        conn.commit()
        raise
    runs.complete_run(
        conn, run_id,
        notes=(
            f"questions={n_questions} attempts={totals[0]} "
            f"configurations={totals[1]} checks={totals[2]} "
            f"mismatches={totals[3]} non_eligible={totals[4]} "
            f"noop_moves={totals[5]} skipped={totals[6]} "
            f"ambiguous={totals[7]} resumed={totals[8]} seeded={totals[9]} "
            f"no_move_logging={totals[10]} resumed_attempt={totals[11]}"
        ),
    )
    conn.commit()

    return ReconstructResult(
        questions=n_questions,
        attempts=totals[0],
        configurations=totals[1],
        checks=totals[2],
        mismatches=totals[3],
        non_eligible=totals[4],
        noop_moves=totals[5],
        skipped_actions=totals[6],
        ambiguous_traces=totals[7],
        run_id=run_id,
        resumed_attempts=totals[8],
        seeded_attempts=totals[9],
        no_move_logging_checks=totals[10],
        resumed_attempt_checks=totals[11],
    )


def reconstruct_question(
    conn: sqlite3.Connection,
    question_id: str,
    config: SegmentationConfig,
    feedback_policy: str = policy.LEGACY,
    idle_threshold_s: float = qc.DEFAULT_IDLE_THRESHOLD_S,
    *,
    seed_resumed_attempts: bool = False,
    regimes: Mapping[int, str] | None = None,
) -> tuple[int, int, int, int, int, int, int, int, int, int, int, int] | None:
    """
    Clear and rebuild the reconstruction layer for one question. Returns
    ``(attempts, configs, checks, mismatches, non_eligible, noops, skipped,
    ambiguous, resumed, seeded, no_move_logging, resumed_attempt)`` or None
    when there is nothing to reconstruct (no events, or P cannot be
    determined). ``regimes`` is the ``import_id → move_logging_from`` map
    (read from the database when not given; the tripwire is
    ``reconstruct_all``'s).
    """
    if regimes is None:
        regimes = logging_regimes.regime_by_import(conn)
    n_cards = determine_n_cards(conn, question_id)
    if n_cards is None:
        log.warning(
            "%s: cannot determine card count (no board, no drag labels); "
            "skipping reconstruction",
            question_id,
        )
        return None
    # The slot count R bounds replayable drop zones — but only when a board is
    # synced; without one, drop indices are taken at face value (as the card
    # count is inferred). None = "no R bound".
    board = load_board(conn, question_id)
    n_zones = board.n_zones if board is not None else None
    # dnd/0014: the board's identifier when this question is a registered
    # alias — recorded on every attempt as its provenance marker. Labels
    # below are parsed with the logged `question_id`, never the canonical.
    canonical = aliases.resolve_question_id(conn, question_id)
    canonical_question_id = canonical if canonical != question_id else None
    # ℓ₀, the non-decoy card count, gates the layered policy (FP-3). Known
    # only with a board; None ⇒ a layered check can't be classified (legacy
    # grades every check and needs no ℓ₀).
    n_nondecoy = n_cards - len(board.decoy_cards) if board is not None else None

    students = [
        row["student_id"]
        for row in conn.execute(
            "SELECT DISTINCT e.student_id FROM dnd_events d "
            "JOIN events e USING (event_id) "
            "WHERE e.problem_name = ? AND d.kind != 'page_view' "
            "ORDER BY e.student_id",
            (question_id,),
        )
    ]
    if not students:
        return None

    attempts = configs = checks = mismatches = non_eligible = 0
    noops = skipped = ambiguous = 0
    resumed = seeded = no_move_logging = resumed_attempt = 0
    with transaction(conn):
        clear_question_results(conn, question_id)
        for student_id in students:
            events = _timeline(conn, question_id, student_id)
            if _order_ambiguous(events):
                # The journal's row_order_ambiguous case: the F2 sort key
                # still ties, so file order (rowid) decided the replay order.
                # TODO(F17b): persist as a per-attempt QC flag.
                ambiguous += 1
                log.warning(
                    "%s/%s: consecutive events tie on (timestamp, "
                    "problem_view); replay order fell back to file order "
                    "(row_order_ambiguous)",
                    question_id, student_id,
                )
            page_times = _page_view_times(conn, student_id)
            segments = _segment(events, page_times, config)
            # The ordered attempt sequence is in hand before any attempt is
            # replayed, so the prior attempt (the previous segment — the
            # most recent earlier attempt by time, on this logged identifier)
            # and, under seeding, its last decodable checked configuration
            # are carried from one iteration to the next: no second pass,
            # no re-decode (dnd/0015).
            prev: _AttemptOutcome | None = None
            for attempt_index, (rule, segment) in enumerate(segments, start=1):
                outcome = _write_attempt(
                    conn,
                    student_id=student_id,
                    question_id=question_id,
                    attempt_index=attempt_index,
                    segment=segment,
                    rule=rule,
                    n_cards=n_cards,
                    n_zones=n_zones,
                    config=config,
                    page_times=page_times,
                    feedback_policy=feedback_policy,
                    n_nondecoy=n_nondecoy,
                    idle_threshold_s=idle_threshold_s,
                    canonical_question_id=canonical_question_id,
                    prior_attempt_id=prev.attempt_id if prev is not None else None,
                    seed=(
                        prev.last_decoded_check
                        if prev is not None and seed_resumed_attempts
                        else None
                    ),
                    seeding=seed_resumed_attempts,
                    regimes=regimes,
                )
                attempts += 1
                configs += outcome.n_configs
                checks += outcome.n_checks
                mismatches += outcome.mismatches
                non_eligible += outcome.non_eligible
                noops += outcome.noops
                skipped += outcome.skipped
                resumed += outcome.resumed
                seeded += outcome.seeded
                no_move_logging += outcome.no_move_logging
                resumed_attempt += outcome.resumed_attempt
                prev = outcome

    log.info(
        "DnD reconstruct %s: %d attempt(s) (%d resumed, %d seeded), "
        "%d configuration(s), %d check(s), %d mismatch(es), %d non-eligible "
        "check(s) (%d no_move_logging, %d resumed_attempt), %d noop move(s), "
        "%d skipped action(s), %d order-ambiguous trace(s)",
        question_id, attempts, resumed, seeded, configs, checks, mismatches,
        non_eligible, no_move_logging, resumed_attempt, noops, skipped,
        ambiguous,
    )
    return (
        attempts, configs, checks, mismatches, non_eligible, noops, skipped,
        ambiguous, resumed, seeded, no_move_logging, resumed_attempt,
    )


def determine_n_cards(conn: sqlite3.Connection, question_id: str) -> int | None:
    """
    The card count P for a question: the board's ``n_cards`` if synced, else
    the highest ``drag`` index seen in the question's derived events (moves'
    source labels and check Inputs). None when neither is available.
    """
    board = load_board(conn, question_id)
    if board is not None:
        return board.n_cards

    highest = 0
    for row in conn.execute(
        "SELECT d.source_label FROM dnd_events d JOIN events e USING (event_id) "
        "WHERE e.problem_name = ? AND d.kind IN ('move', 'takeback') "
        "AND d.source_label IS NOT NULL",
        (question_id,),
    ):
        parsed = decode.parse_label(row["source_label"], question_id)
        if parsed is not None and parsed.kind == "drag":
            highest = max(highest, parsed.index)

    for row in conn.execute(
        "SELECT d.configuration_json FROM dnd_events d JOIN events e "
        "USING (event_id) WHERE e.problem_name = ? AND d.kind = 'check' "
        "AND d.parse_ok = 1 AND d.configuration_json IS NOT NULL",
        (question_id,),
    ):
        configuration = json.loads(row["configuration_json"])
        for cards in configuration.values():
            if not isinstance(cards, list):
                continue
            for label in cards:
                parsed = decode.parse_label(str(label), question_id)
                if parsed is not None and parsed.kind == "drag":
                    highest = max(highest, parsed.index)

    return highest or None


# ---------------------------------------------------------------------------
# Idempotency: clear the reconstruction layer (and its dependents) for a question
# ---------------------------------------------------------------------------


def clear_question_results(conn: sqlite3.Connection, question_id: str) -> int:
    """
    Delete the question's attempts, configurations, segments, and the
    downstream rows (check scores, attempt metrics) that reference its
    attempt ids — those are rebuilt by ``dnd score`` / ``dnd analyze``
    against the new attempt ids. Returns the total number of rows deleted.

    Two callers: each ``reconstruct_question`` pass (its own idempotency
    clear), and ``board.sync_board_for_question`` on a board-hash change (F29
    — results computed against the old key/layout must not read as current,
    and clearing the attempts is what makes the on-demand
    ``scoring_service.run_scoring`` re-reconstruct rather than skip).
    """
    # Attempt-scoped tables are deleted via a subquery, not an IN (?, …) list:
    # a question can carry more attempts than SQLite's bound-parameter limit.
    in_attempts = (
        "(SELECT attempt_id FROM dnd_attempts WHERE question_id = ?)"
    )
    deleted = 0
    deleted += conn.execute(
        "DELETE FROM dnd_check_scores WHERE question_id = ?", (question_id,)
    ).rowcount
    deleted += conn.execute(
        f"DELETE FROM dnd_attempt_metrics WHERE attempt_id IN {in_attempts}",
        (question_id,),
    ).rowcount
    deleted += conn.execute(
        f"DELETE FROM dnd_segments WHERE attempt_id IN {in_attempts}",
        (question_id,),
    ).rowcount
    deleted += conn.execute(
        f"DELETE FROM dnd_configurations WHERE attempt_id IN {in_attempts}",
        (question_id,),
    ).rowcount
    deleted += conn.execute(
        "DELETE FROM dnd_attempts WHERE question_id = ?", (question_id,)
    ).rowcount
    return deleted


# ---------------------------------------------------------------------------
# Reading the timeline and segmenting it into attempts
# ---------------------------------------------------------------------------


def _timeline(
    conn: sqlite3.Connection, question_id: str, student_id: str
) -> list[sqlite3.Row]:
    """One student's non-page DnD events on one question, in action order.

    Action order is the journal's recommended sort key (F2): timestamp, then
    numeric-cast ``problem_view`` (the platform's own within-timestamp
    counter; cast because the column is text and ``'10' < '9'`` lexically),
    then ``rowid`` (file order) as the last-resort tie-breaker. Rows where the
    fallback decided the order are counted by :func:`_order_ambiguous`.
    """
    return conn.execute(
        "SELECT d.event_id, d.kind, d.source_label, d.zone_label, "
        "d.configuration_json, d.feedback, d.parse_ok, "
        "e.session_id, e.timestamp, e.problem_view, e.import_id "
        "FROM dnd_events d JOIN events e USING (event_id) "
        "WHERE e.problem_name = ? AND e.student_id = ? AND d.kind != 'page_view' "
        "ORDER BY e.timestamp, CAST(e.problem_view AS INTEGER), e.rowid",
        (question_id, student_id),
    ).fetchall()


def _order_ambiguous(events: list[sqlite3.Row]) -> bool:
    """Whether consecutive timeline rows tie on (timestamp, problem_view) —
    i.e. the rowid fallback, not the sort key, decided their replay order."""
    return any(
        prev["timestamp"] == cur["timestamp"]
        and prev["problem_view"] == cur["problem_view"]
        for prev, cur in zip(events, events[1:])
    )


def _page_view_times(conn: sqlite3.Connection, student_id: str) -> list[str]:
    """
    Timestamps of the student's page views (any problem). Page rows carry the
    *page's* name, not the question's, so they never appear in `_timeline`;
    they are read separately and used only to mark sitting boundaries (B3.1).
    """
    return [
        row["timestamp"]
        for row in conn.execute(
            "SELECT e.timestamp FROM dnd_events d JOIN events e USING (event_id) "
            "WHERE e.student_id = ? AND d.kind = 'page_view' "
            "AND e.timestamp IS NOT NULL ORDER BY e.timestamp",
            (student_id,),
        )
    ]


def _segment(
    events: list[sqlite3.Row],
    page_times: list[str],
    config: SegmentationConfig,
) -> list[tuple[str, list[sqlite3.Row]]]:
    """
    Split a timeline into ``(rule, [events])`` attempts. `rule` is the trigger
    that started each attempt: 'initial' for the first, else the boundary that
    fired ('session_change', 'page_view', 'gap>Ns', or 'reset').
    """
    if not events:
        return []

    segments: list[tuple[str, list[sqlite3.Row]]] = []
    current: list[sqlite3.Row] = [events[0]]
    rule = "initial"
    for prev, event in zip(events, events[1:]):
        boundary = _boundary_reason(prev, event, page_times, config)
        if boundary is not None:
            segments.append((rule, current))
            current = [event]
            rule = boundary
        else:
            current.append(event)
    segments.append((rule, current))
    return segments


def _boundary_reason(
    prev: sqlite3.Row,
    event: sqlite3.Row,
    page_times: list[str],
    config: SegmentationConfig,
) -> str | None:
    """The segmentation rule that fires between `prev` and `event`, or None."""
    if config.split_on_session_change and prev["session_id"] != event["session_id"]:
        return "session_change"
    if config.split_on_page_view and _page_between(
        prev["timestamp"], event["timestamp"], page_times
    ):
        return "page_view"
    if config.gap_seconds is not None:
        gap = _gap_seconds(prev["timestamp"], event["timestamp"])
        if gap is not None and gap > config.gap_seconds:
            return f"gap>{config.gap_seconds:g}s"
    # A reset is the previous event; treat the *next* event as a fresh attempt
    # only when configured to (off by default — see the module note).
    if config.split_on_reset and prev["kind"] == "reset":
        return "reset"
    return None


def _page_between(prev_ts: str, next_ts: str, page_times: list[str]) -> bool:
    """Whether a page view falls strictly between two event timestamps.

    Timestamps are opaque sortable strings (rule 6), so the comparison is
    lexical — the same order the timeline is read in — and needs no parsing.
    """
    if prev_ts is None or next_ts is None:
        return False
    return any(prev_ts < pt < next_ts for pt in page_times)


def _gap_seconds(prev_ts: str | None, next_ts: str | None) -> float | None:
    """Seconds between two timestamps; None if either is missing or unparseable."""
    if not prev_ts or not next_ts:
        return None
    try:
        return (parse_timestamp(next_ts) - parse_timestamp(prev_ts)).total_seconds()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Replaying one attempt into configurations (+ the B3.2 cross-check)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _AttemptOutcome:
    """What one replayed attempt hands back to the student loop (dnd/0015):
    its counts, its id (the next attempt's ``prior_attempt_id``) and the
    seed a resumed successor would start from under knob 10."""

    attempt_id: int
    n_configs: int
    n_checks: int
    mismatches: int  # stored comparisons that disagree (eligible anchors)
    non_eligible: int  # checks stored not comparable, whatever the cause
    noops: int
    skipped: int
    resumed: bool  # the attempt has a prior attempt
    seeded: bool  # its replay began at the prior attempt's last decodable check
    no_move_logging: int  # checks stored with that cause
    resumed_attempt: int  # checks stored with that cause
    # The decoded configuration of the attempt's last check whose payload
    # decodes (tier (a), whatever cause the check was stored with — the
    # payload is a measurement of the board under any regime; ruling
    # 2026-10-06): the seed of a resumed successor under knob 10. None when
    # no check decodes, which makes the successor fall back to s_0.
    last_decoded_check: tuple[int, ...] | None


def _structural_cause(
    event: sqlite3.Row,
    *,
    resumed: bool,
    seeding: bool,
    regimes: Mapping[int, str] | None,
) -> str | None:
    """The tier-(b) cause of a check, or None: ``no_move_logging`` when the
    check's calendar date (the first ten characters of its opaque timestamp)
    precedes its import's ``move_logging_from``; else ``resumed_attempt``
    when the attempt has a prior attempt and the pass is not seeding. The
    precedence is the 2026-10-05 entry's (the memo's S2 before S1)."""
    if regimes:
        date = regimes.get(event["import_id"])
        ts = event["timestamp"]
        if date is not None and ts is not None and ts[:10] < date:
            return qc.CAUSE_NO_MOVE_LOGGING
    if resumed and not seeding:
        return qc.CAUSE_RESUMED_ATTEMPT
    return None


def _write_attempt(
    conn: sqlite3.Connection,
    *,
    student_id: str,
    question_id: str,
    attempt_index: int,
    segment: list[sqlite3.Row],
    rule: str,
    n_cards: int,
    n_zones: int | None,
    config: SegmentationConfig,
    page_times: list[str],
    feedback_policy: str,
    n_nondecoy: int | None,
    idle_threshold_s: float,
    canonical_question_id: str | None = None,
    prior_attempt_id: int | None = None,
    seed: tuple[int, ...] | None = None,
    seeding: bool = False,
    regimes: Mapping[int, str] | None = None,
) -> _AttemptOutcome:
    """
    Insert one attempt and its replayed configurations.

    ``prior_attempt_id`` is the same student's previous attempt on this
    logged identifier (None for a first attempt); ``seed`` is that attempt's
    last decodable checked configuration, passed only under ``seeding``
    (knob 10): the replay then starts there, with ``seeded = 1`` on every
    configuration, or at ``s_0`` with ``seeded = 0`` when the prior attempt
    had none. Off seeding the column stays NULL. ``regimes`` maps import ids
    to their ``move_logging_from`` date.

    A check carrying a structural cause (``no_move_logging``,
    ``resumed_attempt``) is stored not comparable with that cause — out of
    every agreement denominator — but its comparison and decode outcome
    still feed the flag rollup exactly as before (the shadow comparison), so
    the attempt's flags and readiness are what they were before the causes
    existed.
    """
    timestamps = [e["timestamp"] for e in segment if e["timestamp"] is not None]
    attempt_id = conn.execute(
        "INSERT INTO dnd_attempts (student_id, question_id, attempt_index, "
        "session_id, started_at, ended_at, segmentation_rule, feedback_policy, "
        "canonical_question_id, prior_attempt_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            student_id,
            question_id,
            attempt_index,
            segment[0]["session_id"],
            timestamps[0] if timestamps else None,
            timestamps[-1] if timestamps else None,
            f"{rule}; {config.describe()}",
            feedback_policy,
            canonical_question_id,
            prior_attempt_id,
        ),
    ).lastrowid

    resumed = prior_attempt_id is not None
    seeded = seeding and resumed and seed is not None
    # NULL when the pass does not seed; otherwise 1 on a seeded attempt's
    # configurations and 0 elsewhere, so "resumed and 0" is the fallback.
    seeded_mark = None if not seeding else (1 if seeded else 0)
    state = list(seed) if seeded else [0] * n_cards
    if seeded:
        log.info(
            "%s/%s attempt %d: seeded at the prior attempt's last decodable "
            "checked configuration %s (knob 10)",
            question_id, student_id, attempt_index, tuple(state),
        )
    seq = 0
    _write_config(conn, attempt_id, seq, None, state, is_check=False, seeded=seeded_mark)
    # The replayed trace, one entry per written config: (seq, kind, session,
    # timestamp). X0 has no event; it joins the first event's session and
    # carries no timestamp (F3/F9 segments).
    trace: list[tuple[int, str | None, str | None, str | None]] = [
        (0, None, segment[0]["session_id"], None)
    ]
    n_configs = 1
    n_checks = mismatches = non_eligible = noops = skipped = 0
    checks_matched = 0   # checks whose (shadow) comparison agrees — replay_ok
    flag_mismatches = 0  # checks whose (shadow) comparison disagrees — check_mismatch
    invalid_rows = 0     # rows whose state violates the state model (F17b)
    malformed_checks = 0  # checks whose payload never reached the decoder
    graded_so_far = 0    # running count of graded checks (FP-3 ordinal)
    n_no_move_logging = n_resumed_attempt = 0
    last_decoded_check: tuple[int, ...] | None = None

    for event in segment:
        kind = event["kind"]
        if kind in _STATE_MOVES:
            outcome = _apply_move(state, event, question_id, n_cards, n_zones)
            if outcome == _SKIPPED:
                skipped += 1
                if event["parse_ok"]:
                    # Parsed fine but names an off-board card/slot — the
                    # invalid_state case; unparseable rows are parse_error.
                    invalid_rows += 1
                continue
            is_noop = outcome == _NOOP
            if is_noop:
                noops += 1
            seq += 1
            _write_config(
                conn, attempt_id, seq, event["event_id"], state,
                is_check=False, is_noop=is_noop, seeded=seeded_mark,
            )
            trace.append((seq, kind, event["session_id"], event["timestamp"]))
            n_configs += 1
        elif kind == "reset":
            state = [0] * n_cards
            seq += 1
            _write_config(
                conn, attempt_id, seq, event["event_id"], state,
                is_check=False, seeded=seeded_mark,
            )
            trace.append((seq, kind, event["session_id"], event["timestamp"]))
            n_configs += 1
        elif kind == "check":
            seq += 1
            structural = _structural_cause(
                event, resumed=resumed, seeding=seeding, regimes=regimes
            )
            matches, decode_cause, decoded_s = _cross_check(
                state, event, question_id, n_cards, n_zones,
                structural_cause=structural,
            )
            if decoded_s is not None:
                last_decoded_check = decoded_s
            # The flag rollup, exactly as before the structural causes
            # existed: the decode outcome and the (shadow) comparison.
            if decode_cause is not None:
                if decode_cause == qc.CAUSE_MALFORMED:
                    malformed_checks += 1
                else:
                    invalid_rows += 1
            elif matches:
                checks_matched += 1
            else:
                flag_mismatches += 1
            # What is stored: the first applicable cause in precedence
            # order, with a NULL comparison; a comparison only on an
            # eligible anchor.
            if structural is not None:
                stored_matches, stored_cause = None, structural
                non_eligible += 1
                if structural == qc.CAUSE_NO_MOVE_LOGGING:
                    n_no_move_logging += 1
                else:
                    n_resumed_attempt += 1
            else:
                stored_matches, stored_cause = matches, decode_cause
                if decode_cause is not None:
                    non_eligible += 1
                elif not matches:
                    mismatches += 1
            # FP-3: classify what the student saw. The graded ordinal counts
            # graded checks only — a refused press doesn't advance it.
            n_placed = sum(1 for z in state if z != 0)
            graded = policy.is_graded(feedback_policy, n_placed, n_nondecoy)
            graded_ordinal = None
            if graded:
                graded_so_far += 1
                graded_ordinal = graded_so_far
            layer = policy.disclosure_layer(feedback_policy, graded, graded_ordinal)
            _write_config(
                conn, attempt_id, seq, event["event_id"], state,
                is_check=True, matches_input=stored_matches,
                anchor_cause=stored_cause, disclosure_layer=layer,
                graded_ordinal=graded_ordinal, seeded=seeded_mark,
            )
            trace.append((seq, kind, event["session_id"], event["timestamp"]))
            n_configs += 1
            n_checks += 1

    _write_segments(conn, attempt_id, trace, idle_threshold_s)

    flags = _attempt_flags(
        segment,
        page_times,
        has_board=n_zones is not None,
        n_checks=n_checks,
        checks_matched=checks_matched,
        mismatches=flag_mismatches,
        noops=noops,
        invalid_rows=invalid_rows,
        malformed_checks=malformed_checks,
        idle_threshold_s=idle_threshold_s,
    )
    conn.execute(
        "UPDATE dnd_attempts SET qc_flags_json = ?, readiness = ? "
        "WHERE attempt_id = ?",
        (qc.encode_flags(flags), qc.readiness(flags), attempt_id),
    )

    return _AttemptOutcome(
        attempt_id=attempt_id,
        n_configs=n_configs,
        n_checks=n_checks,
        mismatches=mismatches,
        non_eligible=non_eligible,
        noops=noops,
        skipped=skipped,
        resumed=resumed,
        seeded=seeded,
        no_move_logging=n_no_move_logging,
        resumed_attempt=n_resumed_attempt,
        last_decoded_check=last_decoded_check,
    )


def _attempt_flags(
    segment: list[sqlite3.Row],
    page_times: list[str],
    *,
    has_board: bool,
    n_checks: int,
    checks_matched: int,
    mismatches: int,
    noops: int,
    invalid_rows: int,
    malformed_checks: int = 0,
    idle_threshold_s: float,
) -> set[str]:
    """
    One attempt's QC flag set (F17b), rolled up from the replay outcomes and
    a scan of the segment rows. The vocabulary and the readiness policy live
    in :mod:`~proteus.services.dragndrop.qc`; this is only the signal
    gathering. ``long_idle_gap`` (F9) scans the *logged* rows — like the
    other flag scans, and unlike the active_work spans, which partition the
    replayed configurations — for an action gap over the analytic
    ``idle_threshold_s``. ``malformed_checks`` counts checks whose payload
    never reached the decoder (``parse_error``); on derived data they are
    exactly the ``parse_ok = 0`` check rows the segment scan already sees.
    """
    flags: set[str] = set()
    if not has_board:
        flags.add("missing_source_map")
    if malformed_checks or any(not e["parse_ok"] for e in segment):
        flags.add("parse_error")
    if invalid_rows:
        flags.add("invalid_state")
    if noops:
        flags.add("noop_move")
    if mismatches:
        flags.add("check_mismatch")
    if n_checks and checks_matched == n_checks:
        flags.add("replay_ok")
    if _order_ambiguous(segment):
        flags.add("row_order_ambiguous")
    for prev, cur in zip(segment, segment[1:]):
        if prev["session_id"] != cur["session_id"] or _page_between(
            prev["timestamp"], cur["timestamp"], page_times
        ):
            # Reachable only under non-default segmentation configs — the
            # defaults split attempts at exactly these boundaries.
            flags.add("session_break")
        gap = _gap_seconds(prev["timestamp"], cur["timestamp"])
        if gap is not None and gap > idle_threshold_s:
            # The journal's subsec-action-gaps-processing rule: δ_k > δ_max.
            # A warning (readiness reads `limited`): the replay is intact,
            # but elapsed-time features are suspect.
            flags.add("long_idle_gap")
        if (
            prev["timestamp"] == cur["timestamp"]
            and prev["kind"] == cur["kind"]
            and prev["source_label"] == cur["source_label"]
            and prev["zone_label"] == cur["zone_label"]
            and prev["configuration_json"] == cur["configuration_json"]
            and prev["feedback"] == cur["feedback"]
            and prev["parse_ok"] == cur["parse_ok"]
        ):
            # Byte-identical rows are deduped at ingest (content-hash id);
            # this catches re-logged rows differing only in problem_view.
            # The whole payload must repeat — checks sharing a timestamp
            # and configuration but graded/parsed differently are not
            # duplicates.
            flags.add("duplicate_row")
    return flags


def _apply_move(
    state: list[int],
    event: sqlite3.Row,
    question_id: str,
    n_cards: int,
    n_zones: int | None,
) -> str:
    """
    Apply a move/takeback to `state` in place. Returns ``_SKIPPED`` (leaving
    `state` untouched) when the move cannot be replayed — a malformed row, or a
    card or slot index off the board (the B2.4 gap case) — so the rest of the
    attempt still reconstructs, and ``_NOOP`` when the decoded destination
    equals the card's current zone (``b = a``, the journal's ``noop_move``
    case: the update is zero, the row is flagged rather than dropped). The
    dragzone (a takeback) is always replayable; drop slots are bounded by
    ``n_zones`` only when a board is synced (else the index is taken at face
    value, mirroring the inferred card count).
    """
    if not event["parse_ok"]:
        log.warning(
            "%s: move %s is unparseable (parse_ok=0); skipping in replay",
            question_id, event["event_id"],
        )
        return _SKIPPED
    card = decode.parse_label(event["source_label"] or "", question_id)
    zone = decode.parse_label(event["zone_label"] or "", question_id)
    if card is None or card.kind != "drag" or not (1 <= card.index <= n_cards):
        log.warning(
            "%s: move %s names card %r off the %d-card board; skipping in replay",
            question_id, event["event_id"], event["source_label"], n_cards,
        )
        return _SKIPPED
    if zone is None or zone.kind == "drag":
        log.warning(
            "%s: move %s names zone %r that does not decode; skipping in replay",
            question_id, event["event_id"], event["zone_label"],
        )
        return _SKIPPED
    if zone.kind == "drop" and n_zones is not None and not (1 <= zone.index <= n_zones):
        log.warning(
            "%s: move %s names slot %r off the %d-slot board; skipping in replay",
            question_id, event["event_id"], event["zone_label"], n_zones,
        )
        return _SKIPPED
    if state[card.index - 1] == zone.index:
        # No-op drops occur in real logs (a card released over the zone it
        # already occupies); harmless to the trajectory, flagged for processing.
        return _NOOP
    state[card.index - 1] = zone.index
    return _APPLIED


def _cross_check(
    state: list[int],
    event: sqlite3.Row,
    question_id: str,
    n_cards: int,
    n_zones: int | None,
    *,
    structural_cause: str | None = None,
) -> tuple[bool | None, str | None, tuple[int, ...] | None]:
    """
    Compare the replayed state at a check to the state the check logged in its
    Input (B3.2), under the frozen anchor-eligibility definition
    (paper1-decisions.md, 2026-09-28). Returns ``(matches, cause, decoded)``:

    * a payload that decodes to a unique, board-valid configuration is
      compared: ``matches`` True/False, ``cause`` None, ``decoded`` the
      vector;
    * any other check is never compared: ``matches`` None and ``cause`` one
      of the decode-side names of ``qc.ANCHOR_CAUSE_ORDER`` — ``malformed``
      when the payload never reached the decoder (``parse_ok = 0``, empty,
      or not a JSON object; the attempt's ``parse_error`` flag), else the
      decode-side cause (one card in two zones, an unknown card or zone
      label, a slot outside 1..R; the attempt's ``invalid_state`` flag) —
      and ``decoded`` None. ``n_zones`` is None without a board, and then
      no slot bound applies — as in replay.

    ``structural_cause`` is the tier-(b) cause the caller already found
    (``no_move_logging`` or ``resumed_attempt``), given only so the
    mismatch log line says the comparison is a shadow one; the caller
    decides what is stored (the structural cause with a NULL comparison)
    and what feeds the flags (this comparison, unchanged). The decoded
    vector itself is not persisted — it is re-derivable from the verbatim
    ``configuration_json``, and scoring and the manual-audit script
    recompute it — but it is returned so the student loop can carry the
    last decodable check as the next attempt's seed (knob 10).
    """
    if not event["parse_ok"] or not event["configuration_json"]:
        return None, qc.CAUSE_MALFORMED, None
    try:
        configuration = json.loads(event["configuration_json"])
    except json.JSONDecodeError:
        return None, qc.CAUSE_MALFORMED, None
    if not isinstance(configuration, dict):
        return None, qc.CAUSE_MALFORMED, None
    decoded = decode.decode_configuration(
        configuration, question_id, n_cards, n_zones
    )
    cause = decoded.cause
    if cause is not None:
        log.warning(
            "%s: check %s is not an eligible anchor (%s: duplicate cards %s, "
            "unknown cards %s, unknown zones %s, off-board slots %s) — stored "
            "as not comparable, flagged invalid_state",
            question_id, event["event_id"], cause, decoded.duplicate_cards,
            decoded.unknown_cards, decoded.unknown_zones,
            decoded.offboard_slots,
        )
        return None, cause, None
    matches = tuple(state) == decoded.s
    if not matches:
        if structural_cause is not None:
            log.info(
                "%s: shadow comparison at check %s disagrees — replayed %s, "
                "logged Input %s; stored not comparable (%s), the flag "
                "rollup unchanged",
                question_id, event["event_id"], tuple(state), decoded.s,
                structural_cause,
            )
        else:
            log.warning(
                "%s: reconstruction mismatch at check %s — replayed %s, "
                "logged Input %s (a move row was likely dropped)",
                question_id, event["event_id"], tuple(state), decoded.s,
            )
    return matches, None, decoded.s


def _write_config(
    conn: sqlite3.Connection,
    attempt_id: int,
    seq: int,
    event_id: str | None,
    state: list[int],
    *,
    is_check: bool,
    matches_input: bool | None = None,
    anchor_cause: str | None = None,
    is_noop: bool = False,
    disclosure_layer: str | None = None,
    graded_ordinal: int | None = None,
    seeded: int | None = None,
) -> None:
    conn.execute(
        "INSERT INTO dnd_configurations (attempt_id, seq, event_id, s_json, "
        "is_check, matches_input, anchor_cause, is_noop, disclosure_layer, "
        "graded_ordinal, seeded) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            attempt_id,
            seq,
            event_id,
            json.dumps(list(state)),
            1 if is_check else 0,
            None if matches_input is None else (1 if matches_input else 0),
            anchor_cause,
            1 if is_noop else 0,
            disclosure_layer,
            graded_ordinal,
            seeded,
        ),
    )


# ---------------------------------------------------------------------------
# Segments (F3): the journal's first-class analysis units, per attempt
# ---------------------------------------------------------------------------


def _boundary_spans(
    trace: list[tuple[int, str | None, str | None, str | None]],
    boundary_kind: str,
) -> list[tuple[int, int]]:
    """
    Inclusive ``[start, end]`` seq spans delimited by ``boundary_kind`` rows
    (subsec-attempts-and-segments): the delimiting row belongs to the span it
    ends, rows past the last delimiter form a trailing span (the post-check
    revision unit), and a trace with no delimiter is one open span.
    """
    spans: list[tuple[int, int]] = []
    start = trace[0][0]
    for seq, kind, _, _ in trace:
        if kind == boundary_kind:
            spans.append((start, seq))
            start = seq + 1
    last_seq = trace[-1][0]
    if start <= last_seq or not spans:
        spans.append((start, last_seq))
    return spans


def _session_spans(
    trace: list[tuple[int, str | None, str | None, str | None]],
) -> list[tuple[int, int]]:
    """Maximal runs of one logged session (X0 joins the first run). Splits
    survive inside an attempt only under non-default segmentation configs —
    the defaults turn a session change into an attempt boundary."""
    spans: list[tuple[int, int]] = []
    start = trace[0][0]
    current = trace[0][2]
    for seq, _, session, _ in trace[1:]:
        if session != current:
            spans.append((start, seq - 1))
            start, current = seq, session
    spans.append((start, trace[-1][0]))
    return spans


def _active_work_spans(
    trace: list[tuple[int, str | None, str | None, str | None]],
    idle_threshold_s: float,
) -> list[tuple[int, int]]:
    """
    Maximal runs not interrupted by a long idle gap — the clock-based
    segment type of subsec-segment-tables, delimited where the gap between
    consecutive replayed rows exceeds the analytic ``δ_max``
    (subsec-action-gaps-processing). X0 carries no timestamp and joins the
    first run. A row without a parseable timestamp opens no boundary at
    itself, but it does not reset the clock either: gaps are measured from
    the last parseable timestamp, so a long idle spanning such a row still
    splits at the next timestamped row.
    """
    spans: list[tuple[int, int]] = []
    start = trace[0][0]
    previous_ts = trace[0][3]
    for seq, _, _, ts in trace[1:]:
        gap = _gap_seconds(previous_ts, ts)
        if gap is not None and gap > idle_threshold_s:
            spans.append((start, seq - 1))
            start = seq
        if ts is not None:
            previous_ts = ts
    spans.append((start, trace[-1][0]))
    return spans


def _write_segments(
    conn: sqlite3.Connection,
    attempt_id: int,
    trace: list[tuple[int, str | None, str | None, str | None]],
    idle_threshold_s: float,
) -> None:
    """
    Materialize one attempt's segments into ``dnd_segments`` (migration
    ``dnd/0004``): the three model-level types plus the clock-based
    ``active_work`` type, live since F9 delivered the analytic idle
    threshold it was reserved for.
    """
    by_type = (
        ("check", _boundary_spans(trace, "check")),
        ("reset", _boundary_spans(trace, "reset")),
        ("session", _session_spans(trace)),
        ("active_work", _active_work_spans(trace, idle_threshold_s)),
    )
    for segment_type, spans in by_type:
        for index, (start_seq, end_seq) in enumerate(spans, start=1):
            conn.execute(
                "INSERT INTO dnd_segments (attempt_id, segment_type, "
                "segment_index, start_seq, end_seq) VALUES (?, ?, ?, ?, ?)",
                (attempt_id, segment_type, index, start_seq, end_seq),
            )
