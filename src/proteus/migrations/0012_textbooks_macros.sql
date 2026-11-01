-- =============================================================================
-- Migration 0012: add textbooks.macros (preview-wrapper macro support).
--
-- Forward-only, additive. A PreTeXt book defines its LaTeX macros once, in the
-- main file's <docinfo><macros>. `sync_book_sources` now captures that element
-- and stores it here so the standalone question-preview wrapper can inject the
-- same macros, math that uses a book's macros then renders in the preview
-- instead of failing to compile (PR #69 follow-up, issue 2). A new nullable
-- column with no default, so fresh bootstrap and an in-place upgrade both run
-- this file and reach an identical schema (the parity test holds). Existing rows
-- get NULL (no macros until the next sync).
-- =============================================================================

BEGIN;

ALTER TABLE textbooks ADD COLUMN macros TEXT;

INSERT INTO schema_version (version, description)
    VALUES (12, 'Add textbooks.macros for the preview wrapper (book <docinfo><macros>)');

COMMIT;
