"""
Board/data cross-validation (plan items B2.3/B2.4).

Two independent checks per question, both aimed at the failure modes most
likely to silently corrupt every downstream score (plan R4):

**Key cross-validation (B2.3).** The key inferred from the data — the
configurations of check rows graded ``Correct`` that are *eligible anchors*
(the frozen definition of :mod:`~proteus.services.dragndrop.qc`: a malformed
payload or an invalid decode is never a key witness) — must equal the key
built from the question source. A mismatch means a stale source snapshot or
a positional-indexing bug; more than one distinct "correct" configuration is
just as loud (a cardsort grades against one exact key). Questions with no
eligible Correct check yet simply cannot be validated and are reported as
such; the non-eligible Correct checks are counted beside the result.

**Label coverage (B2.4).** Every ``drag#``/``drop#`` index seen in the
derived events for a question must exist on its board. Gaps usually mean the
question was revised between the export and the synced source. A question
with derived events but no board at all is reported as ``no_board``.

Pure readers — nothing here writes to the database; the CLI surfaces the
report and exits non-zero when anything fails.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass

from proteus.services.dragndrop import aliases, decode
from proteus.services.dragndrop.board import BoardSpec, load_board

log = logging.getLogger(__name__)

KEY_MATCH = "match"
KEY_MISMATCH = "mismatch"
KEY_AMBIGUOUS = "ambiguous"  # multiple distinct Correct configurations
KEY_UNVALIDATED = "no_correct_checks"
NO_BOARD = "no_board"


@dataclass(frozen=True)
class QuestionValidation:
    """The validation outcome for one question."""

    question_id: str
    key_status: str  # KEY_* or NO_BOARD
    source_key: tuple[int, ...] | None
    inferred_keys: tuple[tuple[int, ...], ...]  # distinct, from eligible Correct checks
    n_correct_checks: int  # eligible Correct checks the inference used
    label_gaps: tuple[str, ...]  # labels seen in events, absent from the board
    n_ineligible_correct_checks: int = 0  # Correct checks skipped: not eligible anchors
    # The board's identifier when this question_id is a registered alias
    # (dnd/0014): the key and the labels were validated against the
    # canonical board, with labels parsed under the logged identifier.
    canonical_question_id: str | None = None

    @property
    def ok(self) -> bool:
        return (
            self.key_status in (KEY_MATCH, KEY_UNVALIDATED)
            and not self.label_gaps
        )


def validate_all(
    conn: sqlite3.Connection, question_id: str | None = None
) -> list[QuestionValidation]:
    """
    Validate every question that has a board or derived (non-page) DnD
    events — the union catches both unsynced questions with data and synced
    questions without data. `question_id` restricts to one question.
    """
    if question_id is not None:
        questions = [question_id]
    else:
        with_board = {
            row["question_id"]
            for row in conn.execute("SELECT question_id FROM dnd_boards")
        }
        with_events = {
            row["problem_name"]
            for row in conn.execute(
                "SELECT DISTINCT e.problem_name FROM dnd_events d "
                "JOIN events e USING (event_id) WHERE d.kind != 'page_view'"
            )
        }
        questions = sorted(with_board | with_events)
    return [validate_question(conn, q) for q in questions]


def validate_question(
    conn: sqlite3.Connection, question_id: str
) -> QuestionValidation:
    spec = load_board(conn, question_id)
    canonical = aliases.resolve_question_id(conn, question_id)
    alias_of = canonical if canonical != question_id else None
    inferred, n_correct, n_ineligible = _infer_keys_from_correct_checks(
        conn, question_id,
        spec.n_cards if spec else None,
        spec.n_zones if spec else None,
    )
    if n_ineligible:
        log.warning(
            "%s: %d Correct check(s) are not eligible anchors (malformed or "
            "invalid payload) and were ignored for key inference",
            question_id, n_ineligible,
        )

    if spec is None:
        result = QuestionValidation(
            question_id=question_id,
            key_status=NO_BOARD,
            source_key=None,
            inferred_keys=inferred,
            n_correct_checks=n_correct,
            label_gaps=(),
            n_ineligible_correct_checks=n_ineligible,
            canonical_question_id=alias_of,
        )
        if alias_of is not None:
            log.warning(
                "%s: alias of %s, which has no board — run "
                "`proteus sync-sources` / `proteus dnd sync-boards`",
                question_id, alias_of,
            )
            return result
        # validate_all reaches NO_BOARD only for questions that *do* have
        # derived events; but validate_question is also called directly
        # (`proteus dnd validate --question <id>`), where a typoed or
        # not-yet-ingested id has neither board nor events. Tailor the
        # warning so the latter case isn't told it "derived DnD events".
        if _has_derived_events(conn, question_id):
            log.warning(
                "%s: derived DnD events but no board — run "
                "`proteus sync-sources` / `proteus dnd sync-boards`",
                question_id,
            )
        else:
            log.warning(
                "%s: no board and no derived DnD events — unknown or "
                "not-yet-ingested question_id?", question_id,
            )
        return result

    if n_correct == 0:
        key_status = KEY_UNVALIDATED
    elif len(inferred) > 1:
        key_status = KEY_AMBIGUOUS
    elif inferred[0] == spec.key:
        key_status = KEY_MATCH
    else:
        key_status = KEY_MISMATCH

    if key_status in (KEY_MISMATCH, KEY_AMBIGUOUS):
        # Loud by design (B2.3): this is the stale-source / positional-
        # indexing-bug detector, and everything downstream scores against
        # the key.
        log.error(
            "%s: KEY %s — source key %s, inferred from %d Correct check(s): "
            "%s. Likely a stale source snapshot or a positional-indexing "
            "bug; do not trust scores until resolved.",
            question_id, key_status.upper(), spec.key, n_correct,
            list(inferred),
        )

    gaps = _label_gaps(conn, question_id, spec)
    if gaps:
        log.warning(
            "%s: %d label(s) in the data are absent from the board "
            "(question revised between export and synced source?): %s",
            question_id, len(gaps), gaps,
        )

    return QuestionValidation(
        question_id=question_id,
        key_status=key_status,
        source_key=spec.key,
        inferred_keys=inferred,
        n_correct_checks=n_correct,
        label_gaps=gaps,
        n_ineligible_correct_checks=n_ineligible,
        canonical_question_id=alias_of,
    )


def _has_derived_events(conn: sqlite3.Connection, question_id: str) -> bool:
    """Whether the question has any non-page derived DnD event — the same
    membership `validate_all` uses to enumerate event-bearing questions."""
    row = conn.execute(
        "SELECT 1 FROM dnd_events d JOIN events e USING (event_id) "
        "WHERE e.problem_name = ? AND d.kind != 'page_view' LIMIT 1",
        (question_id,),
    ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# B2.3: key inference from Correct checks
# ---------------------------------------------------------------------------


def _infer_keys_from_correct_checks(
    conn: sqlite3.Connection,
    question_id: str,
    n_cards: int | None,
    n_zones: int | None,
) -> tuple[tuple[tuple[int, ...], ...], int, int]:
    """
    Distinct state vectors among the Correct-graded check rows that are
    eligible anchors, plus the count of those rows and the count of Correct
    rows skipped as non-eligible: a malformed payload (``parse_ok = 0``, or
    otherwise not a JSON object — the ``malformed`` cause) is counted
    without decoding; an invalid decode is counted by its ``cause``. With no
    board (n_cards unknown), P is taken as the highest drag index seen among
    the decodable rows and no slot bound applies — enough to report inferred
    keys for the no_board case, mirroring replay.
    """
    rows = conn.execute(
        "SELECT d.configuration_json, d.parse_ok FROM dnd_events d "
        "JOIN events e USING (event_id) "
        "WHERE e.problem_name = ? AND d.kind = 'check' "
        "AND d.feedback = 'Correct' ORDER BY e.timestamp",
        (question_id,),
    ).fetchall()

    configurations: list[dict] = []
    n_ineligible = 0
    for row in rows:
        if not row["parse_ok"] or not row["configuration_json"]:
            n_ineligible += 1  # malformed: never reaches the decoder
            continue
        try:
            configuration = json.loads(row["configuration_json"])
        except json.JSONDecodeError:
            n_ineligible += 1
            continue
        if not isinstance(configuration, dict):
            n_ineligible += 1
            continue
        configurations.append(configuration)
    if n_cards is None:
        n_cards = max(
            (
                parsed.index
                for configuration in configurations
                for cards in configuration.values()
                if isinstance(cards, list)
                for label in cards
                if (parsed := decode.parse_label(str(label), question_id))
                is not None and parsed.kind == "drag"
            ),
            default=0,
        )

    distinct: list[tuple[int, ...]] = []
    n_eligible = 0
    for configuration in configurations:
        decoded = decode.decode_configuration(
            configuration, question_id, n_cards, n_zones
        )
        if decoded.cause is not None:
            n_ineligible += 1
            continue
        n_eligible += 1
        if decoded.s not in distinct:
            distinct.append(decoded.s)
    return tuple(distinct), n_eligible, n_ineligible


# ---------------------------------------------------------------------------
# B2.4: every label seen must exist on the board
# ---------------------------------------------------------------------------


def _label_gaps(
    conn: sqlite3.Connection, question_id: str, spec: BoardSpec
) -> tuple[str, ...]:
    """Distinct labels in this question's derived events with no board entry."""
    gaps: set[str] = set()

    for row in conn.execute(
        "SELECT d.source_label, d.zone_label FROM dnd_events d "
        "JOIN events e USING (event_id) "
        "WHERE e.problem_name = ? AND d.kind IN ('move', 'takeback') "
        "AND d.parse_ok = 1",
        (question_id,),
    ):
        if not _is_known_card(row["source_label"], question_id, spec):
            gaps.add(row["source_label"])
        if not _is_known_zone(row["zone_label"], question_id, spec):
            gaps.add(row["zone_label"])

    for row in conn.execute(
        "SELECT d.configuration_json FROM dnd_events d "
        "JOIN events e USING (event_id) "
        "WHERE e.problem_name = ? AND d.kind = 'check' AND d.parse_ok = 1",
        (question_id,),
    ):
        configuration = json.loads(row["configuration_json"])
        for zone_label, cards in configuration.items():
            if not _is_known_zone(str(zone_label), question_id, spec):
                gaps.add(str(zone_label))
            if not isinstance(cards, list):
                continue
            for card_label in cards:
                if not _is_known_card(str(card_label), question_id, spec):
                    gaps.add(str(card_label))
    return tuple(sorted(gaps))


def _is_known_card(label: str, question_id: str, spec: BoardSpec) -> bool:
    parsed = decode.parse_label(label, question_id)
    return (
        parsed is not None
        and parsed.kind == "drag"
        and 1 <= parsed.index <= spec.n_cards
    )


def _is_known_zone(label: str, question_id: str, spec: BoardSpec) -> bool:
    parsed = decode.parse_label(label, question_id)
    if parsed is None or parsed.kind == "drag":
        return False
    return parsed.kind == "dragzone" or 1 <= parsed.index <= spec.n_zones
