-- =============================================================================
-- DnD service migration 0011: repeated-state counts (alignment-audit item F8).
--
-- The journal's repeat features (ch-baseline-features
-- subsec-repeat-and-cycle-features), computed by `dnd analyze` over the
-- retained state-changing walk X_0..X_n, the same trace the convergence
-- curve serializes (X0, ordinary moves, resets; noop drops and checks are
-- never board-changing):
--
--   n_distinct_states   V(Γ) = #{X_k : 0 ≤ k ≤ n}, the number of distinct
--                       configurations the attempt visited.
--   n_repeated_states   R_state(Γ) = (n+1) - V(Γ), the number of repeated
--                       state visits ("the board returned to a previously
--                       seen configuration", revision, feedback use,
--                       accident, or exploration; the feature records the
--                       repetition, not the reason).
--
-- Both are nullable with no default: NULL means the row predates this
-- vocabulary (older analyze). A fabricated default would read as a computed
-- value (same posture as dnd/0002..0010).
-- =============================================================================

BEGIN;

ALTER TABLE dnd_attempt_metrics ADD COLUMN n_distinct_states INTEGER;
ALTER TABLE dnd_attempt_metrics ADD COLUMN n_repeated_states INTEGER;

COMMIT;
