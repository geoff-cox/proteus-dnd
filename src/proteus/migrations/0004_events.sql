-- =============================================================================
-- Migration 0004: generic `events` table for shared multi-service ingest.
--
-- Forward-only, additive. Resolves findings F1/F2/F3 (plan §1.2, §2.3): one
-- file is parsed once into a generic, typed event log; each question-type
-- service then derives its own unit-of-analysis tables from `events`. This is
-- a core table (no dependency on any service); service tables may FK into it,
-- never the other way round.
--
-- One row per logged DataShop action. Event identity is content-derived —
-- sha256(student | problem | timestamp | selection | problem_view) — so
-- re-ingesting the same file is idempotent at the event level (PK conflict =
-- skip), exactly as `responses.response_id` makes SA ingest idempotent.
--
-- Timestamps are stored verbatim as opaque sortable strings; parsing into
-- datetimes is the responsibility of each service's derive step, done exactly
-- as core/ingest does (plan rule 6).
--
-- Per the schema.sql forward policy (frozen at v3), this lives only here and
-- is applied by the migration runner on both fresh and existing databases.
-- =============================================================================

BEGIN;

CREATE TABLE events (
    event_id                TEXT PRIMARY KEY,   -- sha256(student|problem|timestamp|selection|problem_view)[:16]
    import_id               INTEGER NOT NULL,
    student_id              TEXT,
    session_id              TEXT,
    problem_name            TEXT,
    selection               TEXT,               -- DataShop Selection: shortanswer, dragNdrop, page, ...
    action                  TEXT,
    input                   TEXT,
    feedback_text           TEXT,
    feedback_classification TEXT,
    problem_view            TEXT,
    timestamp               TEXT,               -- raw, opaque sortable string from the log
    course_id               TEXT,
    FOREIGN KEY (import_id) REFERENCES raw_imports(import_id)
);

CREATE INDEX idx_events_problem   ON events(problem_name);
CREATE INDEX idx_events_student   ON events(student_id);
CREATE INDEX idx_events_selection ON events(selection);

INSERT INTO schema_version (version, description)
    VALUES (4, 'Generic events table (one row per logged DataShop action) for shared multi-service ingest');

COMMIT;
