-- =============================================================================
-- Migration 0008: filter columns on `events` (checklist item CD-1).
--
-- Forward-only, additive. Adds the four nullable dimensions the question-first
-- front door filters by, `class_id`, `week_no`, `chapter`, `subchapter`,
-- populated by the DataShop reader from `Class`, `CF (Week No)`,
-- `Level (Chapter)`, `Level (SubChapter)`. `course_id` (from `CF (Institution)`)
-- is unchanged. There is deliberately NO instructor column: it is not in the
-- export (checklist §0 supersession map, §7 #4 dropped).
--
-- The reconciled checklist calls this "migration 0005", written before the
-- 0005-0007 core migrations (pipeline_runs.service, question_type, source_xml)
-- had landed; the next free core version is 8.
--
-- SA-safety (the load-bearing guarantee): event identity is
--   sha256(student | problem | timestamp | selection | problem_view)
-- which excludes every column added here, so every `event_id` is unchanged and
-- the SA derive stays byte-identical, `test_sa_responses_golden` holds. These
-- are a read/view bound (CD-3, B4.10), never a compute input; B3 stays frozen.
--
-- Existing rows get NULL (additive ADD COLUMN). `proteus reingest-events`
-- backfills them from the retained `.tab`
-- (core.ingest.events.backfill_filter_columns), filling the filter columns on
-- already-stored events without re-deriving any service. New ingests populate
-- the columns directly on insert.
-- =============================================================================

BEGIN;

ALTER TABLE events ADD COLUMN class_id   TEXT;
ALTER TABLE events ADD COLUMN week_no    TEXT;
ALTER TABLE events ADD COLUMN chapter    TEXT;
ALTER TABLE events ADD COLUMN subchapter TEXT;

INSERT INTO schema_version (version, description)
    VALUES (8, 'Add events filter columns (class_id, week_no, chapter, subchapter) for the question-first front door');

COMMIT;
