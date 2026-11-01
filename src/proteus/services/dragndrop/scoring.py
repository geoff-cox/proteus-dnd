"""
Scoring (plan item B3.3): score each checked configuration against the key.

Every scoring *variant* of the journal's ``sec-checked-state-features`` is
computed side by side and persisted together in ``dnd_check_scores`` (plan
R5): the platform's verdict is one of them, the research signal another, and
the journal deliberately keeps both rather than committing early. Since F12
the row also carries the baseline error profile in flat form, ``omissions``
(the grader's ``u``), ``wrong_slot``, and the two decoy leaks, so the
journal's additive decomposition ``d★ = O + W_slot + L_P``
(g-baseline-additive-error-decomposition) is available without recomputing,
plus ``per_card_score`` ``p = σ/P``, completing the feature dictionary's
checked-state family. (``w`` itself stays a computed-only field: it is the
overlapping sum ``W_slot + L_P``, recoverable from the stored parts.) The
labeled-vs-label-free *policy* is resolved (``Resolved 2026-06-12``, recorded
in the since-retired ``ch-ideas``; the book now carries it as the
labeling-gap guidance of ``subsec-when-label-free-is-appropriate``:
correctness is **label-aware**, the labeled score σ is the verdict, σ̃ is
kept as a diagnostic), but the partial-credit choice is still open, so all
variants are kept per-check.

The objects, on the minimal state vector ``s`` (``s[j-1]`` = zone of card
``j``; 0 = tray) against the key ``s*`` and the slot count ``R``:

* **σ** (``sigma``): labeled agreement ``tr(Xᵀ X*)``: cards in their keyed zone.
* **d** (``distance``): ``P - σ``: misplaced cards = moves still needed.
* **per-card** ``c``: the 0/1 vector ``diag(Xᵀ X*)``.
* **g**: baseline-corrected progress ``(σ - σ(X₀))/(P - σ(X₀))``: 0 on the
  empty board, 1 at the key (σ(X₀) = the decoy-card count ``o``).
* **σ̃** (``sigma_tilde``): label-free score: tray-correct cards plus the best
  matching of the student's slots to the key's, via the slot confusion matrix
  and an optimal assignment (``subsec-computing-label-free-agreement``); σ̃ ≥ σ
  always, and σ̃ - σ is the mislabel diagnostic.
* **decoy-source / decoy-target leak**: decoy cards dragged into a slot, and
  cards dropped into a slot the key leaves empty (the two structural-decoy
  error slices).

Pure functions throughout; ``score_checks`` is the only DB-touching entry.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass

from proteus.core import runs
from proteus.core.db import transaction
from proteus.services.dragndrop import aliases, decode
from proteus.services.dragndrop.board import load_board

log = logging.getLogger(__name__)

# Version of this stage's scoring semantics, recorded on each pass's
# pipeline_runs provenance row (F22). Bump when any persisted variant
# changes meaning (the label-aware correctness policy is part of it).
# v2: the F12 error-profile columns (omissions, wrong_slot, per_card_score)
# joined the persisted vocabulary.
# 3: question-identifier aliases (dnd/0014), checks logged under a retired
# identifier are scored against its successor's board (rows keep the logged
# identifier); the run configuration records the alias-set hash.
STAGE_VERSION = 3


@dataclass(frozen=True)
class CheckScore:
    """All scoring variants for one checked configuration, side by side."""

    sigma: int  # labeled agreement
    distance: int  # d = P - σ
    g: float  # baseline-corrected progress
    sigma_tilde: int  # label-free agreement
    per_card: tuple[int, ...]  # diag(Xᵀ X*)
    all_or_nothing: int  # 1 if s == s*
    decoy_source_leak: int  # # decoy-cards that were placed (L_P)
    decoy_target_leak: int  # # cards placed in a decoy-slot (L_R)
    w: int  #cards placed in a wrong slot (grader's reddened count)
    u: int  #non-decoy-cards left in the tray ("left to place"; stored as `omissions`)
    wrong_slot: int  # W_slot: non-decoy cards in a wrong slot (w - L_P)
    per_card_score: float  # p = σ/P


# ---------------------------------------------------------------------------
# Pure metrics on the state vectors
# ---------------------------------------------------------------------------


def sigma(s: tuple[int, ...], key: tuple[int, ...]) -> int:
    """Labeled agreement: the number of cards in their keyed zone."""
    return sum(1 for a, b in zip(s, key) if a == b)


def distance(s: tuple[int, ...], key: tuple[int, ...]) -> int:
    """d = P - σ: misplaced cards, the moves still needed to reach the key."""
    return len(key) - sigma(s, key)


def per_card(s: tuple[int, ...], key: tuple[int, ...]) -> tuple[int, ...]:
    """The 0/1 per-card agreement vector ``diag(Xᵀ X*)``."""
    return tuple(1 if a == b else 0 for a, b in zip(s, key))


def sigma_baseline(key: tuple[int, ...]) -> int:
    """σ(X₀): agreement of the empty board = the number of decoy cards ``o``."""
    return sum(1 for b in key if b == 0)


def g(s: tuple[int, ...], key: tuple[int, ...]) -> float:
    """
    Baseline-corrected progress ``(σ - σ(X₀))/(P - σ(X₀)) = 1 - d/d(X₀)``: 0 on
    the empty board, 1 at the key. Degenerate boards where every card is a
    decoy (d(X₀)=0) read as solved-or-not.
    """
    denominator = len(key) - sigma_baseline(key)
    if denominator == 0:
        return 1.0 if distance(s, key) == 0 else 0.0
    return (sigma(s, key) - sigma_baseline(key)) / denominator


def all_or_nothing(s: tuple[int, ...], key: tuple[int, ...]) -> int:
    """1 iff the configuration equals the key (the live Correct/Incorrect bit)."""
    return 1 if tuple(s) == tuple(key) else 0


def w(s: tuple[int, ...], key: tuple[int, ...]) -> int:
    """Cards placed in a wrong slot (placed, not their keyed zone), reddened."""
    return sum(1 for a, b in zip(s, key) if a != 0 and a != b)


def u(s: tuple[int, ...], key: tuple[int, ...]) -> int:
    """Cards left in the tray though the key places them ("left to place")."""
    return sum(1 for a, b in zip(s, key) if a == 0 and b != 0)


def wrong_slot(s: tuple[int, ...], key: tuple[int, ...]) -> int:
    """
    ``W_slot``: non-decoy cards placed in a slot other than their keyed slot
    (both zones ≥ 1), ``len(errors.misplacements)``. A placed decoy is a
    decoy-card leak, not a wrong-slot placement, so the components of the
    additive decomposition ``d★ = u + W_slot + L_P``
    (g-baseline-additive-error-decomposition, F12) overlap nowhere.
    """
    return sum(1 for a, b in zip(s, key) if a >= 1 and b >= 1 and a != b)


def per_card_score(s: tuple[int, ...], key: tuple[int, ...]) -> float:
    """``p = σ/P``, the feature dictionary's per-card score (F12)."""
    return sigma(s, key) / len(key)


def decoy_source_leak(s: tuple[int, ...], key: tuple[int, ...]) -> int:
    """Decoy cards (keyed to the tray) the student dragged into a slot."""
    return sum(1 for a, b in zip(s, key) if b == 0 and a != 0)


def decoy_zones(key: tuple[int, ...], n_zones: int) -> tuple[int, ...]:
    """Slots 1..R the key leaves empty (no card keyed there)."""
    occupied = set(key)
    return tuple(i for i in range(1, n_zones + 1) if i not in occupied)


def decoy_target_leak(
    s: tuple[int, ...], key: tuple[int, ...], n_zones: int
) -> int:
    """Cards the student dropped into a slot the key leaves empty."""
    empty = set(decoy_zones(key, n_zones))
    return sum(1 for a in s if a in empty)


def confusion_matrix(
    s: tuple[int, ...], key: tuple[int, ...], n_zones: int
) -> list[list[int]]:
    """
    The slot confusion matrix N (the slot-block submatrix of
    ``subsec-partition-confusion-table``): ``N[r-1][r'-1]`` is the number of
    cards the student put in slot ``r`` (1..R) that the key assigns to slot
    ``r'`` (1..R). The tray is excluded, only placed-and-keyed cards.
    """
    matrix = [[0] * n_zones for _ in range(n_zones)]
    for a, b in zip(s, key):
        if 1 <= a <= n_zones and 1 <= b <= n_zones:
            matrix[a - 1][b - 1] += 1
    return matrix


def max_assignment(matrix: list[list[int]]) -> int:
    """
    The best diagonal sum over all relabelings of the slots: ``max_π Σ_r
    N[r][π(r)]``. R is tiny, so this is an exact bitmask DP over column
    subsets (O(R·2^R)), no scipy, and exact for any plausible R.
    """
    n = len(matrix)
    if n == 0:
        return 0
    neg = -1
    best = [neg] * (1 << n)
    best[0] = 0
    for mask in range(1 << n):
        if best[mask] == neg:
            continue
        row = mask.bit_count()  # Python ≥ 3.11
        if row == n:
            continue
        for col in range(n):
            if not (mask >> col) & 1:
                candidate = best[mask] + matrix[row][col]
                nxt = mask | (1 << col)
                if candidate > best[nxt]:
                    best[nxt] = candidate
    return best[(1 << n) - 1]


def sigma_tilde(s: tuple[int, ...], key: tuple[int, ...], n_zones: int) -> int:
    """
    Label-free agreement ``max_π ⟨P_π X, X*⟩``: cards correctly in the tray
    (the tray row is never relabeled) plus the best slot matching. σ̃ ≥ σ.
    """
    tray_correct = sum(1 for a, b in zip(s, key) if a == 0 and b == 0)
    return tray_correct + max_assignment(confusion_matrix(s, key, n_zones))


def score_configuration(
    s: tuple[int, ...], key: tuple[int, ...], n_zones: int
) -> CheckScore:
    """Compute every metric for one checked configuration against the key."""
    return CheckScore(
        sigma=sigma(s, key),
        distance=distance(s, key),
        g=g(s, key),
        sigma_tilde=sigma_tilde(s, key, n_zones),
        per_card=per_card(s, key),
        all_or_nothing=all_or_nothing(s, key),
        decoy_source_leak=decoy_source_leak(s, key),
        decoy_target_leak=decoy_target_leak(s, key, n_zones),
        w=w(s, key),
        u=u(s, key),
        wrong_slot=wrong_slot(s, key),
        per_card_score=per_card_score(s, key),
    )


# ---------------------------------------------------------------------------
# The DB-touching writer
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScoreResult:
    """Summary of one scoring pass."""

    questions: int  # questions with a board that were scored
    scored: int  # check rows scored
    correct: int  # of those, all-or-nothing correct
    skipped: int  # checks with no decodable Input (parse_ok = 0)
    run_id: int  # the pass's pipeline_runs provenance row (F22)


def score_checks(
    conn: sqlite3.Connection, question_id: str | None = None
) -> ScoreResult:
    """
    Score every parseable check of every question that has a board (or just
    `question_id`), writing one ``dnd_check_scores`` row per check. The scored
    configuration is the check's logged ``Input`` (authoritative, rule 6);
    ``attempt_id`` is linked from the reconstructed check config when present.

    A question's scores are cleared and rebuilt. Every pass records a
    ``pipeline_runs`` row carrying its configuration (F22); the pass is a
    single transaction, so the run row commits with the scores it describes.
    """
    if question_id is not None:
        questions = [question_id] if load_board(conn, question_id) else []
        if not questions:
            log.warning("%s: no board; cannot score (run sync-boards)", question_id)
    else:
        questions = [
            row["question_id"]
            for row in conn.execute(
                "SELECT question_id FROM dnd_boards ORDER BY question_id"
            )
        ]

    n_questions = scored = correct = skipped = 0
    with transaction(conn):
        run_id = runs.start_stage_run(
            conn,
            service="dnd",
            stage="score",
            config={
                "stage_version": STAGE_VERSION,
                "question_id": question_id,
                # dnd/0014: the identifier-alias set the boards were bound
                # through (None when no alias is registered).
                "alias_set_sha256": aliases.alias_set_sha256(conn),
            },
        )
        for q in questions:
            spec = load_board(conn, q)
            if spec is None:
                continue
            n_questions += 1
            # The item's logged identifiers: the board's own and every alias
            # retired to it (dnd/0014). Each is scored against this board;
            # labels are parsed and rows are written under the logged one.
            for logged in aliases.item_ids(conn, q):
                conn.execute(
                    "DELETE FROM dnd_check_scores WHERE question_id = ?", (logged,)
                )
                for event in conn.execute(
                    "SELECT d.event_id, d.configuration_json FROM dnd_events d "
                    "JOIN events e USING (event_id) "
                    "WHERE e.problem_name = ? AND d.kind = 'check' "
                    "AND d.parse_ok = 1 AND d.configuration_json IS NOT NULL "
                    # The F2 sort key: numeric-cast problem_view breaks
                    # timestamp ties before the rowid (file-order) fallback.
                    "ORDER BY e.timestamp, CAST(e.problem_view AS INTEGER), e.rowid",
                    (logged,),
                ):
                    decoded = decode.decode_configuration(
                        json.loads(event["configuration_json"]), logged, spec.n_cards
                    )
                    score = score_configuration(decoded.s, spec.key, spec.n_zones)
                    attempt_id = _attempt_for_check(conn, event["event_id"])
                    _write_score(conn, event["event_id"], logged, attempt_id, score)
                    scored += 1
                    correct += score.all_or_nothing
                skipped += conn.execute(
                    "SELECT count(*) AS n FROM dnd_events d JOIN events e "
                    "USING (event_id) WHERE e.problem_name = ? AND d.kind = 'check' "
                    "AND d.parse_ok = 0",
                    (logged,),
                ).fetchone()["n"]
        runs.complete_run(
            conn, run_id,
            notes=(
                f"questions={n_questions} scored={scored} correct={correct} "
                f"skipped={skipped}"
            ),
        )

    log.info(
        "DnD score: %d question(s), %d check(s) scored (%d correct), %d skipped",
        n_questions, scored, correct, skipped,
    )
    return ScoreResult(
        questions=n_questions, scored=scored, correct=correct, skipped=skipped,
        run_id=run_id,
    )


def _attempt_for_check(conn: sqlite3.Connection, event_id: str) -> int | None:
    """The attempt a check belongs to, from its reconstructed config row."""
    row = conn.execute(
        "SELECT attempt_id FROM dnd_configurations "
        "WHERE event_id = ? AND is_check = 1",
        (event_id,),
    ).fetchone()
    return row["attempt_id"] if row is not None else None


def _write_score(
    conn: sqlite3.Connection,
    event_id: str,
    question_id: str,
    attempt_id: int | None,
    score: CheckScore,
) -> None:
    # Persists the scoring variants and the F12 error-profile components
    # (score.u stored under its journal name, `omissions`); score.w stays
    # computed-only, it is the overlapping sum wrong_slot + decoy_source_leak.
    conn.execute(
        "INSERT INTO dnd_check_scores (event_id, attempt_id, question_id, "
        "sigma, distance, g, sigma_tilde, per_card_json, all_or_nothing, "
        "decoy_source_leak, decoy_target_leak, omissions, wrong_slot, "
        "per_card_score) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            event_id,
            attempt_id,
            question_id,
            score.sigma,
            score.distance,
            score.g,
            score.sigma_tilde,
            json.dumps(list(score.per_card)),
            score.all_or_nothing,
            score.decoy_source_leak,
            score.decoy_target_leak,
            score.u,
            score.wrong_slot,
            score.per_card_score,
        ),
    )
