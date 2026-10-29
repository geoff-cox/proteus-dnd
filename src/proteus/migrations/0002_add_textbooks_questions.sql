-- =============================================================================
-- Migration 0002: Add textbooks and questions tables for the question previewer.
--
-- Forward-only. Additive: no existing table is altered. The two new tables
-- back the question-previewer feature described in
-- proteus_ui/proteus_ui_guide.md ("Question previewer" / Phase 3.5).
--
-- The seed INSERT at the bottom mirrors the seed in schema.sql so a fresh
-- bootstrap and a migrated existing database end up in the same state.
--
-- TODO(maintainer): the project has not yet adopted a migration runner
-- (Alembic, yoyo-migrations, or a hand-rolled apply_pending() in db.py).
-- proteus_ui_guide.md explicitly defers that choice ("Deferred
-- infrastructure / Schema migration tooling"). Until a runner is chosen,
-- apply this file manually against an existing database:
--
--     sqlite3 path/to/proteus.db < src/proteus/migrations/0002_add_textbooks_questions.sql
--
-- and confirm that `SELECT MAX(version) FROM schema_version` returns 2.
-- =============================================================================

BEGIN;

CREATE TABLE textbooks (
    textbook_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    git_repo_url     TEXT NOT NULL,
    git_ref          TEXT NOT NULL,                  -- SHA or tag
    book_xml_id      TEXT NOT NULL,                  -- e.g. 'ula-proteus'
    source_dir       TEXT,                           -- local clone path (nullable until importer exists)
    build_dir        TEXT NOT NULL,                  -- local path to built HTML
    added_at         TEXT NOT NULL DEFAULT (datetime('now')),
    notes            TEXT,
    UNIQUE (git_repo_url, git_ref)
);

CREATE TABLE questions (
    question_id      TEXT PRIMARY KEY,               -- matches responses.question_id
    textbook_id      INTEGER NOT NULL,
    xml_id           TEXT NOT NULL,                  -- 'ula-proteus-pivots-reading-q1-part-a'
    parent_xml_id    TEXT,                           -- enclosing <exercise> xml:id, if a task
    section_id       TEXT,                           -- 'sec-pivots'
    section_title    TEXT,
    runestone_url    TEXT,                           -- canonical Runestone URL with fragment
    preview_path     TEXT,                           -- relative path to standalone HTML file
    metadata_json    TEXT NOT NULL,                  -- exercise_type, n_tasks, has_hints, ...
    source           TEXT NOT NULL CHECK (source IN ('auto_extracted','manual','placeholder')),
    source_hash      TEXT,                           -- SHA-256 of render input chunk
    extracted_at     TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (textbook_id) REFERENCES textbooks(textbook_id)
);

CREATE INDEX idx_questions_textbook ON questions(textbook_id);
CREATE INDEX idx_questions_section  ON questions(section_id);

-- TODO(maintainer): confirm git_repo_url, git_ref, and build_dir for the
-- analyzed textbook, or replace this seed row with the real registration
-- before sync_questions runs against this database.
INSERT INTO textbooks (git_repo_url, git_ref, book_xml_id, build_dir, notes)
    VALUES (
        'TODO(maintainer):git_repo_url',
        'TODO(maintainer):git_ref',
        'ula-proteus',
        'TODO(maintainer):build_dir',
        'Placeholder seed row for the analyzed book; values need maintainer confirmation.'
    );

INSERT INTO schema_version (version, description)
    VALUES (2, 'Add textbooks and questions tables for question previewer');

COMMIT;
