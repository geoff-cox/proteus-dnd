-- =============================================================================
-- DnD service migration 0009: attempt-level outcome features (alignment-audit
-- item F10).
--
-- The journal's basic outcome features (ch-baseline-features
-- tab-check-outcome-features), the modeling chapter's outcome variables,
-- materialized per attempt by `dnd analyze` from the persisted outcome layer:
-- one indexed read of the attempt's `dnd_check_scores` rows in replay (seq)
-- order. The scores are the authoritative record (each check's logged Input,
-- rule 6), so a check whose Input never parsed is not retained here and does
-- not advance the ordinal.
--
--   first_check_distance    d*(X_k1): distance to the key at the attempt's
--                           first scored check.
--   final_check_distance    d*(X_kq): distance at the last retained check.
--   first_check_correct     1 iff the first scored check was exactly correct
--                           (d* = 0).
--   eventual_correct        1 iff ANY retained check was correct (the
--                           journal's max_i indicator, a later broken board
--                           does not undo it).
--   correct_check_ordinal   min{i : d*(X_ki) = 0}: the 1-based ordinal of
--                           the first correct check among the attempt's
--                           scored checks; NULL when never correct.
--
-- All five are nullable with no default: NULL means the row predates this
-- vocabulary (older analyze) or the attempt has no scored checks, no board,
-- no parseable check, or `dnd score` has not run yet (run it before
-- `dnd analyze`). A fabricated default would read as a computed value (same
-- posture as dnd/0002..0008).
-- =============================================================================

BEGIN;

ALTER TABLE dnd_attempt_metrics ADD COLUMN first_check_distance INTEGER;
ALTER TABLE dnd_attempt_metrics ADD COLUMN final_check_distance INTEGER;
ALTER TABLE dnd_attempt_metrics ADD COLUMN first_check_correct INTEGER;
ALTER TABLE dnd_attempt_metrics ADD COLUMN eventual_correct INTEGER;
ALTER TABLE dnd_attempt_metrics ADD COLUMN correct_check_ordinal INTEGER;

COMMIT;
