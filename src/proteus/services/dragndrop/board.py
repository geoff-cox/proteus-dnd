"""
The DnD board registry (plan items B2.1/B2.2).

A ``BoardSpec`` is everything the analysis needs to know about one cardsort
question, built from the ``<match>`` blocks of its PreTeXt source
(``questions.source_xml``, written by core sync-sources):

- **cards** (premises) indexed 1..P and **slots** (responses) indexed 1..R,
  both positionally in source order — exactly the order Runestone uses for
  the ``*_drag#`` / ``*_drop#`` data labels (reference page sec-raw-data);
  the dragzone tray is zone 0, never indexed;
- **key** s*: each premise belongs to the response in its own match block;
  a premise in a match with no response is a decoy card (home zone 0), and
  a response in a match with no premise is a decoy slot (meant to stay
  empty).

The source handler (``sync_board_for_question``) is registered with core
sync-sources and re-runnable via ``proteus dnd sync-boards``: it detects the
``<cardsort>`` element, sets ``questions.question_type``, and rebuilds the
``dnd_boards``/``dnd_board_cards``/``dnd_board_zones`` rows whenever the
source hash changes. A hash change also clears the question's stale results
(scores, configurations, attempts, metrics — F29): they were computed
against the old key/layout and must be re-reconstructed and re-scored.

``load_board`` resolves a registered identifier alias first (dnd/0014,
:mod:`~proteus.services.dragndrop.aliases`): a retired identifier binds to
its successor's board here, and nowhere else, so every stage and reader
that looks a board up is covered. A board is never built *under* an alias
identifier, and a board change clears the results of its aliases too.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from proteus.core.db import transaction
from proteus.services.dragndrop import aliases

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class BoardSpec:
    """One cardsort board: P cards, R slots (+ the zone-0 tray), key, decoys."""

    question_id: str
    cards: tuple[str, ...]  # authored content of card j at index j-1
    zones: tuple[str | None, ...]  # index 0 = dragzone (None), 1..R authored
    key: tuple[int, ...]  # s*: home zone of card j at index j-1 (0 = tray)

    @property
    def n_cards(self) -> int:
        return len(self.cards)

    @property
    def n_zones(self) -> int:
        """R — the slots only; the dragzone is zone 0, not counted."""
        return len(self.zones) - 1

    @property
    def decoy_cards(self) -> tuple[int, ...]:
        """Card indices meant to stay in the tray."""
        return tuple(j for j, zone in enumerate(self.key, start=1) if zone == 0)

    @property
    def decoy_zones(self) -> tuple[int, ...]:
        """Slot indices meant to stay empty."""
        occupied = set(self.key)
        return tuple(i for i in range(1, self.n_zones + 1) if i not in occupied)


def _inner_xml(element: ET.Element) -> str:
    """An element's authored content: text plus serialized child markup.

    ``ET.tostring(child)`` includes that child's *tail* — the text between
    it and the next sibling (or the parent's close tag) — so mixed content
    like ``foo <em>bar</em> baz`` round-trips in full (the ``baz`` tail is
    not lost). Do NOT additionally append ``child.tail`` here; that would
    duplicate every tail.
    """
    parts = [element.text or ""]
    for child in element:
        parts.append(ET.tostring(child, encoding="unicode"))
    return "".join(parts).strip()


def board_from_source(question_id: str, source_xml: str) -> BoardSpec | None:
    """
    Build a BoardSpec from a question's PreTeXt source snippet, or return
    None if the snippet contains no ``<cardsort>`` (not a DnD question).

    Raises ValueError on a snippet that *is* a cardsort but is malformed:
    unparseable XML, a match holding more than one response, or an empty
    board. Positional indexing is document order across all match blocks.
    """
    try:
        root = ET.fromstring(source_xml)
    except ET.ParseError as exc:
        raise ValueError(f"{question_id}: source_xml is not parseable: {exc}")

    cardsort = root if root.tag == "cardsort" else root.find(".//cardsort")
    if cardsort is None:
        return None

    cards: list[str] = []
    zones: list[str | None] = [None]  # zone 0, the dragzone
    key: list[int] = []
    for match in cardsort.findall("match"):
        premises = match.findall("premise")
        responses = match.findall("response")
        if len(responses) > 1:
            raise ValueError(
                f"{question_id}: a <match> holds {len(responses)} <response> "
                f"elements; the cardsort structure allows at most one"
            )
        if not premises and not responses:
            log.warning("%s: empty <match> block ignored", question_id)
            continue
        if responses:
            zones.append(_inner_xml(responses[0]))
            home_zone = len(zones) - 1
        else:
            home_zone = 0  # decoy cards stay in the tray
        for premise in premises:
            cards.append(_inner_xml(premise))
            key.append(home_zone)

    if not cards:
        raise ValueError(f"{question_id}: cardsort defines no <premise> cards")
    return BoardSpec(
        question_id=question_id,
        cards=tuple(cards),
        zones=tuple(zones),
        key=tuple(key),
    )


def source_fingerprint(source_xml: str) -> str:
    """The provenance hash stored on dnd_boards (rebuild trigger, B2.1)."""
    return hashlib.sha256(source_xml.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def write_board(
    conn: sqlite3.Connection, spec: BoardSpec, source_hash: str
) -> None:
    """Replace the stored board for spec.question_id (idempotent rebuild)."""
    with transaction(conn):
        for table in ("dnd_board_cards", "dnd_board_zones", "dnd_boards"):
            conn.execute(
                f"DELETE FROM {table} WHERE question_id = ?", (spec.question_id,)
            )
        conn.execute(
            "INSERT INTO dnd_boards (question_id, source_hash, n_cards, "
            "n_zones, key_json) VALUES (?, ?, ?, ?, ?)",
            (
                spec.question_id,
                source_hash,
                spec.n_cards,
                spec.n_zones,
                json.dumps(list(spec.key)),
            ),
        )
        for j, content in enumerate(spec.cards, start=1):
            conn.execute(
                "INSERT INTO dnd_board_cards (question_id, card_index, "
                "content, home_zone) VALUES (?, ?, ?, ?)",
                (spec.question_id, j, content, spec.key[j - 1]),
            )
        decoy_zones = set(spec.decoy_zones)
        for i, content in enumerate(spec.zones):
            conn.execute(
                "INSERT INTO dnd_board_zones (question_id, zone_index, "
                "content, is_decoy) VALUES (?, ?, ?, ?)",
                (spec.question_id, i, content, 1 if i in decoy_zones else 0),
            )


def load_board(conn: sqlite3.Connection, question_id: str) -> BoardSpec | None:
    """
    The stored BoardSpec for a question, or None if no board exists.

    A registered alias identifier (dnd/0014) is resolved to its canonical
    identifier first — the one place the service binds a retired
    identifier to its successor's board — so validate, reconstruct, score,
    analyze, the cohort and trajectory readers and the exports all read the
    right board without keeping their own join. The returned spec carries
    the canonical ``question_id``. Labels are still parsed with the logged
    identifier by every caller: the positional grammar anchors on it.
    """
    question_id = aliases.resolve_question_id(conn, question_id)
    board = conn.execute(
        "SELECT key_json FROM dnd_boards WHERE question_id = ?",
        (question_id,),
    ).fetchone()
    if board is None:
        return None
    cards = [
        row["content"]
        for row in conn.execute(
            "SELECT content FROM dnd_board_cards WHERE question_id = ? "
            "ORDER BY card_index",
            (question_id,),
        )
    ]
    zones = [
        row["content"]
        for row in conn.execute(
            "SELECT content FROM dnd_board_zones WHERE question_id = ? "
            "ORDER BY zone_index",
            (question_id,),
        )
    ]
    return BoardSpec(
        question_id=question_id,
        cards=tuple(cards),
        zones=tuple(zones),
        key=tuple(json.loads(board["key_json"])),
    )


# ---------------------------------------------------------------------------
# The source handler (B2.2)
# ---------------------------------------------------------------------------


def sync_board_for_question(
    conn: sqlite3.Connection, question_row: sqlite3.Row | dict
) -> str:
    """
    The DnD source_handler (plan §2.2 contract): build/refresh one question's
    board from its source_xml.

    Returns a status string: 'created' / 'updated' / 'unchanged', or
    'skipped' when the question carries no source or no <cardsort>. A
    malformed cardsort raises ValueError — the dispatcher (and the
    sync-boards CLI) contains it per question and counts it as an error.

    On a board-hash change, results computed against the old board — the
    question's check scores, reconstructed configurations, attempts, and
    attempt metrics — are cleared so they cannot read as current (F29): the
    key and slot layout they were scored against no longer exist, and the
    cleared attempts make the on-demand recompute re-reconstruct/re-score
    against the new board. When stale rows were actually cleared the status
    is 'updated_results_cleared' instead of 'updated', so both sync
    summaries (core sync-sources and `proteus dnd sync-boards`) say so.
    """
    question_id = question_row["question_id"]
    source_xml = question_row["source_xml"]
    if not source_xml:
        log.debug("%s: no source_xml; nothing to build", question_id)
        return "skipped"

    spec = board_from_source(question_id, source_xml)
    if spec is None:
        return "skipped"
    if aliases.is_alias(conn, question_id):
        # dnd/0014: a retired identifier resolves to its successor's board;
        # building one under it would make the item ambiguous. Refuse, and
        # say so in the sync summary.
        log.warning(
            "%s: a registered alias of %s — no board is built under a retired "
            "identifier (remove the alias first if the source really revived it)",
            question_id, aliases.resolve_question_id(conn, question_id),
        )
        return "alias_refused"

    # The handler, not the walk, is the authority on what a cardsort is —
    # so it also repairs rows synced before the type was known (B2.2).
    if question_row["question_type"] != "cardsort":
        conn.execute(
            "UPDATE questions SET question_type = 'cardsort' "
            "WHERE question_id = ?",
            (question_id,),
        )

    fingerprint = source_fingerprint(source_xml)
    existing = conn.execute(
        "SELECT source_hash FROM dnd_boards WHERE question_id = ?",
        (question_id,),
    ).fetchone()
    if existing is not None and existing["source_hash"] == fingerprint:
        return "unchanged"
    cleared = 0
    if existing is not None:
        # Lazy import: reconstruct imports this module (load_board), so the
        # dependency cannot be module-level. The clear runs in the same
        # implicit transaction write_board commits, so board + invalidation
        # land (or roll back) together.
        from proteus.services.dragndrop.reconstruct import clear_question_results

        # The item's results live under every logged identifier bound to
        # this board — its own and its aliases (dnd/0014).
        for logged in aliases.item_ids(conn, question_id):
            cleared += clear_question_results(conn, logged)
    write_board(conn, spec, fingerprint)
    log.info(
        "%s board %s: P=%d cards, R=%d zones, %d decoy card(s), "
        "%d decoy zone(s)",
        question_id, "updated" if existing else "created",
        spec.n_cards, spec.n_zones,
        len(spec.decoy_cards), len(spec.decoy_zones),
    )
    if existing is None:
        return "created"
    if cleared:
        log.info(
            "%s: board changed — cleared %d stale result row(s) (scores, "
            "configurations, attempts, metrics); re-run "
            "reconstruct/score/analyze",
            question_id, cleared,
        )
        return "updated_results_cleared"
    return "updated"
