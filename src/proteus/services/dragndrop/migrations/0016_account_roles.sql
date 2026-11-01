-- =============================================================================
-- DnD service migration 0016: the account-role table, the governance rulings
-- on staff, researcher and test accounts, applied at derive
-- (cc-task-attrition-ledger §4.1, PR 1 of 2; owner rulings 2026-10-19 at the
-- brief's Checkpoint 1: R1 (a), R2, R5, R7, R12, R13, J1-J4, M6).
--
-- The Paper 1 staff-account rulings (E-02) name the platform accounts that are
-- not students: instructor self-accounts, researcher and test accounts. Their
-- rows are evidence of the platform, not of learning, and the ruling is that
-- they never reach the DnD projection: derive withholds every row of an
-- account ruled exclude = 1, moves, checks, resets and page views alike (J1)
--, while core `events` keeps every row (rule 5: events is the single source
-- of truth; dnd_events is a re-derivable projection). The filter is global by
-- id (J2): one ruling per platform id, whatever the offering.
--
--   dnd_account_roles                 one row per ruled platform id, loaded by
--                                     `proteus dnd roles import` from the
--                                     committed rulings file (the alias-file
--                                     pattern of dnd/0014 and the regime-file
--                                     pattern of dnd/0015) before the first
--                                     `ingest --derive`, the order of
--                                     operations is init → dnd roles import →
--                                     ingest --derive (R1 (a)). An excluded id
--                                     that already has dnd_events rows is
--                                     refused at import and at derive (R7);
--                                     the remedy is to delete the import(s)
--                                     and re-ingest, or a fresh init.
--     anon_student_id                 the platform id exactly as
--                                     events.student_id carries it. A logical
--                                     reference, no FK (R5), like
--                                     dnd_attempts.student_id: a ruling may
--                                     precede the data it governs.
--     role                            instructor | researcher | test | student
--                                    , the documented vocabulary; free text,
--                                     not enforced.
--     exclude                         1 = withheld at derive; 0 = ruled and
--                                     kept (a non-standard id with ordinary
--                                     student activity).
--     pseudonym                       the offering pseudonym of the ruling: a
--                                     comment column (J2), kept so a refusal
--                                     can name the offering instead of the id.
--     raw_rows                        the rulings file's claim of the id's raw
--                                     row count; derive warns (a count, never
--                                     an id) when the events count disagrees.
--     ruled_by, ruled_on              who ruled and when (ISO date).
--     imported_from, imported_sha256  the rulings file's name (never a path)
--                                     and its sha256, the provenance the run
--                                     manifest carries beside counts by role
--                                     and the ruled set's sha256; never a row,
--                                     never an id (J4).
--
-- In the real file the ids embed surnames (M6): no id value is ever written to
-- a log line, an exception message, CLI output or the manifest. The file's
-- `note` column is read and never stored (R13). derive records
-- roles_set_sha256 (a content hash of the ruled set) in its run configuration
-- and the withheld counts in its notes, and its stage version moves 1 → 2
-- (R12).
--
-- With an empty table every stage behaves exactly as before: the golden tests
-- bootstrap fresh databases and are unchanged. Same posture as dnd/0002..0015.
-- =============================================================================

BEGIN;

CREATE TABLE dnd_account_roles (
    anon_student_id  TEXT PRIMARY KEY,   -- the platform id exactly as events.student_id carries it
    role             TEXT NOT NULL,      -- instructor | researcher | test | student (documented vocabulary; free text)
    exclude          INTEGER NOT NULL CHECK (exclude IN (0, 1)),
    pseudonym        TEXT,               -- the offering pseudonym of the ruling (comment column; J2)
    raw_rows         INTEGER,            -- the rulings file's claim of the id's raw row count
    ruled_by         TEXT,
    ruled_on         TEXT,               -- ISO date
    imported_from    TEXT,               -- the rulings file's name (never a path)
    imported_sha256  TEXT
);

COMMIT;
