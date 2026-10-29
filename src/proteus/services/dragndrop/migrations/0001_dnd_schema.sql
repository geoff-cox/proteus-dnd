-- =============================================================================
-- DnD service migration 0001 (plan item B1.1): the Drag-and-Drop service's
-- tables, applied by core.migrate after core's migrations and tracked in the
-- applied_migrations ledger under source 'dnd'.
--
-- Isolation (plan §2.4, lint-enforced by test_isolation_lint): dnd_* tables
-- may FK into core tables (events, questions) and into other dnd_* tables,
-- NEVER into another service's tables.
--
-- Layering:
--   dnd_events          one typed row per dragNdrop*/page event (B1.2 derive)
--   dnd_boards/cards/   the board registry: P cards, R zones, key s*, decoys,
--     zones              built from the cardsort source (B2.1/B2.2)
--   dnd_attempts/        reconstruction layer: replayed configurations and
--     configurations     attempt segmentation (filled by B3.1/B3.2)
--   dnd_check_scores     outcome layer: all scoring variants side-by-side
--                        (filled by B3.3; policy is decided later, plan R5)
--   dnd_attempt_metrics  process layer (filled by B3.5)
--
-- Timestamps stay in core `events` as opaque sortable strings (rule 6);
-- dnd tables reference events rather than copying time columns.
-- =============================================================================

BEGIN;

-- One row per derived DnD-relevant event. `kind` is the service's typed view
-- of the raw Selection column: dragNdrop-drop rows split into move/takeback
-- (takeback = destination dragzone), dragNdrop rows are checks, dragNdrop-reset
-- rows resets, and page rows page_views (kept because they help delimit
-- attempts, plan B3.1).
CREATE TABLE dnd_events (
    event_id           TEXT PRIMARY KEY,
    kind               TEXT NOT NULL CHECK (kind IN ('move', 'takeback', 'check', 'reset', 'page_view')),
    source_label       TEXT,               -- moves: the dragged card's data label (*_drag#)
    zone_label         TEXT,               -- moves: the destination zone's data label (*_drop# or 'dragzone')
    configuration_json TEXT,               -- checks: the Input column verbatim (authoritative; Action truncates)
    feedback           TEXT,               -- checks: Feedback Classification ('Correct' / 'Incorrect')
    parse_ok           INTEGER NOT NULL DEFAULT 1,  -- 0 = the row's payload could not be parsed
    parse_note         TEXT,               -- human-readable parse warnings (kept even when parse_ok = 1)
    FOREIGN KEY (event_id) REFERENCES events(event_id)
);

CREATE INDEX idx_dnd_events_kind ON dnd_events(kind);

-- Board registry (plan B2.1): one board per cardsort question, derived from
-- the <match> blocks of the question source. Indexing is positional in source
-- order — card j is *_drag<j>, slot i is *_drop<i>, and the dragzone tray is
-- zone 0 (never stored as a card's label, always as zone index 0).
CREATE TABLE dnd_boards (
    question_id      TEXT PRIMARY KEY,
    source_hash      TEXT NOT NULL,        -- sha256 of the question source the board was built from
    n_cards          INTEGER NOT NULL,     -- P
    n_zones          INTEGER NOT NULL,     -- R (slots only; the dragzone is zone 0, not counted)
    key_json         TEXT NOT NULL,        -- s* as a JSON list of P zone indices (0 = stays in tray)
    built_at         TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (question_id) REFERENCES questions(question_id)
);

CREATE TABLE dnd_board_cards (
    question_id      TEXT NOT NULL,
    card_index       INTEGER NOT NULL,     -- positional, 1-based: *_drag<card_index>
    content          TEXT NOT NULL,        -- authored premise content (verbatim inner XML)
    home_zone        INTEGER NOT NULL,     -- key zone; 0 = decoy card meant to stay in the tray
    PRIMARY KEY (question_id, card_index),
    FOREIGN KEY (question_id) REFERENCES dnd_boards(question_id)
);

CREATE TABLE dnd_board_zones (
    question_id      TEXT NOT NULL,
    zone_index       INTEGER NOT NULL,     -- 0 = dragzone; 1..R = *_drop<zone_index>
    content          TEXT,                 -- authored response content; NULL for the dragzone
    is_decoy         INTEGER NOT NULL DEFAULT 0,  -- 1 = slot meant to stay empty
    PRIMARY KEY (question_id, zone_index),
    FOREIGN KEY (question_id) REFERENCES dnd_boards(question_id)
);

-- Reconstruction layer (filled by B3.1). student/question are logical
-- references like responses.question_id — events can be derived before the
-- question catalog is synced.
CREATE TABLE dnd_attempts (
    attempt_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id        TEXT NOT NULL,
    question_id       TEXT NOT NULL,
    attempt_index     INTEGER NOT NULL,    -- 1-based per student × question
    session_id        TEXT,
    started_at        TEXT,                -- opaque timestamp strings, as in events
    ended_at          TEXT,
    segmentation_rule TEXT,                -- record of the rule/thresholds used (B3.1)
    UNIQUE (student_id, question_id, attempt_index)
);

CREATE INDEX idx_dnd_attempts_question ON dnd_attempts(question_id);

-- One row per configuration the replay passes through; seq 0 is the initial
-- state X0 (all cards in the tray). s_json is the minimal state vector
-- (s(1), …, s(P)) of zone indices.
CREATE TABLE dnd_configurations (
    config_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_id       INTEGER NOT NULL,
    seq              INTEGER NOT NULL,     -- 0 = X0; increments per action
    event_id         TEXT,                 -- the action producing this state; NULL for X0
    s_json           TEXT NOT NULL,
    is_check         INTEGER NOT NULL DEFAULT 0,
    matches_input    INTEGER,              -- B3.2 cross-check vs the check row's Input; NULL = not a check / not yet checked
    UNIQUE (attempt_id, seq),
    FOREIGN KEY (attempt_id) REFERENCES dnd_attempts(attempt_id),
    FOREIGN KEY (event_id)   REFERENCES dnd_events(event_id)
);

-- Outcome layer (filled by B3.3): every scoring variant side-by-side; the
-- labeled-vs-label-free policy is resolved later per question (plan R5).
CREATE TABLE dnd_check_scores (
    event_id          TEXT PRIMARY KEY,    -- the check event scored
    attempt_id        INTEGER,
    question_id       TEXT NOT NULL,
    sigma             INTEGER,             -- labeled agreement σ
    distance          INTEGER,             -- d = P − σ
    g                 REAL,                -- baseline-corrected (σ−σ(X0))/(P−σ(X0))
    sigma_tilde       INTEGER,             -- label-free σ̃ (confusion-matrix assignment)
    per_card_json     TEXT,                -- per-card correctness list
    all_or_nothing    INTEGER,             -- 1 iff the configuration equals the key
    decoy_source_leak INTEGER,             -- decoy-card leak L_P: count of decoy cards out of the tray
    decoy_target_leak INTEGER,             -- decoy-slot leak L_R: count of cards placed into decoy slots
    FOREIGN KEY (event_id)   REFERENCES dnd_events(event_id),
    FOREIGN KEY (attempt_id) REFERENCES dnd_attempts(attempt_id)
);

CREATE INDEX idx_dnd_check_scores_question ON dnd_check_scores(question_id);

-- Process layer (filled by B3.5).
CREATE TABLE dnd_attempt_metrics (
    attempt_id            INTEGER PRIMARY KEY,
    n_moves               INTEGER,
    n_takebacks           INTEGER,
    n_resets              INTEGER,
    n_checks              INTEGER,
    time_to_first_check_s REAL,
    convergence_json      TEXT,            -- d(t) curve serialized as JSON
    checked_every_move    INTEGER,         -- "checked after every small change" probe indicator
    FOREIGN KEY (attempt_id) REFERENCES dnd_attempts(attempt_id)
);

COMMIT;
