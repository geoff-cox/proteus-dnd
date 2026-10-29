-- =============================================================================
-- Migration 0007: store verbatim PreTeXt question source on questions.
--
-- Forward-only, additive (plan §2.6, the A1.6 slice required by B2.2). The
-- database authority for a question's source snippet: services build their
-- derived artifacts (DnD board specs and keys; later, previews) from this
-- column, never by re-reading the textbook tree at stage time.
--
-- Named source_xml rather than the plan's `source` because the baseline
-- already uses `source` for provenance ('auto_extracted'/'manual'/
-- 'placeholder', CHECK-constrained). Likewise `source_hash` keeps its
-- preview-pipeline meaning (hash of the HTML render chunk); consumers of
-- source_xml hash the column's text themselves (e.g. dnd_boards.source_hash),
-- so the two sync paths never fight over one hash column.
-- =============================================================================

BEGIN;

ALTER TABLE questions ADD COLUMN source_xml TEXT;

INSERT INTO schema_version (version, description)
    VALUES (7, 'Add questions.source_xml: verbatim PreTeXt source snippet written by sync-sources');

COMMIT;
