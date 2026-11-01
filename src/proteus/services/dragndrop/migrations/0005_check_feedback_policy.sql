-- =============================================================================
-- DnD service migration 0005: per-check feedback-policy classification
-- (alignment-audit item FP-3).
--
-- Runestone shipped two cardsort feedback policies (subsec-platform-feedback-
-- policies): the legacy single layer (every check graded, counts + highlight
-- from the first press) and the mid-2026 layered policy (disclosure gated by
-- the placement count). The policy is a property of the platform version at
-- collection time, not of the question source, so it is recorded per attempt
-- (the era assumed when the attempt was interpreted) rather than synced.
--
--   dnd_attempts.feedback_policy    'legacy' | 'layered', the era reconstruct
--                                   applied (conservative default 'legacy',
--                                   every collected export predates the
--                                   change; `dnd reconstruct
--                                   --feedback-policy layered` tags new data).
--                                   Per-import era resolution is the fuller
--                                   F18 story (open-todos, rule 11).
--   dnd_configurations.disclosure_layer  per check: what the student saw,
--                                   'refused' (φ1, ungraded, left-count only),
--                                   'count' (φ2, σ/w/u), 'highlight' (φ3,
--                                   σ/w/u + highlighted wrong cards; legacy is
--                                   always φ3). NULL for non-check rows and
--                                   for a layered attempt with no board (ℓ₀
--                                   unknown). Computed by
--                                   dragndrop/policy.py, the vocabulary's home.
--   dnd_configurations.graded_ordinal    per check: its 1-based ordinal among
--                                   *graded* checks of the attempt (refused
--                                   presses don't advance it). NULL for a
--                                   refused / non-check / undecidable row.
--
-- Both columns are nullable with no default: NULL means the row predates this
-- classification (older reconstruct) or the era can't classify it, a
-- fabricated default would read as a computed status, which the journal's
-- explicit-status discipline forbids (same posture as dnd/0002, dnd/0003).
-- =============================================================================

BEGIN;

ALTER TABLE dnd_attempts ADD COLUMN feedback_policy TEXT
    CHECK (feedback_policy IN ('legacy', 'layered'));

ALTER TABLE dnd_configurations ADD COLUMN disclosure_layer TEXT
    CHECK (disclosure_layer IN ('refused', 'count', 'highlight'));

ALTER TABLE dnd_configurations ADD COLUMN graded_ordinal INTEGER;

COMMIT;
