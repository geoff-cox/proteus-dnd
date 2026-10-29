# Test fixtures

## Short-answer golden anchor (plan item A0.1)

`golden_sa.tab` and `golden_sa_responses.json` are the frozen short-answer
regression anchor used by `test_sa_responses_golden.py`.

### `golden_sa.tab`

A **byte-faithful** subset of `data/S26_ULA_datashop.tab` — rows are copied
verbatim (no re-serialization), so quoting and whitespace are preserved
exactly as the real DataShop export wrote them. It was selected to exercise
the load-bearing normalization and filtering paths:

- 22 `shortanswer` rows, deliberately including
  - the one response containing non-ASCII text (U+2212 MINUS SIGN), which
    pins NFC normalization;
  - several rows with trailing whitespace in `Action`, which pin `rstrip`;
  - one malformed row with an empty `Problem Name`, which the parser skips;
  - a spread across multiple students and questions.
- 11 non-short-answer rows — `dragNdrop-drop`, `dragNdrop`, `dragNdrop-reset`,
  `page`, `mChoice`, `poll`, `sage` — that the SA path must ignore. These are
  the rows the current ingest silently drops; keeping them in the fixture
  means the Phase A refactor (generic `events` parse + per-service `derive`)
  cannot change *which* rows become responses without this test noticing.

### `golden_sa_responses.json`

The recorded result of `SELECT * FROM responses ORDER BY response_id` after
ingesting `golden_sa.tab` through the current public ingest path — 21 rows
(22 short-answer rows minus the malformed empty-`Problem Name` row).

**This baseline is frozen.** Per `CLAUDE.md` rule 1, if
`test_sa_responses_golden` fails, the change under review is wrong — do **not**
regenerate this file to make the test pass. The SA service's future `derive`
step (item A3.4) must reproduce these rows byte-for-byte.

## Core ingest fixture (`cc-task-public-export` PR 1, ruling R17)

- `golden_core.tab` — a **hand-made** DataShop export in the real column
  layout with `golden_sa.tab`'s selection composition (33 rows: 22
  `shortanswer`, 3 `dragNdrop-drop`, 2 `dragNdrop`, 1 `dragNdrop-reset`,
  2 `page`, 1 `mChoice`, 1 `poll`, 1 `sage`) and the properties the core tests
  pin — two `shortanswer` rows with trailing whitespace in `Action`, one
  non-ASCII (U+2212) response, one empty response, one malformed row with an
  empty `Problem Name`, `Class` 1 throughout, `CF (Week No)` 8 except one
  week-10 row, a blank `Level (Chapter)`, a `Level (SubChapter)` on exactly
  the two page rows — but every student (`CoreStudent01`–`06`), item
  (`demo-book__core-*`, the demo DnD item) and response text is invented.
  The service-agnostic tests (`test_core_ingest.py`, `test_cli_ingest.py`,
  `test_cli_status.py`, `test_cli_sync_catalog.py`, `test_catalog.py`, …)
  read it instead of the real-data `golden_sa.tab`, which stays with the SA
  service; the SA tests keep the frozen golden.

## Other fixtures

- `sample.tab`, `sample.csv`, `sample_mangled_timestamps.csv` — small,
  hand-made fixtures for the parser/ingest unit tests.

## Drag-and-drop golden fixtures (the DnD golden rule of `CLAUDE.md`)

- `golden_dnd.tab` — the reference trace of the guide's *Raw Data* section
  (`DemoStudent1`) and the pathological attempt (`DemoStudent2`), pinned by
  the DnD golden test; the manual audit's training packet T2 is drawn from
  the pathological attempt.
- `golden_dnd_worked_example.tab` — the manuscript's six-move worked example
  (`WorkedStudent`), pinned by `test_dnd_worked_example.py`; training packet T1.
- `golden_dnd_training_anomalies.tab` — two hand-made attempts on the same
  item, added 2026-10-15 for training kit v1.1 (the audit protocol's
  2026-10-15 "Training supplement" amendment line): `TrainingStudent3`, a
  no-op drop, a reset and a 320-second gap (QC flags `noop_move`,
  `long_idle_gap`); `TrainingStudent4`, a repeated drop (`duplicate_row`, and
  the `noop_move` it also is) and a drop and a CHECK logged at one timestamp
  and Problem View (`row_order_ambiguous`). Both attempts read `limited`.
  Pinned by `test_dnd_training_anomalies.py`; training packets T3 and T4 are
  drawn from them. The tie is a drop and a CHECK, not two drops, because two
  drops at one timestamp and Problem View share an event identity and the
  later one is dropped at ingest.
- `golden_dnd_paper_tables.tab` — seventeen hand-made single-sitting attempts
  (`PaperStudent*`, 2026-05), added 2026-10-18 for the manuscript-table
  generator's tests (`cc-task-paper-tables.md`, Checkpoint 1 ruling R12):
  five clean attempts on the demo item (`PaperStudent01`–`05`; two end on
  the key, three do not), so one item carries five students with an eligible
  anchor; three disagreeing eligible anchors at card distance exactly 1, one
  per diagnostic signal — a dropped move alone (`PaperStudentDrop`,
  `check_mismatch` only), a drop and a CHECK logged at one timestamp and
  Problem View with the CHECK first in file order (`PaperStudentTie`,
  `row_order_ambiguous`), and an off-board drop to `drop4` beside a dropped
  move (`PaperStudentOffboard`, `invalid_state`); one non-eligible CHECK per
  decode cause (`PaperStudentMalformed`, `PaperStudentDupCard`,
  `PaperStudentUnknownCard`, `PaperStudentUnknownZone`,
  `PaperStudentOffboardSlot`: `malformed`, `duplicate_card`, `unknown_card`,
  `unknown_zone`, `offboard_slot`); a second item `demo-book__demo-shapes-q2`
  on the same four-shape board with one clean attempt (`PaperStudentQ2`), one
  attempt of six agreeing CHECKs (`PaperStudentMany`) and one attempt logged
  under the retired identifier `demo-book__demo-shapes-q2-old`
  (`PaperStudentOld`; the test registers the alias before reconstructing);
  and a board-less item `demo-book__demo-shapes-q3` whose one CHECK
  disagrees with the replay (`PaperStudentNoBoard`, `missing_source_map`,
  still an eligible anchor — no board, no R bound). Pinned by
  `test_dnd_paper_tables_fixture.py`, which ingests it beside the two
  fixtures above: 30 checks, 23 eligible anchors, 19 agreeing, 4
  disagreeing, seven non-eligible checks by cause.
- `golden_dnd_attrition.tab` — sixteen hand-made students and seventeen
  sessions (`Attrition*`, 2026-06, one import of 71 rows; sixteen attempts by
  fifteen students once the staff account is withheld at derive), added 2026-10-19 for the
  staff-account filter and the stage-attrition ledger
  (`cc-task-attrition-ledger.md`, E-02; PR 1 spec §2.6), built so that every
  exclusion step of the manuscript's `subsec-analysis-unit` loses at least
  one row: `AttritionStaff1`, the excluded instructor of
  `account_roles_fixture.csv` (a page row and a clean attempt; withheld at
  derive, kept in `events`); `AttritionRuledKept`, ruled `student, exclude
  no`; three clean attempts (`AttritionClean1`–`3`); `AttritionRevised`
  (first CHECK incorrect, then correct); `AttritionSA` (a `shortanswer` and
  an `mChoice` row beside a one-move attempt); `AttritionNoBoard` on the
  board-less `demo-book__demo-shapes-q9` (`missing_source_map`);
  `AttritionMalformed` (one truncated CHECK payload, `parse_error`);
  `AttritionDupCard` (one card in two zones, `duplicate_card`);
  `AttritionResumed` (a second session two hours later whose only CHECK
  carries a duplicate-card payload under the structural `resumed_attempt`
  cause — the hidden step-5 case); `AttritionRegime` (a single CHECK dated
  2026-06-01, `not json`, stored `no_move_logging` once the test records a
  regime row dated 2026-06-02 against the file's sha256); `AttritionLimited`
  (a 320-second gap, `long_idle_gap`); `AttritionMany` (six CHECKs from one
  student); `AttritionNoCheck` (no CHECK, NULL outcomes); `AttritionTakeback`
  (a takeback and a reset). Every CHECK carries its payload in both `Action`
  and `Input`. Pinned by `test_dnd_attrition_fixture.py` (readiness, flag set
  and per-check `anchor_cause` for every attempt, each hand-derived) and used
  by `test_dnd_account_roles.py` and `test_cli_dnd_roles.py`. Frozen once
  pinned.
- `account_roles_fixture.csv` — the shape of the owner's staff-account
  rulings file of 2026-10-01 (two comment lines and the header `pseudonym,
  anon_student_id, raw_rows, role, exclude, ruled_by, ruled_on, note`) with
  two made-up ids: `AttritionStaff1` (instructor, excluded, 5 raw rows) and
  `AttritionRuledKept` (student, kept, 4 raw rows) — never a real id. Loaded
  by `dnd roles import` in the tests above. Frozen once pinned.
- `question_sources/sec-demo-shapes.ptx` — the demo item's source, synced
  through the real sync path to give the boards (P = 4, R = 3, key 1,1,2,0).

Frozen: never "update" these files or their pinned outputs to make a test
pass; a difference means the replay changed.

