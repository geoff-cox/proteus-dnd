"""
Tests for the staff-account filter at derive (E-02, PR 1 of 2;
``cc-task-attrition-ledger.md`` §4.1; owner rulings R1 (a), R2, R5, R7, R12,
R13 and J1-J4 at the brief's Checkpoint 1, 2026-10-19): the rulings-file
reader, the import, the derive-time filter, the R7 tripwire both ways, the
``raw_rows`` warning, the manifest section, the status line, and the
empty-table no-op on the DnD golden fixture, with the table empty and with
it absent (a database that predates dnd/0016).

The fixture ids are made up (``AttritionStaff1``, ``AttritionRuledKept``);
in the real rulings file the ids embed surnames, so no test here lets an id
reach a message, a log line, the manifest or an assertion message (spec M6).

Hand counts on ``golden_dnd_attrition.tab`` (``test_dnd_attrition_fixture``
derives them row by row): 71 rows, of which 69 are DnD-relevant (the
``shortanswer`` and ``mChoice`` rows of ``AttritionSA`` are not); the staff
id owns 5 of the 69 (a page row, three drops and a check), all withheld; 64
remain, 61 on the demo item and 3 on the board-less ``q9``; 4 of the staff
id's 5 rows are on the demo item (the page row carries the page's name); two
checks are malformed (``AttritionMalformed``'s truncated payload and
``AttritionRegime``'s ``not json``).
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from types import SimpleNamespace

import pytest

from proteus import db
from proteus.core import ingest as core_ingest
from proteus.core import manifest
from proteus.services.dragndrop import account_roles, service
from proteus.services.dragndrop import derive as derive_mod
from proteus.services.dragndrop.derive import derive_events

FIXTURE = "golden_dnd_attrition.tab"
ROLES = "account_roles_fixture.csv"
STAFF = "AttritionStaff1"
KEPT = "AttritionRuledKept"
FIXTURE_IDS = (STAFF, KEPT)
PSEUDONYM = "Y99-B03-P99"
Q = "demo-book__demo-shapes-q1"
HEADER = "pseudonym,anon_student_id,raw_rows,role,exclude,ruled_by,ruled_on,note\n"

# The fixture's DnD-relevant rows: 69 in all, 5 of them the staff id's
# (1 page + 3 drops + 1 check), 4 of those on the demo item; 64 derive.
N_DND_ROWS = 69
N_STAFF_ROWS = 5
N_STAFF_Q1_ROWS = 4
N_DERIVED = N_DND_ROWS - N_STAFF_ROWS
N_DERIVED_Q1 = 61
N_MALFORMED = 2

# The set hash of the fixture's two rows: sha256 over the sorted
# "<id>\t<role>\t<exclude>" lines, newline-joined (spec §2.2).
FIXTURE_SET_SHA256 = hashlib.sha256(
    "\n".join(sorted([
        f"{KEPT}\tstudent\t0",
        f"{STAFF}\tinstructor\t1",
    ])).encode("utf-8")
).hexdigest()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _roles_csv(path, lines, header=HEADER):
    """A rulings file with the fixture's header and the given data lines."""
    path.write_text("# test rulings\n" + header + "".join(f"{line}\n" for line in lines),
                    encoding="utf-8")
    return path


def _count(conn, sql, *params):
    return conn.execute(sql, params).fetchone()[0]


def _latest_run(conn, stage):
    return conn.execute(
        "SELECT config_json, notes FROM pipeline_runs WHERE service = 'dnd' "
        "AND stage = ? ORDER BY run_id DESC LIMIT 1",
        (stage,),
    ).fetchone()


def _assert_no_id(text):
    # Never put the id itself in the assertion message (M6), nor in the
    # traceback: pytest prints the arguments of the failing frame, so the
    # text is dropped before the assert and only the count survives.
    leaked = sum(1 for fixture_id in FIXTURE_IDS if fixture_id in text)
    del text
    assert leaked == 0, f"{leaked} fixture id(s) leaked"


# ---------------------------------------------------------------------------
# The rulings file
# ---------------------------------------------------------------------------
class TestRolesFile:
    def test_constants_match_the_documented_format(self):
        assert account_roles.TABLE == "dnd_account_roles"
        assert account_roles.FILE_COLUMNS == (
            "pseudonym", "anon_student_id", "raw_rows", "role", "exclude",
            "ruled_by", "ruled_on", "note",
        )
        assert account_roles.REQUIRED_COLUMNS == ("anon_student_id", "role", "exclude")
        assert account_roles.ROLE_VOCABULARY == ("instructor", "researcher", "test", "student")

    def test_parses_the_fixture_and_skips_its_comment_lines(self, fixtures_dir):
        rows = account_roles.read_roles_file(fixtures_dir / ROLES)
        assert len(rows) == 2  # the two comment lines are not rows
        assert [r["anon_student_id"] for r in rows] == [STAFF, KEPT]
        assert [r["role"] for r in rows] == ["instructor", "student"]
        assert [r["exclude"] for r in rows] == [True, False]
        assert [r["raw_rows"] for r in rows] == [5, 4]
        assert [r["pseudonym"] for r in rows] == [PSEUDONYM, PSEUDONYM]
        assert [r["ruled_by"] for r in rows] == ["owner", "owner"]
        assert [r["ruled_on"] for r in rows] == ["2026-06-30", "2026-06-30"]

    def test_note_is_read_and_never_returned(self, fixtures_dir):
        rows = account_roles.read_roles_file(fixtures_dir / ROLES)
        assert all("note" not in r for r in rows)
        assert not any("fixture)" in str(v) for r in rows for v in r.values())

    def test_blank_lines_and_comments_anywhere_are_skipped(self, tmp_path):
        path = tmp_path / "roles.csv"
        path.write_text(
            "\n# leading comment\n" + HEADER + "\n"
            f"{PSEUDONYM},{STAFF},5,instructor,yes,owner,2026-06-30,\n"
            "   \n# a comment between rows\n"
            f"{PSEUDONYM},{KEPT},,student,no,,,\n\n",
            encoding="utf-8",
        )
        rows = account_roles.read_roles_file(path)
        assert [(r["anon_student_id"], r["raw_rows"], r["ruled_by"]) for r in rows] == [
            (STAFF, 5, "owner"), (KEPT, None, None),
        ]

    def test_refuses_a_header_lacking_a_required_column(self, tmp_path):
        path = _roles_csv(
            tmp_path / "roles.csv",
            [f"{PSEUDONYM},{STAFF},5,instructor,owner,2026-06-30,"],
            header="pseudonym,anon_student_id,raw_rows,role,ruled_by,ruled_on,note\n",
        )
        with pytest.raises(account_roles.RoleError, match="exclude"):
            account_roles.read_roles_file(path)

    def test_refuses_a_bad_exclude(self, tmp_path):
        path = _roles_csv(
            tmp_path / "roles.csv", [f"{PSEUDONYM},{STAFF},5,instructor,maybe,owner,2026-06-30,"]
        )
        with pytest.raises(account_roles.RoleError) as exc:
            account_roles.read_roles_file(path)
        _assert_no_id(str(exc.value))

    def test_refuses_a_non_integer_raw_rows(self, tmp_path):
        path = _roles_csv(
            tmp_path / "roles.csv", [f"{PSEUDONYM},{STAFF},five,instructor,yes,owner,2026-06-30,"]
        )
        with pytest.raises(account_roles.RoleError, match="raw_rows") as exc:
            account_roles.read_roles_file(path)
        _assert_no_id(str(exc.value))

    def test_refuses_a_file_without_a_header_row(self, tmp_path):
        path = tmp_path / "roles.csv"
        path.write_text("# only comments\n\n# nothing else\n", encoding="utf-8")
        with pytest.raises(account_roles.RoleError, match="header"):
            account_roles.read_roles_file(path)

    @pytest.mark.parametrize("spelling, expected", [
        ("yes", True), ("YES", True), ("1", True), ("true", True), ("True", True),
        ("no", False), ("No", False), ("0", False), ("false", False), ("FALSE", False),
    ])
    def test_exclude_spellings(self, tmp_path, spelling, expected):
        path = _roles_csv(
            tmp_path / "roles.csv", [f"{PSEUDONYM},{STAFF},5,instructor,{spelling},owner,2026-06-30,"]
        )
        [row] = account_roles.read_roles_file(path)
        assert row["exclude"] == expected

    def test_file_sha256_is_the_file_hash(self, fixtures_dir):
        path = fixtures_dir / ROLES
        assert account_roles.file_sha256(path) == hashlib.sha256(path.read_bytes()).hexdigest()


    # The second Copilot round on PR #238: non-strict parsing let a kept
    # row's unterminated note swallow the excluded row after it, and the
    # file read back with the kept row alone. Strict parsing refuses the
    # whole file; the message carries no cell.
    def test_an_unterminated_quote_cannot_absorb_the_rows_after_it(self, tmp_path):
        path = _roles_csv(tmp_path / "absorbing.csv", [
            f'{PSEUDONYM},AttritionClean9,4,student,no,owner,2026-06-30,"an unterminated note',
            f"{PSEUDONYM},{STAFF},5,instructor,yes,owner,2026-06-30,instructor self-account",
        ])
        with pytest.raises(account_roles.RoleError, match="not well-formed CSV") as exc:
            account_roles.read_roles_file(path)
        _assert_no_id(str(exc.value))

    def test_a_comment_prefixed_continuation_line_is_refused_too(self, tmp_path):
        # The line filter drops a multi-line note's continuation that starts
        # with "#", which leaves its quote unterminated: refused, not merged
        # into the row that follows.
        path = _roles_csv(tmp_path / "continuation.csv", [
            f'{PSEUDONYM},AttritionClean9,4,student,no,owner,2026-06-30,"a note that',
            '# continues on a comment-looking line"',
            f"{PSEUDONYM},{STAFF},5,instructor,yes,owner,2026-06-30,instructor self-account",
        ])
        with pytest.raises(account_roles.RoleError, match="not well-formed CSV") as exc:
            account_roles.read_roles_file(path)
        _assert_no_id(str(exc.value))

    def test_quoted_notes_still_parse(self, tmp_path):
        # A comma, a doubled quote and a multi-line note (its continuation
        # kept) are ordinary CSV quoting: both rows read, in order.
        path = _roles_csv(tmp_path / "quoted.csv", [
            f'{PSEUDONYM},AttritionClean9,4,student,no,owner,2026-06-30,"a, ""quoted"" note"',
            f'{PSEUDONYM},{STAFF},5,instructor,yes,owner,2026-06-30,"a note that',
            'continues on a second line"',
        ])
        rows = account_roles.read_roles_file(path)
        assert [(r["anon_student_id"], r["exclude"]) for r in rows] == [
            ("AttritionClean9", False), (STAFF, True),
        ]


# ---------------------------------------------------------------------------
# The import
# ---------------------------------------------------------------------------
class TestImport:
    def test_adds_two_then_unchanged_with_a_stable_set_hash(self, db_conn, fixtures_dir):
        path = fixtures_dir / ROLES
        first = account_roles.import_roles(db_conn, path)
        assert (first.added, first.unchanged) == (2, 0)
        assert first.file_sha256 == account_roles.file_sha256(path)
        assert len(first.rows) == 2
        again = account_roles.import_roles(db_conn, path)
        assert (again.added, again.unchanged) == (0, 2)
        assert again.set_sha256 == first.set_sha256 == account_roles.roles_set_sha256(db_conn)
        assert first.set_sha256 == FIXTURE_SET_SHA256
        assert _count(db_conn, f"SELECT count(*) FROM {account_roles.TABLE}") == 2

    def test_rows_carry_pseudonym_raw_rows_and_provenance(self, db_conn, fixtures_dir):
        path = fixtures_dir / ROLES
        account_roles.import_roles(db_conn, path)
        by_id = {r.anon_student_id: r for r in account_roles.list_roles(db_conn)}
        assert set(by_id) == set(FIXTURE_IDS)
        staff, kept = by_id[STAFF], by_id[KEPT]
        assert isinstance(staff, account_roles.AccountRole)
        assert (staff.role, staff.exclude, staff.pseudonym, staff.raw_rows) == (
            "instructor", True, PSEUDONYM, 5,
        )
        assert (kept.role, kept.exclude, kept.pseudonym, kept.raw_rows) == (
            "student", False, PSEUDONYM, 4,
        )
        for row in (staff, kept):
            assert (row.ruled_by, row.ruled_on) == ("owner", "2026-06-30")
            assert row.imported_from == ROLES  # the name, never a path
            assert row.imported_sha256 == account_roles.file_sha256(path)
        # The note column is never stored (R13).
        columns = {r["name"] for r in db_conn.execute(f"PRAGMA table_info({account_roles.TABLE})")}
        assert "note" not in columns
        assert columns == {
            "anon_student_id", "role", "exclude", "pseudonym", "raw_rows",
            "ruled_by", "ruled_on", "imported_from", "imported_sha256",
        }

    def test_readers(self, db_conn, fixtures_dir):
        account_roles.import_roles(db_conn, fixtures_dir / ROLES)
        assert account_roles.excluded_ids(db_conn) == frozenset({STAFF})
        assert account_roles.load_role_map(db_conn) == {
            STAFF: ("instructor", True), KEPT: ("student", False),
        }
        assert account_roles.counts_by_role(db_conn) == {"instructor": 1, "student": 1}

    def test_empty_table_reads_as_empty(self, db_conn):
        assert account_roles.excluded_ids(db_conn) == frozenset()
        assert account_roles.roles_set_sha256(db_conn) is None
        assert account_roles.list_roles(db_conn) == []
        assert account_roles.load_role_map(db_conn) == {}
        assert account_roles.counts_by_role(db_conn) == {}
        assert account_roles.derived_rows_of_excluded(db_conn) == []
        assert account_roles.raw_rows_mismatches(db_conn) == 0

    @pytest.mark.parametrize("line", [
        pytest.param(f"{PSEUDONYM},{KEPT},4,instructor,no,owner,2026-06-30,", id="role-changed"),
        pytest.param(f"{PSEUDONYM},{STAFF},5,instructor,no,owner,2026-06-30,", id="exclude-changed"),
    ])
    def test_a_changed_role_or_exclude_for_a_known_id_is_refused(
        self, db_conn, fixtures_dir, tmp_path, line
    ):
        account_roles.import_roles(db_conn, fixtures_dir / ROLES)
        before = account_roles.roles_set_sha256(db_conn)
        path = _roles_csv(tmp_path / "changed.csv", [line])
        with pytest.raises(account_roles.RoleError) as exc:
            account_roles.import_roles(db_conn, path)
        _assert_no_id(str(exc.value))
        assert account_roles.roles_set_sha256(db_conn) == before
        assert len(account_roles.list_roles(db_conn)) == 2

    def test_the_same_pair_again_is_unchanged(self, db_conn, fixtures_dir, tmp_path):
        account_roles.import_roles(db_conn, fixtures_dir / ROLES)
        # Same role and exclude, other comment columns: unchanged, nothing rewritten.
        path = _roles_csv(tmp_path / "same.csv", [f"Y98-B01-P01,{STAFF},,instructor,YES,,,"])
        result = account_roles.import_roles(db_conn, path)
        assert (result.added, result.unchanged) == (0, 1)
        [staff] = [r for r in account_roles.list_roles(db_conn) if r.anon_student_id == STAFF]
        assert (staff.pseudonym, staff.raw_rows, staff.imported_from) == (PSEUDONYM, 5, ROLES)

    def test_an_empty_id_is_refused_and_the_file_leaves_no_row(self, db_conn, tmp_path):
        path = _roles_csv(tmp_path / "empty-id.csv", [
            f"{PSEUDONYM},{KEPT},4,student,no,owner,2026-06-30,",
            f"{PSEUDONYM},,5,instructor,yes,owner,2026-06-30,",
        ])
        with pytest.raises(account_roles.RoleError):
            account_roles.import_roles(db_conn, path)
        assert account_roles.list_roles(db_conn) == []

    def test_remove_role(self, db_conn, fixtures_dir):
        account_roles.import_roles(db_conn, fixtures_dir / ROLES)
        before = account_roles.roles_set_sha256(db_conn)
        assert account_roles.remove_role(db_conn, STAFF) is True
        assert account_roles.excluded_ids(db_conn) == frozenset()
        assert account_roles.remove_role(db_conn, STAFF) is False
        after = account_roles.roles_set_sha256(db_conn)
        assert after is not None and after != before
        assert after == hashlib.sha256(f"{KEPT}\tstudent\t0".encode("utf-8")).hexdigest()
        assert account_roles.remove_role(db_conn, KEPT) is True
        assert account_roles.roles_set_sha256(db_conn) is None

    def test_add_role_on_the_add_regime_pattern(self, db_conn):
        # Assumed signature (spec §2.2 names add_role without one; the
        # regime pattern is add_regime(conn, key, value, *, comment columns)):
        # add_role(conn, anon_student_id, role, exclude, ...).
        assert account_roles.add_role(db_conn, "AttritionExtra", "researcher", True) == "added"
        assert account_roles.add_role(db_conn, "AttritionExtra", "researcher", True) == "unchanged"
        with pytest.raises(account_roles.RoleError):
            account_roles.add_role(db_conn, "AttritionExtra", "student", True)
        with pytest.raises(account_roles.RoleError):
            account_roles.add_role(db_conn, "", "student", False)
        assert account_roles.excluded_ids(db_conn) == frozenset({"AttritionExtra"})


    def test_a_malformed_file_imports_nothing(self, db_conn, tmp_path):
        # The second Copilot round on PR #238: the absorbing file (a kept
        # row's unterminated note, then the excluded row) is refused before
        # any write, not imported with the kept row alone.
        path = _roles_csv(tmp_path / "absorbing.csv", [
            f'{PSEUDONYM},AttritionClean9,4,student,no,owner,2026-06-30,"an unterminated note',
            f"{PSEUDONYM},{STAFF},5,instructor,yes,owner,2026-06-30,instructor self-account",
        ])
        with pytest.raises(account_roles.RoleError, match="not well-formed CSV") as exc:
            account_roles.import_roles(db_conn, path)
        _assert_no_id(str(exc.value))
        assert account_roles.list_roles(db_conn) == []
        assert account_roles.excluded_ids(db_conn) == frozenset()
        assert account_roles.roles_set_sha256(db_conn) is None


# ---------------------------------------------------------------------------
# The derive filter (R1 a, J1, J3, R12)
# ---------------------------------------------------------------------------
@pytest.fixture
def filtered(db_conn, fixtures_dir):
    """init → roles import → ingest → derive, the frozen-run order."""
    account_roles.import_roles(db_conn, fixtures_dir / ROLES)
    ingested = core_ingest.ingest_file(db_conn, fixtures_dir / FIXTURE)
    result = derive_events(db_conn)
    return SimpleNamespace(conn=db_conn, ingested=ingested, result=result)


class TestDeriveFilter:
    def test_staff_rows_withheld_from_dnd_events_and_kept_in_events(self, filtered):
        conn = filtered.conn
        assert _count(conn, "SELECT count(*) FROM events WHERE student_id = ?", STAFF) == N_STAFF_ROWS
        assert _count(
            conn,
            "SELECT count(*) FROM dnd_events d JOIN events e USING (event_id) "
            "WHERE e.student_id = ?", STAFF,
        ) == 0

    def test_result_counts(self, filtered):
        r = filtered.result
        assert isinstance(r, derive_mod.DeriveResult)
        assert r.excluded == N_STAFF_ROWS
        # The withheld rows are not selected, so they are not seen (spec §2.3).
        assert (r.seen, r.inserted, r.skipped, r.malformed) == (
            N_DERIVED, N_DERIVED, 0, N_MALFORMED,
        )
        assert _count(filtered.conn, "SELECT count(*) FROM dnd_events") == N_DERIVED

    def test_kept_id_rows_present(self, filtered):
        assert _count(
            filtered.conn,
            "SELECT count(*) FROM dnd_events d JOIN events e USING (event_id) "
            "WHERE e.student_id = ?", KEPT,
        ) == 4

    def test_staff_page_row_withheld_too(self, filtered):
        # J1: the fixture's only page row is the staff id's.
        conn = filtered.conn
        assert _count(
            conn, "SELECT count(*) FROM events WHERE selection = 'page' AND student_id = ?", STAFF
        ) == 1
        assert _count(conn, "SELECT count(*) FROM events WHERE selection = 'page'") == 1
        assert _count(conn, "SELECT count(*) FROM dnd_events WHERE kind = 'page_view'") == 0

    def test_rederive_is_idempotent_and_reports_the_withheld_rows_again(self, filtered):
        again = derive_events(filtered.conn)
        assert again.inserted == 0
        assert again.skipped == filtered.result.inserted == N_DERIVED
        assert again.excluded == N_STAFF_ROWS
        assert again.malformed == 0  # nothing newly written
        assert _count(filtered.conn, "SELECT count(*) FROM dnd_events") == N_DERIVED

    def test_run_config_and_notes(self, filtered):
        conn = filtered.conn
        run = _latest_run(conn, "derive")
        config = json.loads(run["config_json"])
        digest = account_roles.roles_set_sha256(conn)
        assert digest is not None and len(digest) == 64
        assert config["roles_set_sha256"] == digest == FIXTURE_SET_SHA256
        assert config["stage_version"] == 2 == derive_mod.STAGE_VERSION
        assert "excluded=5" in run["notes"]
        assert "excluded_students=1" in run["notes"]
        _assert_no_id(run["notes"])
        _assert_no_id(run["config_json"])

    def test_filters_scope_the_withheld_count(self, db_conn, fixtures_dir):
        account_roles.import_roles(db_conn, fixtures_dir / ROLES)
        ingested = core_ingest.ingest_file(db_conn, fixtures_dir / FIXTURE)
        # The question filter: the staff page row carries the page's name.
        by_question = derive_events(db_conn, question_id=Q)
        assert (by_question.inserted, by_question.excluded) == (N_DERIVED_Q1, N_STAFF_Q1_ROWS)
        # Another import: nothing seen, nothing withheld.
        other = derive_events(db_conn, import_id=ingested.import_id + 1)
        assert (other.seen, other.inserted, other.excluded) == (0, 0, 0)
        # This import: the remaining q9 rows derive, the five staff rows are
        # withheld (counted under the import filter), the q1 rows skipped.
        mine = derive_events(db_conn, import_id=ingested.import_id)
        assert (mine.inserted, mine.skipped, mine.excluded) == (3, N_DERIVED_Q1, N_STAFF_ROWS)

    def test_excluded_defaults_to_zero_on_the_result(self):
        assert derive_mod.DeriveResult.__dataclass_fields__["excluded"].default == 0


# ---------------------------------------------------------------------------
# R7: an excluded id already derived
# ---------------------------------------------------------------------------
class TestR7:
    def test_import_after_derive_refuses_the_excluded_row(self, db_conn, fixtures_dir):
        # The reverse order: derive first (no roles), then the rulings file.
        core_ingest.ingest_file(db_conn, fixtures_dir / FIXTURE)
        derive_events(db_conn)
        assert _count(db_conn, "SELECT count(*) FROM dnd_events") == N_DND_ROWS
        with pytest.raises(account_roles.RoleError) as exc:
            account_roles.import_roles(db_conn, fixtures_dir / ROLES)
        message = str(exc.value)
        assert "instructor" in message and PSEUDONYM in message
        _assert_no_id(message)
        # The whole file is refused: the kept row is not written either.
        assert account_roles.list_roles(db_conn) == []

    def test_forced_row_trips_the_derive_tripwire(self, db_conn, fixtures_dir):
        core_ingest.ingest_file(db_conn, fixtures_dir / FIXTURE)
        derive_events(db_conn)
        runs_before = _count(db_conn, "SELECT count(*) FROM pipeline_runs WHERE stage = 'derive'")
        db_conn.execute(
            f"INSERT INTO {account_roles.TABLE} (anon_student_id, role, exclude, pseudonym) "
            "VALUES (?, 'instructor', 1, ?)",
            (STAFF, PSEUDONYM),
        )
        db_conn.commit()
        assert account_roles.derived_rows_of_excluded(db_conn) == [
            ("instructor", PSEUDONYM, N_STAFF_ROWS),
        ]
        with pytest.raises(account_roles.RoleError) as exc:
            account_roles.assert_no_derived_rows(db_conn)
        message = str(exc.value)
        assert "instructor" in message and PSEUDONYM in message
        assert re.search(r"\b1\b", message) and re.search(r"\b5\b", message)
        assert "init" in message  # the remedy
        _assert_no_id(message)
        # derive refuses before any insert and records no run row.
        with pytest.raises(account_roles.RoleError) as exc:
            derive_events(db_conn)
        _assert_no_id(str(exc.value))
        assert _count(db_conn, "SELECT count(*) FROM pipeline_runs WHERE stage = 'derive'") == runs_before
        assert _count(db_conn, "SELECT count(*) FROM dnd_events") == N_DND_ROWS

    def test_exclude_no_row_for_a_derived_id_is_allowed(self, db_conn, fixtures_dir, tmp_path):
        core_ingest.ingest_file(db_conn, fixtures_dir / FIXTURE)
        derive_events(db_conn)
        path = _roles_csv(
            tmp_path / "kept.csv", [f"{PSEUDONYM},AttritionClean1,4,student,no,owner,2026-06-30,"]
        )
        result = account_roles.import_roles(db_conn, path)
        assert (result.added, result.unchanged) == (1, 0)
        account_roles.assert_no_derived_rows(db_conn)  # silent
        again = derive_events(db_conn)
        assert (again.inserted, again.skipped, again.excluded) == (0, N_DND_ROWS, 0)


# ---------------------------------------------------------------------------
# The write lock (the Copilot round on PR #238)
# ---------------------------------------------------------------------------
class TestWriteLock:
    """derive opens ``BEGIN IMMEDIATE`` before the governance checks and
    holds it through the inserts, so an import on another connection cannot
    land its first exclusion in between the exclusion snapshot and the
    inserts (which would derive the account's rows under ``excluded=0``
    while recording the new hash); a caller already inside a transaction
    keeps its own."""

    def test_an_import_racing_the_snapshot_is_locked_out_then_refused(
        self, tmp_path, fixtures_dir, monkeypatch,
    ):
        # Two connections on one file database: `conn` derives, `other`
        # imports the rulings at the exact point derive has taken its
        # snapshot of the excluded ids (an empty table). Before the lock the
        # import succeeded there and derive went on to write the staff rows.
        path = tmp_path / "race.db"
        conn = db.connect(path)
        db.bootstrap(conn)
        core_ingest.ingest_file(conn, fixtures_dir / FIXTURE)
        other = sqlite3.connect(str(path), timeout=0.2, detect_types=sqlite3.PARSE_DECLTYPES)
        other.row_factory = sqlite3.Row
        outcome = {}
        snapshot = account_roles.excluded_ids

        def snapshot_then_race(c):
            ids = snapshot(c)
            if c is conn and not outcome:
                outcome["locked_by_derive"] = c.in_transaction
                try:
                    account_roles.import_roles(other, fixtures_dir / ROLES)
                    outcome["import"] = "succeeded"
                except sqlite3.OperationalError as exc:
                    outcome["import"] = f"refused: {exc}"
            return ids

        monkeypatch.setattr(account_roles, "excluded_ids", snapshot_then_race)
        try:
            result = derive_events(conn)
            assert outcome["locked_by_derive"] is True
            assert outcome["import"].startswith("refused:") and "locked" in outcome["import"]
            # The snapshot was empty and stayed true: every row derived,
            # nothing withheld, no ruling recorded, no hash.
            assert (result.inserted, result.excluded) == (N_DND_ROWS, 0)
            assert account_roles.list_roles(conn) == []
            assert account_roles.derived_rows_of_excluded(conn) == []
            assert json.loads(_latest_run(conn, "derive")["config_json"])["roles_set_sha256"] is None
            # Once the pass has committed, the same import meets the derived
            # rows and is refused the R7 way (counts and pseudonym, no id).
            with pytest.raises(account_roles.RoleError) as exc:
                account_roles.import_roles(other, fixtures_dir / ROLES)
            message = str(exc.value)
            assert "instructor" in message and PSEUDONYM in message
            _assert_no_id(message)
            assert account_roles.list_roles(conn) == []
        finally:
            other.close()
            conn.close()

    def test_a_caller_inside_a_transaction_keeps_it(self, db_conn, fixtures_dir):
        # Transaction ownership: derive neither opens its own lock nor
        # commits, so the caller's rollback takes the whole pass with it.
        core_ingest.ingest_file(db_conn, fixtures_dir / FIXTURE)
        db_conn.execute("BEGIN")
        result = derive_events(db_conn)
        assert db_conn.in_transaction
        assert result.inserted == N_DND_ROWS
        assert _count(db_conn, "SELECT count(*) FROM dnd_events") == N_DND_ROWS
        db_conn.rollback()
        assert _count(db_conn, "SELECT count(*) FROM dnd_events") == 0
        assert _latest_run(db_conn, "derive") is None

    def test_the_tripwire_refusal_releases_the_lock(self, db_conn, fixtures_dir):
        # A refusal raised under the lock rolls back and leaves no
        # transaction open on the connection (the ingest --derive chain
        # relies on a clean connection for the next service).
        core_ingest.ingest_file(db_conn, fixtures_dir / FIXTURE)
        derive_events(db_conn)
        db_conn.execute(
            f"INSERT INTO {account_roles.TABLE} (anon_student_id, role, exclude, pseudonym) "
            "VALUES (?, 'instructor', 1, ?)",
            (STAFF, PSEUDONYM),
        )
        db_conn.commit()
        with pytest.raises(account_roles.RoleError):
            derive_events(db_conn)
        assert not db_conn.in_transaction


# ---------------------------------------------------------------------------
# The raw_rows claim (R13)
# ---------------------------------------------------------------------------
def _raw_rows_records(caplog):
    return [
        r for r in caplog.records
        if r.levelname == "WARNING" and (
            "raw_rows" in r.getMessage() or "raw row" in r.getMessage()
        )
    ]


class TestRawRowsClaim:
    def test_a_mismatch_warns_once_with_a_count_and_no_id(
        self, db_conn, fixtures_dir, tmp_path, caplog
    ):
        # The kept id has 4 rows in the fixture; the file claims 3.
        path = _roles_csv(tmp_path / "claims.csv", [
            f"{PSEUDONYM},{STAFF},5,instructor,yes,owner,2026-06-30,",
            f"{PSEUDONYM},{KEPT},3,student,no,owner,2026-06-30,",
        ])
        account_roles.import_roles(db_conn, path)
        core_ingest.ingest_file(db_conn, fixtures_dir / FIXTURE)
        assert account_roles.raw_rows_mismatches(db_conn) == 1
        with caplog.at_level("WARNING"):
            result = derive_events(db_conn)
        assert result.excluded == N_STAFF_ROWS
        [record] = _raw_rows_records(caplog)
        assert re.search(r"\b1\b", record.getMessage())
        for r in caplog.records:
            _assert_no_id(r.getMessage())

    def test_matching_claims_warn_nothing(self, db_conn, fixtures_dir, caplog):
        account_roles.import_roles(db_conn, fixtures_dir / ROLES)
        core_ingest.ingest_file(db_conn, fixtures_dir / FIXTURE)
        assert account_roles.raw_rows_mismatches(db_conn) == 0
        with caplog.at_level("WARNING"):
            derive_events(db_conn)
        assert _raw_rows_records(caplog) == []

    def test_a_ruled_id_without_events_is_not_a_mismatch(self, db_conn, fixtures_dir):
        # Before the ingest no ruled id has an events row: the claim is
        # untested, not contradicted.
        account_roles.import_roles(db_conn, fixtures_dir / ROLES)
        assert account_roles.raw_rows_mismatches(db_conn) == 0


# ---------------------------------------------------------------------------
# The manifest section (J4) and the status line
# ---------------------------------------------------------------------------
class TestManifestAndStatus:
    def test_section_carries_counts_and_hashes_only(self, db_conn, fixtures_dir):
        path = fixtures_dir / ROLES
        account_roles.import_roles(db_conn, path)
        section = account_roles.manifest_section(db_conn)
        assert set(section) == {"account_roles"}
        assert section["account_roles"] == {
            "count": 2,
            "excluded": 1,
            "by_role": {"instructor": 1, "student": 1},
            "set_sha256": FIXTURE_SET_SHA256,
            "files": [{"name": ROLES, "sha256": account_roles.file_sha256(path)}],
        }
        text = json.dumps(section, sort_keys=True)
        _assert_no_id(text)
        assert str(fixtures_dir) not in text

    def test_empty_section(self, db_conn):
        assert account_roles.manifest_section(db_conn) == {
            "account_roles": {
                "count": 0, "excluded": 0, "by_role": {}, "set_sha256": None, "files": [],
            }
        }

    def test_run_manifest_carries_the_section_without_an_id(self, db_conn, fixtures_dir):
        account_roles.import_roles(db_conn, fixtures_dir / ROLES)
        body = manifest.build_manifest(db_conn)["body"]
        section = body["services"]["dnd"]
        assert section["account_roles"] == account_roles.manifest_section(db_conn)["account_roles"]
        assert {"question_aliases", "logging_regimes", "account_roles"} <= set(section)
        _assert_no_id(manifest.dump_yaml(body))

    def test_status_line(self, db_conn, fixtures_dir):
        assert service.status(db_conn)["roles"] == "0 excluded of 0 ruled"
        account_roles.import_roles(db_conn, fixtures_dir / ROLES)
        contribution = service.status(db_conn)
        assert contribution["roles"] == "1 excluded of 2 ruled"
        _assert_no_id(json.dumps(contribution))


# ---------------------------------------------------------------------------
# An empty roles table is a no-op (the golden tests bootstrap fresh databases)
# ---------------------------------------------------------------------------
class TestEmptyTableIsANoop:
    def test_golden_dnd_tab_derives_as_before(self, db_conn, golden_dnd_tab):
        core_ingest.ingest_file(db_conn, golden_dnd_tab)
        result = derive_events(db_conn)
        # The counts test_dnd_derive.py::TestDeriveClassification::test_counts pins.
        assert (result.seen, result.inserted, result.skipped, result.malformed) == (13, 13, 0, 2)
        assert result.excluded == 0
        run = _latest_run(db_conn, "derive")
        assert json.loads(run["config_json"])["roles_set_sha256"] is None
        assert "excluded=0" in run["notes"]
        again = derive_events(db_conn)
        assert (again.inserted, again.skipped, again.excluded) == (0, 13, 0)

    def test_absent_table_predating_0016_is_a_no_op_too(self, db_conn, golden_dnd_tab):
        # A database that predates dnd/0016 (spec §2.2: `excluded_ids` is
        # "empty on a database that predates 0016"). `db.connect` applies no
        # migration and no CLI path refuses a database with pending ones, so
        # `proteus dnd derive` on a pre-PR database that has not seen
        # `proteus migrate` takes this path: every reader answers empty,
        # derive runs exactly as before, the status block carries no roles
        # line and the manifest section reports an empty set.
        db_conn.execute(f"DROP TABLE {account_roles.TABLE}")
        db_conn.commit()
        assert db_conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            (account_roles.TABLE,),
        ).fetchone() is None
        assert account_roles.excluded_ids(db_conn) == frozenset()
        assert account_roles.roles_set_sha256(db_conn) is None
        assert account_roles.load_role_map(db_conn) == {}
        assert account_roles.list_roles(db_conn) == []
        assert account_roles.counts_by_role(db_conn) == {}
        assert account_roles.derived_rows_of_excluded(db_conn) == []
        assert account_roles.raw_rows_mismatches(db_conn) == 0
        account_roles.assert_no_derived_rows(db_conn)  # silent
        core_ingest.ingest_file(db_conn, golden_dnd_tab)
        result = derive_events(db_conn)
        assert (result.seen, result.inserted, result.skipped, result.malformed) == (13, 13, 0, 2)
        assert result.excluded == 0
        run = _latest_run(db_conn, "derive")
        assert json.loads(run["config_json"])["roles_set_sha256"] is None
        assert "excluded=0" in run["notes"]
        assert account_roles.manifest_section(db_conn) == {
            "account_roles": {
                "count": 0, "excluded": 0, "by_role": {}, "set_sha256": None, "files": [],
            }
        }
        body = manifest.build_manifest(db_conn)["body"]
        assert body["services"]["dnd"]["account_roles"]["count"] == 0
        assert "roles" not in service.status(db_conn)

    def test_writers_refuse_a_database_predating_0016(self, db_conn, fixtures_dir):
        # The readers answer empty on a pre-0016 database; the writers name
        # the remedy instead of leaking a bare OperationalError from SQLite.
        db_conn.execute(f"DROP TABLE {account_roles.TABLE}")
        db_conn.commit()
        with pytest.raises(account_roles.RoleError, match="migrate"):
            account_roles.import_roles(db_conn, fixtures_dir / ROLES)
        with pytest.raises(account_roles.RoleError, match="migrate"):
            account_roles.add_role(db_conn, "MadeUpId", "test", True)
        with pytest.raises(account_roles.RoleError, match="migrate"):
            account_roles.remove_role(db_conn, "MadeUpId")

    def test_a_null_id_row_withholds_nothing(self, db_conn, golden_dnd_tab):
        # SQLite allows NULL in a TEXT primary key. A hand-planted NULL id
        # must not turn derive's `NOT IN (subquery)` into "withhold every
        # row" (three-valued logic): the subquery and excluded_ids skip it.
        db_conn.execute(
            f"INSERT INTO {account_roles.TABLE} (anon_student_id, role, exclude) "
            "VALUES (NULL, 'test', 1)"
        )
        db_conn.commit()
        assert account_roles.excluded_ids(db_conn) == frozenset()
        core_ingest.ingest_file(db_conn, golden_dnd_tab)
        result = derive_events(db_conn)
        assert (result.seen, result.inserted, result.skipped, result.malformed) == (13, 13, 0, 2)
        assert result.excluded == 0
