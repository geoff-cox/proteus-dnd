"""
Tests for `proteus delete-import` (checklist item B4.14).

The command prints the blast radius (core + per-service per-table counts),
asks for confirmation unless ``--yes`` is passed, and then deletes the import
and its full derived cascade atomically.
"""

from __future__ import annotations

from pathlib import Path

from proteus import cli, db
from proteus.core import registry

# The responses table exists only with the SA service (R14).
SA = "sa" in {s.name for s in registry.available_services()}


def _init_db(db_path: Path) -> None:
    conn = db.connect(db_path)
    try:
        db.bootstrap(conn)
    finally:
        conn.close()


def _count(db_path: Path, table: str, where: str = "1=1") -> int:
    conn = db.connect(db_path)
    try:
        return conn.execute(
            f"SELECT count(*) FROM {table} WHERE {where}"
        ).fetchone()[0]
    finally:
        conn.close()


def _ingest(db_path: Path, tab: Path) -> None:
    assert cli.main(["--db", str(db_path), "ingest", "--derive", str(tab)]) == 0


def test_delete_with_yes_removes_cascade(tmp_path, golden_dnd_tab, capsys):
    db_path = tmp_path / "proteus.db"
    _init_db(db_path)
    _ingest(db_path, golden_dnd_tab)
    capsys.readouterr()

    assert _count(db_path, "events") > 0
    assert _count(db_path, "dnd_events") > 0

    rc = cli.main(["--db", str(db_path), "delete-import", "1", "--yes"])
    assert rc == 0

    out = capsys.readouterr().out
    assert "will remove" in out
    assert "Deleted import #1" in out

    assert _count(db_path, "raw_imports") == 0
    assert _count(db_path, "events") == 0
    assert _count(db_path, "dnd_events") == 0
    if SA:
        assert _count(db_path, "responses") == 0


def test_confirmation_abort_keeps_data(tmp_path, golden_dnd_tab, monkeypatch, capsys):
    db_path = tmp_path / "proteus.db"
    _init_db(db_path)
    _ingest(db_path, golden_dnd_tab)
    capsys.readouterr()
    before = _count(db_path, "events")

    # Decline the prompt: nothing is deleted, exit code signals the abort.
    monkeypatch.setattr("builtins.input", lambda *a, **k: "no")
    rc = cli.main(["--db", str(db_path), "delete-import", "1"])
    assert rc == 1
    assert "Aborted." in capsys.readouterr().out
    assert _count(db_path, "events") == before


def test_confirmation_yes_typed_deletes(tmp_path, golden_dnd_tab, monkeypatch):
    db_path = tmp_path / "proteus.db"
    _init_db(db_path)
    _ingest(db_path, golden_dnd_tab)

    monkeypatch.setattr("builtins.input", lambda *a, **k: "yes")
    rc = cli.main(["--db", str(db_path), "delete-import", "1"])
    assert rc == 0
    assert _count(db_path, "raw_imports") == 0


def test_missing_import_errors(tmp_path, capsys):
    db_path = tmp_path / "proteus.db"
    _init_db(db_path)
    rc = cli.main(["--db", str(db_path), "delete-import", "42", "--yes"])
    assert rc == 1
    assert "No import #42" in capsys.readouterr().err
