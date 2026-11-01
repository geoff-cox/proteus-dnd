"""
Tests for DnD CSV export (plan item B3.7).

The four datasets (checks, check_scores, attempt_metrics, boards) are
exercised after a full backend run on the golden fixture, and the descriptor
wiring is checked so the shared export plumbing can discover them.

``TestImportSha256`` pins the additive offering key (E-08 / E-02, ruling
R9 (a), 2026-10-18): ``import_sha256`` closes the checks, attempt_metrics,
configurations and modeling_table rows and equals the import file's sha256
(``raw_imports.file_hash``, the manifest's ``body.imports[].sha256``); the
other four datasets do not carry it. The attempt-shaped datasets read the key
off the attempt's window (``export._ATTEMPT_IMPORT_JOIN``), not off its
configurations, so an attempt whose every move was skipped in replay still
carries it; the window is qualified by the attempt's session, so two attempts
whose events tie on a timestamp across a session change keep their own imports
(PR #236 Copilot reviews, 2026-10-19).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math

import pytest

from proteus.core import ingest as core_ingest
from proteus.core import manifest
from proteus.core.registry import available_services
from proteus.services.dragndrop import export
from proteus.services.dragndrop.derive import derive_events
from proteus.services.dragndrop.process import analyze_all
from proteus.services.dragndrop.reconstruct import reconstruct_all
from proteus.services.dragndrop.scoring import score_checks

Q1 = "demo-book__demo-shapes-q1"


def _read_csv(text: str) -> tuple[list[str], list[dict]]:
    reader = csv.reader(io.StringIO(text))
    header = next(reader)
    rows = [dict(zip(header, r)) for r in reader]
    return header, rows


def _export(conn, dataset, **kw) -> tuple[list[str], list[dict]]:
    buf = io.StringIO()
    export.export_dataset(conn, dataset, buf, **kw)
    return _read_csv(buf.getvalue())


@pytest.fixture
def pipeline(db_conn, golden_dnd_tab, four_shapes_board):
    core_ingest.ingest_file(db_conn, golden_dnd_tab)
    derive_events(db_conn)
    four_shapes_board(db_conn)
    reconstruct_all(db_conn)
    score_checks(db_conn)
    analyze_all(db_conn)
    return db_conn


class TestDatasets:
    def test_checks(self, pipeline):
        header, rows = _export(pipeline, "checks")
        assert header == list(export.DATASETS["checks"].columns)
        assert len(rows) == 4  # two students × two checks
        # DemoStudent1's two checks are eligible anchors that agree;
        # DemoStudent2's truncated and orphan-label checks are stored not
        # comparable with their cause (dnd/0012), never compared.
        assert {r["matches_input"] for r in rows} == {"1", ""}
        s1 = [r for r in rows if r["student_id"] == "DemoStudent1"]
        s2 = [r for r in rows if r["student_id"] == "DemoStudent2"]
        assert [(r["eligible"], r["anchor_cause"]) for r in s1] == [
            ("1", ""), ("1", ""),
        ]
        assert [(r["matches_input"], r["eligible"], r["anchor_cause"]) for r in s2] == [
            ("", "0", "malformed"), ("", "0", "unknown_card"),
        ]
        # FP-3: the golden pipeline reconstructs under the default legacy era,
        # so every reconstructed check discloses at the highlight layer (legacy
        # grades every check regardless of placement).
        assert {r["disclosure_layer"] for r in rows} == {"highlight"}

    def test_check_scores(self, pipeline):
        header, rows = _export(pipeline, "check_scores")
        assert header == list(export.DATASETS["check_scores"].columns)
        assert len(rows) == 3  # the parse_ok checks
        correct = [r for r in rows if r["feedback"] == "Correct"]
        assert len(correct) == 1
        assert correct[0]["sigma"] == "4"
        assert correct[0]["all_or_nothing"] == "1"
        # F12: the flat error-profile components ride the dataset. The key
        # has no errors; the orphan check's empty board (σ=1) is pure
        # omissions (d = 3 = 3 + 0 + 0).
        assert correct[0]["omissions"] == "0"
        assert correct[0]["wrong_slot"] == "0"
        assert correct[0]["per_card_score"] == "1.0"
        empty = next(r for r in rows if r["sigma"] == "1")
        assert empty["omissions"] == "3"
        assert empty["wrong_slot"] == "0"
        assert empty["distance"] == "3"

    def test_attempt_metrics(self, pipeline):
        header, rows = _export(pipeline, "attempt_metrics")
        assert header == list(export.DATASETS["attempt_metrics"].columns)
        assert len(rows) == 2
        s1 = next(r for r in rows if r["student_id"] == "DemoStudent1")
        # F20: the crossing/nesting identifiers ride every attempt-shaped
        # dataset via the representative-event rollup.
        assert s1["course_id"] == "university"
        assert s1["class_id"] == "1"
        assert s1["week_no"] == "8"
        assert s1["n_moves"] == "5"
        assert s1["n_noop_moves"] == "0"
        assert s1["convergence_json"] == "[3, 2, 1, 0, 1, 0, 3]"
        # F6: the move mix rides the same dataset.
        assert s1["n_fixing"] == "4"
        assert s1["n_lateral"] == "0"
        assert s1["n_breaking"] == "1"
        assert s1["excess_moves"] == "2"
        assert s1["path_excess"] == "2"
        # F7: the check-structure features ride it too.
        assert s1["cadence_json"] == "[4, 1]"
        assert s1["post_check_gains_json"] == "[1]"
        assert s1["responsiveness_json"] == "[1.0]"
        # F9: the time features, under the recorded idle threshold.
        assert s1["total_time_s"] == "85.0"
        assert s1["time_to_first_move_s"] == "0.0"
        assert s1["median_move_gap_s"] == "13.0"
        assert s1["max_action_gap_s"] == "30.0"
        assert s1["active_time_s"] == "85.0"
        assert s1["idle_threshold_s"] == "300.0"
        # F10: the attempt-level outcomes.
        assert s1["first_check_distance"] == "1"
        assert s1["final_check_distance"] == "0"
        assert s1["first_check_correct"] == "0"
        assert s1["eventual_correct"] == "1"
        assert s1["correct_check_ordinal"] == "2"
        # F8: the repeat counts over the 7-state walk (takeback and reset
        # each revisit a seen state).
        assert s1["n_distinct_states"] == "5"
        assert s1["n_repeated_states"] == "2"
        # F-NEW: the phase-window block closes the row (every pre-existing
        # column keeps its position). Pre: the four placements, 35 s to the
        # first check; repair: the takeback (a fix) and the correct check,
        # 20 s (test_dnd_process.TestPhaseWindowFeatures has the hand
        # computation). import_sha256 (ruling R9 (a)) now closes the row,
        # so every tail index moves one further from the end.
        assert header[-15:-4] == [
            "n_effective_moves_pre", "n_excess_moves_pre", "n_takebacks_pre",
            "n_resets_pre", "active_time_pre_s", "n_effective_moves_repair",
            "n_takebacks_repair", "n_resets_repair", "n_checks_repair",
            "active_time_repair_s", "n_fix_moves_repair",
        ]
        # dnd/0014: the item identifier after the alias follows; with no
        # alias registered it equals the logged identifier. dnd/0015: the
        # resumption columns follow. Each student has one attempt, so
        # neither is resumed (and the offering key is last).
        assert header[-4:-1] == ["canonical_question_id", "resumed", "prior_attempt_id"]
        assert header[-1] == "import_sha256"
        assert s1["canonical_question_id"] == s1["question_id"]
        assert (s1["resumed"], s1["prior_attempt_id"]) == ("0", "")
        assert [s1[c] for c in header[-15:-4]] == [
            "4", "0", "0", "0", "35.0", "1", "1", "0", "1", "20.0", "1",
        ]
        # The pathological attempt: pre is cut at its one scored check (the
        # unscored truncated check row precedes it, 10 s earlier); never
        # correct, so the repair window is undefined (NULL → empty cell).
        s2 = next(r for r in rows if r["student_id"] == "DemoStudent2")
        assert [s2[c] for c in header[-15:-10]] == ["0", "0", "0", "0", "10.0"]
        assert [s2[c] for c in header[-10:-4]] == [""] * 6
        assert (s2["resumed"], s2["prior_attempt_id"]) == ("0", "")

    def test_attempt_metrics_carries_qc_flags_and_readiness(self, pipeline):
        # F17b: the export carries the per-attempt QC rollup. Student 1 is a
        # clean, fully-verified replay; student 2 is the pathological trace
        # (unparseable move, orphan-label check, off-board move).
        _, rows = _export(pipeline, "attempt_metrics")
        s1 = next(r for r in rows if r["student_id"] == "DemoStudent1")
        s2 = next(r for r in rows if r["student_id"] == "DemoStudent2")
        assert s1["qc_flags_json"] == '["replay_ok"]'
        assert s1["readiness"] == "ready"
        assert s1["feedback_policy"] == "legacy"  # FP-3 default era
        s2_flags = set(json.loads(s2["qc_flags_json"]))
        assert {"parse_error", "invalid_state"} <= s2_flags
        assert s2["readiness"] == "exclude"

    def test_segments(self, pipeline):
        header, rows = _export(pipeline, "segments")
        assert header == list(export.DATASETS["segments"].columns)
        s1 = [r for r in rows if r["student_id"] == "DemoStudent1"]
        by_type = {}
        for r in s1:
            by_type.setdefault(r["segment_type"], []).append(
                (r["start_seq"], r["end_seq"])
            )
        # The golden trace: checks at seq 5 and 7, reset at seq 8 (F3).
        assert by_type["check"] == [("0", "5"), ("6", "7"), ("8", "8")]
        assert by_type["reset"] == [("0", "8")]
        assert by_type["session"] == [("0", "8")]

    def test_configurations(self, pipeline):
        # The persisted walk behind the F24 trajectory charts (PR #151
        # review): DemoStudent1's X0 + four placements + two checks + the
        # takeback + the reset (9), plus DemoStudent2's X0 and two checks
        # that both graded the untouched board (12 rows in all).
        header, rows = _export(pipeline, "configurations")
        assert header == list(export.DATASETS["configurations"].columns)
        assert len(rows) == 12
        x0_rows = [r for r in rows if r["seq"] == "0"]
        assert len(x0_rows) == 2
        assert all(r["s_json"] == "[0, 0, 0, 0]" for r in x0_rows)
        assert all(r["kind"] == "" for r in x0_rows)  # X0 has no event
        s1 = [r for r in rows if r["student_id"] == "DemoStudent1"]
        assert [r["kind"] for r in s1] == [
            "", "move", "move", "move", "move", "check", "takeback",
            "check", "reset",
        ]
        assert sum(r["is_check"] == "1" for r in s1) == 2
        s2 = [r for r in rows if r["student_id"] == "DemoStudent2"]
        assert [r["kind"] for r in s2] == ["", "check", "check"]

    def test_boards(self, pipeline):
        header, rows = _export(pipeline, "boards")
        assert header == list(export.DATASETS["boards"].columns)
        assert len(rows) == 1
        assert rows[0]["n_cards"] == "4"
        assert rows[0]["key_json"] == "[1, 1, 2, 0]"
        # F18: the item-design covariates (tab-item-design-covariates),
        # derived from the stored board: R = 3 slots, (R+1)^P = 256 states,
        # the circle decoy card, the empty Pentagon slot, o* = (1, 2, 1, 0).
        assert rows[0]["num_slots"] == "3"
        assert rows[0]["state_space_size"] == "256"
        assert rows[0]["num_decoy_cards"] == "1"
        assert rows[0]["num_decoy_slots"] == "1"
        assert rows[0]["key_occupancy"] == "[1, 2, 1, 0]"
        # The era covariates carry the rule-11 conservative defaults until a
        # per-era record exists (open-todos item 21).
        assert rows[0]["feedback_policy"] == "legacy"
        assert rows[0]["check_cost"] == "free"

    def test_modeling_table(self, pipeline):
        # F20 (subsec-modeling-table): one row per attempt joining
        # identifiers, the unit marker, outcomes, design covariates,
        # features, and QC (from the analysis-ready tables only).
        header, rows = _export(pipeline, "modeling_table")
        assert header == list(export.DATASETS["modeling_table"].columns)
        assert len(rows) == 2
        s1 = next(r for r in rows if r["student_id"] == "DemoStudent1")
        assert s1["unit"] == "attempt"
        assert (s1["course_id"], s1["class_id"], s1["week_no"]) == (
            "university", "1", "8"
        )
        assert s1["source_hash"] == "hash"  # the board's source version
        # feedback_policy is the per-attempt FP-3 record; check_cost the
        # configured era default (open-todos item 21).
        assert s1["feedback_policy"] == "legacy"
        assert s1["check_cost"] == "free"
        # Design covariates (F18) beside outcomes (F10) and features.
        assert s1["num_slots"] == "3"
        assert s1["state_space_size"] == "256"
        assert s1["key_occupancy"] == "[1, 2, 1, 0]"
        assert s1["eventual_correct"] == "1"
        assert s1["n_fixing"] == "4"
        assert s1["n_distinct_states"] == "5"
        assert s1["readiness"] == "ready"
        # The pathological attempt is retained with its exclusion signal.
        # Flags stay even when an analysis would drop the row.
        s2 = next(r for r in rows if r["student_id"] == "DemoStudent2")
        assert s2["readiness"] == "exclude"
        assert s2["eventual_correct"] == "0"
        assert s2["correct_check_ordinal"] == ""
        # F-NEW: the phase-window block rides the modeling table too, at the
        # end of the row, with the same values as attempt_metrics; the
        # offering key import_sha256 (ruling R9 (a)) closes both rows.
        assert header[-15:-4] == list(
            export.DATASETS["attempt_metrics"].columns[-15:-4]
        )
        assert header[-4:-1] == ["canonical_question_id", "resumed", "prior_attempt_id"]
        assert header[-1] == "import_sha256"
        assert s1["canonical_question_id"] == "demo-book__demo-shapes-q1"
        assert (s1["resumed"], s1["prior_attempt_id"]) == ("0", "")
        assert s1["n_effective_moves_pre"] == "4"
        assert s1["n_excess_moves_pre"] == "0"
        assert s1["active_time_pre_s"] == "35.0"
        assert s1["n_fix_moves_repair"] == "1"
        assert s2["n_effective_moves_repair"] == ""

    def test_item_summary(self, pipeline):
        # F20 (tab-item-diagnostic-summary-fields): one row per question.
        header, rows = _export(pipeline, "item_summary")
        assert header == list(export.DATASETS["item_summary"].columns)
        assert len(rows) == 1
        item = rows[0]
        # Design + era covariates.
        assert item["num_slots"] == "3"
        assert item["num_decoy_cards"] == "1"
        assert item["feedback_policy"] == "legacy"
        # Outcome rates over the two analyzed attempts (S1 solves, S2 not).
        assert item["first_check_correct_rate"] == "0.0"
        assert item["eventual_correct_rate"] == "0.5"
        assert item["mean_first_check_distance"] == "2.0"
        assert item["mean_final_check_distance"] == "1.5"
        # Process means/medians: moves (5, 0), resets (1, 0),
        # active time (85, 10), time to first check (35, 0).
        assert item["mean_moves"] == "2.5"
        assert item["reset_rate"] == "0.5"
        assert item["median_active_time_s"] == "47.5"
        assert item["median_time_to_first_check_s"] == "17.5"
        # F12 error means over the three scored checks (the journal's
        # decoy-leak names): omissions (0+0+3)/3; card leaks (1+0+0)/3.
        assert item["mean_omissions"] == "1.0"
        assert float(item["mean_decoy_card_leaks"]) == pytest.approx(1 / 3)
        # F11 dispersion over the two final scored states (key and X0):
        # three cards split 50/50 (ln 2) and the decoy card is pure
        # consensus (0), so the mean is 3·ln2/4.
        assert float(item["mean_card_entropy"]) == pytest.approx(
            3 * math.log(2) / 4
        )
        assert float(item["max_card_entropy"]) == pytest.approx(math.log(2))
        assert item["n_common_wrong_states"] == "0"
        # Item 22 (2026-08-15): S2's final scored state is the empty board,
        # abandonment, counted separately, never a "common wrong state".
        assert item["n_abandoned_final"] == "1"
        # F17b QC counts.
        assert item["n_attempts"] == "2"
        assert item["n_replay_ok"] == "1"
        assert item["n_parse_error"] == "1"
        assert item["n_long_idle"] == "0"

    def test_empty_final_never_counts_as_common_wrong(self, pipeline, monkeypatch):
        # Item 22 (2026-08-15): even when the empty board clears the shared
        # threshold, it is abandonment, not a shared misconception. With the
        # threshold forced to 1, S2's empty final would have counted before
        # the exclusion; it must land in n_abandoned_final instead.
        monkeypatch.setattr(export, "DEFAULT_COMMON_STATE_MIN", 1)
        _, rows = _export(pipeline, "item_summary")
        item = rows[0]
        assert item["n_common_wrong_states"] == "0"
        assert item["n_abandoned_final"] == "1"

    def test_corrupt_board_row_exports_blank_covariates(self, pipeline):
        # PR #155 review: a dnd_boards row whose key no longer fits its
        # stored shape (or won't parse) must export with blank covariates,
        # never crash the whole export.
        pipeline.execute(
            "INSERT INTO questions (question_id, textbook_id, xml_id, "
            "metadata_json, source) VALUES ('corrupt-q', 1, 'corrupt', '{}', "
            "'auto_extracted')"
        )
        pipeline.execute(
            "INSERT INTO dnd_boards (question_id, source_hash, n_cards, "
            "n_zones, key_json) VALUES ('corrupt-q', 'h', 4, 3, '[9, 0, 0, 0]')"
        )
        _, rows = _export(pipeline, "boards")
        corrupt = next(r for r in rows if r["question_id"] == "corrupt-q")
        assert corrupt["num_slots"] == ""
        assert corrupt["key_occupancy"] == ""
        # The era covariates are board-independent configuration.
        assert corrupt["feedback_policy"] == "legacy"

    def test_question_filter(self, pipeline):
        _, rows = _export(pipeline, "check_scores", question_id="no-such-question")
        assert rows == []

    def test_unknown_dataset_raises(self, pipeline):
        with pytest.raises(ValueError, match="unknown dataset"):
            _export(pipeline, "nope")


# The four datasets that carry the offering key, with the column each one
# had last before it (ruling R9 (a), 2026-10-18: additive, so the column
# before the key is the pre-existing last column, unchanged).
KEYED = {
    "checks": "seeded",
    "attempt_metrics": "prior_attempt_id",
    "configurations": "seeded",
    "modeling_table": "prior_attempt_id",
}
UNKEYED = ("segments", "check_scores", "boards", "item_summary")


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestImportSha256:
    """The additive offering key (E-08 / E-02, ruling R9 (a), 2026-10-18).

    ``import_sha256`` is ``raw_imports.file_hash``. The plain sha256 of the
    import file's bytes (``core.ingest.imports.file_hash``), the value the
    manifest's ``body.imports[]`` carries beside the offering pseudonym, so
    an archive-only reader can map a row to its offering without the private
    map. The pseudonym itself is never exported.
    """

    def test_key_is_the_fixture_sha256_on_every_row(self, pipeline, golden_dnd_tab):
        # (a) one import: every row of the four datasets carries the hash of
        # the file it came from, computed here from the fixture bytes, equal
        # to raw_imports.file_hash and to the manifest's imports[0].sha256.
        expected = _sha256(golden_dnd_tab)
        assert len(expected) == 64
        [(stored,)] = pipeline.execute("SELECT file_hash FROM raw_imports").fetchall()
        assert stored == expected
        body = manifest.build_manifest(pipeline)["body"]
        assert [i["sha256"] for i in body["imports"]] == [expected]
        for name in KEYED:
            header, rows = _export(pipeline, name)
            assert header == list(export.DATASETS[name].columns), name
            assert header[-1] == "import_sha256", name
            assert rows, name
            assert {r["import_sha256"] for r in rows} == {expected}, name

    def test_two_imports_each_row_carries_its_own_file(
        self, db_conn, golden_dnd_tab, fixtures_dir, four_shapes_board
    ):
        # (b) two imports in one database: two distinct keys; each row carries
        # the hash of the file its student appears in (the fixtures name
        # disjoint students, so the attribution is read from the file bytes,
        # not from the join under test); the configurations of one attempt
        # share one value.
        training = fixtures_dir / "golden_dnd_training_anomalies.tab"
        core_ingest.ingest_file(db_conn, golden_dnd_tab)
        core_ingest.ingest_file(db_conn, training)
        derive_events(db_conn)
        four_shapes_board(db_conn)
        reconstruct_all(db_conn)
        score_checks(db_conn)
        analyze_all(db_conn)
        hashes = {golden_dnd_tab: _sha256(golden_dnd_tab), training: _sha256(training)}
        assert len(set(hashes.values())) == 2
        assert sorted(
            r[0] for r in db_conn.execute("SELECT file_hash FROM raw_imports")
        ) == sorted(hashes.values())

        def owner(student_id: str) -> str:
            [path] = [
                p for p in hashes if student_id.encode("utf-8") in p.read_bytes()
            ]
            return hashes[path]

        for name in KEYED:
            _, rows = _export(db_conn, name)
            assert len({r["import_sha256"] for r in rows}) == 2, name
            for r in rows:
                assert r["import_sha256"] == owner(r["student_id"]), (
                    name, r["student_id"],
                )
        _, configs = _export(db_conn, "configurations")
        per_attempt: dict[str, set[str]] = {}
        for r in configs:
            per_attempt.setdefault(r["attempt_id"], set()).add(r["import_sha256"])
        assert len(per_attempt) == 4  # two attempts per fixture
        assert all(len(v) == 1 for v in per_attempt.values()), per_attempt

    def test_a_skipped_only_attempt_carries_its_import(self, db_conn, four_shapes_board):
        # (e) PR #236 Copilot review (2026-10-19): an attempt whose only move
        # is off-board with a board synced is skipped in replay
        # (test_dnd_reconstruct.TestOffBoardSlot), so it has no configuration
        # with an event (X0 is written with event_id NULL and a skipped move
        # writes nothing). A rollup over the configurations left its key
        # empty and made the archive-only generator refuse the whole archive;
        # the key is read off the attempt window (student, logged identifier,
        # started_at..ended_at) instead.
        file_hash = "ab" * 32
        import_id = db_conn.execute(
            "INSERT INTO raw_imports (file_path, file_hash, file_format, row_count) "
            "VALUES ('offboard.tab', ?, 'datashop_tab', 1)",
            (file_hash,),
        ).lastrowid
        db_conn.execute(
            "INSERT INTO events (event_id, import_id, student_id, session_id, "
            "problem_name, selection, action, input, feedback_text, "
            "feedback_classification, problem_view, timestamp, course_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "offboard-only", import_id, "S", "100", Q1, "dragNdrop-drop",
                f"{Q1}_drag1 -> {Q1}_drop7", "", "", "", "1",
                "2026-02-06 10:00:00.000000", "course",
            ),
        )
        db_conn.commit()
        derive_events(db_conn)
        four_shapes_board(db_conn)  # R = 3, so drop7 is off the board
        result = reconstruct_all(db_conn)
        assert result.skipped_actions == 1  # the skipped-only case, really
        score_checks(db_conn)
        analyze_all(db_conn)
        [(n_attempts,)] = db_conn.execute("SELECT COUNT(*) FROM dnd_attempts").fetchall()
        assert n_attempts == 1
        configs = db_conn.execute(
            "SELECT seq, event_id FROM dnd_configurations ORDER BY seq"
        ).fetchall()
        assert [(c["seq"], c["event_id"]) for c in configs] == [(0, None)]  # X0 only
        for name in ("attempt_metrics", "configurations", "modeling_table"):
            header, rows = _export(db_conn, name)
            assert header[-1] == "import_sha256", name
            assert len(rows) == 1, name
            assert rows[0]["import_sha256"] == file_hash, name
            # The F20 identifiers keep their configurations-based rollup, so
            # they are empty here exactly as before (no configuration event).
            assert rows[0]["course_id"] == "", name

    def test_two_imports_tied_at_a_session_change_keep_their_own_import(
        self, db_conn, four_shapes_board
    ):
        # (f) second Copilot round (2026-10-19): one student, one item, two
        # imports, and the two attempts' events share one timestamp across a
        # session change. A session change is an attempt boundary, so there
        # are two attempts whose time windows coincide; a bare time window
        # would hold both attempts' events and MAX would hand both the same
        # hash. The window is qualified by the attempt's session, so each
        # attempt (and each of its configurations) carries its own import,
        # and each check still reads the key off its own event.
        hashes = {"100": "aa" * 32, "200": "bb" * 32}
        ts = "2026-02-06 10:00:00.000000"
        key = json.dumps({f"{Q1}_drop1": [f"{Q1}_drag1"]})  # the state after the move
        pv = 0
        for session, file_hash in hashes.items():
            import_id = db_conn.execute(
                "INSERT INTO raw_imports (file_path, file_hash, file_format, row_count) "
                "VALUES (?, ?, 'datashop_tab', 2)",
                (f"{session}.tab", file_hash),
            ).lastrowid
            for selection, action, payload in (
                ("dragNdrop-drop", f"{Q1}_drag1 -> {Q1}_drop1", ""),
                ("dragNdrop", key, key),
            ):
                pv += 1
                db_conn.execute(
                    "INSERT INTO events (event_id, import_id, student_id, session_id, "
                    "problem_name, selection, action, input, feedback_text, "
                    "feedback_classification, problem_view, timestamp, course_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        f"tied-{session}-{pv}", import_id, "S", session, Q1, selection,
                        action, payload, "", "Incorrect" if payload else "", str(pv),
                        ts, "course",
                    ),
                )
        db_conn.commit()
        derive_events(db_conn)
        four_shapes_board(db_conn)
        reconstruct_all(db_conn)
        score_checks(db_conn)
        analyze_all(db_conn)
        attempts = db_conn.execute(
            "SELECT attempt_id, session_id, started_at, ended_at, segmentation_rule "
            "FROM dnd_attempts ORDER BY attempt_index"
        ).fetchall()
        assert [a["session_id"] for a in attempts] == ["100", "200"]
        assert attempts[1]["segmentation_rule"].startswith("session_change")
        # The windows coincide: the case a bare time window cannot tell apart.
        assert {(a["started_at"], a["ended_at"]) for a in attempts} == {(ts, ts)}
        by_attempt = {str(a["attempt_id"]): hashes[a["session_id"]] for a in attempts}
        # Per attempt: X0, the move, the check (three configurations; one check).
        expected_rows = {"attempt_metrics": 2, "modeling_table": 2, "configurations": 6, "checks": 2}
        for name, n in expected_rows.items():
            _, rows = _export(db_conn, name)
            assert len(rows) == n, name
            for r in rows:
                assert r["import_sha256"] == by_attempt[r["attempt_id"]], (name, r["attempt_id"])

    def test_absent_from_the_other_datasets(self, pipeline):
        # (c) segments, check_scores, boards and item_summary are unchanged.
        for name in UNKEYED:
            assert "import_sha256" not in export.DATASETS[name].columns, name
            header, _ = _export(pipeline, name)
            assert "import_sha256" not in header, name

    def test_column_before_the_key_is_unchanged(self, pipeline):
        # (d) additive: the pre-existing last column keeps its place just
        # before the key. Seeded on the check-shaped datasets (checks,
        # configurations), prior_attempt_id on the attempt-shaped ones.
        for name, before in KEYED.items():
            cols = export.DATASETS[name].columns
            assert cols[-2:] == (before, "import_sha256"), name
            header, _ = _export(pipeline, name)
            assert header[-2:] == [before, "import_sha256"], name
            assert header.count("import_sha256") == 1, name


class TestDescriptorWiring:
    def test_export_datasets_published_on_descriptor(self):
        (dnd,) = [s for s in available_services() if s.name == "dnd"]
        # 2026-10-19 (E-02 PR 2, cc-task-attrition-ledger): the three
        # Python-assembled ledger datasets join the eight stage views, the
        # way this pin grew at F3, PR #151 and F20 when a dataset was added.
        assert set(dnd.export_datasets) == {
            "checks", "check_scores", "attempt_metrics", "segments",
            "configurations", "boards", "modeling_table", "item_summary",
            "flow", "readiness_split", "exclusion_probe",
        }
