"""
Schema tests for item 16 of the Phase 3.5 build checklist, extended with the
migration-runner bootstrap-parity property (plan item A0.3).

Covers:

1. A fresh bootstrap produces `textbooks` and `questions` tables with the
   documented columns and reaches the migration head.
2. The forward migration (`migrations/0002_add_textbooks_questions.sql`)
   applied to a populated v1 database leaves every pre-existing `responses`
   row byte-identical.
3. Bootstrap parity: a freshly bootstrapped database is structurally
   identical to a v1 database upgraded by the migration runner, for the
   tables the migration covers.
"""

from __future__ import annotations

from pathlib import Path

import re

import pytest

from proteus import db
from proteus.core import db as core_db
from proteus.core import migrate
from proteus.core import registry

# Service-conditional cases (cc-task-public-export PR 1, ruling R14): keyed on
# registry discovery, so the same file runs in the private tree (sa + dnd) and
# in the public export (dnd only).
SA = "sa" in {s.name for s in registry.available_services()}
requires_sa = pytest.mark.skipif(
    not SA, reason="the Short Answer service is not in this tree (R14)"
)


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "proteus"
    / "migrations"
    / "0002_add_textbooks_questions.sql"
)

EXPECTED_TEXTBOOKS_COLUMNS = {
    "textbook_id",
    "git_repo_url",
    "git_ref",
    "book_xml_id",
    "source_dir",
    "build_dir",
    "added_at",
    "notes",
}

EXPECTED_QUESTIONS_COLUMNS = {
    "question_id",
    "textbook_id",
    "xml_id",
    "parent_xml_id",
    "section_id",
    "section_title",
    "runestone_url",
    "preview_path",
    "metadata_json",
    "source",
    "source_hash",
    "extracted_at",
}


def _table_columns(conn, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


class TestFreshBootstrap:
    def test_textbooks_and_questions_tables_exist_with_documented_columns(self, db_conn):
        assert _table_exists(db_conn, "textbooks")
        assert _table_exists(db_conn, "questions")
        # A fresh bootstrap runs migrations through head, so `textbooks` also
        # carries last_synced_at (migration 0011, B4.13) and macros (0012).
        assert (
            _table_columns(db_conn, "textbooks")
            == EXPECTED_TEXTBOOKS_COLUMNS | {"last_synced_at", "macros"}
        )
        # …and `questions` carries question_type (migration 0006),
        # source_xml (0007), and title (0014).
        assert (
            _table_columns(db_conn, "questions")
            == EXPECTED_QUESTIONS_COLUMNS | {"question_type", "source_xml", "title"}
        )

    def test_placeholder_textbook_seed_is_retired(self, db_conn):
        # SP-1 (migration 0010): the TODO(maintainer) placeholder seed is gone;
        # a fresh bootstrap has an empty registry, populated via
        # `proteus textbook add`.
        rows = db_conn.execute("SELECT book_xml_id FROM textbooks").fetchall()
        assert rows == []

    def test_fresh_bootstrap_reaches_schema_head(self, db_conn):
        # The head is the highest version known to either the baseline schema
        # (the core schema's seeded schema_version rows) or the core migration
        # files. Generalising past "max in the baseline" means that when a
        # migration-only change (0004+) lands, this test automatically expects
        # bootstrap to have run the runner and reached it (item A0.3).
        schema_sql = core_db.core_schema_sql()
        schema_versions = {int(m) for m in re.findall(r"VALUES \((\d+),", schema_sql)}
        migration_versions = {m.version for m in migrate.discover_migrations()}
        declared_head = max(schema_versions | migration_versions)

        bootstrapped_head = db_conn.execute(
            "SELECT MAX(version) FROM schema_version"
        ).fetchone()[0]
        assert bootstrapped_head == declared_head

    def test_fresh_bootstrap_has_no_pending_migrations(self, db_conn):
        # Whatever the head is, a fresh bootstrap must already be at it: the
        # runner finds nothing left to apply. This is the invariant that keeps
        # bootstrap and the migration path from drifting.
        assert migrate.pending_migrations(db_conn) == []

    def test_questions_textbook_id_nullable_for_catalog(self, db_conn):
        # CD-2 (migration 0009): a catalogued question has no book until it is
        # identified at upload, so textbook_id is nullable and source='catalog'.
        db_conn.execute(
            "INSERT INTO questions (question_id, textbook_id, xml_id, "
            "metadata_json, source) VALUES ('cat-q', NULL, 'x', '{}', 'catalog')"
        )
        db_conn.commit()
        row = db_conn.execute(
            "SELECT textbook_id, source FROM questions WHERE question_id = 'cat-q'"
        ).fetchone()
        assert row["textbook_id"] is None
        assert row["source"] == "catalog"

    def test_questions_source_check_still_rejects_unknown(self, db_conn):
        import sqlite3

        with pytest.raises(sqlite3.IntegrityError):
            db_conn.execute(
                "INSERT INTO questions (question_id, xml_id, metadata_json, source) "
                "VALUES ('bad-q', 'x', '{}', 'bogus')"
            )


class TestForwardMigration:
    """
    Apply 0002 to a database simulated at schema-version 1 (the two new tables
    dropped, schema_version trimmed to 1). The fixture is populated with real
    responses via the public ingest entry point.
    """

    def _make_v1_db(self, tmp_path: Path):
        db_path = tmp_path / "v1.db"
        conn = db.connect(db_path)
        db.bootstrap(conn)
        # Roll back to the pre-migration state by removing what 0002 added.
        conn.execute("DROP INDEX IF EXISTS idx_questions_textbook")
        conn.execute("DROP INDEX IF EXISTS idx_questions_section")
        conn.execute("DROP TABLE IF EXISTS questions")
        conn.execute("DROP TABLE IF EXISTS textbooks")
        conn.execute("DELETE FROM schema_version WHERE version >= 2")
        conn.commit()
        return conn

    def test_migration_adds_tables_and_preserves_responses(
        self, tmp_path, sample_tab, sa_legacy_ingest
    ):
        # The v1 database is an SA-era one (responses); the SA-era ingest seeds
        # it, so the case is skipped in a tree without the SA service (R14).
        conn = self._make_v1_db(tmp_path)
        try:
            # Sanity: pre-migration state.
            assert not _table_exists(conn, "textbooks")
            assert not _table_exists(conn, "questions")

            # Seed a populated v1 database through the real entry point so the
            # responses rows are produced by the same code path the app uses.
            sa_legacy_ingest(conn, sample_tab)

            before_responses = [
                tuple(row)
                for row in conn.execute(
                    "SELECT response_id, import_id, student_id, question_id, "
                    "response_text, submitted_at, course_id "
                    "FROM responses ORDER BY response_id"
                ).fetchall()
            ]
            before_raw_imports = [
                tuple(row)
                for row in conn.execute(
                    "SELECT import_id, file_path, file_hash, file_format, "
                    "imported_at, row_count, notes "
                    "FROM raw_imports ORDER BY import_id"
                ).fetchall()
            ]
            assert before_responses, "fixture seeded zero responses; check fixture"

            # Apply the migration exactly as a maintainer would.
            conn.executescript(MIGRATION_PATH.read_text())

            # (a) Both new tables exist with the documented columns.
            assert _table_exists(conn, "textbooks")
            assert _table_exists(conn, "questions")
            assert _table_columns(conn, "textbooks") == EXPECTED_TEXTBOOKS_COLUMNS
            assert _table_columns(conn, "questions") == EXPECTED_QUESTIONS_COLUMNS

            # Migration bumped the version marker.
            assert (
                conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
                == 2
            )

            # (b) Every pre-existing responses row is byte-identical afterward.
            # Compare raw_imports too, since responses depend on import_id.
            after_responses = [
                tuple(row)
                for row in conn.execute(
                    "SELECT response_id, import_id, student_id, question_id, "
                    "response_text, submitted_at, course_id "
                    "FROM responses ORDER BY response_id"
                ).fetchall()
            ]
            after_raw_imports = [
                tuple(row)
                for row in conn.execute(
                    "SELECT import_id, file_path, file_hash, file_format, "
                    "imported_at, row_count, notes "
                    "FROM raw_imports ORDER BY import_id"
                ).fetchall()
            ]
            assert after_responses == before_responses
            assert after_raw_imports == before_raw_imports
        finally:
            conn.close()


def _full_schema(conn) -> dict[tuple[str, str], str]:
    """All user-defined schema objects keyed by (type, name) → CREATE text."""
    return {
        (r["type"], r["name"]): r["sql"]
        for r in conn.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        )
        if r["sql"] is not None
    }


class TestBootstrapParity:
    """
    Item A0.3 / A1.3 / A3.5: a previous-release database (the frozen v3
    baseline) upgraded by the migration runner must end up structurally
    identical to a fresh bootstrap.

    The baseline is built by applying the baseline script alone — the core
    schema plus the SA service's schema files, no migrations — which is
    exactly what a database from the prior release looks like: version 3,
    without any of the 0004+ objects. (The A3.5 split relocated the DDL into
    two files; the combined text is byte-identical to the old schema.sql, so
    this still faithfully reproduces a real prior-release database.) The
    runner then upgrades it to head, and the full schema must match a fresh
    bootstrap object-for-object.
    """

    @pytest.fixture(autouse=True)
    def _baseline_script(self, baseline_v3_script):
        self._baseline_sql = baseline_v3_script

    def _fresh(self):
        conn = db.connect(":memory:")
        db.bootstrap(conn)
        return conn

    def _baseline(self):
        conn = db.connect(":memory:")
        conn.executescript(self._baseline_sql)
        conn.commit()
        return conn

    def test_baseline_alone_is_v3_without_migration_objects(self):
        conn = self._baseline()
        try:
            # Genuinely "old": the baseline is frozen at v3 and carries none
            # of the migration-only objects (events, pipeline_runs.service…).
            assert conn.execute(
                "SELECT MAX(version) FROM schema_version"
            ).fetchone()[0] == 3
            assert not _table_exists(conn, "events")
        finally:
            conn.close()

    @requires_sa
    def test_baseline_includes_sa_service_tables(self):
        # The split (A3.5) must not lose the SA slice: a bootstrap-composed
        # baseline still carries the service-owned tables and triggers.
        conn = self._baseline()
        try:
            for table in ("responses", "screenings", "codebook", "vocabulary"):
                assert _table_exists(conn, table), table
            trigger = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' "
                "AND name='trg_matches_accepted_only'"
            ).fetchone()
            assert trigger is not None
        finally:
            conn.close()

    def test_baseline_upgraded_by_runner_matches_fresh_bootstrap(self):
        fresh = self._fresh()
        upgraded = self._baseline()
        try:
            # Invoke the runner exactly as `proteus migrate` does: core
            # migrations plus each registered service's (the dnd source
            # exists from B1.1 on).
            from proteus.core import registry

            migrate.migrate(
                upgraded, service_sources=registry.migration_sources()
            )
            # The runner added the migration-only objects …
            assert _table_exists(upgraded, "events")
            # … including the SA-service-owned migrations: the v3 baseline
            # has neither codebook.surface_markers (CP1.1, 0001) nor
            # vocabulary.surface_forms (CP3.10, 0002); both the upgraded
            # and the fresh database must (when the SA service is there).
            for conn in (upgraded, fresh) if SA else ():
                cols = {
                    r["name"]
                    for r in conn.execute("PRAGMA table_info(codebook)")
                }
                assert "surface_markers" in cols
                vocab_cols = {
                    r["name"]
                    for r in conn.execute("PRAGMA table_info(vocabulary)")
                }
                assert "surface_forms" in vocab_cols
            # … and the full schema now matches a fresh bootstrap exactly.
            assert _full_schema(upgraded) == _full_schema(fresh)
            assert (
                upgraded.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
                == fresh.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
            )
        finally:
            fresh.close()
            upgraded.close()

    def test_retire_seed_preserves_a_partially_edited_placeholder(self):
        # Migration 0010 deletes only the pristine placeholder (all three
        # TODO(maintainer) fields). A seed whose build_dir a maintainer edited
        # must survive the migration.
        conn = self._baseline()  # v3, with the pristine placeholder seed
        try:
            conn.execute(
                "UPDATE textbooks SET build_dir = '/real/build' "
                "WHERE book_xml_id = 'ula-proteus'"
            )
            conn.commit()

            migrate.migrate(conn)  # applies core 0004..0010

            row = conn.execute(
                "SELECT build_dir FROM textbooks WHERE book_xml_id = 'ula-proteus'"
            ).fetchone()
            assert row is not None  # not deleted
            assert row["build_dir"] == "/real/build"  # the edit is intact
        finally:
            conn.close()

    @requires_sa  # the v3 run rows need the SA version tables and children
    def test_pipeline_runs_rebuild_preserves_rows_children_and_sequence(self):
        # Migration 0015 (audit task A2 / BUG-02) rebuilds `pipeline_runs` to
        # drop the NOT NULL on vocab_version/codebook_version. On a populated
        # v3 database the rebuild must keep every run row byte-identical,
        # keep the SA child rows referencing them (seven SA tables FK into
        # pipeline_runs), restore FK enforcement, and preserve the
        # AUTOINCREMENT high-water mark so a run_id is never reused — even
        # one whose row was deleted before the upgrade.
        import sqlite3

        conn = self._baseline()  # v3: both columns still NOT NULL
        try:
            conn.execute("INSERT INTO vocabulary_versions (label) VALUES ('v')")
            conn.execute("INSERT INTO codebook_versions (label) VALUES ('c')")
            run1 = conn.execute(
                "INSERT INTO pipeline_runs (status, prompt_version, model_id, "
                "vocab_version, codebook_version, notes) "
                "VALUES ('completed', 'p1', 'm1', 1, 1, 'first')"
            ).lastrowid
            run2 = conn.execute(
                "INSERT INTO pipeline_runs (status, prompt_version, model_id, "
                "vocab_version, codebook_version) "
                "VALUES ('running', 'p2', 'm2', 1, 1)"
            ).lastrowid
            conn.execute(
                "INSERT INTO raw_imports (file_path, file_hash, file_format) "
                "VALUES ('f.tab', 'h', 'datashop_tab')"
            )
            conn.execute(
                "INSERT INTO responses (response_id, import_id, student_id, "
                "question_id, response_text) VALUES ('r1', 1, 's', 'q', 't')"
            )
            conn.execute(
                "INSERT INTO screenings (response_id, run_id, label) "
                "VALUES ('r1', ?, 'INCLUDE')",
                (run1,),
            )
            # Delete the top run row: MAX(run_id) alone would now undercount
            # the AUTOINCREMENT sequence the rebuild must carry over.
            conn.execute("DELETE FROM pipeline_runs WHERE run_id = ?", (run2,))
            conn.commit()
            before = tuple(
                conn.execute(
                    "SELECT run_id, started_at, completed_at, status, "
                    "prompt_version, model_id, vocab_version, "
                    "codebook_version, notes FROM pipeline_runs"
                ).fetchone()
            )

            migrate.migrate(conn)  # applies core 0004..0015

            after = conn.execute("SELECT * FROM pipeline_runs").fetchone()
            assert tuple(after)[:9] == before
            assert (after["service"], after["stage"], after["config_json"]) == (
                "sa", None, None,
            )
            # The referencing SA child survived and nothing dangles.
            assert conn.execute(
                "SELECT count(*) FROM screenings WHERE run_id = ?", (run1,)
            ).fetchone()[0] == 1
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
            # Enforcement is genuinely restored after the rebuild.
            assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            # The columns are now nullable — the point of the migration.
            notnull = {
                r["name"]: r["notnull"]
                for r in conn.execute("PRAGMA table_info(pipeline_runs)")
            }
            assert notnull["vocab_version"] == 0
            assert notnull["codebook_version"] == 0
            # A NULL-version run inserts cleanly, and the deleted run2's id is
            # not reused (the high-water mark survived the rebuild).
            run3 = conn.execute(
                "INSERT INTO pipeline_runs (status, prompt_version, model_id) "
                "VALUES ('running', 'p3', 'm3')"
            ).lastrowid
            assert run3 == run2 + 1
            # The version FKs are gone — with foreign_keys ON, SQLite refuses
            # all DML on a child whose declared parent table is absent (even
            # inserting NULL), so the decoupling converts both clauses to
            # logical references: core DDL no longer names the SA tables.
            ddl = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'pipeline_runs'"
            ).fetchone()["sql"]
            assert "REFERENCES vocabulary_versions" not in ddl
            assert "REFERENCES codebook_versions" not in ddl
            # …while the FKs *into* pipeline_runs still hold: a screening
            # under a nonexistent run is rejected.
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO screenings (response_id, run_id, label) "
                    "VALUES ('r1', 999, 'INCLUDE')"
                )
            conn.commit()
        finally:
            conn.close()

    def test_questions_rebuild_preserves_referencing_child_with_fk_on(self):
        # CD-2 migration 0009 rebuilds `questions` to relax textbook_id. With
        # foreign_keys ON (connect() enables it), dropping the parent would trip
        # a child FK (dnd_boards.question_id) on a database that already has
        # rows; the migration disables enforcement around the rebuild and
        # restores it. Prove a referencing row survives and FKs come back on.
        import sqlite3

        conn = self._baseline()  # v3: questions.textbook_id is NOT NULL
        try:
            assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            # q1 belongs to its own registered book (not the placeholder seed,
            # which migration 0010 retires) so the seed delete is unconflicted.
            tb = conn.execute(
                "INSERT INTO textbooks (git_repo_url, git_ref, book_xml_id, "
                "build_dir) VALUES ('r', 'v', 'rebuild-book', 'b')"
            ).lastrowid
            conn.execute(
                "INSERT INTO questions (question_id, textbook_id, xml_id, "
                "metadata_json, source) VALUES ('q1', ?, 'x', '{}', 'auto_extracted')",
                (tb,),
            )
            conn.execute(
                "CREATE TABLE _child (qid TEXT, "
                "FOREIGN KEY (qid) REFERENCES questions(question_id))"
            )
            conn.execute("INSERT INTO _child (qid) VALUES ('q1')")
            conn.commit()

            # Apply core migrations through head (includes the 0009 rebuild).
            migrate.migrate(conn)

            assert conn.execute(
                "SELECT count(*) FROM _child WHERE qid = 'q1'"
            ).fetchone()[0] == 1
            assert conn.execute(
                "SELECT count(*) FROM questions WHERE question_id = 'q1'"
            ).fetchone()[0] == 1
            # Enforcement is genuinely restored after the rebuild.
            assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO _child (qid) VALUES ('nope')")
            # textbook_id is now nullable: a catalog row inserts cleanly.
            conn.execute(
                "INSERT INTO questions (question_id, xml_id, metadata_json, source) "
                "VALUES ('q2', 'x', '{}', 'catalog')"
            )
            conn.commit()
        finally:
            conn.close()
