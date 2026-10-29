"""
CLI tests for `proteus dnd roles` (E-02, PR 1 of 2; spec §2.4) in the style
of `test_cli_dnd.py`: `roles import` / `list` / `remove` on the regime
pattern, the R7 refusal at import after a derive, the `excluded:` line
`dnd derive` prints, and the R7 refusal at derive (`dnd derive` and `ingest
--derive` under a ruling that reached the table after a derive). Every
assertion on output also checks that no fixture id reached stdout or stderr
(M6: the real ids embed surnames).
"""

from __future__ import annotations

import pytest

from proteus import cli, db
from proteus.services.dragndrop import account_roles

FIXTURE = "golden_dnd_attrition.tab"
ROLES = "account_roles_fixture.csv"
STAFF = "AttritionStaff1"
KEPT = "AttritionRuledKept"
FIXTURE_IDS = (STAFF, KEPT)
PSEUDONYM = "Y99-B03-P99"


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "proteus.db"
    assert cli.main(["--db", str(path), "init"]) == 0
    return path


@pytest.fixture
def roles_file(fixtures_dir):
    return fixtures_dir / ROLES


@pytest.fixture
def attrition_tab(fixtures_dir):
    return fixtures_dir / FIXTURE


def _assert_no_id(text):
    # The text is dropped before the assert so a failure's traceback (which
    # prints the frame's arguments) cannot echo the leaked id (M6).
    leaked = sum(1 for fixture_id in FIXTURE_IDS if fixture_id in text)
    del text
    assert leaked == 0, f"{leaked} fixture id(s) leaked"


def _set_sha256(db_path):
    conn = db.connect(db_path)
    try:
        return account_roles.roles_set_sha256(conn)
    finally:
        conn.close()


def _dnd_events(db_path):
    conn = db.connect(db_path)
    try:
        return conn.execute("SELECT count(*) FROM dnd_events").fetchone()[0]
    finally:
        conn.close()


def _force_excluded_row(db_path):
    """The forced-row pattern of test_dnd_account_roles.py::TestR7: the
    importer refuses an excluded id that already has derived rows, so the
    test writes the row itself to stand for a ruling that reached the table
    after a derive (the reverse of the frozen-run order)."""
    conn = db.connect(db_path)
    try:
        conn.execute(
            f"INSERT INTO {account_roles.TABLE} (anon_student_id, role, exclude, pseudonym) "
            "VALUES (?, 'instructor', 1, ?)",
            (STAFF, PSEUDONYM),
        )
        conn.commit()
    finally:
        conn.close()


class TestRolesCommands:
    def test_no_subcommand_exits_2(self, db_path, capsys):
        assert cli.main(["--db", str(db_path), "dnd", "roles"]) == 2
        assert "roles" in capsys.readouterr().err

    def test_import_list_remove(self, db_path, roles_file, fixtures_dir, capsys):
        base = ["--db", str(db_path), "dnd", "roles"]
        assert cli.main([*base, "import", str(roles_file)]) == 0
        captured = capsys.readouterr()
        out = captured.out
        assert "added" in out and "unchanged" in out
        assert "excluded" in out and "ruled" in out
        assert ROLES in out and str(fixtures_dir) not in out
        digest = _set_sha256(db_path)
        assert digest is not None and digest[:12] in out
        _assert_no_id(out + captured.err)

        # A second import is idempotent.
        assert cli.main([*base, "import", str(roles_file)]) == 0
        out = capsys.readouterr().out
        assert "unchanged" in out
        _assert_no_id(out)

        # list: an ordinal, pseudonym, role, exclude, raw_rows, ruled_on,
        # ruled_by, the file name — never the id.
        assert cli.main([*base, "list"]) == 0
        captured = capsys.readouterr()
        out = captured.out
        assert PSEUDONYM in out
        assert "instructor" in out and "student" in out
        assert "owner" in out and "2026-06-30" in out
        assert ROLES in out and str(fixtures_dir) not in out
        _assert_no_id(out + captured.err)

        # remove takes the id and prints only "removed".
        assert cli.main([*base, "remove", STAFF]) == 0
        captured = capsys.readouterr()
        assert "removed" in captured.out
        _assert_no_id(captured.out + captured.err)
        assert cli.main([*base, "remove", STAFF]) == 1
        captured = capsys.readouterr()
        assert "no such row" in captured.out + captured.err
        _assert_no_id(captured.out + captured.err)
        assert cli.main([*base, "list"]) == 0
        out = capsys.readouterr().out
        assert "student" in out and "instructor" not in out
        _assert_no_id(out)

    def test_import_of_a_missing_file_exits_1(self, db_path, tmp_path, capsys):
        assert cli.main(
            ["--db", str(db_path), "dnd", "roles", "import", str(tmp_path / "missing.csv")]
        ) == 1

    def test_commands_on_a_database_predating_0016(self, db_path, roles_file, capsys):
        # The Copilot round on PR #238: `remove` left the writer's RoleError
        # uncaught (a traceback where `import` and `dnd derive` print the
        # remedy). All three commands on a database without the table: the
        # two writers exit 1 naming `migrate`, the reader answers empty.
        conn = db.connect(db_path)
        try:
            conn.execute(f"DROP TABLE {account_roles.TABLE}")
            conn.commit()
        finally:
            conn.close()
        base = ["--db", str(db_path), "dnd", "roles"]
        assert cli.main([*base, "remove", STAFF]) == 1
        captured = capsys.readouterr()
        assert "ERROR" in captured.err and "migrate" in captured.err
        assert "Traceback" not in captured.err
        _assert_no_id(captured.out + captured.err)
        assert cli.main([*base, "import", str(roles_file)]) == 1
        captured = capsys.readouterr()
        assert "ERROR" in captured.err and "migrate" in captured.err
        _assert_no_id(captured.out + captured.err)
        assert cli.main([*base, "list"]) == 0
        captured = capsys.readouterr()
        assert "0 ruled" in captured.out
        _assert_no_id(captured.out + captured.err)

    def test_import_after_a_derive_is_refused(self, db_path, roles_file, attrition_tab, capsys):
        # The reverse of the frozen-run order: ingest and derive first.
        assert cli.main(["--db", str(db_path), "ingest", str(attrition_tab)]) == 0
        assert cli.main(["--db", str(db_path), "dnd", "derive"]) == 0
        capsys.readouterr()
        assert cli.main(["--db", str(db_path), "dnd", "roles", "import", str(roles_file)]) == 1
        captured = capsys.readouterr()
        assert "ERROR:" in captured.err
        assert "instructor" in captured.err and PSEUDONYM in captured.err
        _assert_no_id(captured.out + captured.err)
        assert _set_sha256(db_path) is None  # the whole file was refused


class TestDeriveReportsTheWithheldRows:
    def test_derive_prints_the_excluded_line(self, db_path, roles_file, attrition_tab, capsys):
        # The frozen-run order: init → roles import → ingest → derive.
        assert cli.main(["--db", str(db_path), "dnd", "roles", "import", str(roles_file)]) == 0
        assert cli.main(["--db", str(db_path), "ingest", str(attrition_tab)]) == 0
        capsys.readouterr()
        assert cli.main(["--db", str(db_path), "dnd", "derive"]) == 0
        captured = capsys.readouterr()
        out = captured.out
        # 69 DnD-relevant rows, the staff id's 5 withheld (hand count in
        # test_dnd_account_roles.py).
        assert "inserted:  64" in out
        assert "excluded:  5" in out
        assert "rows of excluded accounts withheld" in out and "dnd roles" in out
        assert "malformed: 2" in out
        _assert_no_id(out + captured.err)

        # The status block carries the roles line.
        assert cli.main(["--db", str(db_path), "status"]) == 0
        captured = capsys.readouterr()
        assert "Drag-and-Drop (dnd):" in captured.out
        assert "roles:" in captured.out and "1 excluded of 2 ruled" in captured.out
        _assert_no_id(captured.out + captured.err)

    def test_ingest_derive_chains_the_filtered_derive(self, db_path, roles_file, attrition_tab, capsys):
        assert cli.main(["--db", str(db_path), "dnd", "roles", "import", str(roles_file)]) == 0
        capsys.readouterr()
        assert cli.main(["--db", str(db_path), "ingest", "--derive", str(attrition_tab)]) == 0
        captured = capsys.readouterr()
        assert "dnd derive: inserted=64" in captured.out
        _assert_no_id(captured.out + captured.err)
        conn = db.connect(db_path)
        try:
            assert conn.execute(
                "SELECT count(*) FROM dnd_events d JOIN events e USING (event_id) "
                "WHERE e.student_id = ?", (STAFF,),
            ).fetchone()[0] == 0
            assert conn.execute(
                "SELECT count(*) FROM events WHERE student_id = ?", (STAFF,)
            ).fetchone()[0] == 5
        finally:
            conn.close()


class TestDeriveRefusesUnderR7:
    """R7 at derive, through the CLI (spec §2.4: a `RoleError` prints
    `ERROR: …` and returns 1): with an excluded id that already has derived
    rows, `dnd derive` refuses before any insert, and `ingest --derive`
    reports the dnd step as failed (core's per-service tolerance: the ingest
    and the other services' derives stay committed). Both name counts,
    roles and pseudonyms only, and leave dnd_events as it was."""

    # The attrition fixture's 69 DnD-relevant rows, all derived when no
    # ruling is loaded (hand count in test_dnd_account_roles.py).
    N_DND_ROWS = 69

    def test_dnd_derive_exits_1_with_the_refusal_and_inserts_nothing(
        self, db_path, attrition_tab, capsys
    ):
        assert cli.main(["--db", str(db_path), "ingest", str(attrition_tab)]) == 0
        assert cli.main(["--db", str(db_path), "dnd", "derive"]) == 0
        assert _dnd_events(db_path) == self.N_DND_ROWS
        _force_excluded_row(db_path)
        capsys.readouterr()
        assert cli.main(["--db", str(db_path), "dnd", "derive"]) == 1
        captured = capsys.readouterr()
        assert "ERROR:" in captured.err
        assert "instructor" in captured.err and PSEUDONYM in captured.err
        assert "init" in captured.err  # the remedy
        assert "inserted:" not in captured.out and "excluded:" not in captured.out
        _assert_no_id(captured.out + captured.err)
        assert _dnd_events(db_path) == self.N_DND_ROWS

    def test_ingest_derive_reports_the_failed_dnd_step(
        self, db_path, attrition_tab, golden_dnd_tab, capsys
    ):
        assert cli.main(["--db", str(db_path), "ingest", str(attrition_tab)]) == 0
        assert cli.main(["--db", str(db_path), "dnd", "derive"]) == 0
        _force_excluded_row(db_path)
        capsys.readouterr()
        # A second file: the ingest commits, the dnd derive refuses.
        assert cli.main(
            ["--db", str(db_path), "ingest", "--derive", str(golden_dnd_tab)]
        ) == 1
        captured = capsys.readouterr()
        assert "Ingest complete:" in captured.out
        assert "dnd derive: inserted=" not in captured.out
        assert "FAILED (dnd):" in captured.err
        assert "instructor" in captured.err and PSEUDONYM in captured.err
        _assert_no_id(captured.out + captured.err)
        assert _dnd_events(db_path) == self.N_DND_ROWS
        conn = db.connect(db_path)
        try:
            assert conn.execute("SELECT count(*) FROM raw_imports").fetchone()[0] == 2
        finally:
            conn.close()
