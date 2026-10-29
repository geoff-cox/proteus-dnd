"""
Tests for the question catalog (checklist item CD-2).

`sync_catalog` materializes one `questions` row per distinct non-page problem in
`events`, with a registry-inferred type, leaving textbook_id NULL and
source='catalog'; it is idempotent and never clobbers a source-synced type.
`question_catalog` is the read-time projection the front door consumes.
"""

from __future__ import annotations

import logging

import pytest

from proteus.core import catalog
from proteus.core.ingest import backfill_events
from proteus.core.ingest import ingest_file as core_ingest_file
from proteus.core.ingest.events import Event, write_events
from proteus.core import registry

# Service-conditional cases (cc-task-public-export PR 1, ruling R14): keyed on
# registry discovery, so the same file runs in the private tree (sa + dnd) and
# in the public export (dnd only).
SA = "sa" in {s.name for s in registry.available_services()}
requires_sa = pytest.mark.skipif(
    not SA, reason="the Short Answer service is not in this tree (R14)"
)


def _register_import(conn, tag: str = "a") -> int:
    cur = conn.execute(
        "INSERT INTO raw_imports (file_path, file_hash, file_format, row_count) "
        "VALUES (?, ?, 'datashop_tab', 0)",
        (f"{tag}.tab", f"hash-{tag}"),
    )
    conn.commit()
    return cur.lastrowid


def _ev(problem, selection, *, student="s1", ts="2026-01-01 09:00:00", pv="1",
        class_id="1", course_id="C1", week_no="8") -> Event:
    return Event(
        student_id=student, session_id="x", problem_name=problem,
        selection=selection, action="", input="", feedback_text="",
        feedback_classification="", problem_view=pv, timestamp=ts,
        course_id=course_id, class_id=class_id, week_no=week_no,
    )


# A scenario covering: a serviced SA question (in two classes/courses), a
# serviced DnD question (three selection variants → one question), two unserviced
# types, a page event on a real question (ignored), and a page-only "problem"
# (never catalogued).
def _seed_events(conn) -> int:
    import_id = _register_import(conn)
    events = [
        _ev("qa", "shortanswer", student="s1", ts="2026-01-01 09:00:00",
            class_id="1", course_id="C1"),
        _ev("qa", "shortanswer", student="s2", ts="2026-01-05 09:00:00",
            class_id="2", course_id="C2"),
        _ev("qa", "page", student="s1", ts="2026-01-01 08:59:00"),  # ignored
        _ev("qd", "dragNdrop", student="s1", ts="2026-01-02 09:00:00"),
        _ev("qd", "dragNdrop-drop", student="s1", ts="2026-01-02 09:00:00"),
        _ev("qd", "dragNdrop-reset", student="s1", ts="2026-01-02 09:00:00"),
        _ev("qm", "mChoice", student="s1", ts="2026-01-03 09:00:00",
            class_id="3", course_id="C3"),
        _ev("qp", "poll", student="s1", ts="2026-01-04 09:00:00"),
        _ev("qpage", "page", student="s1", ts="2026-01-01 07:00:00"),  # page-only
    ]
    write_events(conn, import_id, events)
    return import_id


class TestInferQuestionType:
    @requires_sa
    def test_sa_inference_against_real_registry(self):
        index = catalog._selection_index()
        assert catalog.infer_question_type({"shortanswer"}, index=index) == (
            "shortanswer", "sa",
        )

    def test_inference_against_real_registry(self):
        index = catalog._selection_index()
        assert catalog.infer_question_type(
            {"dragNdrop", "dragNdrop-drop", "dragNdrop-reset"}, index=index
        ) == ("cardsort", "dnd")
        # Unserviced: the selection value is the type; no service.
        assert catalog.infer_question_type({"mChoice"}, index=index) == (
            "mChoice", None,
        )
        # Multiple unserviced selections -> deterministic (sorted-first).
        assert catalog.infer_question_type({"sage", "poll"}, index=index) == (
            "poll", None,
        )
        assert catalog.infer_question_type(set(), index=index) == (None, None)


class TestSyncCatalog:
    def test_creates_one_row_per_nonpage_problem(self, db_conn):
        _seed_events(db_conn)
        result = catalog.sync_catalog(db_conn)

        assert result.total == 4
        assert result.created == 4
        # qa (sa) and qd (dnd) are serviced; without the SA service qa is an
        # unserviced question whose type is its selection value.
        assert result.serviced == (2 if SA else 1)
        assert result.unserviced == (2 if SA else 3)  # qm, qp (+ qa)
        assert result.by_type == {
            "shortanswer": 1, "cardsort": 1, "mChoice": 1, "poll": 1,
        }

        rows = {
            r["question_id"]: r
            for r in db_conn.execute(
                "SELECT question_id, question_type, textbook_id, source "
                "FROM questions"
            ).fetchall()
        }
        assert set(rows) == {"qa", "qd", "qm", "qp"}  # qpage excluded
        assert rows["qa"]["question_type"] == "shortanswer"
        assert rows["qd"]["question_type"] == "cardsort"
        assert rows["qm"]["question_type"] == "mChoice"
        assert rows["qp"]["question_type"] == "poll"
        for r in rows.values():
            assert r["textbook_id"] is None
            assert r["source"] == "catalog"

    def test_idempotent(self, db_conn):
        _seed_events(db_conn)
        catalog.sync_catalog(db_conn)
        second = catalog.sync_catalog(db_conn)

        assert second.total == 4
        assert second.created == 0
        assert db_conn.execute("SELECT count(*) FROM questions").fetchone()[0] == 4

    def test_never_clobbers_source_synced_type(self, db_conn):
        # A textbook-synced question (real textbook_id, type set by source sync)
        # that also appears in events must be left untouched by the catalog.
        db_conn.execute(
            "INSERT INTO textbooks (textbook_id, git_repo_url, git_ref, "
            "book_xml_id, build_dir) VALUES (1, 'r', 'v', 'demo', 'b')"
        )
        db_conn.execute(
            "INSERT INTO questions (question_id, textbook_id, xml_id, "
            "metadata_json, source, question_type) "
            "VALUES ('qd', 1, 'real-xml', '{}', 'auto_extracted', 'cardsort')"
        )
        db_conn.commit()
        _seed_events(db_conn)

        result = catalog.sync_catalog(db_conn)

        row = db_conn.execute(
            "SELECT textbook_id, xml_id, source, question_type "
            "FROM questions WHERE question_id = 'qd'"
        ).fetchone()
        assert row["textbook_id"] == 1  # not overwritten with NULL
        assert row["xml_id"] == "real-xml"
        assert row["source"] == "auto_extracted"
        assert row["question_type"] == "cardsort"
        assert result.created == 3  # qa, qm, qp; qd already existed

    def test_fills_missing_type_on_existing_row(self, db_conn):
        # A pre-existing row with a NULL type (e.g. synced before 0006) gets its
        # type filled by the catalog.
        db_conn.execute(
            "INSERT INTO textbooks (textbook_id, git_repo_url, git_ref, "
            "book_xml_id, build_dir) VALUES (1, 'r', 'v', 'demo', 'b')"
        )
        db_conn.execute(
            "INSERT INTO questions (question_id, textbook_id, xml_id, "
            "metadata_json, source, question_type) "
            "VALUES ('qa', 1, 'real-xml', '{}', 'auto_extracted', NULL)"
        )
        db_conn.commit()
        _seed_events(db_conn)

        catalog.sync_catalog(db_conn)

        row = db_conn.execute(
            "SELECT source, question_type FROM questions WHERE question_id = 'qa'"
        ).fetchone()
        assert row["question_type"] == "shortanswer"  # filled
        assert row["source"] == "auto_extracted"  # other columns untouched


class TestNullSelectionGuard:
    """A12/BUG-12: a NULL selection fails ``NOT IN``, so a question-bearing
    row with no selection silently vanished from both catalog queries. The
    rule is now explicit — excluded, with a data-quality warning — instead
    of an accident of SQL NULL semantics."""

    def _insert_null_selection_row(self, conn, import_id):
        conn.execute(
            "INSERT INTO events (event_id, import_id, student_id, "
            "session_id, problem_name, selection, action, input, "
            "feedback_text, feedback_classification, problem_view, "
            "timestamp) "
            "VALUES ('nullsel', ?, 's1', 'x', 'qnull', NULL, '', '', '', "
            "'', '1', '2026-01-06 09:00:00')",
            (import_id,),
        )
        conn.commit()

    def test_null_selection_is_excluded_loudly(self, db_conn, caplog):
        import_id = _seed_events(db_conn)
        self._insert_null_selection_row(db_conn, import_id)

        with caplog.at_level(logging.WARNING, logger="proteus.core.catalog"):
            result = catalog.sync_catalog(db_conn)

        assert result.total == 4  # qnull is not catalogued...
        assert not db_conn.execute(
            "SELECT 1 FROM questions WHERE question_id = 'qnull'"
        ).fetchone()
        # ...but no longer silently: the sync names the malformed rows.
        assert "NULL selection" in caplog.text
        # The read-time projection applies the same explicit rule.
        entries = {e.question_id for e in catalog.question_catalog(db_conn)}
        assert "qnull" not in entries

    def test_clean_events_produce_no_warning(self, db_conn, caplog):
        _seed_events(db_conn)
        with caplog.at_level(logging.WARNING, logger="proteus.core.catalog"):
            catalog.sync_catalog(db_conn)
        assert "NULL selection" not in caplog.text


class TestQuestionCatalog:
    def test_read_time_projection(self, db_conn):
        _seed_events(db_conn)
        catalog.sync_catalog(db_conn)

        entries = {e.question_id: e for e in catalog.question_catalog(db_conn)}
        assert set(entries) == {"qa", "qd", "qm", "qp"}  # qpage excluded

        qa = entries["qa"]
        assert qa.serviced_by == ("sa" if SA else None)
        assert qa.question_type == "shortanswer"
        assert qa.has_source is False
        assert qa.n_events == 2  # the page event is excluded
        assert qa.first_seen == "2026-01-01 09:00:00"
        assert qa.last_seen == "2026-01-05 09:00:00"
        assert qa.classes == ("1", "2")
        assert qa.courses == ("C1", "C2")
        assert qa.selections == ("shortanswer",)

        qd = entries["qd"]
        assert qd.serviced_by == "dnd"
        assert qd.question_type == "cardsort"
        assert qd.n_events == 3
        assert qd.selections == ("dragNdrop", "dragNdrop-drop", "dragNdrop-reset")

        assert entries["qm"].serviced_by is None
        assert entries["qp"].serviced_by is None

    def test_has_source_reflects_source_xml(self, db_conn):
        _seed_events(db_conn)
        catalog.sync_catalog(db_conn)
        db_conn.execute(
            "UPDATE questions SET source_xml = '<x/>' WHERE question_id = 'qa'"
        )
        db_conn.commit()

        entries = {e.question_id: e for e in catalog.question_catalog(db_conn)}
        assert entries["qa"].has_source is True
        assert entries["qd"].has_source is False


class TestIngestHooks:
    def _expected_catalog_count(self, conn) -> int:
        return conn.execute(
            "SELECT COUNT(DISTINCT problem_name) FROM events "
            "WHERE selection <> 'page' AND problem_name <> ''"
        ).fetchone()[0]

    def test_ingest_file_populates_catalog(self, db_conn, golden_core_tab):
        core_ingest_file(db_conn, golden_core_tab)

        expected = self._expected_catalog_count(db_conn)
        assert expected > 0
        assert (
            db_conn.execute("SELECT count(*) FROM questions").fetchone()[0]
            == expected
        )
        # The malformed empty-Problem-Name row never becomes a catalog row.
        assert db_conn.execute(
            "SELECT count(*) FROM questions WHERE question_id = ''"
        ).fetchone()[0] == 0

    def test_reingest_events_populates_catalog(
        self, db_conn, golden_sa_tab, sa_legacy_ingest
    ):
        # Legacy SA import (responses, no events) then backfill -> catalog runs.
        sa_legacy_ingest(db_conn, golden_sa_tab)
        assert db_conn.execute("SELECT count(*) FROM questions").fetchone()[0] == 0

        backfill_events(db_conn, golden_sa_tab)

        expected = self._expected_catalog_count(db_conn)
        assert expected > 0
        assert (
            db_conn.execute("SELECT count(*) FROM questions").fetchone()[0]
            == expected
        )
