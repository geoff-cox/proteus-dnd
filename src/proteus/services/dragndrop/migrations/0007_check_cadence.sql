-- =============================================================================
-- DnD service migration 0007: check-cadence sequence, post-check gains, and
-- responsiveness (alignment-audit item F7).
--
-- The journal's check-structure features (ch-baseline-features
-- sec-check-and-feedback-features), computed by `dnd analyze` from the replayed
-- configurations and persisted per attempt beside the move mix. Until now
-- only the `checked_every_move` boolean survived the cadence computation;
-- the sequence itself was discarded and the gains/responsiveness never
-- computed.
--
--   cadence_json            the check cadence n0..n(q-1)
--                           (subsec-check-cadence): ordinary moves
--                           before the first check, then between each
--                           consecutive check pair, q entries for q checks
--                           ('[]' for a checkless attempt). Rows flagged
--                           noop_move are excluded and resets never advance
--                           the count (the F1 processing default). Key-free.
--   post_check_gains_json   the post-check gains G_i = d*(X_ki) - d*(X_ki+1)
--                           (subsec-post-check-gain) between consecutive
--                           checked states, q-1 entries. Needs the board
--                           key, so NULL for a board-less question.
--   responsiveness_json     the responsiveness ρ_i = |H_i ∩ M_i| / |H_i|
--                           (subsec-responsiveness-baseline) per consecutive
--                           check pair, q-1 entries, each a fraction or
--                           JSON null where ρ is undefined: the check did
--                           not disclose at the highlight layer (FP-3's
--                           per-check classification; legacy checks always
--                           do), the highlighted set is empty, or the layer
--                           is unclassified (older reconstruct). Needs the
--                           board key, so NULL without one.
--
-- All three are nullable with no default: NULL means the row predates this
-- vocabulary (older analyze) or (for the two key-dependent columns) the
-- question has no board key. A fabricated default would read as a computed
-- value (same posture as dnd/0002, dnd/0003, dnd/0005, dnd/0006).
-- =============================================================================

BEGIN;

ALTER TABLE dnd_attempt_metrics ADD COLUMN cadence_json TEXT;
ALTER TABLE dnd_attempt_metrics ADD COLUMN post_check_gains_json TEXT;
ALTER TABLE dnd_attempt_metrics ADD COLUMN responsiveness_json TEXT;

COMMIT;
