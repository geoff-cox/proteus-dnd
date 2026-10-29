-- =============================================================================
-- Migration 0006: promote question_type out of questions.metadata_json.
--
-- Forward-only, additive (plan §2.4, §2.6). A first-class, nullable column so
-- a question's type (shortanswer, cardsort, …) can be queried and indexed
-- without unpacking metadata_json. The core sync command will populate it
-- (A1.6, deferred until the service registry exists); metadata_json keeps the
-- value too for one release of back-compat.
-- =============================================================================

BEGIN;

ALTER TABLE questions ADD COLUMN question_type TEXT;

INSERT INTO schema_version (version, description)
    VALUES (6, 'Add questions.question_type promoted out of metadata_json');

COMMIT;
