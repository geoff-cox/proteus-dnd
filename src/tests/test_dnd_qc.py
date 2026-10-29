"""
Tests for the QC flag vocabulary + readiness policy (alignment item F17b).

Pure-function tests only: the vocabulary encoding and the
subsec-feature-readiness mapping. The end-to-end rollup (reconstruct writing
``dnd_attempts.qc_flags_json`` / ``.readiness`` from real timelines) is
tested beside the replay in ``test_dnd_reconstruct.TestQcRollup``, and the
export surface in ``test_dnd_export``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from proteus.services.dragndrop import qc


class TestEncodeFlags:
    def test_canonical_order_regardless_of_input_order(self):
        encoded = qc.encode_flags({"noop_move", "parse_error", "replay_ok"})
        assert json.loads(encoded) == ["replay_ok", "parse_error", "noop_move"]

    def test_empty_set_encodes_to_empty_array(self):
        assert qc.encode_flags(set()) == "[]"

    def test_unknown_flag_is_rejected_loudly(self):
        # A typo silently dropped from storage would defeat the discipline.
        with pytest.raises(ValueError, match="unknown QC flag"):
            qc.encode_flags({"replay_ok", "totally_fine"})

    def test_vocabulary_matches_the_journal_table(self):
        # tab-quality-control-flags, in table order.
        assert qc.FLAG_ORDER == (
            "replay_ok", "check_mismatch", "parse_error",
            "unknown_event_type", "missing_source_map",
            "row_order_ambiguous", "invalid_state", "noop_move",
            "duplicate_row", "session_break", "long_idle_gap",
        )


class TestAnchorCauses:
    def test_vocabulary_and_precedence_order(self):
        # paper1-decisions.md 2026-09-28 (cc-task-anchor-eligibility), amended
        # 2026-10-05 to two tiers (cc-task-resumed-anchors): the one cause
        # stored on a non-eligible check, first applicable name wins — the
        # two structural causes before the five decode causes.
        assert qc.ANCHOR_CAUSE_ORDER == (
            "no_move_logging", "resumed_attempt",
            "malformed", "duplicate_card", "unknown_card", "unknown_zone",
            "offboard_slot",
        )

    def test_migration_check_constraint_mirrors_the_vocabulary(self):
        # dnd/0015 re-adds the column with the seven-name CHECK (dnd/0012's
        # five-name CHECK is history).
        sql = (
            Path(qc.__file__).parent / "migrations" / "0015_resumed_anchors.sql"
        ).read_text(encoding="utf-8")
        for cause in qc.ANCHOR_CAUSE_ORDER:
            assert f"'{cause}'" in sql, cause


class TestReadiness:
    def test_verified_replay_is_ready(self):
        assert qc.readiness({"replay_ok"}) == qc.READY

    def test_noop_move_is_informational_and_stays_ready(self):
        # F1 flags, replays, and downstream-excludes no-op drops; the state
        # model holds and every anchor agrees, so the attempt stays ready.
        assert qc.readiness({"replay_ok", "noop_move"}) == qc.READY

    def test_no_flags_means_no_verified_anchor_hence_limited(self):
        # An attempt with no comparable check cannot claim replay_ok.
        assert qc.readiness(set()) == qc.LIMITED

    def test_warning_flags_demote_to_limited(self):
        # unknown_event_type is reserved (no signal emits it yet) but is in
        # the vocabulary, so its readiness consequence is pinned too.
        for warning in (
            "check_mismatch", "parse_error", "unknown_event_type",
            "row_order_ambiguous", "duplicate_row", "session_break",
            "long_idle_gap",
        ):
            assert qc.readiness({"replay_ok", warning}) == qc.LIMITED, warning

    def test_missing_source_map_excludes(self):
        assert qc.readiness({"missing_source_map", "replay_ok"}) == qc.EXCLUDE

    def test_invalid_state_excludes_even_when_anchors_agree(self):
        assert qc.readiness({"replay_ok", "invalid_state"}) == qc.EXCLUDE
