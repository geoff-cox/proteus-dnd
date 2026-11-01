-- =============================================================================
-- DnD service migration 0008: time features (alignment-audit item F9).
--
-- The journal's basic timing summaries (ch-baseline-features
-- tab-basic-time-features), computed by `dnd analyze` from the replayed
-- rows' timestamps and persisted per attempt. Until now only
-- time_to_first_check_s existed. All values are seconds (REAL); the clock
-- is the reconstructed attempt, rows that could not be replayed are
-- excluded, the module's standing posture.
--
--   total_time_s            elapsed time, first to last replayed action.
--   time_to_first_move_s    first action to the first ordinary move
--                           (move/takeback; flagged noop drops are not
--                           ordinary moves). NULL when the attempt has none.
--   median_move_gap_s       median gap between consecutive ordinary moves.
--                           NULL with fewer than two.
--   max_action_gap_s        largest gap between consecutive logged actions
--                           of any kind (noop drops and checks included,
--                           they are logged activity). NULL with fewer than
--                           two.
--   active_time_s           total time after excluding action gaps larger
--                           than the analytic idle threshold δ_max
--                           (subsec-action-gaps-processing), the journal's
--                           "excluding" option, the conservative default.
--   idle_threshold_s        the δ_max active_time_s was computed under, so
--                           the row is self-describing (the journal insists
--                           the threshold be reported; same posture as
--                           dnd_attempts.segmentation_rule). Also recorded
--                           in the pass's pipeline_runs config (F22).
--
-- The same δ_max drives the reconstruct-side `long_idle_gap` QC flag
-- (dnd/0003, qc.py) and the active_work segments (the type dnd/0004
-- reserved), which land beside these columns in the same change-set.
--
-- All six are nullable with no default: NULL means the row predates this
-- vocabulary (older analyze) or the value is undefined for the attempt (no
-- moves, a single action). A fabricated default would read as a computed
-- value (same posture as dnd/0002..0007).
-- =============================================================================

BEGIN;

ALTER TABLE dnd_attempt_metrics ADD COLUMN total_time_s REAL;
ALTER TABLE dnd_attempt_metrics ADD COLUMN time_to_first_move_s REAL;
ALTER TABLE dnd_attempt_metrics ADD COLUMN median_move_gap_s REAL;
ALTER TABLE dnd_attempt_metrics ADD COLUMN max_action_gap_s REAL;
ALTER TABLE dnd_attempt_metrics ADD COLUMN active_time_s REAL;
ALTER TABLE dnd_attempt_metrics ADD COLUMN idle_threshold_s REAL;

COMMIT;
