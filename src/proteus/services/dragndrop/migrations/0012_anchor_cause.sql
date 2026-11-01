-- =============================================================================
-- DnD service migration 0012: the per-check anchor-eligibility cause
-- (cc-task-anchor-eligibility; paper1-decisions.md, 2026-09-28 "Anchor
-- eligibility, FROZEN").
--
-- A check is an *eligible anchor* when its Input payload decodes to a unique,
-- board-valid configuration under the synced item specification: every card
-- and zone label decodes to an index, no card is listed in two zones, and
-- every slot index lies in 1..R. Only eligible anchors are compared with the
-- replay (dnd_configurations.matches_input 1/0), so they are the denominator
-- of every agreement rate. Any other check is stored as not comparable,
-- matches_input NULL, with exactly one cause here, so the Results can report
-- the non-eligible checks by cause:
--
--   dnd_configurations.anchor_cause   NULL on every non-check row and on every
--                                     eligible check; on a non-eligible check
--                                     one of (precedence order; the first
--                                     applicable name is stored):
--                                       'malformed'      payload empty, not
--                                                        JSON, or not an
--                                                        object (parse_ok = 0;
--                                                        flag parse_error)
--                                       'duplicate_card' one card in two zones
--                                       'unknown_card'   card label off the
--                                                        grammar or outside
--                                                        1..P
--                                       'unknown_zone'   zone label off the
--                                                        grammar
--                                       'offboard_slot'  slot index outside
--                                                        1..R (only testable
--                                                        with a board)
--                                     The last four flag invalid_state on the
--                                     attempt. The vocabulary's single home is
--                                     dragndrop/qc.py (ANCHOR_CAUSE_ORDER);
--                                     this CHECK mirrors it.
--
-- Nullable with no default: a NULL on a check row written by an older
-- reconstruct (stage version < 3) means "not classified", re-run
-- `proteus dnd reconstruct`. Before this migration, a check that failed any
-- test above was still compared on a best-effort decode (a duplicate card
-- kept its first zone, an unknown label was dropped, an off-board slot was
-- taken at face value), so its matches_input could read 1 or 0; the stage
-- version bump (reconstruct v3) marks that boundary in pipeline_runs. Same
-- posture as dnd/0002..0011.
-- =============================================================================

BEGIN;

ALTER TABLE dnd_configurations ADD COLUMN anchor_cause TEXT
    CHECK (anchor_cause IN (
        'malformed', 'duplicate_card', 'unknown_card', 'unknown_zone',
        'offboard_slot'
    ));

COMMIT;
