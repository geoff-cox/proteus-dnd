-- =============================================================================
-- DnD service migration 0015: the two structural anchor causes, the resumed-
-- attempt provenance, the seeded-replay marker and the logging-regime table
-- (cc-task-resumed-anchors; paper1-decisions.md 2026-10-05 "P8 decided,
-- hybrid; P1 amended, (ii′) as a cause; anchor eligibility two tiers; D3.2
-- knob 10"; owner rulings 2026-10-06 at the brief's Checkpoint 1).
--
-- Anchor eligibility gained a second tier: beside (a) "the payload decodes
-- to a unique, board-valid configuration" (dnd/0012, five causes), a check is
-- an eligible anchor only when (b) the replay it would be compared against is
-- a measurement. Two structural tests, computed from the timestamp, the
-- student, the question and the attempt sequence, never from an outcome,
-- say when it is not:
--
--   dnd_configurations.anchor_cause   the CHECK now admits seven names, in
--                                     precedence order (the first applicable
--                                     name is stored; the rest are logged):
--                                       'no_move_logging' the check's calendar
--                                                        date precedes its
--                                                        import's
--                                                        move_logging_from
--                                       'resumed_attempt' the attempt has a
--                                                        prior attempt and the
--                                                        run did not seed
--                                       then the five decode causes of
--                                       dnd/0012 unchanged.
--                                     SQLite cannot alter a CHECK in place:
--                                     the column is dropped and re-added
--                                     (ruling 2026-10-06, option a), the same
--                                     ADD COLUMN … CHECK dnd/0012 used. Stored
--                                     causes read NULL until the next
--                                     reconstruct, the "not classified"
--                                     reading dnd/0012's header already gives
--                                     a NULL, and the stage version bump
--                                     (reconstruct v5) forces that re-run.
--                                     DROP COLUMN needs SQLite 3.35 (2021-03)
--                                     or later.
--   dnd_attempts.prior_attempt_id     written by reconstruct (stage version 5):
--                                     the attempt_id of the same student's
--                                     most recent earlier attempt on the same
--                                     logged identifier in this database,
--                                     under the frozen segmentation; NULL
--                                     unless the attempt is resumed. INTEGER,
--                                     the type of attempt_id itself (the brief
--                                     wrote TEXT); a logical reference, like
--                                     canonical_question_id (dnd/0014), a
--                                     column, not a QC flag.
--   dnd_configurations.seeded         NULL unless the pass seeded resumed
--                                     attempts (D3.2 knob 10, "seeded
--                                     replay"); then 1 on every configuration
--                                     of a resumed attempt whose replay began
--                                     at the prior attempt's last decodable
--                                     checked configuration, 0 elsewhere, so
--                                     "resumed and seeded = 0" reads as the
--                                     s_0 fallback (the prior attempt had no
--                                     decodable check).
--   dnd_logging_regimes               one row per raw export (file_sha256 =
--                                     raw_imports.file_hash) whose component
--                                     logged no drop or reset row before a
--                                     calendar date: move_logging_from is the
--                                     date of the offering's first move row,
--                                     read from the committed diagnosis memo,
--                                     never from the data. Loaded by
--                                     `proteus dnd regime import` from the
--                                     committed file logging_regimes/
--                                     ula-proteus.csv (the alias-file pattern
--                                     of dnd/0014); the set's sha256 is
--                                     recorded by reconstruct and the run
--                                     manifest. Logical reference only: the
--                                     importer refuses a sha256 no import
--                                     carries. Keys are hashes and dates,
--                                     never a path, a pseudonym or a name.
--
-- Nullable, no defaults; with an empty regime table and seeding off, every
-- stage's output is unchanged except that a check of a resumed attempt is
-- stored not comparable with its cause instead of being compared (its
-- comparison still feeds the attempt's flags, so readiness is untouched).
-- Same posture as dnd/0002..0014.
-- =============================================================================

BEGIN;

ALTER TABLE dnd_configurations DROP COLUMN anchor_cause;

ALTER TABLE dnd_configurations ADD COLUMN anchor_cause TEXT
    CHECK (anchor_cause IN (
        'no_move_logging', 'resumed_attempt',
        'malformed', 'duplicate_card', 'unknown_card', 'unknown_zone',
        'offboard_slot'
    ));

ALTER TABLE dnd_attempts ADD COLUMN prior_attempt_id INTEGER;

ALTER TABLE dnd_configurations ADD COLUMN seeded INTEGER;

CREATE TABLE dnd_logging_regimes (
    file_sha256        TEXT PRIMARY KEY,  -- raw_imports.file_hash of the export
    move_logging_from  TEXT NOT NULL,     -- ISO calendar date of the offering's first move row
    reason             TEXT,
    source_memo        TEXT,              -- the committed memo the date is read from (a file name, never a path)
    recorded_on        TEXT,              -- ISO date the row was recorded
    imported_from      TEXT,              -- the regime file's name (never a path)
    imported_sha256    TEXT               -- sha256 of that file
);

COMMIT;
