"""
Tests for the run manifest (expansion memo E-03; cc-task-frozen-run-tooling).

Determinism, the no-path rule, the pseudonym map, the provenance-incomplete
marker, the YAML emitter's round trip through its own parser, the diff, and
the `proteus manifest` command.
"""

from __future__ import annotations

import json

import pytest

from proteus import cli, db
from proteus.core import ingest as core_ingest
from proteus.core import manifest, runs
from proteus.services.dragndrop import process
from proteus.services.dragndrop.derive import derive_events
from proteus.services.dragndrop.reconstruct import reconstruct_all
from proteus.services.dragndrop.scoring import score_checks


@pytest.fixture
def run_db(tmp_path, golden_dnd_tab, four_shapes_board):
    """A file database after the full DnD run on the golden fixture, with a
    textbook registered from a local file:// URL (the frozen run's shape)."""
    path = tmp_path / "run.sqlite"
    conn = db.connect(path)
    db.bootstrap(conn)
    core_ingest.ingest_file(conn, golden_dnd_tab)
    derive_events(conn)
    conn.execute(
        "INSERT OR IGNORE INTO textbooks (textbook_id, git_repo_url, git_ref, "
        "book_xml_id, build_dir) VALUES (1, 'file:///home/someone/era-checkout', "
        "'6239d8b5', 'demo-book', 'b')"
    )
    four_shapes_board(conn)
    reconstruct_all(conn)
    score_checks(conn)
    process.analyze_all(conn)
    conn.commit()
    conn.close()
    return path


def _open(path):
    return db.connect(path)


class TestBuild:
    def test_deterministic_fingerprint(self, run_db):
        conn = _open(run_db)
        try:
            a = manifest.build_manifest(conn, db_path=run_db)
            b = manifest.build_manifest(conn, db_path=run_db)
        finally:
            conn.close()
        assert a["fingerprint"] == b["fingerprint"]
        assert a["body"] == b["body"]
        assert len(a["fingerprint"]) == 64

    def test_fingerprint_moves_with_the_data(self, run_db):
        conn = _open(run_db)
        try:
            before = manifest.build_manifest(conn)
            conn.execute("UPDATE raw_imports SET row_count = row_count + 1")
            conn.commit()
            after = manifest.build_manifest(conn)
        finally:
            conn.close()
        assert before["fingerprint"] != after["fingerprint"]
        assert manifest.diff_manifests(before, after) == [
            "~ imports[0].row_count: 14 -> 15"
        ]

    def test_no_path_is_recorded(self, run_db, golden_dnd_tab):
        conn = _open(run_db)
        try:
            m = manifest.build_manifest(conn, db_path=run_db)
        finally:
            conn.close()
        text = manifest.dump_yaml(m)
        assert golden_dnd_tab.name not in text
        assert str(golden_dnd_tab.parent) not in text
        assert "era-checkout" not in text
        assert m["body"]["textbooks"][0]["git_repo_url"] == "file://<redacted local path>"
        assert m["body"]["textbooks"][0]["git_ref"] == "6239d8b5"
        # The database hash is recorded, its path is not.
        assert len(m["body"]["database_sha256"]) == 64
        assert str(run_db) not in text

    def test_remote_repo_url_is_kept(self, run_db):
        conn = _open(run_db)
        try:
            conn.execute(
                "UPDATE textbooks SET git_repo_url = 'https://github.com/x/y.git'"
            )
            m = manifest.build_manifest(conn)
        finally:
            conn.close()
        assert m["body"]["textbooks"][0]["git_repo_url"] == "https://github.com/x/y.git"

    def test_imports_by_hash_and_pseudonym(self, run_db, tmp_path):
        conn = _open(run_db)
        try:
            sha = conn.execute("SELECT file_hash FROM raw_imports").fetchone()["file_hash"]
            unmapped = manifest.build_manifest(conn)
            mapping = tmp_path / "map.csv"
            mapping.write_text(
                "# private map\npseudonym,sha256,path\nY00-B03-P11,"
                f"{sha},/never/printed.tab\n"
            )
            mapped = manifest.build_manifest(conn, pseudonyms=manifest.load_pseudonyms(mapping))
        finally:
            conn.close()
        assert unmapped["body"]["imports"][0]["pseudonym"] == f"unmapped-{sha[:12]}"
        assert mapped["body"]["imports"][0]["pseudonym"] == "Y00-B03-P11"
        assert mapped["body"]["imports"][0]["sha256"] == sha
        assert "file_path" not in mapped["body"]["imports"][0]

    def test_runs_schema_counts_and_incomplete_provenance(self, run_db):
        conn = _open(run_db)
        try:
            # An LLM-style run recorded by prompt and model only (no stage,
            # no F22 config): surfaced as provenance incomplete, never hidden.
            runs.start_run(conn, prompt_version="stage2-v1", model_id="m", service="sa")
            conn.commit()
            m = manifest.build_manifest(conn)
        finally:
            conn.close()
        body = m["body"]
        stages = [r["stage"] for r in body["pipeline_runs"]]
        assert stages[:4] == ["derive", "reconstruct", "score", "analyze"]
        assert body["pipeline_runs"][-1]["provenance"] == "incomplete"
        assert "provenance" not in body["pipeline_runs"][0]
        analyze = body["pipeline_runs"][3]
        assert analyze["config"]["stage_version"] == process.STAGE_VERSION
        assert analyze["config"]["idle_threshold_s"] == 300.0
        ledger = body["schema"]["applied_migrations"]
        assert {"source": "dnd", "version": 13, "name": "0013_phase_window_features"} in ledger
        assert body["schema"]["core_version"] >= 3
        assert body["table_counts"]["dnd_attempts"] == 2
        assert body["table_counts"]["raw_imports"] == 1
        assert body["questions"]["with_source"] == 0  # the fixture board has no source_xml
        assert body["package_version"]
        assert body["not_captured"]
        # The per-service sections arrive through the descriptors' manifest
        # hook (manifest v2): the DnD identifier-alias set, empty here.
        assert body["services"]["dnd"]["question_aliases"] == {
            "count": 0, "set_sha256": None, "rows": [],
        }

    def test_manifest_version_is_recorded(self, run_db):
        conn = _open(run_db)
        try:
            m = manifest.build_manifest(conn)
        finally:
            conn.close()
        assert m["body"]["manifest_version"] == manifest.MANIFEST_VERSION


class TestYaml:
    def test_round_trip_of_the_emitted_subset(self):
        value = {
            "a": 1, "b": 2.5, "c": None, "d": True, "e": False,
            "s": 'with "quotes", colons: and unicode — δ',
            "empty_map": {}, "empty_list": [],
            "list": [1, "x", None, {"k": [1, 2]}, [3, 4]],
            "nested": {"m": {"n": {"o": "deep"}}, "l": [{"p": 1, "q": "r"}, {"p": 2, "q": "s"}]},
            "key: with colon": "v",
        }
        text = manifest.dump_yaml(value)
        assert manifest.parse_yaml(text) == value

    def test_strings_are_always_quoted(self):
        text = manifest.dump_yaml({"n": "123", "t": "true", "v": "null"})
        assert manifest.parse_yaml(text) == {"n": "123", "t": "true", "v": "null"}

    def test_write_and_read(self, run_db, tmp_path):
        conn = _open(run_db)
        try:
            m = manifest.build_manifest(conn, db_path=run_db)
        finally:
            conn.close()
        path = manifest.write_manifest(m, tmp_path / "manifest.yaml")
        text = path.read_text()
        assert text.startswith("# PROTEUS run manifest v2")
        back = manifest.read_manifest(path)
        assert back == m


class TestDiff:
    def test_identical(self):
        m = {"fingerprint": "f", "generated_at": "t1", "body": {"a": 1, "l": [1, 2]}}
        n = {"fingerprint": "f", "generated_at": "t2", "body": {"a": 1, "l": [1, 2]}}
        assert manifest.diff_manifests(m, n) == []

    def test_added_removed_changed(self):
        m = {"body": {"a": 1, "b": {"c": 2}, "l": [1]}}
        n = {"body": {"a": 2, "l": [1, 3], "z": "new"}}
        assert manifest.diff_manifests(m, n) == [
            "~ a: 1 -> 2",
            "- b.c: 2",
            "+ l[1]: 3",
            "+ z: 'new'",
        ]


class TestCli:
    def test_manifest_write_and_diff(self, run_db, tmp_path, capsys):
        out1 = tmp_path / "m1.yaml"
        assert cli.main(["--db", str(run_db), "manifest", "-o", str(out1)]) == 0
        err = capsys.readouterr().err
        assert "Wrote manifest" in err
        assert cli.main(["--db", str(run_db), "manifest", "diff", str(out1), str(out1)]) == 0
        assert "identical" in capsys.readouterr().out
        conn = _open(run_db)
        conn.execute("UPDATE raw_imports SET row_count = 99")
        conn.commit()
        conn.close()
        out2 = tmp_path / "m2.yaml"
        assert cli.main(["--db", str(run_db), "manifest", "-o", str(out2)]) == 0
        capsys.readouterr()
        assert cli.main(["--db", str(run_db), "manifest", "diff", str(out1), str(out2)]) == 1
        out = capsys.readouterr().out
        assert "imports[0].row_count: 14 -> 99" in out

    def test_manifest_to_stdout(self, run_db, capsys):
        assert cli.main(["--db", str(run_db), "manifest"]) == 0
        out = capsys.readouterr().out
        parsed = manifest.parse_yaml(out)
        assert parsed["body"]["table_counts"]["dnd_attempts"] == 2
        assert json.dumps(parsed["body"], sort_keys=True)  # serializable

    def test_missing_db(self, tmp_path, capsys):
        assert cli.main(["--db", str(tmp_path / "none.db"), "manifest"]) == 1
