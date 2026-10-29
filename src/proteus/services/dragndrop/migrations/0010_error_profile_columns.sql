-- =============================================================================
-- DnD service migration 0010: error-profile components as flat columns
-- (alignment-audit item F12).
--
-- The journal's additive error decomposition (ch-baseline-features
-- g-baseline-additive-error-decomposition) splits a checked state's
-- distance exactly:
--
--     d* = omissions + wrong_slot + decoy_card_leak
--          (O)         (W_slot)     (L_P = decoy_source_leak, stored)
--
-- The two decoy leaks have been persisted since dnd/0001; the other two
-- components were computed on demand (scoring.u; errors.misplacements) but
-- never stored, so the documented decomposition was not available in flat
-- form. Written per check by `dnd score`:
--
--   omissions        O: non-decoy cards left in the tray though the key
--                    places them ("left to place", the grader's u).
--   wrong_slot       W_slot: non-decoy cards placed in a slot other than
--                    their keyed slot. A placed decoy is a decoy-card leak,
--                    not a wrong-slot placement — the components overlap
--                    nowhere, which is what makes the decomposition exact.
--   per_card_score   p = sigma / P (tab-minimal-baseline-feature-dictionary's
--                    checked-state family, completing it in flat form beside
--                    sigma, distance, and g).
--
-- All three are nullable with no default: NULL means the row predates this
-- vocabulary (older score pass) — re-run `dnd score`. A fabricated default
-- would read as a computed value (same posture as dnd/0002..0009).
-- =============================================================================

BEGIN;

ALTER TABLE dnd_check_scores ADD COLUMN omissions INTEGER;
ALTER TABLE dnd_check_scores ADD COLUMN wrong_slot INTEGER;
ALTER TABLE dnd_check_scores ADD COLUMN per_card_score REAL;

COMMIT;
