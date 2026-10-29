"""
CLI tests for the `proteus dnd` namespace (plan item B1.3; B2.2/B2.3/B2.4
commands join as they land).

The namespace is registered through the DnD ServiceDescriptor exactly like
`sa` (item A2.2); these tests exercise the end-to-end wiring, not the stage
logic (covered in test_dnd_derive and friends).
"""

from __future__ import annotations

import shutil

import pytest

from proteus import cli, db
from proteus.core import registry
from proteus.services.dragndrop import export

# The SA line of `ingest --derive` prints only in a tree with the SA service (R14).
SA = "sa" in {s.name for s in registry.available_services()}


@pytest.fixture
def source_dir(tmp_path, fixtures_dir):
    src = tmp_path / "ptx-src"
    src.mkdir()
    shutil.copy(
        fixtures_dir / "question_sources/sec-demo-shapes.ptx",
        src / "sec-demo-shapes.ptx",
    )
    return src


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "proteus.db"
    assert cli.main(["--db", str(path), "init"]) == 0
    return path


class TestDndNamespace:
    def test_root_parser_has_dnd_namespace(self):
        parser = cli._build_parser()
        sub = next(
            a for a in parser._actions
            if isinstance(a, cli.argparse._SubParsersAction)
        )
        assert "dnd" in sub.choices
        dnd_sub = next(
            a for a in sub.choices["dnd"]._actions
            if isinstance(a, cli.argparse._SubParsersAction)
        )
        assert "derive" in dnd_sub.choices

    def test_export_help_documents_every_dataset(self):
        """`dnd export --help` glosses every dataset the registry offers.

        The help used to hand-list four of the eight; it is generated from
        ``export.DATASETS`` now, and this pins that so a ninth dataset can't
        land selectable-but-undocumented.
        """
        parser = cli._build_parser()
        sub = next(
            a for a in parser._actions
            if isinstance(a, cli.argparse._SubParsersAction)
        )
        dnd_sub = next(
            a for a in sub.choices["dnd"]._actions
            if isinstance(a, cli.argparse._SubParsersAction)
        )
        dataset_arg = next(
            a for a in dnd_sub.choices["export"]._actions
            if a.dest == "dataset"
        )
        assert set(dataset_arg.choices) == set(export.DATASETS)
        for name, spec in export.DATASETS.items():
            assert f"'{name}' — {spec.summary}" in dataset_arg.help

    def test_derive_end_to_end(self, db_path, golden_dnd_tab, capsys):
        assert cli.main(["--db", str(db_path), "ingest", str(golden_dnd_tab)]) == 0
        capsys.readouterr()
        assert cli.main(["--db", str(db_path), "dnd", "derive"]) == 0
        out = capsys.readouterr().out
        assert "seen:      13" in out
        assert "inserted:  13" in out
        assert "malformed: 2" in out

    def test_derive_question_filter(self, db_path, golden_dnd_tab, capsys):
        assert cli.main(["--db", str(db_path), "ingest", str(golden_dnd_tab)]) == 0
        capsys.readouterr()
        assert cli.main(
            [
                "--db", str(db_path), "dnd", "derive",
                "--question", "demo-book__demo-shapes-q1",
            ]
        ) == 0
        assert "seen:      12" in capsys.readouterr().out

    def test_ingest_derive_runs_both_services(self, db_path, golden_dnd_tab, capsys):
        # `proteus ingest --derive` chains every available service's derive
        # (item A3.3); with the DnD service registered, both lines print.
        assert cli.main(
            ["--db", str(db_path), "ingest", "--derive", str(golden_dnd_tab)]
        ) == 0
        out = capsys.readouterr().out
        if SA:
            assert "sa derive: inserted=1" in out  # the single shortanswer row
        assert "dnd derive: inserted=13" in out

    def test_sync_sources_and_sync_boards_end_to_end(
        self, db_path, source_dir, tmp_path, capsys
    ):
        conn = db.connect(db_path)
        try:
            conn.execute(
                "INSERT INTO textbooks (git_repo_url, git_ref, book_xml_id, "
                "build_dir) VALUES ('r', 'v', 'demo-book', 'b')"
            )
            conn.commit()
            textbook_id = conn.execute(
                "SELECT textbook_id FROM textbooks WHERE book_xml_id = 'demo-book'"
            ).fetchone()["textbook_id"]
        finally:
            conn.close()

        # sync-sources walks the tree, catalogs the questions, and the DnD
        # source handler builds boards in the same pass.
        assert cli.main(
            [
                "--db", str(db_path), "sync-sources",
                "--source-dir", str(source_dir),
                "--textbook-id", str(textbook_id),
                "--output-dir", str(tmp_path / "cache"),
            ]
        ) == 0
        out = capsys.readouterr().out
        # Two cardsorts, plus the two short answers when the SA service is
        # there to claim them (R14).
        assert f"extracted:     {4 if SA else 2}" in out
        assert "dnd source handler: created=2" in out
        assert (
            tmp_path / "cache" / "demo-book" / "demo-book__demo-shapes-q1.ptx"
        ).is_file()

        # sync-boards re-runs the handler over the already-synced questions.
        assert cli.main(["--db", str(db_path), "dnd", "sync-boards"]) == 0
        out = capsys.readouterr().out
        if SA:
            assert "skipped=2" in out  # the two short-answer questions
        assert "unchanged=2" in out
        assert "boards registered:     2" in out

    def test_validate_reports_and_exits_nonzero_on_gaps(
        self, db_path, source_dir, golden_dnd_tab, tmp_path, capsys
    ):
        conn = db.connect(db_path)
        try:
            conn.execute(
                "INSERT INTO textbooks (git_repo_url, git_ref, book_xml_id, "
                "build_dir) VALUES ('r', 'v', 'demo-book', 'b')"
            )
            conn.commit()
            textbook_id = conn.execute(
                "SELECT textbook_id FROM textbooks WHERE book_xml_id = 'demo-book'"
            ).fetchone()["textbook_id"]
        finally:
            conn.close()
        assert cli.main(
            [
                "--db", str(db_path), "sync-sources",
                "--source-dir", str(source_dir),
                "--textbook-id", str(textbook_id),
                "--output-dir", str(tmp_path / "cache"),
            ]
        ) == 0
        assert cli.main(
            ["--db", str(db_path), "ingest", "--derive", str(golden_dnd_tab)]
        ) == 0
        capsys.readouterr()

        # The fixture plants off-board labels, so the full run fails …
        assert cli.main(["--db", str(db_path), "dnd", "validate"]) == 1
        out = capsys.readouterr().out
        assert "key=match" in out
        assert "label gaps (3)" in out
        assert "1 failing" in out

        # … while the clean (eventless) question validates green.
        assert cli.main(
            [
                "--db", str(db_path), "dnd", "validate",
                "--question", "demo-book__demo-shapes-q2",
            ]
        ) == 0
        assert "0 failing" in capsys.readouterr().out

    def test_reconstruct_end_to_end(self, db_path, golden_dnd_tab, capsys):
        assert cli.main(
            ["--db", str(db_path), "ingest", "--derive", str(golden_dnd_tab)]
        ) == 0
        capsys.readouterr()
        assert cli.main(["--db", str(db_path), "dnd", "reconstruct"]) == 0
        out = capsys.readouterr().out
        assert "attempts:        2" in out
        assert "checks:          4" in out
        assert "mismatches:      0" in out
        # dnd/0012: DemoStudent2's two non-eligible checks are counted, not
        # compared.
        assert "not comparable:  2" in out
        assert "noop moves:      0" in out
        # FP-3: the default era is reported in the summary.
        assert "feedback policy: legacy" in out

    def test_reconstruct_layered_feedback_policy(
        self, db_path, golden_dnd_tab, capsys
    ):
        assert cli.main(
            ["--db", str(db_path), "ingest", "--derive", str(golden_dnd_tab)]
        ) == 0
        capsys.readouterr()
        assert cli.main(
            ["--db", str(db_path), "dnd", "reconstruct",
             "--feedback-policy", "layered"]
        ) == 0
        assert "feedback policy: layered" in capsys.readouterr().out
        conn = db.connect(db_path)
        try:
            assert conn.execute(
                "SELECT DISTINCT feedback_policy FROM dnd_attempts"
            ).fetchone()[0] == "layered"
        finally:
            conn.close()

    def test_reconstruct_score_pipeline_end_to_end(
        self, db_path, source_dir, golden_dnd_tab, tmp_path, capsys
    ):
        conn = db.connect(db_path)
        try:
            conn.execute(
                "INSERT INTO textbooks (git_repo_url, git_ref, book_xml_id, "
                "build_dir) VALUES ('r', 'v', 'demo-book', 'b')"
            )
            conn.commit()
            textbook_id = conn.execute(
                "SELECT textbook_id FROM textbooks WHERE book_xml_id = 'demo-book'"
            ).fetchone()["textbook_id"]
        finally:
            conn.close()
        assert cli.main(
            [
                "--db", str(db_path), "sync-sources",
                "--source-dir", str(source_dir),
                "--textbook-id", str(textbook_id),
                "--output-dir", str(tmp_path / "cache"),
            ]
        ) == 0
        assert cli.main(
            ["--db", str(db_path), "ingest", "--derive", str(golden_dnd_tab)]
        ) == 0
        assert cli.main(["--db", str(db_path), "dnd", "reconstruct"]) == 0
        capsys.readouterr()
        assert cli.main(["--db", str(db_path), "dnd", "score"]) == 0
        out = capsys.readouterr().out
        assert "scored:    3" in out
        assert "correct:   1" in out

        assert cli.main(["--db", str(db_path), "dnd", "analyze"]) == 0
        out = capsys.readouterr().out
        assert "attempts:         2" in out
        assert "with convergence: 2" in out

        # The export view is reachable through the same namespace.
        assert cli.main(
            ["--db", str(db_path), "dnd", "export", "check_scores"]
        ) == 0
        out = capsys.readouterr().out
        assert out.splitlines()[0].startswith("event_id,attempt_id,question_id")
        assert "Correct" in out

    def test_status_includes_dnd_contribution(self, db_path, golden_dnd_tab, capsys):
        assert cli.main(
            ["--db", str(db_path), "ingest", "--derive", str(golden_dnd_tab)]
        ) == 0
        capsys.readouterr()
        assert cli.main(["--db", str(db_path), "status"]) == 0
        out = capsys.readouterr().out
        assert "Drag-and-Drop (dnd):" in out
        assert "check=4" in out
