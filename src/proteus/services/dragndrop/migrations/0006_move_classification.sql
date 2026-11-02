-- =============================================================================
-- DnD service migration 0006: move classification and excess moves
-- (alignment-audit item F6).
--
-- The journal's fixing/lateral/breaking vocabulary (subsec-move-classification)
-- and the two inefficiency measures built on it (subsec-excess-moves), computed
-- by `dnd analyze` from the replayed distance curve and persisted per attempt:
--
--   n_fixing / n_lateral / n_breaking   counts F, L, B of ordinary moves with
--                                       distance change Δd = -1 / 0 / +1,
--                                       summed over the attempt's reset-free
--                                       segments (a reset is not an ordinary
--                                       move and is never classified; rows
--                                       flagged noop_move are excluded, the
--                                       F1 processing default).
--   excess_moves                        E_move = n - (F - B) = 2B + L, the
--                                       movement that does not contribute to
--                                       net progress toward the key.
--   path_excess                         E_path = Σ over reset-free segments of
--                                       n_seg - d_X(X_start, X_end): moves
--                                       beyond the shortest path between each
--                                       segment's own endpoints (graph distance
--                                       = cards whose zones differ). Key-free,
--                                       so computed even for board-less
--                                       attempts.
--
-- All five are nullable with no default: NULL means the row predates this
-- vocabulary (older analyze) or (for the four key-dependent columns) the
-- question has no board key. A fabricated default would read as a computed
-- value (same posture as dnd/0002, dnd/0003, dnd/0005).
-- =============================================================================

BEGIN;

ALTER TABLE dnd_attempt_metrics ADD COLUMN n_fixing INTEGER;
ALTER TABLE dnd_attempt_metrics ADD COLUMN n_lateral INTEGER;
ALTER TABLE dnd_attempt_metrics ADD COLUMN n_breaking INTEGER;
ALTER TABLE dnd_attempt_metrics ADD COLUMN excess_moves INTEGER;
ALTER TABLE dnd_attempt_metrics ADD COLUMN path_excess INTEGER;

COMMIT;
