-- =============================================================================
-- Migration 0009: make questions.textbook_id nullable; allow source='catalog'.
--
-- Forward-only (checklist item CD-2). The question catalog materializes one
-- `questions` row per distinct problem seen in `events`, so question_id stays
-- the single join key across the catalog, question source (SP-2), and every
-- service's tables. But a catalogued question has no textbook until its book is
-- identified at upload and synced (SP-1/SP-2), the .tab carries no book id,
-- so `textbook_id` must be nullable, and catalog rows carry source='catalog'.
--
-- SQLite cannot drop a column's NOT NULL in place, so this rebuilds the table
-- (the documented 12-step procedure). `foreign_keys` is toggled OFF around the
-- rebuild because dropping the parent `questions` with enforcement ON would
-- run an implicit DELETE that trips child FKs (dnd_boards.question_id) on a
-- database that already has boards. The PRAGMA sits OUTSIDE the BEGIN/COMMIT:
-- foreign_keys cannot be changed inside a transaction, and executescript
-- commits before running, so it takes effect, then is restored after COMMIT.
--
-- Nothing is added or removed column-wise; column names are unchanged. Fresh
-- bootstrap and an in-place upgrade both run this exact file, so the rebuilt
-- table is byte-identical between them (parity test holds).
-- =============================================================================

PRAGMA foreign_keys=OFF;

BEGIN;

CREATE TABLE questions_new (
    question_id      TEXT PRIMARY KEY,
    textbook_id      INTEGER,
    xml_id           TEXT NOT NULL,
    parent_xml_id    TEXT,
    section_id       TEXT,
    section_title    TEXT,
    runestone_url    TEXT,
    preview_path     TEXT,
    metadata_json    TEXT NOT NULL,
    source           TEXT NOT NULL CHECK (source IN ('auto_extracted','manual','placeholder','catalog')),
    source_hash      TEXT,
    extracted_at     TEXT NOT NULL DEFAULT (datetime('now')),
    question_type    TEXT,
    source_xml       TEXT,
    FOREIGN KEY (textbook_id) REFERENCES textbooks(textbook_id)
);

INSERT INTO questions_new (
    question_id, textbook_id, xml_id, parent_xml_id, section_id, section_title,
    runestone_url, preview_path, metadata_json, source, source_hash,
    extracted_at, question_type, source_xml
)
SELECT
    question_id, textbook_id, xml_id, parent_xml_id, section_id, section_title,
    runestone_url, preview_path, metadata_json, source, source_hash,
    extracted_at, question_type, source_xml
FROM questions;

DROP TABLE questions;
ALTER TABLE questions_new RENAME TO questions;

CREATE INDEX idx_questions_textbook ON questions(textbook_id);
CREATE INDEX idx_questions_section  ON questions(section_id);

INSERT INTO schema_version (version, description)
    VALUES (9, 'Make questions.textbook_id nullable and allow source=catalog for events-derived catalog rows');

COMMIT;

PRAGMA foreign_keys=ON;
