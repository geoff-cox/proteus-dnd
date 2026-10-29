-- =============================================================================
-- DnD service migration 0002: the `noop_move` flag (alignment-audit item F1).
--
-- The journal's replay rules (ch-data-processing, subsec-reconstruction-rules)
-- say a drop whose destination equals the card's current zone (b = a) is a
-- no-op: the state update is zero. Such rows occur in real logs; the pipeline
-- must flag them with `noop_move` (the QC-flag vocabulary of
-- tab-quality-control-flags) and exclude them from move counts by default, so
-- trajectory features count state changes rather than raw drop rows.
--
--   dnd_configurations.is_noop      set by reconstruct when the replayed drop
--                                   left the board unchanged (the flagged row
--                                   is still written, so the event stays
--                                   traceable in the reconstruction layer)
--   dnd_attempt_metrics.n_noop_moves  how many flagged rows analyze excluded
--                                     from n_moves/cadence, so the raw drop
--                                     count (n_moves + n_noop_moves) stays
--                                     recoverable in flat form
-- =============================================================================

BEGIN;

ALTER TABLE dnd_configurations ADD COLUMN is_noop INTEGER NOT NULL DEFAULT 0;

ALTER TABLE dnd_attempt_metrics ADD COLUMN n_noop_moves INTEGER;

COMMIT;
