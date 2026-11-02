"""
DB-3: the non-destructive legacy-database notice.

Before the data/ refactor the default DB was a repo-root ``./proteus.db``.
On startup with the default in effect, if a legacy ``./proteus.db`` is present
and the new default ``<repo>/data/proteus.db`` does not yet exist, the CLI
prints a one-line pointer to the new location and the move command, and
moves/deletes nothing (Decision 2). It stays silent with an explicit ``--db``
and once the database has been moved (the new default exists).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from proteus import cli
from proteus.core import db as core_db


@pytest.fixture
def repo_root(tmp_path, monkeypatch):
    """Repoint the default-DB anchor at a tmp dir (never the real repo data/)."""
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(core_db, "_repo_root", lambda: root)
    return root


def _legacy_in_cwd(tmp_path, monkeypatch) -> Path:
    """Drop a dummy legacy ./proteus.db in an isolated cwd; return its path."""
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    legacy = cwd / "proteus.db"
    legacy.write_bytes(b"legacy-db-bytes")
    monkeypatch.chdir(cwd)
    return legacy


def test_notice_points_to_new_location_and_moves_nothing(
    repo_root, tmp_path, monkeypatch, capsys
):
    legacy = _legacy_in_cwd(tmp_path, monkeypatch)
    before = legacy.read_bytes()
    new_default = repo_root / "data" / "proteus.db"

    cli.main(["status"])  # default --db resolves data/ and checks the legacy DB

    err = capsys.readouterr().err
    assert "legacy database at ./proteus.db" in err
    assert str(new_default) in err
    assert "mv ./proteus.db" in err
    # Strictly non-destructive: the legacy file is untouched and nothing was
    # created at the new default, not even the data/ directory, since
    # `status` bails on the missing DB (Copilot review).
    assert legacy.exists() and legacy.read_bytes() == before
    assert not new_default.exists()
    assert not new_default.parent.exists()


def test_silent_with_explicit_db(repo_root, tmp_path, monkeypatch, capsys):
    _legacy_in_cwd(tmp_path, monkeypatch)

    cli.main(["--db", str(tmp_path / "custom.db"), "status"])

    assert "legacy database" not in capsys.readouterr().err


def test_silent_once_new_default_exists(repo_root, tmp_path, monkeypatch, capsys):
    _legacy_in_cwd(tmp_path, monkeypatch)

    # init creates the new default (the notice prints once here)...
    cli.main(["init"])
    capsys.readouterr()  # drain

    # ...then a later default command is silent: the new default now exists.
    cli.main(["status"])
    assert "legacy database" not in capsys.readouterr().err


def test_no_notice_without_legacy_db(repo_root, tmp_path, monkeypatch, capsys):
    # An isolated cwd with no ./proteus.db at all.
    cwd = tmp_path / "clean"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    cli.main(["status"])

    assert "legacy database" not in capsys.readouterr().err
