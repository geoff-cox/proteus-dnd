-- =============================================================================
-- Migration 0014: store the question's authored <title> on questions (FU-3).
--
-- Forward-only, additive (2026-07-03 triage decision: store the inner XML
-- verbatim). sync-sources fills the column from the naming unit's direct
-- <title> child — text plus serialized child markup, so a math-bearing title
-- like "Multiplying by <m>A</m>" is kept as authored; display layers degrade
-- it readably through the existing tag-stripper. NULL when the unit has no
-- title.
--
-- Backfill caveat: this migration adds the column only. Rows synced before it
-- keep title NULL until the next `proteus sync-sources` run repopulates them
-- (the sync detects the missing title even when the snippet is unchanged and
-- reports the rows as "titles backfilled" in its summary).
-- =============================================================================

BEGIN;

ALTER TABLE questions ADD COLUMN title TEXT;

INSERT INTO schema_version (version, description)
    VALUES (14, 'Add questions.title: verbatim inner XML of the naming unit''s <title>, written by sync-sources');

COMMIT;
