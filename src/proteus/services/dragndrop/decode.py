"""
Positional-label decoding (plan §2.5 / reference page sec-raw-data).

The raw data never uses authored card/zone names; it assigns positional
labels in source order:

    card:  <question_id>_drag<j>     (j = 1..P)
    zone:  <question_id>_drop<i>     (i = 1..R)
    tray:  the literal ``dragzone``  (zone 0, never indexed)

This module is the single home of that grammar: parsing one label, and
decoding a whole check-row configuration (the JSON object mapping each slot
to the list of cards it holds) into the minimal state vector
s = (s(1), …, s(P)) used everywhere downstream. Cards absent from the JSON
are implicitly in the dragzone (zone 0). The decode also reports everything
that keeps the payload from being an *eligible anchor*, the decode-side
causes of :mod:`~proteus.services.dragndrop.qc` (``ANCHOR_CAUSE_ORDER``).

Pure functions, no database access.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

from proteus.services.dragndrop import qc

# Where a label can point: the tray, a card, or a slot.
DRAGZONE = "dragzone"


@dataclass(frozen=True)
class ParsedLabel:
    """One decoded positional label."""

    kind: str  # 'drag' | 'drop' | 'dragzone'
    index: int  # 1-based for drag/drop; 0 for the dragzone


def parse_label(label: str, question_id: str) -> ParsedLabel | None:
    """
    Decode one raw-data label for `question_id`. Returns None for a label
    that doesn't follow the positional grammar (wrong question prefix,
    non-numeric suffix, …), callers flag those, never crash on them.
    """
    label = label.strip()
    if label == DRAGZONE:
        return ParsedLabel(kind=DRAGZONE, index=0)
    m = re.fullmatch(re.escape(question_id) + r"_(drag|drop)(\d+)", label)
    if m is None:
        return None
    index = int(m.group(2))
    # drag/drop are 1-based (drag1.., drop1..); index 0 is reserved for the
    # literal `dragzone` tray, so `*_drag0` / `*_drop0` are malformed. Reject
    # them here rather than let `drop0` slip through as an implicit zone-0
    # tray in decode_configuration (which bounds card indices but takes zone
    # indices at face value).
    if index < 1:
        return None
    return ParsedLabel(kind=m.group(1), index=index)


@dataclass(frozen=True)
class DecodedConfiguration:
    """
    A check row's configuration as the minimal state vector, plus everything
    that didn't decode cleanly, the decode-side anchor-eligibility causes.
    `unknown_cards` / `unknown_zones` are the B1.4 "orphan" case, flagged,
    never fatal (likely cause: the question was revised between the export
    and the synced source, plan B2.4); `duplicate_cards` the one-card-two-
    zones case; `offboard_slots` the slot indices beyond R, reported only
    when R was given to :func:`decode_configuration`.
    """

    s: tuple[int, ...]  # s(j) for j = 1..P; 0 = dragzone
    unknown_cards: tuple[str, ...]  # card labels off the grammar or outside 1..P
    unknown_zones: tuple[str, ...]  # zone labels off the grammar (or not a list)
    duplicate_cards: tuple[int, ...]  # card indices listed in more than one zone
    offboard_slots: tuple[int, ...] = ()  # slot indices > R, when R was given

    @property
    def unknown_labels(self) -> tuple[str, ...]:
        """Every label that did not decode: zones first, then cards."""
        return self.unknown_zones + self.unknown_cards

    @property
    def cause(self) -> str | None:
        """
        The anchor-eligibility cause this decode surfaced, or None when the
        payload decodes to a unique, board-valid configuration (an eligible
        anchor). One name, in the precedence order of
        ``qc.ANCHOR_CAUSE_ORDER``; every finding stays readable on the
        fields above.
        """
        if self.duplicate_cards:
            return qc.CAUSE_DUPLICATE_CARD
        if self.unknown_cards:
            return qc.CAUSE_UNKNOWN_CARD
        if self.unknown_zones:
            return qc.CAUSE_UNKNOWN_ZONE
        if self.offboard_slots:
            return qc.CAUSE_OFFBOARD_SLOT
        return None


def decode_configuration(
    configuration: Mapping[str, object],
    question_id: str,
    n_cards: int,
    n_zones: int | None = None,
) -> DecodedConfiguration:
    """
    Decode a parsed check-row JSON object (zone label → list of card labels)
    into s = (s(1), …, s(P)).

    Cards not mentioned anywhere sit in the dragzone (zone 0), that is the
    raw format's convention, not an error. Labels that don't decode, decode
    to the wrong kind, or carry an index outside 1..P (cards) are collected
    in `unknown_cards` / `unknown_zones`; the cards listed under an unknown
    zone stay in the tray, but their labels are still checked. A card listed
    under two different zones keeps the first zone and is reported in
    `duplicate_cards`; a card repeated within one zone's list is a single
    placement, not a finding.

    Slot indices are placed at face value. When the board's slot count
    `n_zones` is given, every slot index beyond it (occupied or not) is
    reported in `offboard_slots`, the anchor-eligibility test of
    paper1-decisions.md (2026-09-28); without a board there is no R bound,
    as in replay. The B2.4 validation reports the same gaps per question.
    """
    s = [0] * n_cards
    first_zone: dict[int, str] = {}  # card index -> the zone label that placed it
    unknown_cards: list[str] = []
    unknown_zones: list[str] = []
    duplicates: list[int] = []
    offboard: list[int] = []

    for zone_label, cards in configuration.items():
        label = str(zone_label)
        zone = parse_label(label, question_id)
        zone_known = zone is not None and zone.kind != "drag"
        if not zone_known:
            unknown_zones.append(label)
        if not isinstance(cards, list):
            if zone_known:
                unknown_zones.append(label)
            continue
        if (
            zone_known
            and zone.kind == "drop"
            and n_zones is not None
            and zone.index > n_zones
            and zone.index not in offboard
        ):
            offboard.append(zone.index)
        for card_label in cards:
            card = parse_label(str(card_label), question_id)
            if card is None or card.kind != "drag" or not (1 <= card.index <= n_cards):
                unknown_cards.append(str(card_label))
                continue
            if not zone_known:
                # A valid card under an unknown zone: not a placement (it
                # stays in the tray) and so never a duplicate; the zone is
                # already reported.
                continue
            placed_by = first_zone.get(card.index)
            if placed_by is not None:
                # A repeat. Under a different zone it is the one-card-two-
                # zones case (the first zone is kept); within the same
                # zone's list it is the same placement again.
                if placed_by != label and card.index not in duplicates:
                    duplicates.append(card.index)
                continue
            first_zone[card.index] = label
            s[card.index - 1] = zone.index
    return DecodedConfiguration(
        s=tuple(s),
        unknown_cards=tuple(unknown_cards),
        unknown_zones=tuple(unknown_zones),
        duplicate_cards=tuple(duplicates),
        offboard_slots=tuple(offboard),
    )
