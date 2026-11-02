-- =============================================================================
-- PROTEUS Automation Pipeline, core (service-agnostic) SQLite schema
-- Version: 0.3 baseline, relocated (item A3.5)
--
-- This file holds the core slice of the frozen version-3 baseline: provenance
-- (raw_imports, pipeline_runs), question metadata (textbooks, questions), and
-- schema_version. The short-answer service's slice of the same baseline lives
-- in services/shortanswer/schema/shortanswer.sql; db.bootstrap applies this
-- file, then each registered service's schema files, then the migration
-- runner. The two files together are byte-identical DDL to the single
-- schema.sql they replace.
--
-- FORWARD POLICY (migration runner, plan A0.x): this file is part of the
-- frozen *baseline* (schema version 3). Do NOT add new tables/columns here.
-- Every change from version 4 onward lives ONLY in a `migrations/NNNN_*.sql`
-- file. Adding a change to BOTH this file and a migration would double-create
-- it on bootstrap.
--
-- Known wart, recorded: pipeline_runs carries NOT NULL FKs into the SA
-- service's vocabulary_versions/codebook_versions tables (a pre-split design
-- decision frozen into the baseline). SQLite resolves FK targets at DML time,
-- so creating pipeline_runs before the SA tables is fine; loosening the
-- coupling needs a forward migration and is out of A3 scope. Migration 0015
-- is that loosening: it rebuilds pipeline_runs with both columns nullable
-- and the FKs converted to logical references (A2/BUG-02). The baseline DDL
-- below stays frozen at the v3 shape.
--
-- Notes:
--   * SQLite stores timestamps as TEXT in ISO-8601 form. They sort correctly
--     and are human-readable.
--   * Primary keys are TEXT (hash-derived) where idempotent re-imports matter,
--     INTEGER autoincrement otherwise.
--   * Foreign keys are off by default in SQLite; PRAGMA foreign_keys = ON;
--     must be set per connection.
--   * CHECK constraints enforce enum-like fields without a separate lookup.
--   * SQLite tolerates forward references in CREATE TABLE (a parent table need
--     not exist yet), so section order is for readability, not dependency.
-- =============================================================================

PRAGMA foreign_keys = ON;

-- ----------------------------------------------------------------------------
-- 1. Provenance: where did this data come from?
-- ----------------------------------------------------------------------------

CREATE TABLE raw_imports (
    import_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path        TEXT NOT NULL,
    file_hash        TEXT NOT NULL UNIQUE,           -- SHA-256 of the file
    file_format      TEXT NOT NULL CHECK (file_format IN ('datashop_tab', 'runestone_useinfo_csv')),
    imported_at      TEXT NOT NULL DEFAULT (datetime('now')),
    row_count        INTEGER,
    notes            TEXT
);

CREATE TABLE pipeline_runs (
    run_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at       TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at     TEXT,
    status           TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed', 'partial')),
    prompt_version   TEXT NOT NULL,                  -- e.g. 'stage2-v1', 'stage3-v0.3'
    model_id         TEXT NOT NULL,                  -- e.g. 'claude-sonnet-4-6'
    vocab_version    INTEGER NOT NULL,
    codebook_version INTEGER NOT NULL,
    notes            TEXT,
    FOREIGN KEY (vocab_version)    REFERENCES vocabulary_versions(version_id),
    FOREIGN KEY (codebook_version) REFERENCES codebook_versions(version_id)
);

-- ----------------------------------------------------------------------------
-- 2. Question previewer: textbook catalog + extracted-question cache
--
-- The relationship from responses.question_id to questions.question_id is
-- deliberately logical, not a foreign key: responses can be ingested before
-- the textbook is synced, and `questions` is a derived cache that may lag.
-- ----------------------------------------------------------------------------

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

-- Seed: one textbooks row for the analyzed book. The book_xml_id 'ula-proteus'
-- is established in the design doc; the remaining fields are placeholders.
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

-- ----------------------------------------------------------------------------
-- 3. Schema migrations
-- ----------------------------------------------------------------------------

CREATE TABLE schema_version (
    version          INTEGER PRIMARY KEY,
    applied_at       TEXT NOT NULL DEFAULT (datetime('now')),
    description      TEXT
);

INSERT INTO schema_version (version, description)
    VALUES (1, 'Initial schema');
INSERT INTO schema_version (version, description)
    VALUES (2, 'Add textbooks and questions tables for question previewer');
INSERT INTO schema_version (version, description)
    VALUES (3, 'cK¢ objects: controls library acceptance/provenance; operators, '
            || 'representations, documents tables; question_controls working '
            || 'codebook; correctness digits on matches and review_codes; '
            || 'triggers enforcing the accepted-only assignment gate');

-- =============================================================================
-- End of core schema.
-- =============================================================================
