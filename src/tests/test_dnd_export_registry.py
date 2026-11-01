"""
The export registry hook of the attrition ledger (PR 2 spec §2.1, §2.3,
§2.4; E-02): ``_Dataset`` takes exactly one of ``build_sql`` /
``build_rows``, ``export_dataset`` dispatches a ``build_rows`` dataset by
iterating dicts keyed by the dataset's columns and writing them in column
order (a missing key is an error, never a blank), the three ledger datasets
sit at the end of ``DATASETS`` and ride the CLI help while the descriptor
keeps the eight stage views, and ``dnd export flow -o`` writes a file. In the style of ``test_dnd_export.py``
and ``test_cli_dnd.py``, in a new file so neither pinned file is touched.

Privacy (M6): the CLI run's stdout, stderr and the written file are checked
for the fixture's ids and pseudonyms, which are read from the database so
this module names none; the needle list is opaque to reprs and dropped
before any assert, and counts are bound to a name before they are asserted,
so no failure message carries one either.
"""

from __future__ import annotations

import csv
import io

import pytest

from proteus import cli, db
from proteus.core import ingest as core_ingest
from proteus.core.ingest.imports import file_hash
from proteus.core.registry import available_services
from proteus.services.dragndrop import account_roles, export, logging_regimes
from proteus.services.dragndrop.derive import derive_events
from proteus.services.dragndrop.process import analyze_all
from proteus.services.dragndrop.reconstruct import reconstruct_all
from proteus.services.dragndrop.scoring import score_checks

FIXTURE = "golden_dnd_attrition.tab"
ROLES = "account_roles_fixture.csv"
REGIME_DATE = "2026-06-02"
Q1 = "demo-book__demo-shapes-q1"

LEDGER = ("flow", "readiness_split", "exclusion_probe")
EIGHT = (
    "checks", "check_scores", "attempt_metrics", "segments",
    "configurations", "boards", "modeling_table", "item_summary",
)


def _read_csv(text: str) -> tuple[list[str], list[dict]]:
    reader = csv.reader(io.StringIO(text))
    header = next(reader)
    rows = [dict(zip(header, r)) for r in reader]
    return header, rows


def _toy(**kw) -> export._Dataset:
    """A keyword-constructed dataset with the four required fields plus `kw`."""
    return export._Dataset(
        name="toy", columns=("b", "a"), summary="a toy", description="A toy dataset.", **kw,
    )


# ---------------------------------------------------------------------------
# The hook itself
# ---------------------------------------------------------------------------
class TestDatasetHooks:
    def test_neither_hook_raises(self):
        with pytest.raises(TypeError):
            _toy()

    def test_both_hooks_raise(self):
        with pytest.raises(TypeError):
            _toy(build_sql=lambda f: ("SELECT 1", []), build_rows=lambda conn, f: [])

    def test_exactly_one_hook_is_accepted(self):
        sql = _toy(build_sql=lambda f: ("SELECT 1 AS b, 2 AS a", []))
        rows = _toy(build_rows=lambda conn, f: [])
        assert sql.build_rows is None and rows.build_sql is None
        assert sql.derive_row is None and rows.derive_row is None

    def test_the_eight_datasets_keep_build_sql(self):
        for name in EIGHT:
            spec = export.DATASETS[name]
            assert spec.build_sql is not None and spec.build_rows is None, name
        # The required fields come first, so keyword construction is unchanged
        # and every spec still carries a summary and a description.
        for spec in export.DATASETS.values():
            assert spec.summary and spec.description, spec.name


class TestBuildRowsDispatch:
    def test_writes_header_and_rows_in_column_order(self, db_conn, monkeypatch):
        # The rows come back keyed by column in any order; the file is in
        # the dataset's column order.
        spec = _toy(build_rows=lambda conn, filters: iter([{"a": 1, "b": 2}, {"b": 4, "a": 3}]))
        monkeypatch.setitem(export.DATASETS, "toy", spec)
        buf = io.StringIO()
        n = export.export_dataset(db_conn, "toy", buf)
        assert n == 2
        assert buf.getvalue() == "b,a\n2,1\n4,3\n"

    def test_passes_the_connection_and_the_question_filter(self, db_conn, monkeypatch):
        seen = []

        def build_rows(conn, filters):
            seen.append((conn is db_conn, dict(filters)))
            return []

        monkeypatch.setitem(export.DATASETS, "toy", _toy(build_rows=build_rows))
        buf = io.StringIO()
        assert export.export_dataset(db_conn, "toy", buf, question_id="x") == 0
        assert export.export_dataset(db_conn, "toy", buf) == 0
        assert seen == [(True, {"question_id": "x"}), (True, {"question_id": None})]
        assert buf.getvalue() == "b,a\nb,a\n"  # the header alone, twice

    def test_a_missing_key_is_an_error_never_a_blank(self, db_conn, monkeypatch):
        monkeypatch.setitem(export.DATASETS, "toy", _toy(build_rows=lambda conn, f: [{"a": 1}]))
        with pytest.raises((KeyError, ValueError)):
            export.export_dataset(db_conn, "toy", io.StringIO())

    def test_an_extra_key_is_ignored(self, db_conn, monkeypatch):
        monkeypatch.setitem(
            export.DATASETS, "toy", _toy(build_rows=lambda conn, f: [{"a": 1, "b": 2, "c": 3}])
        )
        buf = io.StringIO()
        assert export.export_dataset(db_conn, "toy", buf) == 1
        assert buf.getvalue() == "b,a\n2,1\n"

    def test_build_sql_datasets_still_dispatch(self, db_conn, monkeypatch):
        monkeypatch.setitem(
            export.DATASETS, "toy", _toy(build_sql=lambda f: ("SELECT 1 AS b, 2 AS a", []))
        )
        buf = io.StringIO()
        assert export.export_dataset(db_conn, "toy", buf) == 1
        assert buf.getvalue() == "b,a\n1,2\n"


# ---------------------------------------------------------------------------
# Registration: the registry, the descriptor, the CLI help
# ---------------------------------------------------------------------------
class TestRegistration:
    def test_the_three_are_registered_last(self):
        names = tuple(export.dataset_names())
        assert names[:8] == EIGHT
        assert names[-3:] == LEDGER
        assert len(names) == 11
        for name in LEDGER:
            spec = export.DATASETS[name]
            assert spec.name == name
            assert spec.build_rows is not None and spec.build_sql is None, name
            assert spec.summary and spec.description, name

    def test_attempt_import_join_is_exports_text_verbatim(self):
        # attrition keys an attempt's offering with its own copy of
        # export's attempt-window rollup (spec §2.2: reused verbatim, kept
        # as a copy so attrition never imports export); the two must not
        # drift.
        from proteus.services.dragndrop import attrition

        assert attrition.ATTEMPT_IMPORT_JOIN == export._ATTEMPT_IMPORT_JOIN

    def test_attrition_imports_first_without_a_cycle(self):
        # The registry imports attrition at the end of export; attrition must
        # not import export back at module level (spec §2.1), or a process
        # that imports attrition first meets a half-initialized export. A
        # fresh interpreter is the only place that order can be tried.
        import subprocess
        import sys

        probe = (
            "import proteus.services.dragndrop.attrition as attrition\n"
            "from proteus.services.dragndrop import export\n"
            "assert hasattr(attrition, '__all__')\n"
            "assert [n for n in export.dataset_names()][-3:] == "
            "['flow', 'readiness_split', 'exclusion_probe']\n"
            "print('ok')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True, check=False,
        )
        # M6 hygiene: the probe's traceback names checkout paths (an
        # ImportError on a partially initialized module quotes the file), so
        # the assertion message carries the exception type only, and the
        # return code is bound first (an introspected CompletedProcess would
        # print its stderr). Rerun the probe by hand for the rest.
        returncode, stdout = result.returncode, result.stdout.strip()
        stderr = result.stderr.strip()
        kind = stderr.splitlines()[-1].split(":", 1)[0] if stderr else "no stderr"
        del result, stderr
        assert returncode == 0, f"attrition-first import failed ({kind})"
        assert stdout == "ok"

    def test_the_descriptor_publishes_the_whole_registry(self):
        # The descriptor's export surface is the registry: the eight
        # SQL-backed stage views and the three Python-assembled ledger
        # datasets alike (spec R3, §2.3): what `dnd export` offers, the
        # descriptor publishes, each name bound to the identical spec object.
        # The pin in test_dnd_export.py grew by the three names the way it
        # grew at F3, PR #151 and F20 (the orchestrator's call, 2026-10-19).
        (dnd,) = [s for s in available_services() if s.name == "dnd"]
        assert set(dnd.export_datasets) == set(EIGHT) | set(LEDGER)
        assert set(LEDGER) <= set(export.DATASETS)
        for name in EIGHT + LEDGER:
            assert dnd.export_datasets[name] is export.DATASETS[name]

    def test_cli_help_names_the_three_from_their_summaries(self):
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
            a for a in dnd_sub.choices["export"]._actions if a.dest == "dataset"
        )
        assert set(LEDGER) <= set(dataset_arg.choices)
        for name in LEDGER:
            assert f"'{name}': {export.DATASETS[name].summary}" in dataset_arg.help


# ---------------------------------------------------------------------------
# `dnd export flow -o` through the CLI on a file database
# ---------------------------------------------------------------------------
class _Needles:
    """The private values no output may carry, opaque to reprs: pytest
    prints a failing test's funcargs (`built_db` among them) and a failing
    frame's arguments, so the values live behind an iterator and a repr
    that shows only their number (M6)."""

    __slots__ = ("_values",)

    def __init__(self, values) -> None:
        self._values = tuple(v for v in values if v)

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        return f"<{len(self._values)} needles>"


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "proteus.db"
    assert cli.main(["--db", str(path), "init"]) == 0
    return path


@pytest.fixture
def built_db(db_path, fixtures_dir, four_shapes_board):
    """The attrition build of test_dnd_attrition_fixture.py on a file
    database, so the CLI can open it. Returns (path, needles): the private
    values no output may carry."""
    conn = db.connect(db_path)
    try:
        tab = fixtures_dir / FIXTURE
        account_roles.import_roles(conn, fixtures_dir / ROLES)
        core_ingest.ingest_file(conn, tab)
        derive_events(conn)
        four_shapes_board(conn)
        logging_regimes.add_regime(conn, file_hash(tab), REGIME_DATE, reason="fixture")
        reconstruct_all(conn)
        score_checks(conn)
        analyze_all(conn)
        conn.commit()
        ids = [r[0] for r in conn.execute("SELECT DISTINCT student_id FROM events")]
        pseudonyms = [
            r[0] for r in conn.execute(
                f"SELECT DISTINCT pseudonym FROM {account_roles.TABLE} WHERE pseudonym IS NOT NULL"
            )
        ]
    finally:
        conn.close()
    n_ids = len(ids)  # bound first: a failing assert prints only the number
    assert n_ids == 16
    assert bool(pseudonyms)
    return db_path, _Needles([*ids, *pseudonyms, "Y99"])


def _assert_no_leak(text: str, needles) -> None:
    # The text and the needles are dropped before the assert so a failure's
    # traceback (which prints the frame's arguments) cannot echo a leaked
    # value or the needle list (M6).
    leaked = sum(1 for needle in needles if needle in text)
    del text, needles
    assert leaked == 0, f"{leaked} private value(s) leaked"


class TestCli:
    def test_export_flow_writes_a_file(self, built_db, tmp_path, capsys):
        path, needles = built_db
        out = tmp_path / "flow.csv"
        assert cli.main(["--db", str(path), "dnd", "export", "flow", "-o", str(out)]) == 0
        captured = capsys.readouterr()
        assert "Wrote" in captured.err and "row(s)" in captured.err
        text = out.read_text(encoding="utf-8")
        header, rows = _read_csv(text)
        assert header == list(export.DATASETS["flow"].columns)
        assert rows
        # The same bytes the library writes.
        conn = db.connect(path)
        try:
            buf = io.StringIO()
            n = export.export_dataset(conn, "flow", buf)
        finally:
            conn.close()
        assert n == len(rows)
        assert buf.getvalue() == text
        _assert_no_leak(captured.out + captured.err + text, needles)

    @pytest.mark.parametrize("dataset", ("readiness_split", "exclusion_probe"))
    def test_export_the_other_two_with_a_question_scope(self, built_db, tmp_path, dataset, capsys):
        path, needles = built_db
        out = tmp_path / f"{dataset}.csv"
        assert cli.main(
            ["--db", str(path), "dnd", "export", dataset, "-o", str(out), "--question", Q1]
        ) == 0
        captured = capsys.readouterr()
        text = out.read_text(encoding="utf-8")
        header, rows = _read_csv(text)
        assert header == list(export.DATASETS[dataset].columns)
        assert rows
        # q1's attempts only: the board-less item never appears.
        assert all(r["key"] != "demo-book__demo-shapes-q9" for r in rows)
        _assert_no_leak(captured.out + captured.err + text, needles)

    def test_export_flow_to_stdout(self, built_db, capsys):
        path, needles = built_db
        assert cli.main(["--db", str(path), "dnd", "export", "flow"]) == 0
        captured = capsys.readouterr()
        header, rows = _read_csv(captured.out)
        assert header == list(export.DATASETS["flow"].columns)
        assert rows
        _assert_no_leak(captured.out + captured.err, needles)
