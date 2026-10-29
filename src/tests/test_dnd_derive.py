"""
Tests for DnD derive and label decoding (plan items B1.2/B1.4).

The fixture (golden_dnd.tab) encodes the reference page's illustrative
four-shape attempt plus the pathological rows the checklist calls out:

- one row of every kind (move, takeback, check, reset, page_view);
- the truncated-`Action`-but-valid-`Input` check (rule 6: Input is
  authoritative; Action truncates);
- quoted-JSON unescaping from the .tab format (the export wraps JSON in
  quotes and doubles the inner ones; the reader must hand derive clean JSON);
- an orphan check (configuration referencing an unknown drag label →
  flagged in parse_note, not fatal);
- a check whose Input itself is truncated (invalid JSON → parse_ok = 0,
  raw text preserved);
- a move with an unparseable Action (parse_ok = 0).
"""

from __future__ import annotations

import json

import pytest

from proteus.core import ingest as core_ingest
from proteus.services.dragndrop import decode
from proteus.services.dragndrop.derive import classify_event, derive_events

Q = "demo-book__demo-shapes-q1"


@pytest.fixture
def derived(db_conn, golden_dnd_tab):
    """Ingest the fixture and run one derive pass; returns (conn, result)."""
    core_ingest.ingest_file(db_conn, golden_dnd_tab)
    result = derive_events(db_conn)
    return db_conn, result


def _rows(conn, **where):
    sql = (
        "SELECT d.*, e.problem_name, e.timestamp FROM dnd_events d "
        "JOIN events e USING (event_id)"
    )
    clauses, params = [], []
    for col, value in where.items():
        clauses.append(f"d.{col} = ?")
        params.append(value)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY e.timestamp"
    return conn.execute(sql, params).fetchall()


class TestDeriveClassification:
    def test_counts(self, derived):
        _, result = derived
        # 13 DnD-relevant rows; the shortanswer row is not seen at all.
        assert result.seen == 13
        assert result.inserted == 13
        assert result.skipped == 0
        # The truncated-Input check and the garbage move.
        assert result.malformed == 2

    def test_every_kind_present(self, derived):
        conn, _ = derived
        by_kind = {
            row["kind"]: row["n"]
            for row in conn.execute(
                "SELECT kind, count(*) AS n FROM dnd_events GROUP BY kind"
            )
        }
        assert by_kind == {
            # Four placements, the off-board move, and the unparseable
            # Action row (written as a move with parse_ok = 0).
            "move": 6,
            "takeback": 1,
            "check": 4,
            "reset": 1,
            "page_view": 1,
        }

    def test_moves_carry_labels(self, derived):
        conn, _ = derived
        first = _rows(conn, kind="move")[0]
        assert first["source_label"] == f"{Q}_drag1"
        assert first["zone_label"] == f"{Q}_drop1"
        assert first["parse_ok"] == 1

    def test_takeback_is_classified_by_dragzone_destination(self, derived):
        conn, _ = derived
        (takeback,) = _rows(conn, kind="takeback")
        assert takeback["source_label"] == f"{Q}_drag4"
        assert takeback["zone_label"] == "dragzone"

    def test_reset_recorded_bare(self, derived):
        conn, _ = derived
        (reset,) = _rows(conn, kind="reset")
        assert reset["source_label"] is None
        assert reset["configuration_json"] is None
        assert reset["parse_ok"] == 1

    def test_page_row_recorded_as_page_view(self, derived):
        conn, _ = derived
        (page,) = _rows(conn, kind="page_view")
        assert page["problem_name"] == "sec-demo-shapes"

    def test_shortanswer_rows_are_not_derived(self, derived):
        conn, _ = derived
        row = conn.execute(
            "SELECT count(*) AS n FROM dnd_events d JOIN events e "
            "USING (event_id) WHERE e.selection = 'shortanswer'"
        ).fetchone()
        assert row["n"] == 0

    def test_classify_event_rejects_foreign_selection(self):
        with pytest.raises(ValueError):
            classify_event({"selection": "shortanswer", "event_id": "x"})


class TestCheckRows:
    def test_input_is_authoritative_despite_truncated_action(self, derived):
        # The fixture's first check truncates Action mid-JSON exactly like
        # the real export; Input holds the full configuration. The derived
        # row must carry the *complete* JSON (and it must round-trip,
        # proving the .tab quote-doubling was undone by the reader).
        conn, _ = derived
        first_check = _rows(conn, kind="check", feedback="Incorrect")[0]
        assert first_check["parse_ok"] == 1
        configuration = json.loads(first_check["configuration_json"])
        assert configuration[f"{Q}_drop3"] == [f"{Q}_drag4"]
        assert configuration[f"{Q}_drop1"] == [f"{Q}_drag1", f"{Q}_drag2"]

    def test_correct_check_carries_feedback(self, derived):
        conn, _ = derived
        (correct,) = _rows(conn, kind="check", feedback="Correct")
        configuration = json.loads(correct["configuration_json"])
        assert configuration[f"{Q}_drop3"] == []

    def test_truncated_input_is_malformed_but_preserved(self, derived):
        conn, _ = derived
        (bad,) = _rows(conn, kind="check", parse_ok=0)
        assert bad["parse_note"].startswith("check Input is not valid JSON")
        # The raw evidence survives for forensics.
        assert bad["configuration_json"].startswith(f'{{"{Q}_drop1"')

    def test_orphan_label_is_flagged_not_fatal(self, derived):
        conn, _ = derived
        orphans = [
            row
            for row in _rows(conn, kind="check", parse_ok=1)
            if row["parse_note"]
        ]
        assert len(orphans) == 1
        assert "unknown card label 'other-question_drag1'" in orphans[0]["parse_note"]


class TestMalformedMoves:
    def test_garbage_action_is_written_with_parse_ok_zero(self, derived):
        conn, _ = derived
        bad_moves = _rows(conn, kind="move", parse_ok=0)
        assert len(bad_moves) == 1
        assert "garbage-action" in bad_moves[0]["parse_note"]
        assert bad_moves[0]["source_label"] is None


class TestIdempotencyAndFilters:
    def test_rederive_skips_everything(self, derived):
        conn, _ = derived
        again = derive_events(conn)
        assert again.seen == 13
        assert again.inserted == 0
        assert again.skipped == 13
        assert again.malformed == 0  # nothing newly written

    def test_question_filter_excludes_page_rows(self, db_conn, golden_dnd_tab):
        core_ingest.ingest_file(db_conn, golden_dnd_tab)
        result = derive_events(db_conn, question_id=Q)
        assert result.seen == 12  # everything but the page row
        assert (
            db_conn.execute(
                "SELECT count(*) AS n FROM dnd_events WHERE kind = 'page_view'"
            ).fetchone()["n"]
            == 0
        )

    def test_import_filter(self, db_conn, golden_dnd_tab):
        ingest = core_ingest.ingest_file(db_conn, golden_dnd_tab)
        assert derive_events(db_conn, import_id=ingest.import_id).inserted == 13
        assert derive_events(db_conn, import_id=ingest.import_id + 1).seen == 0


class TestParseLabel:
    def test_drag_and_drop_labels(self):
        assert decode.parse_label(f"{Q}_drag3", Q) == decode.ParsedLabel("drag", 3)
        assert decode.parse_label(f"{Q}_drop12", Q) == decode.ParsedLabel("drop", 12)

    def test_dragzone_is_zone_zero(self):
        assert decode.parse_label("dragzone", Q) == decode.ParsedLabel("dragzone", 0)

    def test_foreign_or_malformed_labels_return_none(self):
        assert decode.parse_label("other-question_drag1", Q) is None
        assert decode.parse_label(f"{Q}_dragX", Q) is None
        assert decode.parse_label(f"{Q}_drag", Q) is None
        assert decode.parse_label("", Q) is None

    def test_zero_index_is_rejected(self):
        # drag/drop are 1-based; index 0 is reserved for the literal
        # `dragzone` tray, so drag0/drop0 are malformed (a drop0 must never
        # slip through as an implicit zone-0 tray downstream).
        assert decode.parse_label(f"{Q}_drag0", Q) is None
        assert decode.parse_label(f"{Q}_drop0", Q) is None


class TestDecodeConfiguration:
    def test_reference_page_first_check(self):
        # s = (1, 1, 2, 3): both triangles in zone 1, square in 2, circle
        # forced into the decoy slot 3 (the s vector of sec-configuration-vector).
        configuration = {
            f"{Q}_drop1": [f"{Q}_drag1", f"{Q}_drag2"],
            f"{Q}_drop2": [f"{Q}_drag3"],
            f"{Q}_drop3": [f"{Q}_drag4"],
        }
        decoded = decode.decode_configuration(configuration, Q, n_cards=4)
        assert decoded.s == (1, 1, 2, 3)
        assert decoded.unknown_labels == ()
        assert decoded.duplicate_cards == ()

    def test_absent_cards_sit_in_the_dragzone(self):
        configuration = {
            f"{Q}_drop1": [f"{Q}_drag1", f"{Q}_drag2"],
            f"{Q}_drop2": [f"{Q}_drag3"],
            f"{Q}_drop3": [],
        }
        decoded = decode.decode_configuration(configuration, Q, n_cards=4)
        assert decoded.s == (1, 1, 2, 0)  # the key configuration s*

    def test_unknown_labels_are_collected_not_fatal(self):
        configuration = {
            f"{Q}_drop1": ["other-question_drag1", f"{Q}_drag9"],
            "mystery-zone": [f"{Q}_drag2"],
        }
        decoded = decode.decode_configuration(configuration, Q, n_cards=4)
        assert decoded.s == (0, 0, 0, 0)
        assert "other-question_drag1" in decoded.unknown_labels
        assert f"{Q}_drag9" in decoded.unknown_labels  # index beyond P
        assert "mystery-zone" in decoded.unknown_labels

    def test_duplicate_card_keeps_first_zone(self):
        configuration = {
            f"{Q}_drop1": [f"{Q}_drag1"],
            f"{Q}_drop2": [f"{Q}_drag1"],
        }
        decoded = decode.decode_configuration(configuration, Q, n_cards=4)
        assert decoded.s == (1, 0, 0, 0)
        assert decoded.duplicate_cards == (1,)
        assert decoded.cause == "duplicate_card"

    def test_unknown_labels_are_split_by_kind(self):
        # dnd/0012: the decoder tells unknown cards from unknown zones so the
        # stored cause can; `unknown_labels` stays as the union.
        configuration = {
            f"{Q}_drop1": ["other-question_drag1", f"{Q}_drag9"],
            "mystery-zone": [f"{Q}_drag2"],
        }
        decoded = decode.decode_configuration(configuration, Q, n_cards=4)
        assert decoded.unknown_cards == ("other-question_drag1", f"{Q}_drag9")
        assert decoded.unknown_zones == ("mystery-zone",)
        assert decoded.unknown_labels == (
            "mystery-zone", "other-question_drag1", f"{Q}_drag9",
        )
        assert decoded.cause == "unknown_card"  # cards outrank zones

    def test_offboard_slot_is_reported_only_when_r_is_given(self):
        configuration = {
            f"{Q}_drop1": [f"{Q}_drag1"],
            f"{Q}_drop7": [f"{Q}_drag2"],
            f"{Q}_drop8": [],
        }
        unbounded = decode.decode_configuration(configuration, Q, n_cards=4)
        # No R: face value, as replay without a board — an eligible decode.
        assert unbounded.s == (1, 7, 0, 0)
        assert unbounded.offboard_slots == ()
        assert unbounded.cause is None
        bounded = decode.decode_configuration(
            configuration, Q, n_cards=4, n_zones=3
        )
        # The vector is unchanged; the test is reported, an empty off-board
        # slot included (the payload names a slot the board does not have).
        assert bounded.s == (1, 7, 0, 0)
        assert bounded.offboard_slots == (7, 8)
        assert bounded.cause == "offboard_slot"

    def test_repeat_within_one_zone_is_not_a_duplicate(self):
        # The frozen rule rejects a card listed in two *zones*; the same card
        # twice in one zone's list is one placement, so the anchor stays
        # eligible (PR #216 review).
        configuration = {f"{Q}_drop1": [f"{Q}_drag1", f"{Q}_drag1"]}
        decoded = decode.decode_configuration(
            configuration, Q, n_cards=4, n_zones=3
        )
        assert decoded.s == (1, 0, 0, 0)
        assert decoded.duplicate_cards == ()
        assert decoded.cause is None

    def test_cards_under_an_unknown_zone_are_still_checked(self):
        # The zone is reported and its cards stay in the tray, but their
        # labels are still examined, so an unknown card label there is
        # found and outranks the unknown zone (PR #216 review). A valid card
        # under an unknown zone is not a placement, hence never a duplicate.
        configuration = {
            "mystery-zone": ["other-question_drag1", f"{Q}_drag2"],
            f"{Q}_drop1": [f"{Q}_drag2"],
        }
        decoded = decode.decode_configuration(
            configuration, Q, n_cards=4, n_zones=3
        )
        assert decoded.unknown_zones == ("mystery-zone",)
        assert decoded.unknown_cards == ("other-question_drag1",)
        assert decoded.duplicate_cards == ()
        assert decoded.s == (0, 1, 0, 0)
        assert decoded.cause == "unknown_card"

    def test_cause_precedence_follows_the_vocabulary_order(self):
        everything = {
            f"{Q}_drop1": [f"{Q}_drag1"],
            f"{Q}_drop2": [f"{Q}_drag1", f"{Q}_drag9"],
            "mystery-zone": [],
            f"{Q}_drop7": [],
        }
        decoded = decode.decode_configuration(everything, Q, n_cards=4, n_zones=3)
        assert decoded.duplicate_cards == (1,)
        assert decoded.unknown_cards == (f"{Q}_drag9",)
        assert decoded.unknown_zones == ("mystery-zone",)
        assert decoded.offboard_slots == (7,)
        assert decoded.cause == "duplicate_card"
        del everything[f"{Q}_drop2"]
        everything[f"{Q}_drop3"] = [f"{Q}_drag9"]
        assert decode.decode_configuration(
            everything, Q, n_cards=4, n_zones=3
        ).cause == "unknown_card"
        del everything[f"{Q}_drop3"]
        assert decode.decode_configuration(
            everything, Q, n_cards=4, n_zones=3
        ).cause == "unknown_zone"
        del everything["mystery-zone"]
        assert decode.decode_configuration(
            everything, Q, n_cards=4, n_zones=3
        ).cause == "offboard_slot"
        del everything[f"{Q}_drop7"]
        assert decode.decode_configuration(
            everything, Q, n_cards=4, n_zones=3
        ).cause is None
