"""
Tests for the shared event-ingest pipeline (plan items A1.2 + A1.4 + A1.5).

The core ingest parses *every* row of a DataShop export into the generic
`events` table — no Selection filter — so any question-type service can later
derive its own tables. The load-bearing invariant for the whole refactor is
that the short-answer rows captured in `events` still reconstruct today's
`responses` byte-for-byte; that is anchored here against the frozen golden
baseline (and will be enforced through the real SA derive in A3).
"""

from __future__ import annotations

import hashlib
import sqlite3

import pytest

from proteus.core.ingest import IngestResult, backfill_events
from proteus.core.ingest import ingest_file as ingest_events
from proteus.core.ingest.events import Event
from proteus.core.ingest.readers import datashop as core_datashop
from proteus.core.ingest.readers import runestone as core_runestone
from proteus.core import registry

# Service-conditional cases (cc-task-public-export PR 1, ruling R14): keyed on
# registry discovery, so the same file runs in the private tree (sa + dnd) and
# in the public export (dnd only).
SA = "sa" in {s.name for s in registry.available_services()}
requires_sa = pytest.mark.skipif(
    not SA, reason="the Short Answer service is not in this tree (R14)"
)

# The core fixture's selection composition (golden_core.tab, hand-made; the
# same composition as the real-data golden_sa.tab, which the SA tests keep):
# 33 rows total.
EXPECTED_PER_SELECTION = {
    "shortanswer": 22,
    "page": 2,
    "mChoice": 1,
    "dragNdrop-drop": 3,
    "dragNdrop": 2,
    "poll": 1,
    "sage": 1,
    "dragNdrop-reset": 1,
}
EXPECTED_TOTAL = sum(EXPECTED_PER_SELECTION.values())  # 33


def _events(conn):
    return conn.execute(
        "SELECT event_id, import_id, student_id, session_id, problem_name, "
        "selection, action, input, feedback_text, feedback_classification, "
        "problem_view, timestamp, course_id FROM events"
    ).fetchall()


class TestPureReader:
    def test_reads_every_row_no_selection_filter(self, golden_core_tab):
        evs = list(core_datashop.read(golden_core_tab))
        assert len(evs) == EXPECTED_TOTAL
        from collections import Counter

        assert dict(Counter(e.selection for e in evs)) == EXPECTED_PER_SELECTION

    def test_missing_required_columns_raises(self, tmp_path):
        f = tmp_path / "broken.tab"
        f.write_text("Time\tAnon Student Id\nx\ty\n")
        with pytest.raises(ValueError, match="missing required columns"):
            list(core_datashop.read(f))

    def test_problem_view_column_is_required(self, tmp_path):
        # Problem View is part of event_id, so the reader must require it
        # rather than let it silently default to empty for every row.
        f = tmp_path / "no_pv.tab"
        f.write_text(
            "Time\tAnon Student Id\tProblem Name\tSelection\n"
            "2026-01-01 00:00:00\ts1\tq1\tshortanswer\n"
        )
        with pytest.raises(ValueError, match="Problem View"):
            list(core_datashop.read(f))

    def test_action_kept_raw_for_service_normalization(self, golden_core_tab):
        # The reader must not normalize Action; SA derive applies normalize_text
        # downstream. At least one fixture row has trailing whitespace in Action.
        evs = list(core_datashop.read(golden_core_tab))
        assert any(e.action != e.action.rstrip() for e in evs)

    def test_reader_maps_filter_columns_from_golden(self, golden_core_tab):
        # CD-1: Class/CF (Week No)/Level (Chapter)/Level (SubChapter) become the
        # event's filter dimensions. The core fixture has Class=1 throughout,
        # Week No in {8, 10}, an always-blank Chapter, and exactly two rows with
        # a SubChapter — so a blank cell maps to None, a present one to its text.
        evs = list(core_datashop.read(golden_core_tab))
        assert {e.class_id for e in evs} == {"1"}
        assert {e.week_no for e in evs} == {"8", "10"}
        assert all(e.chapter is None for e in evs)  # blank cell -> None
        assert sum(1 for e in evs if e.subchapter is not None) == 2

    def test_reader_filter_columns_blank_or_absent_become_none(self, tmp_path):
        # A blank cell and a column the export omits both map to None (never ""),
        # matching how course_id has always been handled.
        f = tmp_path / "filters.tab"
        f.write_text(
            "Time\tAnon Student Id\tProblem Name\tSelection\tProblem View\t"
            "Class\tCF (Week No)\tLevel (Chapter)\tLevel (SubChapter)\n"
            # fully populated
            "2026-01-01 00:00:00\ts1\tq1\tshortanswer\t1\t"
            "7\t9\tChapter 3\tSection 3.2\n"
            # blank filter cells -> None
            "2026-01-01 00:00:01\ts2\tq1\tshortanswer\t1\t \t\t\t\n"
        )
        a, b = list(core_datashop.read(f))
        assert (a.class_id, a.week_no, a.chapter, a.subchapter) == (
            "7", "9", "Chapter 3", "Section 3.2",
        )
        assert (b.class_id, b.week_no, b.chapter, b.subchapter) == (
            None, None, None, None,
        )
        # course_id's column is absent entirely here -> None, not "".
        assert a.course_id is None


class TestEventIdentity:
    def test_event_id_is_deterministic_content_hash(self):
        e = Event(
            student_id="s1", session_id="888", problem_name="q1",
            selection="shortanswer", action="hi", input="", feedback_text="",
            feedback_classification="", problem_view="1", timestamp="2026-01-01 00:00:00",
            course_id="university",
        )
        expected = hashlib.sha256(
            "s1|q1|2026-01-01 00:00:00|shortanswer|1".encode("utf-8")
        ).hexdigest()[:16]
        assert e.event_id == expected

    def test_identity_ignores_action_and_feedback(self):
        base = dict(
            student_id="s1", session_id="888", problem_name="q1",
            selection="shortanswer", input="", feedback_text="",
            feedback_classification="", problem_view="1",
            timestamp="2026-01-01 00:00:00", course_id=None,
        )
        assert Event(action="a", **base).event_id == Event(action="b", **base).event_id

    def test_identity_ignores_filter_columns(self):
        # CD-1 SA-safety: the filter dimensions are not part of event identity,
        # so adding/populating them leaves event_id unchanged — the guarantee
        # that the SA derive stays byte-identical (golden test holds).
        base = dict(
            student_id="s1", session_id="888", problem_name="q1",
            selection="shortanswer", action="hi", input="", feedback_text="",
            feedback_classification="", problem_view="1",
            timestamp="2026-01-01 00:00:00", course_id="u",
        )
        bare = Event(**base)
        with_filters = Event(
            **base, class_id="1", week_no="8", chapter="c", subchapter="s"
        )
        assert bare.event_id == with_filters.event_id

    def test_intra_file_collision_with_different_payload_warns(
        self, db_conn, caplog
    ):
        # Two DISTINCT logged rows sharing the identity fields (the hash
        # excludes action/input) collide on event_id; the second is skipped.
        # That skip is silent data loss, so write_events must warn about it —
        # distinguishably from the benign identical-payload duplicate and
        # from the normal re-ingest skip (audit 2026-08-21, BUG identity
        # collision; see dev/audits/2026-08-21-pipeline-audit/).
        from proteus.core.ingest.events import write_events

        base = dict(
            student_id="s1", session_id="888", problem_name="q1",
            selection="dragNdrop-drop", input="", feedback_text="",
            feedback_classification="", problem_view="1",
            timestamp="2026-01-01 00:00:00", course_id=None,
        )
        first = Event(action="q1_drag1 -> q1_drop1", **base)
        second = Event(action="q1_drag2 -> q1_drop2", **base)  # distinct row
        db_conn.execute(
            "INSERT INTO raw_imports (file_path, file_hash, file_format) "
            "VALUES ('x', 'h-collision', 'datashop_tab')"
        )
        import_id = db_conn.execute(
            "SELECT import_id FROM raw_imports WHERE file_hash = 'h-collision'"
        ).fetchone()["import_id"]

        import logging

        with caplog.at_level(logging.WARNING, logger="proteus.core.ingest.events"):
            inserted, skipped = write_events(db_conn, import_id, [first, second])
        assert (inserted, skipped) == (1, 1)
        assert any(
            "Intra-file event_id collision" in rec.message
            for rec in caplog.records
        ), "distinct-payload identity collision must be warned about"

        # A benign re-ingest of the same row (a later call) stays quiet.
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="proteus.core.ingest.events"):
            inserted, skipped = write_events(db_conn, import_id, [first])
        assert (inserted, skipped) == (0, 1)
        assert not any(
            "Intra-file event_id collision" in rec.message
            for rec in caplog.records
        ), "an ordinary re-ingest duplicate must not raise the collision warning"

        # Re-ingesting the whole collision-bearing file (both rows now skip
        # against the stored one) must still surface the collision: the first
        # skip is recorded so the second, distinct-payload skip warns
        # (PR #165 review).
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="proteus.core.ingest.events"):
            inserted, skipped = write_events(db_conn, import_id, [first, second])
        assert (inserted, skipped) == (0, 2)
        assert any(
            "Intra-file event_id collision" in rec.message
            for rec in caplog.records
        ), "a re-ingest of a collision-bearing file must still warn"


class TestCoreIngest:
    def test_ingest_writes_all_rows_to_events(self, db_conn, golden_core_tab):
        result = ingest_events(db_conn, golden_core_tab)
        assert isinstance(result, IngestResult)
        assert result.inserted == EXPECTED_TOTAL
        assert result.skipped == 0
        assert result.total_seen == EXPECTED_TOTAL
        assert result.per_selection == EXPECTED_PER_SELECTION
        assert len(_events(db_conn)) == EXPECTED_TOTAL

    def test_ingest_persists_filter_columns(self, db_conn, golden_core_tab):
        # CD-1: a fresh ingest writes the filter dimensions straight to events.
        ingest_events(db_conn, golden_core_tab)
        rows = db_conn.execute(
            "SELECT class_id, week_no, chapter, subchapter FROM events"
        ).fetchall()
        assert {r["class_id"] for r in rows} == {"1"}
        assert {r["week_no"] for r in rows} == {"8", "10"}
        assert all(r["chapter"] is None for r in rows)
        assert sum(1 for r in rows if r["subchapter"] is not None) == 2

    @requires_sa
    def test_ingest_does_not_write_responses(self, db_conn, golden_core_tab):
        # Stage 0 is responses-agnostic: SA responses come later, from derive
        # (the table itself exists only with the SA service).
        ingest_events(db_conn, golden_core_tab)
        n = db_conn.execute("SELECT count(*) FROM responses").fetchone()[0]
        assert n == 0

    def test_ingest_registers_one_import(self, db_conn, golden_core_tab):
        result = ingest_events(db_conn, golden_core_tab)
        rows = db_conn.execute(
            "SELECT import_id, file_format FROM raw_imports"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["import_id"] == result.import_id
        assert rows[0]["file_format"] == "datashop_tab"

    def test_reingest_same_file_refused(self, db_conn, golden_core_tab):
        ingest_events(db_conn, golden_core_tab)
        with pytest.raises(FileExistsError, match="already imported"):
            ingest_events(db_conn, golden_core_tab)

    def test_events_are_idempotent_at_event_level(self, db_conn, golden_core_tab):
        # Writing the same events twice (e.g. via backfill) skips duplicates.
        from proteus.core.ingest import events as events_mod

        evs = list(core_datashop.read(golden_core_tab))
        import_id = db_conn.execute(
            "INSERT INTO raw_imports (file_path, file_hash, file_format, row_count) "
            "VALUES ('x','hashx','datashop_tab',?)",
            (len(evs),),
        ).lastrowid
        db_conn.commit()
        ins1, skip1 = events_mod.write_events(db_conn, import_id, evs)
        ins2, skip2 = events_mod.write_events(db_conn, import_id, evs)
        assert (ins1, skip1) == (EXPECTED_TOTAL, 0)
        assert (ins2, skip2) == (0, EXPECTED_TOTAL)

    def test_non_dedup_integrity_error_propagates(self, db_conn, golden_core_tab):
        # A non-duplicate integrity error (here a bad import_id foreign key)
        # must propagate, not be silently counted as a skipped duplicate.
        from proteus.core.ingest import events as events_mod

        evs = list(core_datashop.read(golden_core_tab))[:1]
        with pytest.raises(sqlite3.IntegrityError):
            events_mod.write_events(db_conn, 999_999, evs)

    def test_missing_file_raises(self, db_conn, tmp_path):
        with pytest.raises(FileNotFoundError):
            ingest_events(db_conn, tmp_path / "nope.tab")

    def test_unknown_extension_requires_explicit_format(self, db_conn, tmp_path):
        f = tmp_path / "data.txt"
        f.write_text("nope")
        with pytest.raises(ValueError, match="auto-detect"):
            ingest_events(db_conn, f)


class TestBackfill:
    def test_backfill_after_sa_ingest_adds_events_only(
        self, db_conn, golden_sa_tab, sa_legacy_ingest
    ):
        # Realistic A1 flow: an existing DB was SA-ingested (responses present,
        # no events). Backfill adds events under the SAME import_id, untouched
        # responses.
        sa_result = sa_legacy_ingest(db_conn, golden_sa_tab)
        responses_before = db_conn.execute(
            "SELECT count(*) FROM responses"
        ).fetchone()[0]
        assert responses_before == 21  # golden baseline

        bf = backfill_events(db_conn, golden_sa_tab)
        assert bf.import_id == sa_result.import_id
        assert bf.inserted == EXPECTED_TOTAL
        assert len(_events(db_conn)) == EXPECTED_TOTAL
        # Events are linked to the existing import; no new raw_imports row.
        assert db_conn.execute("SELECT count(*) FROM raw_imports").fetchone()[0] == 1
        # Responses untouched.
        assert db_conn.execute("SELECT count(*) FROM responses").fetchone()[0] == 21

    def test_backfill_is_idempotent(self, db_conn, golden_sa_tab, sa_legacy_ingest):
        sa_legacy_ingest(db_conn, golden_sa_tab)
        backfill_events(db_conn, golden_sa_tab)
        again = backfill_events(db_conn, golden_sa_tab)
        assert again.inserted == 0
        assert again.skipped == EXPECTED_TOTAL
        assert len(_events(db_conn)) == EXPECTED_TOTAL

    def test_backfill_requires_prior_import(self, db_conn, golden_core_tab):
        with pytest.raises(ValueError, match="not yet imported"):
            backfill_events(db_conn, golden_core_tab)


class TestFilterColumnBackfill:
    """
    CD-1 backfill: events ingested before migration 0008 carry NULL filter
    columns; re-parsing the retained file fills those blanks in place, scoped to
    the import and never overwriting an already-populated value. New ingests
    already populate them on insert.
    """

    def _register_import(self, conn, n_rows: int, tag: str = "a") -> int:
        import_id = conn.execute(
            "INSERT INTO raw_imports (file_path, file_hash, file_format, row_count) "
            "VALUES (?, ?, 'datashop_tab', ?)",
            (f"{tag}.tab", f"hash-{tag}", n_rows),
        ).lastrowid
        conn.commit()
        return import_id

    def _seed_pre0008(self, conn, import_id, evs) -> None:
        # Insert via the old 13-column shape so the filter columns stay NULL,
        # exactly as a pre-migration-0008 ingest left them.
        for e in evs:
            conn.execute(
                "INSERT INTO events (event_id, import_id, student_id, session_id, "
                "problem_name, selection, action, input, feedback_text, "
                "feedback_classification, problem_view, timestamp, course_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    e.event_id, import_id, e.student_id, e.session_id,
                    e.problem_name, e.selection, e.action, e.input,
                    e.feedback_text, e.feedback_classification, e.problem_view,
                    e.timestamp, e.course_id,
                ),
            )
        conn.commit()

    def test_backfill_fills_preexisting_null_rows(self, db_conn, golden_core_tab):
        from proteus.core.ingest import events as events_mod

        evs = list(core_datashop.read(golden_core_tab))
        import_id = self._register_import(db_conn, len(evs))
        self._seed_pre0008(db_conn, import_id, evs)
        assert db_conn.execute(
            "SELECT count(*) FROM events WHERE class_id IS NOT NULL"
        ).fetchone()[0] == 0

        updated = events_mod.backfill_filter_columns(db_conn, import_id, evs)

        assert updated == EXPECTED_TOTAL
        rows = db_conn.execute(
            "SELECT class_id, week_no, subchapter FROM events"
        ).fetchall()
        assert {r["class_id"] for r in rows} == {"1"}
        assert {r["week_no"] for r in rows} == {"8", "10"}
        assert sum(1 for r in rows if r["subchapter"] is not None) == 2

    def test_backfill_idempotent_and_scoped_to_import(self, db_conn, golden_core_tab):
        # Resolves the review: the import_id parameter is load-bearing (a
        # different import touches nothing), and a second run is a no-op.
        from proteus.core.ingest import events as events_mod

        evs = list(core_datashop.read(golden_core_tab))
        import_id = self._register_import(db_conn, len(evs))
        self._seed_pre0008(db_conn, import_id, evs)

        # Scoped to the import: a non-matching import_id backfills nothing.
        assert events_mod.backfill_filter_columns(db_conn, import_id + 999, evs) == 0
        assert db_conn.execute(
            "SELECT count(*) FROM events WHERE class_id IS NOT NULL"
        ).fetchone()[0] == 0

        # First run fills every pre-0008 row; the second finds none left.
        assert (
            events_mod.backfill_filter_columns(db_conn, import_id, evs)
            == EXPECTED_TOTAL
        )
        assert events_mod.backfill_filter_columns(db_conn, import_id, evs) == 0

    def test_backfill_never_clobbers_populated_rows(self, db_conn, golden_core_tab):
        # Resolves the review: backfill fills only all-NULL rows, so an already
        # populated value (e.g. if the retained export were later edited) is not
        # overwritten.
        from proteus.core.ingest import events as events_mod

        evs = list(core_datashop.read(golden_core_tab))
        import_id = self._register_import(db_conn, len(evs))
        events_mod.write_events(db_conn, import_id, evs)  # populated on insert
        edited = evs[0]
        db_conn.execute(
            "UPDATE events SET class_id = 'EDITED' WHERE event_id = ?",
            (edited.event_id,),
        )
        db_conn.commit()

        assert events_mod.backfill_filter_columns(db_conn, import_id, evs) == 0
        assert db_conn.execute(
            "SELECT class_id FROM events WHERE event_id = ?", (edited.event_id,)
        ).fetchone()[0] == "EDITED"

    def test_backfill_events_path_populates_filter_columns(
        self, db_conn, golden_sa_tab, sa_legacy_ingest
    ):
        # The realistic A1.5 -> CD-1 flow: a legacy SA-only DB (responses, no
        # events) reingested. write_events inserts events with the columns set;
        # the backfill pass then guarantees population regardless.
        sa_legacy_ingest(db_conn, golden_sa_tab)
        assert db_conn.execute("SELECT count(*) FROM events").fetchone()[0] == 0

        backfill_events(db_conn, golden_sa_tab)

        rows = db_conn.execute("SELECT class_id, week_no FROM events").fetchall()
        assert len(rows) == EXPECTED_TOTAL
        assert all(r["class_id"] == "1" for r in rows)
        assert all(r["week_no"] is not None for r in rows)


# The forward golden anchor that lived here (an inline events→responses
# reconstruction) has been replaced by the real SA derive; the authority is
# now test_sa_responses_golden.TestSaDeriveGolden (item A3.4).


class TestRunestoneReader:
    """
    The core Runestone useinfo reader (A3.3): every CSV row becomes an Event,
    no event-type filter, so `.csv` exports flow through the same shared
    ingest as `.tab` files.
    """

    def test_reads_every_row_no_event_filter(self, sample_csv):
        evs = list(core_runestone.read(sample_csv))
        assert len(evs) == 5
        by_selection = {}
        for e in evs:
            by_selection[e.selection] = by_selection.get(e.selection, 0) + 1
        assert by_selection == {"shortanswer": 3, "page": 2}

    def test_event_field_mapping(self, sample_csv):
        first = next(iter(core_runestone.read(sample_csv)))
        assert first.student_id == "212I-005-21"
        assert first.problem_name == (
            "ula-proteus__ula-proteus-1-4-pivots-reading-q3"
        )
        assert first.action.startswith("With 4 equations")
        assert first.timestamp == "2026-01-16 21:44:22"
        assert first.course_id == "StLouis_S26_ULA"
        # Columns the useinfo format does not carry stay empty, keeping the
        # event_id identity well-formed.
        assert first.session_id == ""
        assert first.problem_view == ""
        assert first.input == ""

    def test_missing_required_columns_raises(self, tmp_path):
        bad = tmp_path / "bad.csv"
        bad.write_text("id,timestamp,sid\n1,2026-01-01 00:00:00,s1\n")
        with pytest.raises(ValueError, match="missing required columns"):
            list(core_runestone.read(bad))

    def test_csv_autodetected_and_ingested_to_events(self, db_conn, sample_csv):
        result = ingest_events(db_conn, sample_csv)
        assert result.file_format == "runestone_useinfo_csv"
        assert result.inserted == 5
        assert result.per_selection == {"shortanswer": 3, "page": 2}
        assert len(_events(db_conn)) == 5
