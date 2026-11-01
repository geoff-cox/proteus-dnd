-- =============================================================================
-- Migration 0010: retire the placeholder textbooks seed (checklist item SP-1).
--
-- Forward-only. The frozen v3 baseline (core.sql) seeds one textbooks row for
-- the analyzed book with TODO(maintainer) placeholders for git_repo_url /
-- git_ref / build_dir. SP-1 makes the textbooks table a real registry, one row
-- per book, populated via `proteus textbook add <book> <git_url> <build_dir>`
-- (core.textbooks.add_textbook), so the hardcoded placeholder is retired here.
--
-- This is a data change only (no schema change): a fresh bootstrap creates the
-- seed from core.sql and then deletes it here; an in-place upgrade deletes the
-- seed it already has. Both reach an empty registry, so fresh-bootstrap ==
-- migrated-DB still holds (the parity test compares schema, unaffected).
--
-- The DELETE is matched to all three TODO(maintainer) placeholder values
-- (git_repo_url, git_ref AND build_dir), so a real book a maintainer may have
-- already registered (or a placeholder they partially edited) is never
-- touched. The placeholder has no children (nothing can be synced against a
-- TODO URL), so the delete is safe with foreign keys enforced.
-- =============================================================================

BEGIN;

DELETE FROM textbooks
 WHERE book_xml_id  = 'ula-proteus'
   AND git_repo_url = 'TODO(maintainer):git_repo_url'
   AND git_ref      = 'TODO(maintainer):git_ref'
   AND build_dir    = 'TODO(maintainer):build_dir';

INSERT INTO schema_version (version, description)
    VALUES (10, 'Retire the placeholder textbooks seed; books are registered via `proteus textbook add` (SP-1)');

COMMIT;
