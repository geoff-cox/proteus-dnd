"""
DB-1/DB-4: the single canonical default-DB-path helper.

`proteus.core.db.default_db_path()` is the one source of truth for the default
database location: ``<repo>/data/proteus.db`` (the repository's ``data/``
directory). It is pure unless asked to create ``<repo>/data``
(``create_parent=True``), so help text/display/tests never touch the
filesystem. The CLI resolves its default through it only when ``--db`` is
omitted; an explicit ``--db`` (and ``:memory:``) never triggers dir creation.
"""

from __future__ import annotations

import pytest

from proteus import cli, db
from proteus.core import db as core_db
from proteus.core.db import default_db_path


@pytest.fixture
def repo_root(tmp_path, monkeypatch):
    """Repoint the default-DB anchor at a tmp dir (never the real repo data/)."""
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(core_db, "_repo_root", lambda: root)
    return root


def test_returns_repo_data_proteus_db(repo_root):
    assert default_db_path() == repo_root / "data" / "proteus.db"


def test_pure_by_default_creates_no_directory(repo_root):
    # Merely computing the path must not touch the filesystem.
    default_db_path()
    assert not (repo_root / "data").exists()


def test_create_parent_makes_the_directory(repo_root):
    path = default_db_path(create_parent=True)
    assert path == repo_root / "data" / "proteus.db"
    assert (repo_root / "data").is_dir()
    # The directory is created; the database file itself is not.
    assert not path.exists()


def test_exposed_through_proteus_db_shim():
    # Callers using `from proteus import db` reach the same helper.
    assert db.default_db_path is default_db_path


def test_cli_default_lands_in_repo_data(repo_root, tmp_path, monkeypatch):
    # Isolated cwd so a stray ./proteus.db can't interfere.
    cwd = tmp_path / "run"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    assert cli.main(["init"]) == 0  # no --db -> the default

    assert (repo_root / "data" / "proteus.db").exists()
    assert not (cwd / "proteus.db").exists()  # cwd stays clean


def test_cli_read_command_on_default_creates_no_dir(repo_root, tmp_path, monkeypatch):
    # A read/inspect command that bails on a missing DB must not create the
    # data/ directory just from resolving the default (Copilot review): dir
    # creation is reserved for the commands that actually create the DB.
    cwd = tmp_path / "run"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    assert cli.main(["status"]) == 1  # "No database at <repo>/data/proteus.db"
    assert not (repo_root / "data").exists()


def test_cli_explicit_db_bypasses_default(repo_root, tmp_path):
    custom = tmp_path / "custom.db"

    assert cli.main(["--db", str(custom), "init"]) == 0

    assert custom.exists()
    assert not (repo_root / "data").exists()  # default helper never invoked


def test_connect_memory_creates_no_directory(repo_root):
    # connect() applies no default and creates no dirs (it is also called with
    # :memory:/tmp_path); only the default helper does, and only on demand.
    conn = db.connect(":memory:")
    try:
        assert not (repo_root / "data").exists()
    finally:
        conn.close()
