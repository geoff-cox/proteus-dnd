-- =============================================================================
-- DnD service migration 0014: question-identifier aliases at board binding
-- (cc-task-question-id-alias; paper1-decisions.md 2026-09-27 P4 and the
-- 2026-09-16 record; owner decisions 2026-10-04).
--
-- One ULA item was renamed in source on 2025-07-05 while Runestone kept
-- serving the old identifier through assignments, so both identifiers carry
-- attempts and the collection-era source has a board only under the new one.
-- Raw rows, event ids and problem names are never rewritten (the event-
-- identity contract); instead a retired identifier is aliased to its
-- successor where a board is looked up and where items are grouped
-- (dragndrop/aliases.py is the resolver's single home):
--
--   dnd_question_aliases   one row per retired identifier. Resolution is one
--                          step: the importer refuses chains and self-aliases;
--                          an alias identifier that has its own board is
--                          refused unless that board is explicitly retired,
--                          which the row records. Logical references only,
--                          an alias identifier may be known to `events`
--                          alone, a canonical identifier to `dnd_boards`.
--   dnd_attempts.canonical_question_id
--                          written by reconstruct (stage version 4): the
--                          canonical identifier when the attempt's question
--                          was bound through an alias, NULL otherwise, the
--                          per-attempt provenance marker. A column, not a QC
--                          flag: the flag vocabulary stays at eleven names and
--                          readiness is untouched.
--
-- Nullable, no defaults; with an empty alias table every stage's output is
-- unchanged. Same posture as dnd/0002..0013.
-- =============================================================================

BEGIN;

CREATE TABLE dnd_question_aliases (
    alias_question_id          TEXT PRIMARY KEY,  -- the retired (logged) identifier
    canonical_question_id      TEXT NOT NULL,     -- its successor: the board's identifier
    reason                     TEXT,
    source_commit              TEXT,              -- the rename commit in the book's source
    recorded_on                TEXT,              -- ISO date the alias was recorded
    imported_from              TEXT,              -- the alias file's name (never a path)
    imported_sha256            TEXT,              -- sha256 of that file
    replaced_board_source_hash TEXT               -- the alias identifier's own board, when one was retired
);

CREATE INDEX idx_dnd_question_aliases_canonical
    ON dnd_question_aliases(canonical_question_id);

ALTER TABLE dnd_attempts ADD COLUMN canonical_question_id TEXT;

COMMIT;
