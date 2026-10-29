-- =============================================================================
-- Migration 0013: add pipeline_runs.stage / .config_json (checklist item F22).
--
-- Forward-only, additive. Run-level provenance for the non-LLM stage passes:
-- the journal's processing chapter (subsec-provenance-and-reproducibility)
-- wants every analysis-ready table traceable to the parser version, idle
-- threshold, attempt-boundary rule, and exclusion defaults that produced it.
-- `stage` names the pass within its service (e.g. 'reconstruct'); `config_json`
-- carries that pass's configuration as a JSON object. Both are nullable with
-- no default — existing SA rows (whose configuration lives in prompt_version/
-- model_id) get NULL, and fresh bootstrap and an in-place upgrade both run
-- this file and reach an identical schema (the parity test holds).
-- =============================================================================

BEGIN;

ALTER TABLE pipeline_runs ADD COLUMN stage TEXT;
ALTER TABLE pipeline_runs ADD COLUMN config_json TEXT;

INSERT INTO schema_version (version, description)
    VALUES (13, 'Add pipeline_runs.stage/.config_json for run-level stage provenance (F22)');

COMMIT;
