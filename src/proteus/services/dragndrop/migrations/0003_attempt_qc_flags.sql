-- =============================================================================
-- DnD service migration 0003: per-attempt QC flags + feature readiness
-- (alignment-audit item F17b).
--
-- The journal's processing chapter prescribes an explicit reconstruction
-- status: quality-control flags stored rather than handled through informal
-- notes (tab-quality-control-flags), and a three-level readiness status
-- computed per attempt from the flags, never from the student outcome
-- (subsec-feature-readiness). The per-row signals already exist
-- (dnd_events.parse_ok, dnd_configurations.matches_input / is_noop, the F2
-- ambiguity scan); this migration adds the per-attempt rollup they feed.
--
--   dnd_attempts.qc_flags_json  JSON array of flag names, in the canonical
--                               vocabulary order (see dragndrop/qc.py, the
--                               single home of the vocabulary), written by
--                               reconstruct while replaying the attempt
--   dnd_attempts.readiness      'ready' | 'limited' | 'exclude', the
--                               subsec-feature-readiness status computed
--                               from the flags by qc.readiness()
--
-- Both columns are deliberately nullable with no default: NULL means the
-- attempt row predates this rollup (written by an older reconstruct pass)
-- and its status has not been computed, re-run `proteus dnd reconstruct`.
-- A fabricated default ('[]' / 'limited') would read as a computed status,
-- which is exactly what the journal's explicit-status discipline forbids.
-- Same posture as dnd/0002's nullable n_noop_moves.
-- =============================================================================

BEGIN;

ALTER TABLE dnd_attempts ADD COLUMN qc_flags_json TEXT;

ALTER TABLE dnd_attempts ADD COLUMN readiness TEXT
    CHECK (readiness IN ('ready', 'limited', 'exclude'));

COMMIT;
