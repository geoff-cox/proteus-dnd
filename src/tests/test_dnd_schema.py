"""
Tests for the DnD service schema (plan item B1.1).

The dnd_* tables arrive as the first service-owned migration (dnd/0001),
applied by the runner after core's migrations and recorded in the
applied_migrations ledger. A fresh bootstrap and a migrated database must
both end up with them (the bootstrap-parity property extends to service
sources; see test_schema_migration.TestBootstrapParity).

The cross-service FK invariant ("no DnD table FKs into an SA table") is
enforced generically in test_isolation_lint.TestSchemaIsolation.
"""

from __future__ import annotations

import sqlite3

import pytest

DND_TABLES = (
    "dnd_events",
    "dnd_boards",
    "dnd_board_cards",
    "dnd_board_zones",
    "dnd_attempts",
    "dnd_configurations",
    "dnd_segments",
    "dnd_check_scores",
    "dnd_attempt_metrics",
    "dnd_question_aliases",
    "dnd_logging_regimes",
    "dnd_account_roles",
)


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


class TestDndSchema:
    def test_fresh_bootstrap_creates_every_dnd_table(self, db_conn):
        for table in DND_TABLES:
            assert _table_exists(db_conn, table), table

    def test_migrations_recorded_in_service_ledger(self, db_conn):
        rows = db_conn.execute(
            "SELECT version, name FROM applied_migrations WHERE source = 'dnd' "
            "ORDER BY version"
        ).fetchall()
        assert [(r["version"], r["name"]) for r in rows] == [
            (1, "0001_dnd_schema"),
            (2, "0002_noop_move_flag"),
            (3, "0003_attempt_qc_flags"),
            (4, "0004_segments"),
            (5, "0005_check_feedback_policy"),
            (6, "0006_move_classification"),
            (7, "0007_check_cadence"),
            (8, "0008_time_features"),
            (9, "0009_outcome_features"),
            (10, "0010_error_profile_columns"),
            (11, "0011_repeat_state_features"),
            (12, "0012_anchor_cause"),
            (13, "0013_phase_window_features"),
            (14, "0014_question_aliases"),
            (15, "0015_resumed_anchors"),
            (16, "0016_account_roles"),
        ]

    def test_noop_move_columns_exist(self, db_conn):
        # Migration dnd/0002 (audit item F1): the noop_move flag on the
        # reconstruction layer and the excluded-count on the metrics row.
        config_cols = {
            r["name"] for r in db_conn.execute("PRAGMA table_info(dnd_configurations)")
        }
        metric_cols = {
            r["name"] for r in db_conn.execute("PRAGMA table_info(dnd_attempt_metrics)")
        }
        assert "is_noop" in config_cols
        assert "n_noop_moves" in metric_cols

    def test_qc_flag_columns_exist(self, db_conn):
        # Migration dnd/0003 (audit item F17b): the per-attempt QC flag
        # rollup and the three-level feature-readiness status.
        attempt_cols = {
            r["name"] for r in db_conn.execute("PRAGMA table_info(dnd_attempts)")
        }
        assert "qc_flags_json" in attempt_cols
        assert "readiness" in attempt_cols

    def test_readiness_is_constrained_to_the_vocabulary(self, db_conn):
        with pytest.raises(sqlite3.IntegrityError):
            db_conn.execute(
                "INSERT INTO dnd_attempts (student_id, question_id, "
                "attempt_index, readiness) VALUES ('s', 'q', 1, 'maybe')"
            )

    def test_move_mix_columns_exist(self, db_conn):
        # Migration dnd/0006 (audit item F6): the fixing/lateral/breaking
        # counts and the two excess measures on the metrics row.
        metric_cols = {
            r["name"] for r in db_conn.execute("PRAGMA table_info(dnd_attempt_metrics)")
        }
        assert {
            "n_fixing", "n_lateral", "n_breaking", "excess_moves", "path_excess"
        } <= metric_cols

    def test_check_structure_columns_exist(self, db_conn):
        # Migration dnd/0007 (audit item F7): the cadence sequence, the
        # post-check gains, and the responsiveness on the metrics row.
        metric_cols = {
            r["name"] for r in db_conn.execute("PRAGMA table_info(dnd_attempt_metrics)")
        }
        assert {
            "cadence_json", "post_check_gains_json", "responsiveness_json"
        } <= metric_cols

    def test_time_feature_columns_exist(self, db_conn):
        # Migration dnd/0008 (audit item F9): the basic timing summaries and
        # the recorded analytic idle threshold on the metrics row.
        metric_cols = {
            r["name"] for r in db_conn.execute("PRAGMA table_info(dnd_attempt_metrics)")
        }
        assert {
            "total_time_s", "time_to_first_move_s", "median_move_gap_s",
            "max_action_gap_s", "active_time_s", "idle_threshold_s",
        } <= metric_cols

    def test_outcome_columns_exist(self, db_conn):
        # Migration dnd/0009 (audit item F10): the modeling chapter's
        # attempt-level outcomes on the metrics row.
        metric_cols = {
            r["name"] for r in db_conn.execute("PRAGMA table_info(dnd_attempt_metrics)")
        }
        assert {
            "first_check_distance", "final_check_distance",
            "first_check_correct", "eventual_correct", "correct_check_ordinal",
        } <= metric_cols

    def test_phase_window_columns_exist(self, db_conn):
        # Migration dnd/0013 (Paper 1 F-NEW): the pre / repair window
        # restrictions of the movement and time features on the metrics row.
        metric_cols = {
            r["name"] for r in db_conn.execute("PRAGMA table_info(dnd_attempt_metrics)")
        }
        assert {
            "n_effective_moves_pre", "n_excess_moves_pre", "n_takebacks_pre",
            "n_resets_pre", "active_time_pre_s",
            "n_effective_moves_repair", "n_takebacks_repair",
            "n_resets_repair", "n_checks_repair", "active_time_repair_s",
            "n_fix_moves_repair",
        } <= metric_cols

    def test_error_profile_columns_exist(self, db_conn):
        # Migration dnd/0010 (audit item F12): the flat error-profile
        # components and the per-card score on the outcome layer.
        score_cols = {
            r["name"] for r in db_conn.execute("PRAGMA table_info(dnd_check_scores)")
        }
        assert {"omissions", "wrong_slot", "per_card_score"} <= score_cols

    def test_anchor_cause_column_exists_and_is_constrained(self, db_conn):
        # Migration dnd/0012 (cc-task-anchor-eligibility): the per-check
        # anchor-eligibility cause, nullable, CHECKed to the qc.py vocabulary.
        config_cols = {
            r["name"] for r in db_conn.execute("PRAGMA table_info(dnd_configurations)")
        }
        assert "anchor_cause" in config_cols
        db_conn.execute(
            "INSERT INTO dnd_attempts (student_id, question_id, attempt_index) "
            "VALUES ('s', 'q', 1)"
        )
        for seq, cause in enumerate((
            None, "malformed", "duplicate_card", "unknown_card",
            "unknown_zone", "offboard_slot",
        ), start=1):
            db_conn.execute(
                "INSERT INTO dnd_configurations (attempt_id, seq, s_json, "
                "is_check, anchor_cause) VALUES (1, ?, '[0]', 1, ?)",
                (seq, cause),
            )
        with pytest.raises(sqlite3.IntegrityError):
            db_conn.execute(
                "INSERT INTO dnd_configurations (attempt_id, seq, s_json, "
                "is_check, anchor_cause) VALUES (1, 9999, '[0]', 1, 'ambiguous')"
            )

    def test_feedback_policy_columns_exist(self, db_conn):
        # Migration dnd/0005 (audit item FP-3): the per-attempt era and the
        # per-check disclosure classification.
        attempt_cols = {
            r["name"] for r in db_conn.execute("PRAGMA table_info(dnd_attempts)")
        }
        config_cols = {
            r["name"] for r in db_conn.execute("PRAGMA table_info(dnd_configurations)")
        }
        assert "feedback_policy" in attempt_cols
        assert "disclosure_layer" in config_cols
        assert "graded_ordinal" in config_cols

    def test_feedback_policy_is_constrained_to_the_vocabulary(self, db_conn):
        with pytest.raises(sqlite3.IntegrityError):
            db_conn.execute(
                "INSERT INTO dnd_attempts (student_id, question_id, "
                "attempt_index, feedback_policy) VALUES ('s', 'q', 1, 'psychic')"
            )

    def test_disclosure_layer_is_constrained_to_the_vocabulary(self, db_conn):
        attempt_id = db_conn.execute(
            "INSERT INTO dnd_attempts (student_id, question_id, attempt_index) "
            "VALUES ('s', 'q', 1)"
        ).lastrowid
        with pytest.raises(sqlite3.IntegrityError):
            db_conn.execute(
                "INSERT INTO dnd_configurations (attempt_id, seq, s_json, "
                "is_check, disclosure_layer) VALUES (?, 1, '[]', 1, 'glowing')",
                (attempt_id,),
            )

    def test_segment_type_is_constrained_to_the_vocabulary(self, db_conn):
        # Migration dnd/0004 (audit item F3); 'active_work' is reserved but
        # allowed — only off-vocabulary types are rejected.
        attempt_id = db_conn.execute(
            "INSERT INTO dnd_attempts (student_id, question_id, attempt_index) "
            "VALUES ('s', 'q', 1)"
        ).lastrowid
        with pytest.raises(sqlite3.IntegrityError):
            db_conn.execute(
                "INSERT INTO dnd_segments (attempt_id, segment_type, "
                "segment_index, start_seq, end_seq) VALUES (?, 'sprint', 1, 0, 1)",
                (attempt_id,),
            )
        db_conn.execute(
            "INSERT INTO dnd_segments (attempt_id, segment_type, "
            "segment_index, start_seq, end_seq) VALUES (?, 'active_work', 1, 0, 1)",
            (attempt_id,),
        )

    def test_dnd_events_kind_is_constrained(self, db_conn):
        db_conn.execute(
            "INSERT INTO raw_imports (file_path, file_hash, file_format) "
            "VALUES ('x.tab', 'hash', 'datashop_tab')"
        )
        db_conn.execute(
            "INSERT INTO events (event_id, import_id, selection) "
            "VALUES ('ev1', 1, 'dragNdrop')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            db_conn.execute(
                "INSERT INTO dnd_events (event_id, kind) VALUES ('ev1', 'wiggle')"
            )

    def test_dnd_events_requires_a_real_core_event(self, db_conn):
        # FK into core events: a derived row cannot exist without its event.
        with pytest.raises(sqlite3.IntegrityError):
            db_conn.execute(
                "INSERT INTO dnd_events (event_id, kind) "
                "VALUES ('no-such-event', 'move')"
            )
