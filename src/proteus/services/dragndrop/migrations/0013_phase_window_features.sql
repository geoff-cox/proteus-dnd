-- =============================================================================
-- DnD service migration 0013: phase-window features for the RQ3 contrast
-- (Paper 1 decision F-NEW, 2026-09-28; cc-task-phase-window-features).
--
-- The P5 comparison (paper1-decisions.md, 2026-09-28) is restructured into
-- three blocks so that no compared quantity is fixed by the grouping. Block 1
-- compares the groups on the trajectory *before the first check*; block 3
-- describes the first-check-incorrect group's *repair* from the first check
-- to the first correct check. The whole-attempt features (dnd/0001..0011)
-- cover the attempt end to end; these columns restrict the movement and
-- time families to the two windows. Computed by `dnd analyze` over the
-- replayed configuration sequence (the same walk and clock every other
-- attempt feature reads), with the windows delimited by the F10 scored
-- checks (dnd/0009): the first check is the attempt's first scored check,
-- the first correct check the scored check at `correct_check_ordinal`.
--
--   pre window     replayed rows strictly before the first check. Defined
--                  whenever the attempt has a scored check; a `_pre` column
--                  on an attempt whose first check is its first replayed
--                  row is 0, not NULL.
--   repair window  rows after the first check up to and including the
--                  first correct check. Defined only when
--                  first_check_correct = 0 and eventual_correct = 1.
--
--   n_effective_moves_{pre,repair}  ordinary moves that changed the state
--                                   (noop drops excluded — the whole-attempt
--                                   n_moves rule) in the window.
--   n_excess_moves_pre              effective moves in pre minus the
--                                   configuration-graph distance from X0 to
--                                   the replayed state at the first check
--                                   (the minimum moves that reach the state
--                                   checked, not the key), floored at 0. No
--                                   excess is defined for the repair window.
--   n_takebacks_{pre,repair}        effective moves whose destination is
--                                   the tray, in the window.
--   n_resets_{pre,repair}           replayed reset rows in the window.
--   n_checks_repair                 replayed check rows in repair: the first
--                                   check itself excluded, the first correct
--                                   check included.
--   active_time_{pre,repair}_s      sum of the replayed-clock gaps at or
--                                   below the same δ_max as active_time_s
--                                   (idle_threshold_s), each gap assigned
--                                   to the window of its later row; the gap
--                                   ending at the first check belongs to
--                                   pre, the gap starting at it (and any gap
--                                   spanning it) to repair.
--   n_fix_moves_repair              effective moves in repair whose key
--                                   distance fell by one (the F6 fixing
--                                   class, within the window).
--
-- All eleven are nullable with no default: NULL means the window is
-- undefined for the attempt (no scored check; a repair window on an attempt
-- that was correct at its first check or never correct), the attempt has no
-- row on the replayed clock (the two time columns), or the row predates
-- this vocabulary (older analyze). A fabricated default would read as a
-- computed value (same posture as dnd/0002..0012).
-- =============================================================================

BEGIN;

ALTER TABLE dnd_attempt_metrics ADD COLUMN n_effective_moves_pre INTEGER;
ALTER TABLE dnd_attempt_metrics ADD COLUMN n_excess_moves_pre INTEGER;
ALTER TABLE dnd_attempt_metrics ADD COLUMN n_takebacks_pre INTEGER;
ALTER TABLE dnd_attempt_metrics ADD COLUMN n_resets_pre INTEGER;
ALTER TABLE dnd_attempt_metrics ADD COLUMN active_time_pre_s REAL;
ALTER TABLE dnd_attempt_metrics ADD COLUMN n_effective_moves_repair INTEGER;
ALTER TABLE dnd_attempt_metrics ADD COLUMN n_takebacks_repair INTEGER;
ALTER TABLE dnd_attempt_metrics ADD COLUMN n_resets_repair INTEGER;
ALTER TABLE dnd_attempt_metrics ADD COLUMN n_checks_repair INTEGER;
ALTER TABLE dnd_attempt_metrics ADD COLUMN active_time_repair_s REAL;
ALTER TABLE dnd_attempt_metrics ADD COLUMN n_fix_moves_repair INTEGER;

COMMIT;
