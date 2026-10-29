"""
Tests for the question-identifier alias at board binding (dnd/0014;
cc-task-question-id-alias, the P4 RREF repair).

The fixture is built by hand: the four-shapes board under the canonical
identifier X, and a trace logged under the retired identifier X_OLD whose
labels carry X_OLD (the positional grammar anchors on the logged id). With
the alias X_OLD → X the trace binds to X's board: P comes from the board (4,
not the 3 the labels alone would give), no `missing_source_map`, the check
scores against the key, and the item aggregates both identifiers. Every
refusal writes nothing; with an empty alias table nothing changes.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from proteus import cli, db
from proteus.core import ingest as core_ingest
from proteus.core import manifest
from proteus.services.dragndrop import (
    aliases,
    board,
    cohort,
    contrast,
    export,
    process,
    sensitivity,
    trajectory,
    validate,
)
from proteus.services.dragndrop.derive import derive_events
from proteus.services.dragndrop.reconstruct import reconstruct_all
from proteus.services.dragndrop.scoring import score_checks

X = "demo-book__demo-shapes-q1"  # the canonical identifier: the four-shapes board
X_OLD = "demo-book__demo-shapes-q1-old"  # the retired identifier: events only
OTHER = "demo-book__other"  # a catalog-only identifier (no events, no board)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------
def _move(q, card, zone):
    target = f"{q}_drop{zone}" if zone else "dragzone"
    return "dragNdrop-drop", f"{q}_drag{card} -> {target}", "Incorrect"


def _check(q, feedback, **zones):
    payload = json.dumps({
        f"{q}_drop{z}": [f"{q}_drag{c}" for c in cards] for z, cards in zones.items()
    })
    return "dragNdrop", payload, feedback


def _ts(seconds):
    minutes, sec = divmod(int(seconds), 60)
    return f"2026-02-06 11:{minutes:02d}:{sec:02d}.000000"


# The key (1, 1, 2, 0) placed with cards 1–3 and checked Correct: the labels
# alone witness P = 3; the board says P = 4 (the circle stays in the tray).
CORRECT_TRACE = [
    ("o1", _move(X_OLD, 1, 1), 0),
    ("o2", _move(X_OLD, 2, 1), 5),
    ("o3", _move(X_OLD, 3, 2), 10),
    ("o4", _check(X_OLD, "Correct", **{"1": [1, 2], "2": [3]}), 15),
]
# A Correct check that shows the square in the Triangle slot: a key the
# four-shapes board disagrees with.
WRONG_KEY_TRACE = [
    ("w1", _move(X_OLD, 1, 1), 0),
    ("w2", _move(X_OLD, 2, 1), 5),
    ("w3", _move(X_OLD, 3, 1), 10),
    ("w4", _check(X_OLD, "Correct", **{"1": [1, 2, 3]}), 15),
]


def _seed(conn, question_id, rows, student="AliasStudent", tag="alias"):
    """Raw `events` rows for one student on `question_id`, then derive."""
    import_id = conn.execute(
        "INSERT INTO raw_imports (file_path, file_hash, file_format, row_count) "
        "VALUES (?, ?, 'datashop_tab', ?)",
        (f"{tag}.tab", f"h-{tag}-{question_id}-{len(rows)}", len(rows)),
    ).lastrowid
    for i, (eid, (selection, action, feedback), secs) in enumerate(rows):
        conn.execute(
            "INSERT INTO events (event_id, import_id, student_id, session_id, "
            "problem_name, selection, action, input, feedback_text, "
            "feedback_classification, problem_view, timestamp, course_id) "
            "VALUES (?, ?, ?, '100', ?, ?, ?, ?, '', ?, ?, ?, 'course')",
            (eid, import_id, student, question_id, selection, action, action,
             feedback, str(i + 1), _ts(secs)),
        )
    conn.commit()
    derive_events(conn)


def _catalog_row(conn, question_id):
    conn.execute(
        "INSERT OR IGNORE INTO questions (question_id, textbook_id, xml_id, "
        "metadata_json, source, question_type) "
        "VALUES (?, NULL, ?, '{}', 'catalog', 'cardsort')",
        (question_id, question_id),
    )
    conn.commit()


def _run_all(conn):
    reconstruct_all(conn)
    score_checks(conn)
    process.analyze_all(conn)
    conn.commit()


def _export(conn, name, question_id=None):
    buf = io.StringIO()
    export.export_dataset(conn, name, buf, question_id=question_id)
    rows = list(csv.DictReader(io.StringIO(buf.getvalue())))
    header = buf.getvalue().splitlines()[0].split(",")
    return header, rows


def _attempt_rows(conn, question_id):
    return conn.execute(
        "SELECT * FROM dnd_attempts WHERE question_id = ? ORDER BY attempt_id",
        (question_id,),
    ).fetchall()


@pytest.fixture
def alias_db(tmp_path, four_shapes_board):
    """A file database: the board under X, the correct trace under X_OLD, no
    alias registered yet."""
    path = tmp_path / "alias.sqlite"
    conn = db.connect(path)
    db.bootstrap(conn)
    four_shapes_board(conn)
    _seed(conn, X_OLD, CORRECT_TRACE)
    conn.close()
    return path


def _alias_file(path: Path, rows, header=None):
    header = header or list(aliases.FILE_COLUMNS)
    lines = ["# aliases for a test book", "", ",".join(header)]
    for row in rows:
        lines.append(",".join(f'"{v}"' if v else "" for v in row))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------
class TestResolver:
    def test_empty_table_is_the_identity(self, alias_db):
        conn = db.connect(alias_db)
        try:
            assert aliases.resolve_question_id(conn, X_OLD) == X_OLD
            assert not aliases.is_alias(conn, X_OLD)
            assert aliases.aliases_of(conn, X) == ()
            assert aliases.item_ids(conn, X) == (X,)
            assert aliases.alias_set_sha256(conn) is None
            assert aliases.load_alias_map(conn) == {}
            assert aliases.list_aliases(conn) == []
        finally:
            conn.close()

    def test_add_resolve_and_hash(self, alias_db):
        conn = db.connect(alias_db)
        try:
            status = aliases.add_alias(
                conn, X_OLD, X, reason="renamed in source", source_commit="abc1234"
            )
            assert status == "added"
            assert aliases.resolve_question_id(conn, X_OLD) == X
            assert aliases.resolve_question_id(conn, X) == X
            assert aliases.is_alias(conn, X_OLD) and not aliases.is_alias(conn, X)
            assert aliases.aliases_of(conn, X) == (X_OLD,)
            # The item named by the canonical id aggregates both identifiers;
            # the alias names only itself.
            assert aliases.item_ids(conn, X) == (X, X_OLD)
            assert aliases.item_ids(conn, X_OLD) == (X_OLD,)
            digest = aliases.alias_set_sha256(conn)
            assert digest is not None and len(digest) == 64
            # Idempotent: the same pair again changes nothing.
            assert aliases.add_alias(conn, X_OLD, X) == "unchanged"
            assert aliases.alias_set_sha256(conn) == digest
            [row] = aliases.list_aliases(conn)
            assert (row.alias_question_id, row.canonical_question_id) == (X_OLD, X)
            assert row.reason == "renamed in source"
            assert row.source_commit == "abc1234"
            assert row.recorded_on and len(row.recorded_on) == 10  # today, ISO
            assert row.imported_from is None and row.replaced_board_source_hash is None
        finally:
            conn.close()

    def test_hash_is_over_the_mapping_only(self, alias_db):
        conn = db.connect(alias_db)
        try:
            aliases.add_alias(conn, X_OLD, X, reason="one wording")
            a = aliases.alias_set_sha256(conn)
            aliases.remove_alias(conn, X_OLD)
            aliases.add_alias(conn, X_OLD, X, reason="another wording", source_commit="zzz")
            assert aliases.alias_set_sha256(conn) == a
        finally:
            conn.close()

    def test_load_board_resolves_the_alias(self, alias_db):
        conn = db.connect(alias_db)
        try:
            assert board.load_board(conn, X_OLD) is None
            aliases.add_alias(conn, X_OLD, X)
            spec = board.load_board(conn, X_OLD)
            assert spec is not None
            assert spec.question_id == X  # the canonical board
            assert spec.n_cards == 4 and spec.key == (1, 1, 2, 0)
        finally:
            conn.close()

    def test_remove(self, alias_db):
        conn = db.connect(alias_db)
        try:
            aliases.add_alias(conn, X_OLD, X)
            assert aliases.remove_alias(conn, X_OLD) is True
            assert aliases.remove_alias(conn, X_OLD) is False
            assert aliases.resolve_question_id(conn, X_OLD) == X_OLD
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Refusals write nothing
# ---------------------------------------------------------------------------
class TestRefusals:
    def test_self_alias(self, alias_db):
        conn = db.connect(alias_db)
        try:
            with pytest.raises(aliases.AliasError, match="cannot alias itself"):
                aliases.add_alias(conn, X_OLD, X_OLD)
            assert aliases.list_aliases(conn) == []
        finally:
            conn.close()

    def test_chain_through_an_alias_canonical(self, alias_db):
        conn = db.connect(alias_db)
        try:
            aliases.add_alias(conn, X_OLD, X)
            _catalog_row(conn, OTHER)
            with pytest.raises(aliases.AliasError, match="itself an alias"):
                aliases.add_alias(conn, OTHER, X_OLD)  # OTHER → X_OLD → X
            assert aliases.load_alias_map(conn) == {X_OLD: X}
        finally:
            conn.close()

    def test_chain_from_a_canonical(self, alias_db):
        conn = db.connect(alias_db)
        try:
            aliases.add_alias(conn, X_OLD, X)
            with pytest.raises(aliases.AliasError, match="canonical identifier of another"):
                aliases.add_alias(conn, X, OTHER)  # X_OLD → X → OTHER
            assert aliases.load_alias_map(conn) == {X_OLD: X}
        finally:
            conn.close()

    def test_alias_with_its_own_board(self, alias_db):
        conn = db.connect(alias_db)
        try:
            with pytest.raises(aliases.AliasError, match="has its own board"):
                aliases.add_alias(conn, X, OTHER)
            assert aliases.list_aliases(conn) == []
            assert board.load_board(conn, X) is not None
        finally:
            conn.close()

    def test_unknown_alias_identifier(self, alias_db):
        conn = db.connect(alias_db)
        try:
            with pytest.raises(aliases.AliasError, match="unknown here"):
                aliases.add_alias(conn, "demo-book__nowhere", X)
            assert aliases.list_aliases(conn) == []
        finally:
            conn.close()

    def test_repointing_an_alias(self, alias_db):
        conn = db.connect(alias_db)
        try:
            aliases.add_alias(conn, X_OLD, X)
            with pytest.raises(aliases.AliasError, match="already an alias of"):
                aliases.add_alias(conn, X_OLD, OTHER)
            assert aliases.load_alias_map(conn) == {X_OLD: X}
        finally:
            conn.close()

    def test_canonical_without_a_board_is_allowed_with_a_warning(self, alias_db, caplog):
        conn = db.connect(alias_db)
        try:
            _catalog_row(conn, OTHER)
            with caplog.at_level("WARNING"):
                assert aliases.add_alias(conn, X_OLD, OTHER) == "added"
            assert "has no board yet" in caplog.text
            # validate reports no_board for the alias, naming its canonical.
            r = validate.validate_question(conn, X_OLD)
            assert r.key_status == validate.NO_BOARD
            assert r.canonical_question_id == OTHER
        finally:
            conn.close()


class TestWriteLock:
    def test_validation_runs_under_the_write_lock(self, alias_db, monkeypatch):
        """BEGIN IMMEDIATE is taken before `_plan` reads the mapping, so while
        one writer validates, a second connection cannot start a write —
        two writers could otherwise validate against the same mapping and
        commit A → B and B → A (Copilot review on PR #219)."""
        import sqlite3

        seen: dict[str, bool] = {}
        original = aliases._plan

        def probing_plan(conn, *args, **kwargs):
            seen["in_transaction"] = conn.in_transaction
            other = sqlite3.connect(str(alias_db), timeout=0.0)
            try:
                with pytest.raises(sqlite3.OperationalError, match="locked"):
                    other.execute("BEGIN IMMEDIATE")
            finally:
                other.close()
            return original(conn, *args, **kwargs)

        monkeypatch.setattr(aliases, "_plan", probing_plan)
        conn = db.connect(alias_db)
        try:
            assert aliases.add_alias(conn, X_OLD, X) == "added"
            assert seen["in_transaction"] is True
            assert not conn.in_transaction  # committed, lock released
            assert aliases.resolve_question_id(conn, X_OLD) == X
        finally:
            conn.close()

    def test_a_refused_import_rolls_back_and_releases_the_lock(self, alias_db, tmp_path):
        path = _alias_file(
            tmp_path / "chain.csv",
            [(X_OLD, X, "", "", ""), (OTHER, X_OLD, "a chain", "", "")],
        )
        conn = db.connect(alias_db)
        try:
            _catalog_row(conn, OTHER)
            with pytest.raises(aliases.AliasError, match="itself an alias"):
                aliases.import_aliases(conn, path)
            assert not conn.in_transaction
            assert aliases.list_aliases(conn) == []
            assert aliases.add_alias(conn, X_OLD, X) == "added"  # the lock is free
        finally:
            conn.close()


class TestReplaceBoard:
    def test_retiring_the_alias_identifiers_own_board(self, alias_db, four_shapes_board):
        conn = db.connect(alias_db)
        try:
            four_shapes_board(conn, X_OLD)  # X_OLD gets its own (identical) board
            _run_all(conn)
            assert len(_attempt_rows(conn, X_OLD)) == 1
            with pytest.raises(aliases.AliasError, match="has its own board"):
                aliases.add_alias(conn, X_OLD, X)
            assert aliases.add_alias(conn, X_OLD, X, replace_board=True) == "added"
            # The board is gone, its results are cleared, the retirement recorded.
            assert conn.execute(
                "SELECT count(*) AS n FROM dnd_boards WHERE question_id = ?", (X_OLD,)
            ).fetchone()["n"] == 0
            assert _attempt_rows(conn, X_OLD) == []
            assert board.load_board(conn, X_OLD).question_id == X
            [row] = aliases.list_aliases(conn)
            assert row.replaced_board_source_hash == "hash"
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# The alias file
# ---------------------------------------------------------------------------
class TestImport:
    def test_import_is_idempotent_and_records_provenance(self, alias_db, tmp_path):
        path = _alias_file(
            tmp_path / "book.csv",
            [(X_OLD, X, "renamed", "abc1234", "2026-09-16")],
        )
        conn = db.connect(alias_db)
        try:
            first = aliases.import_aliases(conn, path)
            assert (first.added, first.unchanged) == (1, 0)
            assert len(first.file_sha256) == 64 and first.file_sha256 == aliases.file_sha256(path)
            assert first.set_sha256 == aliases.alias_set_sha256(conn)
            [row] = first.rows
            assert row.imported_from == "book.csv"  # a name, never a path
            assert row.imported_sha256 == first.file_sha256
            assert row.recorded_on == "2026-09-16"
            assert row.source_commit == "abc1234"
            second = aliases.import_aliases(conn, path)
            assert (second.added, second.unchanged) == (0, 1)
            assert second.set_sha256 == first.set_sha256
        finally:
            conn.close()

    def test_a_refused_row_leaves_the_table_untouched(self, alias_db, tmp_path):
        path = _alias_file(
            tmp_path / "chain.csv",
            [(X_OLD, X, "", "", ""), (OTHER, X_OLD, "a chain", "", "")],
        )
        conn = db.connect(alias_db)
        try:
            _catalog_row(conn, OTHER)
            with pytest.raises(aliases.AliasError, match="itself an alias"):
                aliases.import_aliases(conn, path)
            assert aliases.list_aliases(conn) == []
        finally:
            conn.close()

    def test_header_is_checked(self, alias_db, tmp_path):
        path = _alias_file(tmp_path / "bad.csv", [(X_OLD, X)], header=["old", "new"])
        conn = db.connect(alias_db)
        try:
            with pytest.raises(aliases.AliasError, match="header lacks"):
                aliases.import_aliases(conn, path)
        finally:
            conn.close()

    def test_the_committed_ula_file(self):
        path = Path(aliases.__file__).parent / "aliases" / "ula-proteus.csv"
        [row] = aliases.read_alias_file(path)
        assert row["alias_question_id"] == (
            "ula-proteus__ula_proteus_Section_1_2_RREF_using_EROs_v1"
        )
        assert row["canonical_question_id"] == (
            "ula-proteus__ula_proteus_1_2_RREF_using_EROs_v1"
        )
        assert row["source_commit"] == "4d317e78"
        assert "4d317e78" in row["reason"] and "assignments" in row["reason"]


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------
class TestValidate:
    def test_alias_validates_against_the_canonical_board(self, alias_db, capsys):
        conn = db.connect(alias_db)
        try:
            # Before the alias: events but no board.
            assert validate.validate_question(conn, X_OLD).key_status == validate.NO_BOARD
            aliases.add_alias(conn, X_OLD, X)
            results = {r.question_id: r for r in validate.validate_all(conn)}
            old = results[X_OLD]
            assert old.key_status == validate.KEY_MATCH and old.ok
            assert old.canonical_question_id == X
            assert old.source_key == (1, 1, 2, 0)
            assert old.inferred_keys == ((1, 1, 2, 0),)  # P from the board: 4 entries
            assert old.label_gaps == ()
            # The canonical identifier itself has no events here.
            assert results[X].key_status == validate.KEY_UNVALIDATED
            assert results[X].canonical_question_id is None
        finally:
            conn.close()
        assert cli.main(["--db", str(alias_db), "dnd", "validate"]) == 0
        out = capsys.readouterr().out
        assert f"{X_OLD}: key=match (alias of {X})" in out
        assert "0 failing" in out

    def test_a_wrong_alias_fails_loudly(self, tmp_path, four_shapes_board, capsys):
        path = tmp_path / "wrong.sqlite"
        conn = db.connect(path)
        try:
            db.bootstrap(conn)
            four_shapes_board(conn)
            _seed(conn, X_OLD, WRONG_KEY_TRACE)
            aliases.add_alias(conn, X_OLD, X)
            r = validate.validate_question(conn, X_OLD)
            assert r.key_status == validate.KEY_MISMATCH and not r.ok
            assert r.canonical_question_id == X
            assert r.inferred_keys == ((1, 1, 1, 0),)
        finally:
            conn.close()
        assert cli.main(["--db", str(path), "dnd", "validate"]) == 1
        assert f"key=mismatch (alias of {X})" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# reconstruct → score → analyze → export, end to end
# ---------------------------------------------------------------------------
class TestStages:
    def test_without_the_alias_the_old_identifier_is_excluded(self, alias_db):
        conn = db.connect(alias_db)
        try:
            _run_all(conn)
            [a] = _attempt_rows(conn, X_OLD)
            assert "missing_source_map" in json.loads(a["qc_flags_json"])
            assert a["readiness"] == "exclude"
            assert a["canonical_question_id"] is None
            # P inferred from the labels alone: three cards.
            s0 = conn.execute(
                "SELECT s_json FROM dnd_configurations WHERE attempt_id = ? AND seq = 0",
                (a["attempt_id"],),
            ).fetchone()["s_json"]
            assert json.loads(s0) == [0, 0, 0]
            assert conn.execute(
                "SELECT count(*) AS n FROM dnd_check_scores"
            ).fetchone()["n"] == 0
        finally:
            conn.close()

    def test_with_the_alias_the_trace_binds_to_the_board(self, alias_db):
        conn = db.connect(alias_db)
        try:
            aliases.add_alias(conn, X_OLD, X, reason="test")
            _run_all(conn)
            [a] = _attempt_rows(conn, X_OLD)
            flags = json.loads(a["qc_flags_json"])
            assert "missing_source_map" not in flags
            assert flags == ["replay_ok"] and a["readiness"] == "ready"
            assert a["canonical_question_id"] == X  # the provenance marker
            # P from the board: four cards, the circle in the tray throughout.
            s0 = conn.execute(
                "SELECT s_json FROM dnd_configurations WHERE attempt_id = ? AND seq = 0",
                (a["attempt_id"],),
            ).fetchone()["s_json"]
            assert json.loads(s0) == [0, 0, 0, 0]
            # Scored against the key; the row keeps the logged identifier.
            [score] = conn.execute("SELECT * FROM dnd_check_scores").fetchall()
            assert score["question_id"] == X_OLD and score["distance"] == 0
            assert score["attempt_id"] == a["attempt_id"]
            # Analyzed with the key: outcomes and the convergence curve exist.
            m = conn.execute(
                "SELECT * FROM dnd_attempt_metrics WHERE attempt_id = ?", (a["attempt_id"],)
            ).fetchone()
            assert m["first_check_correct"] == 1 and m["first_check_distance"] == 0
            assert m["convergence_json"] is not None
            assert m["n_effective_moves_pre"] == 3
        finally:
            conn.close()

    def test_run_configurations_carry_the_alias_set_hash(self, alias_db):
        conn = db.connect(alias_db)
        try:
            _run_all(conn)  # no alias: None recorded
            aliases.add_alias(conn, X_OLD, X)
            _run_all(conn)
            digest = aliases.alias_set_sha256(conn)
            rows = conn.execute(
                "SELECT stage, config_json FROM pipeline_runs WHERE service = 'dnd' "
                "AND stage IN ('reconstruct', 'score', 'analyze') ORDER BY run_id"
            ).fetchall()
            recorded = [(r["stage"], json.loads(r["config_json"])["alias_set_sha256"]) for r in rows]
            assert recorded == [
                ("reconstruct", None), ("score", None), ("analyze", None),
                ("reconstruct", digest), ("score", digest), ("analyze", digest),
            ]
        finally:
            conn.close()

    def test_scoring_the_canonical_question_scores_its_aliases(self, alias_db):
        conn = db.connect(alias_db)
        try:
            aliases.add_alias(conn, X_OLD, X)
            reconstruct_all(conn)
            result = score_checks(conn, question_id=X)  # the item, not the one id
            assert result.scored == 1
            assert conn.execute(
                "SELECT question_id FROM dnd_check_scores"
            ).fetchone()["question_id"] == X_OLD
            # Scoring the alias alone covers that identifier's own checks.
            conn.execute("DELETE FROM dnd_check_scores")
            assert score_checks(conn, question_id=X_OLD).scored == 1
        finally:
            conn.close()

    def test_exports_carry_the_canonical_identifier(self, alias_db):
        conn = db.connect(alias_db)
        try:
            aliases.add_alias(conn, X_OLD, X)
            _run_all(conn)
            # dnd/0015 appended its own columns after canonical_question_id
            # (seeded on the check-shaped datasets; resumed / prior_attempt_id
            # on the attempt-shaped ones), in the same append-only posture.
            tail = {
                "attempt_metrics": ["resumed", "prior_attempt_id", "import_sha256"],
                "segments": [],
                "configurations": ["seeded", "import_sha256"],
                "checks": ["seeded", "import_sha256"],
                "check_scores": [],
            }
            for name, after in tail.items():
                header, rows = _export(conn, name)
                assert header[-1 - len(after):] == ["canonical_question_id", *after], name
                assert rows and all(r["question_id"] == X_OLD for r in rows), name
                assert all(r["canonical_question_id"] == X for r in rows), name
            header, [row] = _export(conn, "modeling_table")
            assert header[-4:] == ["canonical_question_id", "resumed", "prior_attempt_id", "import_sha256"]
            assert (row["question_id"], row["canonical_question_id"]) == (X_OLD, X)
            # The board columns are joined through the canonical identifier.
            assert row["source_hash"] == "hash" and row["n_cards"] == "4"
            assert row["num_slots"] == "3" and row["readiness"] == "ready"
            header, [b] = _export(conn, "boards")
            assert header[-1] == "aliases"
            assert (b["question_id"], b["aliases"]) == (X, X_OLD)
            header, [item] = _export(conn, "item_summary")
            assert header[-1] == "aliases"
            assert (item["question_id"], item["aliases"]) == (X, X_OLD)
            # One row per board, aggregating the alias's attempt and check.
            assert item["n_attempts"] == "1" and item["n_replay_ok"] == "1"
            assert item["first_check_correct_rate"] == "1.0"
            assert item["mean_omissions"] == "0.0"
        finally:
            conn.close()

    def test_item_level_readers_see_the_whole_item(self, alias_db):
        conn = db.connect(alias_db)
        try:
            aliases.add_alias(conn, X_OLD, X)
            _run_all(conn)
            # contrast: the population's items are canonical identifiers.
            [row] = contrast.read_population(conn)
            assert row.question_id == X and row.group == contrast.GROUP_CORRECT
            # cohort / trajectory: the canonical id covers the alias's rows,
            # and the alias id reads against the canonical board.
            data = cohort.cohort_data(conn, X)
            assert data.question_id == X
            assert cohort.cohort_data(conn, X_OLD).question_id == X_OLD
            assert trajectory.trajectory_data(conn, X) is not None
            assert trajectory.trajectory_data(conn, X_OLD) is not None
            # A filter that selects nothing on the canonical identifier: the
            # item still has walks (under the alias), so the bundle is empty,
            # not unavailable (None) — the availability probe sees the aliases.
            empty = trajectory.trajectory_data(
                conn, X, filter_spec=trajectory.FilterSpec(course_id="no-such-course")
            )
            assert empty is not None
            assert trajectory.trajectory_data(conn, OTHER) is None  # no board at all
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Invariance: the canonical identifier's own attempts never move
# ---------------------------------------------------------------------------
class TestInvariance:
    def test_aliasing_leaves_the_canonical_attempts_byte_identical(
        self, tmp_path, golden_dnd_tab, four_shapes_board
    ):
        def build(name, with_alias):
            path = tmp_path / name
            conn = db.connect(path)
            db.bootstrap(conn)
            core_ingest.ingest_file(conn, golden_dnd_tab)
            derive_events(conn)
            four_shapes_board(conn)
            if with_alias:
                _seed(conn, X_OLD, CORRECT_TRACE)
                aliases.add_alias(conn, X_OLD, X)
            _run_all(conn)
            out = {}
            for dataset in ("attempt_metrics", "modeling_table", "checks", "check_scores",
                            "configurations", "segments"):
                _, rows = _export(conn, dataset)
                out[dataset] = [r for r in rows if r["question_id"] == X]
            conn.close()
            return out

        plain = build("plain.sqlite", with_alias=False)
        aliased = build("aliased.sqlite", with_alias=True)
        for dataset, rows in plain.items():
            assert aliased[dataset] == rows, dataset

    def test_empty_alias_table_changes_no_export_value(self, tmp_path, golden_dnd_tab, four_shapes_board):
        path = tmp_path / "plain.sqlite"
        conn = db.connect(path)
        try:
            db.bootstrap(conn)
            core_ingest.ingest_file(conn, golden_dnd_tab)
            derive_events(conn)
            four_shapes_board(conn)
            _run_all(conn)
            for name in ("attempt_metrics", "modeling_table", "checks", "check_scores",
                         "configurations", "segments"):
                _, rows = _export(conn, name)
                assert rows and all(
                    r["canonical_question_id"] == r["question_id"] for r in rows
                ), name
            for name in ("boards", "item_summary"):
                _, rows = _export(conn, name)
                assert [r["aliases"] for r in rows] == [""], name
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# sync-boards: never under an alias; a board change clears the aliases too
# ---------------------------------------------------------------------------
class TestSyncBoards:
    def test_no_board_is_built_under_an_alias(self, alias_db):
        from conftest import FOUR_SHAPES_XML

        conn = db.connect(alias_db)
        try:
            aliases.add_alias(conn, X_OLD, X)
            row = {"question_id": X_OLD, "source_xml": FOUR_SHAPES_XML,
                   "question_type": "cardsort"}
            assert board.sync_board_for_question(conn, row) == "alias_refused"
            assert conn.execute(
                "SELECT count(*) AS n FROM dnd_boards WHERE question_id = ?", (X_OLD,)
            ).fetchone()["n"] == 0
        finally:
            conn.close()

    def test_a_board_change_clears_the_alias_results_too(self, alias_db):
        from conftest import FOUR_SHAPES_XML

        conn = db.connect(alias_db)
        try:
            aliases.add_alias(conn, X_OLD, X)
            _run_all(conn)
            assert len(_attempt_rows(conn, X_OLD)) == 1
            revised = FOUR_SHAPES_XML.replace("square", "rectangle")
            row = {"question_id": X, "source_xml": revised, "question_type": "cardsort"}
            assert board.sync_board_for_question(conn, row) == "updated_results_cleared"
            assert _attempt_rows(conn, X_OLD) == []  # F29 reaches the alias
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# The harness guard and the manifest
# ---------------------------------------------------------------------------
class TestProvenance:
    def test_harness_refuses_a_primary_whose_aliases_changed_after_the_run(
        self, tmp_path, golden_dnd_tab, four_shapes_board
    ):
        primary = tmp_path / "primary.sqlite"
        conn = db.connect(primary)
        db.bootstrap(conn)
        core_ingest.ingest_file(conn, golden_dnd_tab)
        derive_events(conn)
        four_shapes_board(conn)
        _run_all(conn)
        _catalog_row(conn, "demo-book__retired")
        aliases.add_alias(conn, "demo-book__retired", X)  # after the run
        conn.close()
        with pytest.raises(sensitivity.SensitivityError, match="alias_set_sha256 differs"):
            sensitivity.run_configuration(primary, tmp_path / "out", sensitivity.PRIMARY)

    def test_harness_accepts_a_primary_run_under_its_aliases(self, alias_db, tmp_path):
        conn = db.connect(alias_db)
        aliases.add_alias(conn, X_OLD, X)
        _run_all(conn)
        digest = aliases.alias_set_sha256(conn)
        conn.close()
        summary = sensitivity.run_configuration(alias_db, tmp_path / "out", sensitivity.PRIMARY)
        assert summary.n_attempts == 1
        config = json.loads((tmp_path / "out" / "primary" / "config.json").read_text())
        for stage in ("reconstruct", "score", "analyze"):
            run = next(r for r in config["pipeline_runs"] if r["stage"] == stage)
            assert run["config"]["alias_set_sha256"] == digest

    def test_manifest_records_the_alias_set(self, alias_db, tmp_path):
        path = _alias_file(tmp_path / "book.csv", [(X_OLD, X, "renamed", "abc1234", "2026-09-16")])
        conn = db.connect(alias_db)
        try:
            aliases.import_aliases(conn, path)
            section = manifest.build_manifest(conn)["body"]["services"]["dnd"]["question_aliases"]
            assert section["count"] == 1
            assert section["set_sha256"] == aliases.alias_set_sha256(conn)
            [row] = section["rows"]
            assert row["alias_question_id"] == X_OLD
            assert row["canonical_question_id"] == X
            assert row["imported_from"] == "book.csv"
            assert str(tmp_path) not in json.dumps(section)  # never a path
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
class TestCli:
    def test_add_list_remove(self, alias_db, capsys):
        base = ["--db", str(alias_db), "dnd", "alias"]
        assert cli.main([*base, "add", X_OLD, X, "--reason", "renamed",
                         "--source-commit", "abc1234"]) == 0
        out = capsys.readouterr().out
        assert f"Alias added: {X_OLD} -> {X}" in out and "alias set sha256:" in out
        assert cli.main([*base, "add", X_OLD, X]) == 0
        assert "Alias unchanged" in capsys.readouterr().out
        assert cli.main([*base, "list"]) == 0
        out = capsys.readouterr().out
        assert "Question-identifier aliases: 1" in out
        assert f"{X_OLD} -> {X}" in out and "commit abc1234" in out
        assert cli.main([*base, "add", X, X]) == 1
        assert "cannot alias itself" in capsys.readouterr().err
        assert cli.main([*base, "remove", X_OLD]) == 0
        assert "Alias removed" in capsys.readouterr().out
        assert cli.main([*base, "remove", X_OLD]) == 1
        assert "No alias" in capsys.readouterr().err
        assert cli.main(base) == 2

    def test_import(self, alias_db, tmp_path, capsys):
        path = _alias_file(tmp_path / "book.csv", [(X_OLD, X, "renamed", "abc1234", "")])
        base = ["--db", str(alias_db), "dnd", "alias"]
        assert cli.main([*base, "import", str(path)]) == 0
        out = capsys.readouterr().out
        assert "added:     1" in out and "unchanged: 0" in out
        assert "from book.csv" in out and str(tmp_path) not in out
        assert cli.main([*base, "import", str(path)]) == 0
        assert "unchanged: 1" in capsys.readouterr().out
        assert cli.main([*base, "import", str(tmp_path / "missing.csv")]) == 1
