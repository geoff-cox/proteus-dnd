"""
Tests for board/data cross-validation (plan items B2.3/B2.4).

The fixture pipeline is the real one: sync the four-shape board from the
source fixture, ingest golden_dnd.tab, derive. DemoStudent1's Correct check
decodes to exactly the source key (1, 1, 2, 0) — the B2.3 match case — and
DemoStudent2's off-board move (drag9 -> drop7) plus the orphan card label
are the B2.4 gaps the fixture plants deliberately.
"""

from __future__ import annotations

import json
import logging
import shutil

import pytest

from proteus.core import ingest as core_ingest
from proteus.core import sources
from proteus.services.dragndrop import validate
from proteus.services.dragndrop.derive import derive_events

Q = "demo-book__demo-shapes-q1"


@pytest.fixture
def populated(db_conn, fixtures_dir, golden_dnd_tab, tmp_path):
    """Boards synced + events ingested + derived."""
    textbook_id = db_conn.execute(
        "INSERT INTO textbooks (git_repo_url, git_ref, book_xml_id, build_dir) "
        "VALUES ('r', 'v', 'demo-book', 'b')"
    ).lastrowid
    src = tmp_path / "src"
    src.mkdir()
    shutil.copy(
        fixtures_dir / "question_sources/sec-demo-shapes.ptx",
        src / "sec-demo-shapes.ptx",
    )
    sources.sync_question_sources(
        db_conn, src, textbook_id=textbook_id, output_dir=tmp_path / "cache"
    )
    core_ingest.ingest_file(db_conn, golden_dnd_tab)
    derive_events(db_conn)
    return db_conn


def _result_for(results, question_id):
    return next(r for r in results if r.question_id == question_id)


class TestKeyCrossValidation:
    def test_correct_check_confirms_source_key(self, populated):
        result = validate.validate_question(populated, Q)
        assert result.key_status == validate.KEY_MATCH
        assert result.source_key == (1, 1, 2, 0)
        assert result.inferred_keys == ((1, 1, 2, 0),)
        assert result.n_correct_checks == 1

    def test_no_correct_checks_is_unvalidated_but_ok(self, populated):
        # q2 has a board but no events at all.
        result = validate.validate_question(populated, "demo-book__demo-shapes-q2")
        assert result.key_status == validate.KEY_UNVALIDATED
        assert result.n_correct_checks == 0
        assert result.ok

    def test_stale_key_is_flagged_loudly(self, populated, caplog):
        # Simulate a stale source snapshot: the stored key disagrees with
        # the data's Correct configuration.
        populated.execute(
            "UPDATE dnd_boards SET key_json = ? WHERE question_id = ?",
            (json.dumps([1, 1, 2, 3]), Q),
        )
        with caplog.at_level(logging.ERROR):
            result = validate.validate_question(populated, Q)
        assert result.key_status == validate.KEY_MISMATCH
        assert not result.ok
        assert any("KEY MISMATCH" in m for m in caplog.messages)

    def test_two_distinct_correct_configurations_are_ambiguous(self, populated):
        # Plant a second, different "Correct" check directly.
        populated.execute(
            "INSERT INTO events (event_id, import_id, student_id, "
            "problem_name, selection, timestamp) "
            "VALUES ('planted1', 1, 'S3', ?, 'dragNdrop', "
            "'2026-02-07 10:00:00.000000')",
            (Q,),
        )
        configuration = {f"{Q}_drop1": [f"{Q}_drag1"]}
        populated.execute(
            "INSERT INTO dnd_events (event_id, kind, configuration_json, "
            "feedback) VALUES ('planted1', 'check', ?, 'Correct')",
            (json.dumps(configuration),),
        )
        result = validate.validate_question(populated, Q)
        assert result.key_status == validate.KEY_AMBIGUOUS
        assert len(result.inferred_keys) == 2
        assert not result.ok

    def test_non_eligible_correct_checks_do_not_witness_the_key(
        self, populated, caplog
    ):
        # cc-task-anchor-eligibility: a Correct check whose payload names a
        # slot off the R=3 board is not an eligible anchor, so it is counted
        # and skipped rather than inferring a second, different key (before
        # dnd/0012 this read KEY_AMBIGUOUS). The off-board label is still a
        # B2.4 gap: the question's label coverage fails as before.
        populated.execute(
            "INSERT INTO events (event_id, import_id, student_id, "
            "problem_name, selection, timestamp) "
            "VALUES ('planted2', 1, 'S4', ?, 'dragNdrop', "
            "'2026-02-07 11:00:00.000000')",
            (Q,),
        )
        configuration = {
            f"{Q}_drop1": [f"{Q}_drag1", f"{Q}_drag2"],
            f"{Q}_drop2": [f"{Q}_drag3"],
            f"{Q}_drop7": [f"{Q}_drag4"],
        }
        populated.execute(
            "INSERT INTO dnd_events (event_id, kind, configuration_json, "
            "feedback) VALUES ('planted2', 'check', ?, 'Correct')",
            (json.dumps(configuration),),
        )
        with caplog.at_level(logging.WARNING):
            result = validate.validate_question(populated, Q)
        assert result.key_status == validate.KEY_MATCH
        assert result.inferred_keys == ((1, 1, 2, 0),)
        assert result.n_correct_checks == 1
        assert result.n_ineligible_correct_checks == 1
        assert f"{Q}_drop7" in result.label_gaps
        assert "not eligible anchors" in caplog.text

    def test_malformed_correct_checks_are_counted_as_ineligible(self, populated):
        # A Correct check whose Input never parsed (parse_ok = 0) is the
        # `malformed` cause: counted among the ignored Correct checks, never
        # decoded, and no witness for the key (PR #216 review).
        populated.execute(
            "INSERT INTO events (event_id, import_id, student_id, "
            "problem_name, selection, timestamp) "
            "VALUES ('planted3', 1, 'S5', ?, 'dragNdrop', "
            "'2026-02-07 12:00:00.000000')",
            (Q,),
        )
        populated.execute(
            "INSERT INTO dnd_events (event_id, kind, configuration_json, "
            "feedback, parse_ok, parse_note) VALUES ('planted3', 'check', "
            "'{\"truncated', 'Correct', 0, 'check Input is not valid JSON')"
        )
        result = validate.validate_question(populated, Q)
        assert result.key_status == validate.KEY_MATCH
        assert result.inferred_keys == ((1, 1, 2, 0),)
        assert result.n_correct_checks == 1
        assert result.n_ineligible_correct_checks == 1


class TestLabelCoverage:
    def test_off_board_and_orphan_labels_are_reported(self, populated):
        result = validate.validate_question(populated, Q)
        assert result.label_gaps == (
            f"{Q}_drag9",  # move source beyond P=4
            f"{Q}_drop7",  # move destination beyond R=3
            "other-question_drag1",  # the orphan check's card label
        )
        assert not result.ok  # gaps fail the question even with key=match

    def test_on_board_labels_produce_no_gaps(self, populated):
        # Remove the two planted bad rows; everything that remains is on
        # the board, so the question validates clean.
        populated.execute(
            "DELETE FROM dnd_events WHERE event_id IN ("
            "SELECT d.event_id FROM dnd_events d JOIN events e "
            "USING (event_id) WHERE d.parse_note IS NOT NULL "
            "OR d.zone_label LIKE '%drop7')"
        )
        result = validate.validate_question(populated, Q)
        assert result.label_gaps == ()
        assert result.ok


class TestValidateAll:
    def test_union_of_boards_and_event_questions(self, populated):
        results = validate.validate_all(populated)
        assert [r.question_id for r in results] == [
            Q,
            "demo-book__demo-shapes-q2",
        ]

    def test_events_without_board_reported_as_no_board(self, populated, caplog):
        for table in ("dnd_board_cards", "dnd_board_zones", "dnd_boards"):
            populated.execute(
                f"DELETE FROM {table} WHERE question_id = ?", (Q,)
            )
        with caplog.at_level(logging.WARNING):
            result = _result_for(validate.validate_all(populated), Q)
        assert result.key_status == validate.NO_BOARD
        assert not result.ok
        # The inferred key is still reported. Without a board, P is taken
        # from the highest drag index seen in Correct checks — the decoy
        # card never appears there, so only three cards are visible.
        assert result.inferred_keys == ((1, 1, 2),)
        # Events present, board absent: the message points at sync, not at
        # an unknown question.
        assert "derived DnD events but no board" in caplog.text


class TestValidateUnknownQuestion:
    def test_unknown_question_id_is_not_told_it_has_events(
        self, populated, caplog
    ):
        # `proteus dnd validate --question <typo>`: neither board nor events.
        with caplog.at_level(logging.WARNING):
            result = validate.validate_question(populated, "demo-book__typo")
        assert result.key_status == validate.NO_BOARD
        assert not result.ok
        assert "no board and no derived DnD events" in caplog.text
        assert "derived DnD events but no board" not in caplog.text
