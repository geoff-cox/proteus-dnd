-- =============================================================================
-- Migration 0005: add pipeline_runs.service for per-service run provenance.
--
-- Forward-only, additive (plan §2.4). Every existing run is short-answer, so
-- the column defaults to 'sa'; services tag their own runs going forward
-- (SA wiring lands in A3.5). Additive ADD COLUMN with a DEFAULT backfills
-- existing rows without a table rebuild.
-- =============================================================================

BEGIN;

ALTER TABLE pipeline_runs ADD COLUMN service TEXT NOT NULL DEFAULT 'sa';

INSERT INTO schema_version (version, description)
    VALUES (5, 'Add pipeline_runs.service (default sa) for per-service run provenance');

COMMIT;
