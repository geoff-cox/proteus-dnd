"""
QC flag vocabulary + feature readiness (alignment item F17b).

The journal's processing chapter prescribes an explicit reconstruction
status: per-attempt quality-control flags stored rather than handled through
informal notes (tab-quality-control-flags), and a three-level readiness
status computed from the flags, never from the student outcome
(subsec-feature-readiness). This module is the single home of that
vocabulary and of the readiness policy; :mod:`reconstruct` assembles each
attempt's flag set while replaying it (every per-row signal exists by then)
and persists both columns on ``dnd_attempts`` (migration ``dnd/0003``).
Since migration ``dnd/0012`` this module is also the home of the per-check
**anchor-eligibility cause** vocabulary (``ANCHOR_CAUSE_ORDER`` below; seven
names since dnd/0015): the one name reconstruct stores on a check it could
not treat as an eligible anchor. Pure functions, no database access.

Flags emitted today, and the per-row signal each rolls up:

* ``replay_ok``: every check anchor in the attempt agrees with the replay
  (all ``matches_input = 1``; an attempt with no comparable check cannot
  claim it). A check that is not a measurement under tier (b) below
  (``resumed_attempt``, ``no_move_logging``) is stored with a NULL
  comparison but still enters this rollup through its *shadow* comparison
  (the replay-versus-payload test made exactly as before), so no attempt's
  readiness moved when the two causes arrived (owner ruling 2026-10-06 at
  the code brief's Checkpoint 1 C).
* ``check_mismatch``: at least one checked state disagrees with the
  replayed state (``matches_input = 0``, B3.2), or a tier-(b) check whose
  shadow comparison disagrees.
* ``parse_error``: at least one event payload in the segment failed to
  parse (``dnd_events.parse_ok = 0``).
* ``row_order_ambiguous``: consecutive rows tie on
  ``(timestamp, problem_view)``, so file order decided the replay order
  (the F2 scan, applied to the attempt's own segment).
* ``invalid_state``: a state violates the state model
  (subsec-state-validity-checks): a check payload is not an eligible anchor
  for a decode-side cause (``decode.DecodedConfiguration.cause``: one card
  in two zones (the case the audit found silently resolved) a card or
  zone label off the item map, or a slot index outside 1..R), or a
  parseable move names an off-board card/slot (skipped in replay).
* ``noop_move``: at least one drop re-targeted the card's current zone
  (``is_noop``, F1). Informational: the row is flagged, replayed, and
  excluded from move counts downstream, so it does not demote readiness.
* ``duplicate_row``: consecutive rows repeat (timestamp, kind, payload).
  Byte-identical duplicates are already dropped at ingest (the event-id
  content hash), so this catches re-logged rows that differ only in
  ``problem_view``.
* ``session_break``: a session or page boundary survives inside the
  attempt (reachable only under non-default segmentation configs; the
  defaults split attempts at those boundaries).
* ``missing_source_map``: no synced board: the key and the label map are
  unavailable, so replay ran with an inferred card count.
* ``long_idle_gap``: an action gap in the attempt's trace exceeds the
  *analytic* idle threshold (F9; ``DEFAULT_IDLE_THRESHOLD_S`` below), which
  the journal keeps distinct from the segmentation gap threshold
  (subsec-action-gaps-processing). A warning, not informational: the replay
  is intact but elapsed-time features are suspect, so readiness reads
  ``limited``.

In the vocabulary but awaiting its signal (kept in ``FLAG_ORDER`` so this
stays the one place to extend):

* ``unknown_event_type``: needs the ``unknown`` event kind, parked with F5.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

__all__ = [
    "DEFAULT_IDLE_THRESHOLD_S",
    "FLAG_ORDER",
    "ANCHOR_CAUSE_ORDER",
    "CAUSE_NO_MOVE_LOGGING",
    "CAUSE_RESUMED_ATTEMPT",
    "CAUSE_MALFORMED",
    "CAUSE_DUPLICATE_CARD",
    "CAUSE_UNKNOWN_CARD",
    "CAUSE_UNKNOWN_ZONE",
    "CAUSE_OFFBOARD_SLOT",
    "READY",
    "LIMITED",
    "EXCLUDE",
    "encode_flags",
    "readiness",
]

# The analytic idle threshold δ_max (subsec-action-gaps-processing): the gap
# beyond which time is presumed idle rather than active work. Deliberately
# distinct from SegmentationConfig.gap_seconds (the attempt boundary), a gap
# can be far too long to count as active time yet far too short to end the
# sitting. The single default here keeps the two consumers agreeing: the
# reconstruct-side `long_idle_gap` flag / active-work segments and the
# analyze-side `active_time` computation, each recording the value it used
# (F22 run config; `dnd_attempt_metrics.idle_threshold_s`). The value is an
# unsettled analytic choice (the journal only insists it be *reported*), so
# it is configuration with a conservative default per rule 11.
# TODO(open-todos §2): revisit the default δ_max (and whether it deserves a
# CLI surface) when the first timing analysis lands.
DEFAULT_IDLE_THRESHOLD_S = 300.0

# The canonical vocabulary, in storage/display order (tab-quality-control-flags).
FLAG_ORDER: tuple[str, ...] = (
    "replay_ok",
    "check_mismatch",
    "parse_error",
    "unknown_event_type",
    "missing_source_map",
    "row_order_ambiguous",
    "invalid_state",
    "noop_move",
    "duplicate_row",
    "session_break",
    "long_idle_gap",
)

# The per-check anchor-eligibility causes (paper1-decisions.md, 2026-09-28
# "Anchor eligibility, FROZEN", amended 2026-10-05 to two tiers;
# cc-task-anchor-eligibility, cc-task-resumed-anchors). A check is an
# *eligible anchor* when (a) its Input payload decodes to a unique,
# board-valid configuration under the synced item specification and (b) the
# replay it would be compared against is a measurement: the attempt is not
# resumed and the check was logged on or after the calendar date its
# offering began logging moves. Only eligible anchors are compared with the
# replay, so they are the denominator of every agreement rate. Any other
# check is stored by reconstruct as not comparable
# (``dnd_configurations.matches_input`` NULL) with exactly one cause here
# (``dnd_configurations.anchor_cause``, migrations dnd/0012 and dnd/0015,
# whose CHECK constraint mirrors this tuple). In precedence order, a check
# failing more than one test stores the first applicable name; the rest are
# logged:
#
# * ``no_move_logging``: structural (tier b): the check's calendar date
#   precedes its import's ``move_logging_from`` date in
#   ``dnd_logging_regimes``, so no move row of its attempt could have been
#   logged and the replay is ``s_0`` by construction. Computed from the
#   timestamp and the regime table only.
# * ``resumed_attempt``: structural (tier b): the attempt has a prior
#   attempt by the same student on the same logged identifier
#   (``dnd_attempts.prior_attempt_id``), whose last checked board the
#   platform restored without logging a move; every check of such an
#   attempt carries the cause while seeding is off (D3.2 knob 10 seeds the
#   replay instead and does not apply it). Computed from student, question
#   and the attempt sequence only.
# * ``malformed``: the payload never reached the decoder: empty, not JSON,
#   or not an object of zone label → card-label list (``dnd_events.parse_ok
#   = 0``; the attempt carries ``parse_error``).
# * ``duplicate_card``: one card listed under two zones.
# * ``unknown_card``: a card label outside the positional grammar, or with
#   an index outside 1..P.
# * ``unknown_zone``: a zone label outside the positional grammar.
# * ``offboard_slot``: a slot index outside 1..R, whether or not a card
#   sits in it. Testable only with a synced board; without one the index is
#   taken at face value, as in replay.
#
# The last four are the decode-side causes (``decode.DecodedConfiguration
# .cause``) and each raises ``invalid_state`` on the attempt. The two
# tier-(b) causes raise no flag of their own: the check's shadow comparison
# and its decode outcome feed the flag rollup exactly as they did before the
# causes existed, so ``replay_ok``, ``check_mismatch``, ``parse_error`` and
# ``invalid_state`` (and with them readiness) are untouched.
CAUSE_NO_MOVE_LOGGING = "no_move_logging"
CAUSE_RESUMED_ATTEMPT = "resumed_attempt"
CAUSE_MALFORMED = "malformed"
CAUSE_DUPLICATE_CARD = "duplicate_card"
CAUSE_UNKNOWN_CARD = "unknown_card"
CAUSE_UNKNOWN_ZONE = "unknown_zone"
CAUSE_OFFBOARD_SLOT = "offboard_slot"
ANCHOR_CAUSE_ORDER: tuple[str, ...] = (
    CAUSE_NO_MOVE_LOGGING,
    CAUSE_RESUMED_ATTEMPT,
    CAUSE_MALFORMED,
    CAUSE_DUPLICATE_CARD,
    CAUSE_UNKNOWN_CARD,
    CAUSE_UNKNOWN_ZONE,
    CAUSE_OFFBOARD_SLOT,
)

# The three-level feature-readiness status (subsec-feature-readiness).
READY = "ready"
LIMITED = "limited"
EXCLUDE = "exclude"

# Flags an attempt may carry and still be `ready`: replay_ok is the positive
# anchor signal, and noop_move is informational (F1 flags, replays, and
# excludes the row downstream, the state model holds and anchors agree).
_READY_TOLERATED = frozenset({"replay_ok", "noop_move"})

# Flags that make key-based or checked-state features unavailable/unreliable
# for the whole attempt (the journal's `exclude` examples: a missing item
# map, malformed check states).
_EXCLUDING = frozenset({"missing_source_map", "invalid_state"})


def encode_flags(flags: Iterable[str]) -> str:
    """The persisted form: a JSON array in canonical vocabulary order.

    Unknown flag names are rejected loudly, the vocabulary lives here, and a
    typo silently dropped from storage would defeat the whole discipline.
    """
    flag_set = set(flags)
    unknown = flag_set - set(FLAG_ORDER)
    if unknown:
        raise ValueError(f"unknown QC flag(s): {sorted(unknown)}")
    return json.dumps([f for f in FLAG_ORDER if f in flag_set])


def readiness(flags: Iterable[str]) -> str:
    """
    The subsec-feature-readiness status for one attempt, from its flags only
    (never from the student outcome, so difficult responses are not excluded
    for being analytically inconvenient):

    * ``exclude``: the attempt lacks what the baseline features need: no
      item map (key-based features) or an invalid state (checked-response
      features).
    * ``ready``: the item map is available, parsing succeeded, and every
      check anchor agrees with the replay (``replay_ok``), with nothing else
      flagged beyond the informational ``noop_move``.
    * ``limited``: everything else: warning flags are present, or there is
      no check anchor to verify the replay against; some features can still
      be computed.
    """
    flag_set = set(flags)
    if flag_set & _EXCLUDING:
        return EXCLUDE
    if "replay_ok" in flag_set and flag_set <= _READY_TOLERATED:
        return READY
    return LIMITED
