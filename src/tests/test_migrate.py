"""
Tests for the forward-only migration runner (plan item A0.2).

Runner *mechanics* (discovery, version reads, dry-run, the transaction-per-file
contract, failure rollback, service sources) are exercised against isolated
synthetic migration directories so they stay stable as real migrations are
added. A small set of tests pins facts about the real core migrations on disk.
The fresh-bootstrap == migrated-DB parity property lives in
test_schema_migration.py (item A0.3).
"""

from __future__ import annotations

import pytest

from proteus import db
from proteus.core import migrate


def _table_exists(conn, table: str) -> bool:
    return (
        conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        is not None
    )


def _head_version() -> int:
    """Highest version among the real core migration files on disk."""
    return max(m.version for m in migrate.discover_migrations())


def _write_core_migration(
    directory,
    version: int,
    body: str,
    *,
    record_version: bool = True,
    wrap: bool = True,
) -> None:
    """Write a synthetic core-style migration file numbered `version`."""
    parts = []
    if wrap:
        parts.append("BEGIN;")
    parts.append(body)
    if record_version:
        parts.append(
            f"INSERT INTO schema_version (version, description) "
            f"VALUES ({version}, 'synthetic test migration {version}');"
        )
    if wrap:
        parts.append("COMMIT;")
    (directory / f"{version:04d}_test.sql").write_text("\n".join(parts) + "\n")


class TestDiscovery:
    def test_finds_core_migrations(self):
        migs = migrate.discover_migrations()
        versions = [m.version for m in migs]
        assert versions == sorted(versions)
        # The core migrations on disk today; extend when a new one lands.
        assert versions == [2, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]
        m0004 = next(m for m in migs if m.version == 4)
        assert m0004.name == "0004_events"
        assert m0004.source == migrate.CORE_SOURCE
        assert m0004.path.exists()

    def test_ignores_non_migration_files(self, tmp_path):
        (tmp_path / "README.md").write_text("not a migration")
        (tmp_path / "0001_init.sql").write_text("BEGIN; COMMIT;")
        migs = migrate.discover_migrations(tmp_path)
        assert [m.version for m in migs] == [1]

    def test_ignores_non_padded_version_prefix(self, tmp_path):
        # The contract is a four-digit zero-padded prefix; a non-padded name is
        # not silently accepted as a migration.
        (tmp_path / "1_init.sql").write_text("BEGIN; COMMIT;")
        (tmp_path / "0002_real.sql").write_text("BEGIN; COMMIT;")
        migs = migrate.discover_migrations(tmp_path)
        assert [m.version for m in migs] == [2]

    def test_missing_directory_returns_empty(self, tmp_path):
        assert migrate.discover_migrations(tmp_path / "nope") == []

    def test_duplicate_version_raises(self, tmp_path):
        (tmp_path / "0005_a.sql").write_text("BEGIN; COMMIT;")
        (tmp_path / "0005_b.sql").write_text("BEGIN; COMMIT;")
        with pytest.raises(migrate.MigrationError, match="duplicate migration version 5"):
            migrate.discover_migrations(tmp_path)


class TestVersionReads:
    def test_empty_database_is_version_zero(self):
        conn = db.connect(":memory:")
        try:
            assert migrate.current_version(conn) == 0
        finally:
            conn.close()

    def test_fresh_bootstrap_is_at_head(self, db_conn):
        assert migrate.current_version(db_conn) == _head_version()

    def test_pending_is_empty_on_fresh_database(self, db_conn):
        assert migrate.pending_migrations(db_conn) == []

    def test_pending_lists_migrations_above_current(self, db_conn, tmp_path):
        nxt = _head_version() + 1
        _write_core_migration(tmp_path, nxt, "CREATE TABLE scratch (id INTEGER);")
        pending = migrate.pending_migrations(db_conn, migrations_dir=tmp_path)
        assert [m.version for m in pending] == [nxt]


class TestMigrate:
    def test_noop_on_fresh_database(self, db_conn):
        result = migrate.migrate(db_conn)
        assert result.applied == ()
        assert result.final_version == _head_version()
        assert result.dry_run is False

    def test_applies_pending_migration(self, db_conn, tmp_path):
        nxt = _head_version() + 1
        _write_core_migration(tmp_path, nxt, "CREATE TABLE scratch (id INTEGER);")
        result = migrate.migrate(db_conn, migrations_dir=tmp_path)
        assert [m.version for m in result.applied] == [nxt]
        assert result.final_version == nxt
        assert _table_exists(db_conn, "scratch")

    def test_applies_only_once(self, db_conn, tmp_path):
        nxt = _head_version() + 1
        _write_core_migration(tmp_path, nxt, "CREATE TABLE scratch (id INTEGER);")
        migrate.migrate(db_conn, migrations_dir=tmp_path)
        result = migrate.migrate(db_conn, migrations_dir=tmp_path)
        assert result.applied == ()
        assert result.final_version == nxt

    def test_dry_run_lists_without_applying(self, db_conn, tmp_path):
        nxt = _head_version() + 1
        _write_core_migration(tmp_path, nxt, "CREATE TABLE scratch (id INTEGER);")
        result = migrate.migrate(db_conn, migrations_dir=tmp_path, dry_run=True)
        assert result.dry_run is True
        assert [m.version for m in result.pending] == [nxt]
        assert result.applied == ()
        assert migrate.current_version(db_conn) == _head_version()
        assert not _table_exists(db_conn, "scratch")

    def test_preserves_responses_across_migration(
        self, db_conn, golden_sa_tab, tmp_path, sa_legacy_ingest
    ):
        # Seed real responses through the SA-era path (skipped without the SA
        # service, R14), then apply a migration; the responses must be untouched.
        sa_legacy_ingest(db_conn, golden_sa_tab)
        before = [
            tuple(r)
            for r in db_conn.execute(
                "SELECT response_id, response_text, submitted_at "
                "FROM responses ORDER BY response_id"
            ).fetchall()
        ]
        assert before

        nxt = _head_version() + 1
        _write_core_migration(tmp_path, nxt, "CREATE TABLE scratch (id INTEGER);")
        migrate.migrate(db_conn, migrations_dir=tmp_path)

        after = [
            tuple(r)
            for r in db_conn.execute(
                "SELECT response_id, response_text, submitted_at "
                "FROM responses ORDER BY response_id"
            ).fetchall()
        ]
        assert after == before


class TestFileContract:
    def test_missing_transaction_wrapper_raises(self, db_conn, tmp_path):
        nxt = _head_version() + 1
        (tmp_path / f"{nxt:04d}_no_txn.sql").write_text(
            "CREATE TABLE scratch (id INTEGER);\n"
            f"INSERT INTO schema_version (version, description) VALUES ({nxt}, 'x');\n"
        )
        with pytest.raises(migrate.MigrationError, match="wrap its body"):
            migrate.migrate(db_conn, migrations_dir=tmp_path)
        assert migrate.current_version(db_conn) == _head_version()
        assert not _table_exists(db_conn, "scratch")

    def test_core_migration_without_version_insert_raises(self, db_conn, tmp_path):
        # A core migration that forgets its schema_version insert must be
        # rejected BEFORE its DDL can commit: the file owns its COMMIT, so a
        # post-hoc check would leave a half-applied schema even as it raises.
        nxt = _head_version() + 1
        (tmp_path / f"{nxt:04d}_forgot_version.sql").write_text(
            "BEGIN;\nCREATE TABLE scratch (id INTEGER);\nCOMMIT;\n"
        )
        with pytest.raises(migrate.MigrationError, match="must record its version"):
            migrate.migrate(db_conn, migrations_dir=tmp_path)
        # No half-applied schema: the scratch table was never created and the
        # version is unchanged.
        assert migrate.current_version(db_conn) == _head_version()
        assert not _table_exists(db_conn, "scratch")

    def test_failure_restores_foreign_keys_pragma(self, db_conn, tmp_path):
        # A rebuild-style migration (0009, 0015) toggles the connection-level
        # foreign_keys PRAGMA OFF outside its BEGIN/COMMIT. On failure the
        # rollback restores the data but not connection state, so the runner
        # itself must restore the PRAGMA — otherwise a caller that catches
        # MigrationError keeps using a connection that silently violates the
        # db.connect enforcement guarantee (PR #167 review).
        assert db_conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        nxt = _head_version() + 1
        (tmp_path / f"{nxt:04d}_fk_toggle_boom.sql").write_text(
            "PRAGMA foreign_keys=OFF;\n"
            "BEGIN;\n"
            "CREATE TABLE scratch (id INTEGER);\n"
            f"INSERT INTO schema_version (version, description) VALUES ({nxt}, 'x');\n"
            "INSERT INTO nonexistent_table (id) VALUES (1);\n"
            "COMMIT;\n"
            "PRAGMA foreign_keys=ON;\n"
        )
        with pytest.raises(migrate.MigrationError, match="failed to apply"):
            migrate.migrate(db_conn, migrations_dir=tmp_path)
        assert db_conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert migrate.current_version(db_conn) == _head_version()
        assert not _table_exists(db_conn, "scratch")

    def test_failure_rolls_back_and_leaves_version_unchanged(self, db_conn, tmp_path):
        nxt = _head_version() + 1
        (tmp_path / f"{nxt:04d}_boom.sql").write_text(
            "BEGIN;\n"
            "CREATE TABLE scratch (id INTEGER);\n"
            f"INSERT INTO schema_version (version, description) VALUES ({nxt}, 'x');\n"
            "INSERT INTO nonexistent_table (id) VALUES (1);\n"
            "COMMIT;\n"
        )
        with pytest.raises(migrate.MigrationError, match="failed to apply"):
            migrate.migrate(db_conn, migrations_dir=tmp_path)
        assert migrate.current_version(db_conn) == _head_version()
        assert not _table_exists(db_conn, "scratch")


class TestServiceSources:
    """
    The provisional service-source path (TODO in migrate.py): a service
    migration directory with its own NNNN numbering, applied after core and
    tracked in the applied_migrations ledger.
    """

    def test_service_migration_applied_after_core_and_recorded(self, db_conn, tmp_path):
        svc_dir = tmp_path / "svc_migrations"
        svc_dir.mkdir()
        (svc_dir / "0001_svc_init.sql").write_text(
            "BEGIN;\nCREATE TABLE svc_thing (id INTEGER PRIMARY KEY);\nCOMMIT;\n"
        )

        result = migrate.migrate(db_conn, service_sources=[("svc", svc_dir)])

        names = [m.name for m in result.applied]
        assert names == ["0001_svc_init"]
        assert _table_exists(db_conn, "svc_thing")
        # The ledger records this source (alongside any real services'
        # bootstrap-applied migrations, e.g. dnd/0001).
        row = db_conn.execute(
            "SELECT source, version FROM applied_migrations WHERE source = 'svc'"
        ).fetchone()
        assert tuple(row) == ("svc", 1)

    def test_service_migration_is_idempotent(self, db_conn, tmp_path):
        svc_dir = tmp_path / "svc_migrations"
        svc_dir.mkdir()
        (svc_dir / "0001_svc_init.sql").write_text(
            "BEGIN;\nCREATE TABLE svc_thing (id INTEGER PRIMARY KEY);\nCOMMIT;\n"
        )
        migrate.migrate(db_conn, service_sources=[("svc", svc_dir)])
        result = migrate.migrate(db_conn, service_sources=[("svc", svc_dir)])
        assert result.applied == ()
        n = db_conn.execute(
            "SELECT count(*) FROM applied_migrations WHERE source = 'svc'"
        ).fetchone()[0]
        assert n == 1

    def test_core_runs_before_services(self, db_conn, tmp_path):
        # A synthetic core migration (pending) plus a service migration: core
        # must apply first.
        core_dir = tmp_path / "core"
        core_dir.mkdir()
        nxt = _head_version() + 1
        _write_core_migration(core_dir, nxt, "CREATE TABLE core_thing (id INTEGER);")
        svc_dir = tmp_path / "svc"
        svc_dir.mkdir()
        (svc_dir / "0001_svc_init.sql").write_text(
            "BEGIN;\nCREATE TABLE svc_thing (id INTEGER PRIMARY KEY);\nCOMMIT;\n"
        )
        result = migrate.migrate(
            db_conn, migrations_dir=core_dir, service_sources=[("svc", svc_dir)]
        )
        sources = [m.source for m in result.applied]
        assert sources == [migrate.CORE_SOURCE, "svc"]
