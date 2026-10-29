-- =============================================================================
-- Migration 0011: add textbooks.last_synced_at (checklist item B4.13).
--
-- Forward-only, additive. The Textbooks screen shows when each registered book's
-- source was last synced (SP-2); NULL = never synced. `sync_book_sources` stamps
-- it on a successful sync. A new nullable column with no default, so fresh
-- bootstrap and an in-place upgrade both run this file and reach an identical
-- schema (the parity test holds). Existing rows get NULL (never synced).
-- =============================================================================

BEGIN;

ALTER TABLE textbooks ADD COLUMN last_synced_at TEXT;

INSERT INTO schema_version (version, description)
    VALUES (11, 'Add textbooks.last_synced_at for the Textbooks screen last-sync (B4.13)');

COMMIT;
