-- =============================================================================
-- Migration 0015: decouple pipeline_runs from the SA-owned version tables.
--
-- Forward-only (audit task A2, resolving BUG-02). The baseline froze a
-- pre-split design decision (its recorded wart) into pipeline_runs: two
-- NOT NULL foreign keys into the SA service's vocabulary_versions/
-- codebook_versions tables. A database bootstrapped without the SA service
-- (its package broken or absent) has pipeline_runs but not the SA tables, so
-- *any* run insert fails, bricking the DnD stages and breaking the
-- registry's failure-isolation guarantee.
--
-- The decoupling needs both halves:
--   * NOT NULL dropped, so core can record a run with no vocabulary/codebook
--     version. runs.start_run/start_stage_run keep creating and recording the
--     stub 'default' versions whenever the SA tables are present, NULL
--     appears only where they are absent.
--   * The FK clauses converted to logical references. Nullability alone is
--     not enough: with foreign_keys ON, SQLite refuses ALL DML on a child
--     table whose declared parent table does not exist, even inserting NULL
--     ("no such table: main.vocabulary_versions"). Core DDL naming
--     service-owned tables was the wart; the columns now mirror the
--     baseline's own precedent of responses.question_id -> questions
--     ("deliberately logical, not a foreign key"). The version tables are
--     append-only by convention (rows are never deleted), and the ids are
--     written exclusively by runs.ensure_*_version, so the enforcement given
--     up here guarded no real write path.
--
-- SQLite cannot drop a column constraint in place, so this rebuilds the table
-- (the documented 12-step procedure, as migration 0009 did for questions).
-- The rebuilt table is the baseline shape plus the columns migrations added:
-- service (0005) and stage/config_json (0013). `foreign_keys` is toggled OFF
-- around the rebuild because seven SA tables (screenings, chains,
-- dispositions, …) reference pipeline_runs(run_id): dropping the parent with
-- enforcement ON would run an implicit DELETE that trips those child FKs on
-- any database with recorded runs. The PRAGMA sits OUTSIDE the BEGIN/COMMIT:
-- foreign_keys cannot be changed inside a transaction, and executescript
-- commits before running, so it takes effect, then is restored after COMMIT.
--
-- Fresh bootstrap and an in-place upgrade both run this exact file, so the
-- rebuilt table is byte-identical between them (parity test holds).
-- =============================================================================

PRAGMA foreign_keys=OFF;

BEGIN;

CREATE TABLE pipeline_runs_new (
    run_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at       TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at     TEXT,
    status           TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed', 'partial')),
    prompt_version   TEXT NOT NULL,                  -- e.g. 'stage2-v1', 'stage3-v0.3'
    model_id         TEXT NOT NULL,                  -- e.g. 'claude-sonnet-4-6'
    vocab_version    INTEGER,                        -- logical ref to SA vocabulary_versions; NULL when SA is absent (0015)
    codebook_version INTEGER,                        -- logical ref to SA codebook_versions; NULL when SA is absent (0015)
    notes            TEXT,
    service          TEXT NOT NULL DEFAULT 'sa',     -- per-service run provenance (0005)
    stage            TEXT,                           -- non-LLM stage provenance (0013, F22)
    config_json      TEXT                            -- non-LLM stage provenance (0013, F22)
);

INSERT INTO pipeline_runs_new (
    run_id, started_at, completed_at, status, prompt_version, model_id,
    vocab_version, codebook_version, notes, service, stage, config_json
)
SELECT
    run_id, started_at, completed_at, status, prompt_version, model_id,
    vocab_version, codebook_version, notes, service, stage, config_json
FROM pipeline_runs;

-- Preserve the AUTOINCREMENT high-water mark: the row copy re-derives it as
-- MAX(run_id), which undercounts if the top run rows were ever deleted (no
-- app path does, but run_ids are provenance and must never be reused).
UPDATE sqlite_sequence
   SET seq = (SELECT seq FROM sqlite_sequence WHERE name = 'pipeline_runs')
 WHERE name = 'pipeline_runs_new'
   AND seq < (SELECT seq FROM sqlite_sequence WHERE name = 'pipeline_runs');
INSERT INTO sqlite_sequence (name, seq)
SELECT 'pipeline_runs_new', s.seq
  FROM sqlite_sequence s
 WHERE s.name = 'pipeline_runs'
   AND NOT EXISTS (SELECT 1 FROM sqlite_sequence WHERE name = 'pipeline_runs_new');

DROP TABLE pipeline_runs;
ALTER TABLE pipeline_runs_new RENAME TO pipeline_runs;

INSERT INTO schema_version (version, description)
    VALUES (15, 'Decouple pipeline_runs from SA version tables: vocab_version/codebook_version nullable, logical refs (A2/BUG-02)');

COMMIT;

PRAGMA foreign_keys=ON;
