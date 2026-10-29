"""
Tests for the DnD board registry (plan items B2.1/B2.2).

The unit anchor is the reference page's four-shape question
(sec-sample-question): P=4 cards, R=3 slots, key s* = (1, 1, 2, 0), the
circle a decoy card and Pentagon a decoy slot. The integration tests run the
real source handler through core sync-sources over the fixture source tree.
"""

from __future__ import annotations

import shutil

import pytest

from proteus.core import sources
from proteus.core.registry import available_services
from proteus.services.dragndrop import board

Q = "demo-book__demo-shapes-q1"

FOUR_SHAPES_XML = """
<exercise xml:id="demo-shapes-q1">
  <statement><p>Match each shape to its general category.</p></statement>
  <cardsort>
    <match>
      <premise>equilateral triangle</premise>
      <premise>right triangle</premise>
      <response>Triangle</response>
    </match>
    <match>
      <premise>square</premise>
      <response>Quadrilateral</response>
    </match>
    <match>
      <response>Pentagon</response>
    </match>
    <match>
      <premise>circle</premise>
    </match>
  </cardsort>
</exercise>
"""


class TestBoardFromSource:
    def test_four_shape_reference_board(self):
        spec = board.board_from_source(Q, FOUR_SHAPES_XML)
        assert spec.n_cards == 4
        assert spec.n_zones == 3
        assert spec.cards == (
            "equilateral triangle",
            "right triangle",
            "square",
            "circle",
        )
        assert spec.zones == (None, "Triangle", "Quadrilateral", "Pentagon")
        assert spec.key == (1, 1, 2, 0)  # s* from the reference page
        assert spec.decoy_cards == (4,)  # the circle stays in the tray
        assert spec.decoy_zones == (3,)  # Pentagon stays empty

    def test_positional_indexing_is_document_order_across_matches(self):
        xml = """
        <cardsort>
          <match><premise>a</premise><response>RA</response></match>
          <match><premise>b</premise><premise>c</premise><response>RB</response></match>
        </cardsort>
        """
        spec = board.board_from_source("q", xml)
        assert spec.cards == ("a", "b", "c")
        assert spec.zones == (None, "RA", "RB")
        assert spec.key == (1, 2, 2)
        assert spec.decoy_cards == ()
        assert spec.decoy_zones == ()

    def test_markup_in_premises_is_preserved(self):
        xml = """
        <cardsort>
          <match>
            <premise><m>\\begin{bmatrix} 1 \\amp 0 \\end{bmatrix}</m></premise>
            <response>Identity</response>
          </match>
        </cardsort>
        """
        spec = board.board_from_source("q", xml)
        assert spec.cards[0].startswith("<m>")
        assert "bmatrix" in spec.cards[0]

    def test_non_cardsort_source_returns_none(self):
        assert board.board_from_source("q", "<exercise><shortanswer /></exercise>") is None

    def test_malformed_match_raises(self):
        xml = "<cardsort><match><premise>a</premise><response>r1</response><response>r2</response></match></cardsort>"
        with pytest.raises(ValueError, match="at most one"):
            board.board_from_source("q", xml)

    def test_empty_cardsort_raises(self):
        with pytest.raises(ValueError, match="no <premise>"):
            board.board_from_source("q", "<cardsort><match><response>r</response></match></cardsort>")

    def test_unparseable_source_raises(self):
        with pytest.raises(ValueError, match="not parseable"):
            board.board_from_source("q", "<cardsort><match>")


class TestPersistence:
    def test_write_load_round_trip(self, db_conn):
        spec = board.board_from_source(Q, FOUR_SHAPES_XML)
        db_conn.execute(
            "INSERT INTO textbooks (textbook_id, git_repo_url, git_ref, book_xml_id, "
            "build_dir) VALUES (1, 'r', 'v', 'demo-book', 'b')"
        )
        db_conn.execute(
            "INSERT INTO questions (question_id, textbook_id, xml_id, "
            "metadata_json, source) VALUES (?, 1, 'demo-shapes-q1', '{}', "
            "'auto_extracted')",
            (Q,),
        )
        board.write_board(db_conn, spec, source_hash="abc")
        assert board.load_board(db_conn, Q) == spec

        zones = db_conn.execute(
            "SELECT zone_index, content, is_decoy FROM dnd_board_zones "
            "WHERE question_id = ? ORDER BY zone_index",
            (Q,),
        ).fetchall()
        assert [tuple(z) for z in zones] == [
            (0, None, 0),  # the dragzone, never a decoy
            (1, "Triangle", 0),
            (2, "Quadrilateral", 0),
            (3, "Pentagon", 1),
        ]

    def test_load_missing_board_returns_none(self, db_conn):
        assert board.load_board(db_conn, "nope") is None


@pytest.fixture
def synced(db_conn, fixtures_dir, tmp_path):
    """A database synced from the fixture source tree via the real registry."""
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
    result = sources.sync_question_sources(
        db_conn, src, textbook_id=textbook_id, output_dir=tmp_path / "cache"
    )
    return db_conn, result, textbook_id


class TestSourceHandler:
    def test_sync_sources_builds_boards_through_the_registry(self, synced):
        conn, result, _ = synced
        assert result.handled["dnd"] == {"created": 2}
        spec = board.load_board(conn, Q)
        assert spec is not None
        assert spec.key == (1, 1, 2, 0)
        # The second (Runestone-labelled) question got a board too.
        q2 = board.load_board(conn, "demo-book__demo-shapes-q2")
        assert q2 is not None
        assert q2.key == (1, 2)
        # The shortanswer question did not.
        assert board.load_board(conn, "demo-book__demo-shapes-sa1") is None

    def test_rerun_is_unchanged_on_same_source(self, synced):
        conn, _, textbook_id = synced
        services = available_services()
        handled = sources.dispatch_source_handlers(
            conn, services=services, textbook_id=textbook_id
        )
        assert handled["dnd"] == {"unchanged": 2}

    def test_changed_source_hash_triggers_rebuild(self, synced):
        conn, _, _ = synced
        row = conn.execute(
            "SELECT * FROM questions WHERE question_id = ?", (Q,)
        ).fetchone()
        changed = dict(row)
        changed["source_xml"] = row["source_xml"].replace("circle", "ellipse")
        assert board.sync_board_for_question(conn, changed) == "updated"
        assert board.load_board(conn, Q).cards[3] == "ellipse"

    def test_handler_sets_question_type_when_missing(self, synced):
        conn, _, _ = synced
        conn.execute(
            "UPDATE questions SET question_type = NULL WHERE question_id = ?",
            (Q,),
        )
        row = conn.execute(
            "SELECT * FROM questions WHERE question_id = ?", (Q,)
        ).fetchone()
        assert board.sync_board_for_question(conn, row) == "unchanged"
        assert (
            conn.execute(
                "SELECT question_type FROM questions WHERE question_id = ?",
                (Q,),
            ).fetchone()["question_type"]
            == "cardsort"
        )

    def test_handler_skips_questions_without_cardsort(self, synced):
        conn, _, _ = synced
        row = conn.execute(
            "SELECT * FROM questions WHERE question_id = ?",
            ("demo-book__demo-shapes-sa1",),
        ).fetchone()
        if row is None:
            # Without the SA service the short-answer question is never
            # catalogued; the handler's contract is the same on a hand-built
            # row carrying its cardsort-free source (R14).
            row = {
                "question_id": "demo-book__demo-shapes-sa1",
                "question_type": "shortanswer",
                "source_xml": (
                    '<exercise xml:id="demo-shapes-sa1"><statement><p>Why?</p>'
                    "</statement><response/></exercise>"
                ),
            }
        assert board.sync_board_for_question(conn, row) == "skipped"


# The four-shape source re-authored without the Pentagon decoy slot: still
# P=4 cards and key (1,1,2,0), but R drops 3 → 2 (the F17a/F29 stale-board
# scenario: a re-sync that shrinks the board).
TWO_SLOT_XML = """
<exercise xml:id="demo-shapes-q1">
  <statement><p>Match each shape to its general category.</p></statement>
  <cardsort>
    <match>
      <premise>equilateral triangle</premise>
      <premise>right triangle</premise>
      <response>Triangle</response>
    </match>
    <match>
      <premise>square</premise>
      <response>Quadrilateral</response>
    </match>
    <match>
      <premise>circle</premise>
    </match>
  </cardsort>
</exercise>
"""


def _result_counts(conn, question_id) -> dict[str, int]:
    """Row counts of the question's reconstruction layer + downstream."""
    in_attempts = (
        "(SELECT attempt_id FROM dnd_attempts WHERE question_id = ?)"
    )
    queries = {
        "attempts": "SELECT count(*) FROM dnd_attempts WHERE question_id = ?",
        "configurations": (
            f"SELECT count(*) FROM dnd_configurations WHERE attempt_id IN {in_attempts}"
        ),
        "metrics": (
            f"SELECT count(*) FROM dnd_attempt_metrics WHERE attempt_id IN {in_attempts}"
        ),
        "scores": "SELECT count(*) FROM dnd_check_scores WHERE question_id = ?",
    }
    return {
        name: conn.execute(sql, (question_id,)).fetchone()[0]
        for name, sql in queries.items()
    }


class TestStaleResultInvalidation:
    """F29: a board-hash change clears the question's stale results (scores,
    configurations, attempts, metrics), forcing re-reconstruct/re-score; a
    same-hash re-sync leaves them alone. Without the clear, scores computed
    against the old key read as current after `dnd sync-boards`."""

    @pytest.fixture
    def scored(self, db_conn, golden_dnd_worked_example_tab, four_shapes_board):
        """The worked example fully piped: ingest → derive → board →
        reconstruct → score, so every result table has rows to invalidate."""
        from proteus.core import ingest as core_ingest
        from proteus.services.dragndrop.derive import derive_events
        from proteus.services.dragndrop.reconstruct import reconstruct_all
        from proteus.services.dragndrop.scoring import score_checks

        core_ingest.ingest_file(db_conn, golden_dnd_worked_example_tab)
        derive_events(db_conn)
        four_shapes_board(db_conn)
        reconstruct_all(db_conn)
        score_checks(db_conn)
        return db_conn

    def test_board_change_clears_stale_results(self, scored):
        before = _result_counts(scored, Q)
        assert before["scores"] > 0 and before["configurations"] > 0

        row = {"question_id": Q, "source_xml": TWO_SLOT_XML,
               "question_type": "cardsort"}
        status = board.sync_board_for_question(scored, row)

        assert status == "updated_results_cleared"
        assert board.load_board(scored, Q).n_zones == 2
        after = _result_counts(scored, Q)
        assert after == {
            "attempts": 0, "configurations": 0, "metrics": 0, "scores": 0,
        }

    def test_recompute_after_change_rebuilds_against_new_board(self, scored):
        from proteus.services.dragndrop import cohort
        from proteus.services.dragndrop.reconstruct import reconstruct_all
        from proteus.services.dragndrop.scoring import score_checks

        row = {"question_id": Q, "source_xml": TWO_SLOT_XML,
               "question_type": "cardsort"}
        board.sync_board_for_question(scored, row)
        reconstruct_all(scored)
        score_checks(scored)
        # The rebuilt results fit the new 2-slot board: the cohort read finds
        # nothing stale to exclude (the F17a guard stays quiet).
        data = cohort.cohort_data(scored, Q)
        assert data.n_zones == 2
        assert data.n_checks > 0
        assert data.n_excluded == 0

    def test_unchanged_rerun_keeps_results(self, scored):
        # First re-sync from real source XML (the fixture board was written
        # with a placeholder hash, so this clears and re-stamps), then rebuild.
        from proteus.services.dragndrop.reconstruct import reconstruct_all
        from proteus.services.dragndrop.scoring import score_checks

        row = {"question_id": Q, "source_xml": FOUR_SHAPES_XML,
               "question_type": "cardsort"}
        assert board.sync_board_for_question(scored, row) == "updated_results_cleared"
        reconstruct_all(scored)
        score_checks(scored)
        before = _result_counts(scored, Q)
        assert before["scores"] > 0

        # Same source again: unchanged, and nothing is cleared.
        assert board.sync_board_for_question(scored, row) == "unchanged"
        assert _result_counts(scored, Q) == before
