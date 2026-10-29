-- =============================================================================
-- DnD service migration 0004: materialized segments (alignment-audit item F3).
--
-- The journal treats check/reset/session/active-work segments as first-class
-- analysis units (ch-event-trajectory-model subsec-attempts-and-segments;
-- ch-data-processing subsec-segment-tables) and prescribes a `segments`
-- analysis-ready table (tab-analysis-ready-tables) so one reconstructed trace
-- supports many analyses without reparsing the log. Until now the check
-- segments existed only implicitly (cohort grouping checks per attempt).
--
-- One row per segment: a contiguous, inclusive span [start_seq, end_seq] of
-- the attempt's dnd_configurations.seq (seq 0 = X0). Written by reconstruct
-- while replaying the attempt; cleared and rebuilt with the rest of the
-- reconstruction layer. The three model-level types are emitted today:
--
--   check    boundary after each check row; the closing check belongs to the
--            span it ends, a trailing span after the last check is the
--            post-check-revision tail, and an attempt with no checks is one
--            open segment
--   reset    the same rule with reset rows
--   session  maximal runs of one logged session (splits survive inside an
--            attempt only under non-default segmentation configs)
--
-- 'active_work' is reserved in the CHECK but not emitted yet: its delimiter
-- is the analytic idle threshold (distinct from the segmentation gap), which
-- lands with F9's time features.
-- =============================================================================

BEGIN;

CREATE TABLE dnd_segments (
    segment_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_id    INTEGER NOT NULL,
    segment_type  TEXT NOT NULL CHECK (segment_type IN ('check', 'reset', 'session', 'active_work')),
    segment_index INTEGER NOT NULL,     -- 1-based within (attempt, type)
    start_seq     INTEGER NOT NULL,     -- first dnd_configurations.seq in the span
    end_seq       INTEGER NOT NULL,     -- last seq in the span (inclusive)
    UNIQUE (attempt_id, segment_type, segment_index),
    FOREIGN KEY (attempt_id) REFERENCES dnd_attempts(attempt_id)
);

CREATE INDEX idx_dnd_segments_attempt ON dnd_segments(attempt_id);

COMMIT;
