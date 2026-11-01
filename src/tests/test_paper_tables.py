"""
The manuscript-table generator (``proteus.paper1.paper_tables``, the
``proteus-paper-tables`` command; ``docs/dnd/scripts/paper_tables.py`` until 2026-10-27;
expansion memo E-08, ``cc-task-paper-tables.md`` Task B under the owner's
Checkpoint 1 rulings of 2026-10-18: R1 (a) a planning script, R2 CSV plus
PreTeXt fragments, R3 items convention A by default, R4 the figure sorted by
eligible-anchor count, R5 (a) the five-student cell guard, R7 (a) the
diagnostic categories, R11 (a) the manifest's hand block, R13 (a) the
primary-configuration refusal).

The tests run on a fixture archive built once per module from the three DnD
fixtures ``golden_dnd.tab``, ``golden_dnd_training_anomalies.tab`` and
``golden_dnd_paper_tables.tab`` (three imports, two boards, one alias; the
planted facts are pinned by ``test_dnd_paper_tables_fixture.py``) and never on
an export. The generator reads only that archive; the tests never hand it a
database. Two smaller archives are built the same way for the PR #236 Copilot
review round (2026-10-19): one from the first two fixtures alone (four
students, so the pooled cohort itself falls under the cell guard:
``TestPooledGuard``) and one with an off-board-only attempt added under the
first import (``TestOffBoardOnlyAttempt``: the attempt-window offering key).
The RQ3 subset is the ``contrast.read_population`` rule mirrored from the
export (``TestRQ3Subset``: 6 on the fixture archive, not the 9 ready attempts
with an eligible anchor). A fourth archive, ``golden_dnd_attrition.tab`` alone
with the roles fixture imported and a logging-regime row added between ingest
and derive (``_attrition_setup``), carries the cell-by-cell pin of
``tab-corpus-flow`` (``TestCorpusFlow``; E-08's one-commit follow-up under
ruling R8 (ii), reading the attrition ledger's ``flow`` export of PR #239.
every cell a literal re-derived from the fixture, the ledger's own pins in
``test_dnd_attrition_ledger.py`` being the authority).

The reference (brief §3.6, hand-computed from the Wilson formula, z = 1.959964)
is in ``TestReference``'s docstring and pinned to four decimals.
"""

from __future__ import annotations

import csv
import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from paper1_archive import (  # noqa: F401 — the shared archive helpers and constants
    EM_DASH, EXPOSED, FIXTURE_FILES, FIXTURES, FLOW_LABELS, FORBIDDEN_COLUMNS,
    OUTPUT_FILES, POOLED_FILES, PSEUDONYM, Q1, Q2, Q2_OLD, Q3, RUN_ID, SECTION,
    SHA256, TERM, XML_ID, _data_rows, _register_board, _sha, build_database,
    build_fixture_archive, by, columns_of, copy_archive, hand_block, latest_run,
    read_display, rewrite_manifest,
)
from proteus import db
from proteus.core import ingest as core_ingest
from proteus.core import manifest
from proteus.core.ingest.events import Event, write_events
from proteus.paper1 import paper_tables as paper_tables_mod
from proteus.services.dragndrop import (
    account_roles, aliases, board, contrast, export, logging_regimes, qc,
)
from proteus.services.dragndrop.derive import derive_events
from proteus.services.dragndrop.process import analyze_all
from proteus.services.dragndrop.reconstruct import reconstruct_all
from proteus.services.dragndrop.scoring import score_checks

ROOT = Path(__file__).resolve().parents[2]
# The generator's source, for the archive-only and never-writes-the-manuscript
# pins (a module of proteus.paper1 since 2026-10-27; docs/dnd/scripts before).
SCRIPT = Path(paper_tables_mod.__file__)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def gen():
    return paper_tables_mod


@pytest.fixture(scope="module")
def archive(tmp_path_factory):
    return build_fixture_archive(tmp_path_factory.mktemp("archive"))


@pytest.fixture(scope="module")
def out_a(gen, archive, tmp_path_factory):
    out = tmp_path_factory.mktemp("displays") / "A"
    assert gen.main(["--archive", str(archive), "--out", str(out)]) == 0
    return out


@pytest.fixture(scope="module")
def out_b(gen, archive, tmp_path_factory):
    out = tmp_path_factory.mktemp("displays") / "B"
    assert gen.main(["--archive", str(archive), "--out", str(out), "--items-convention", "B"]) == 0
    return out


@pytest.fixture(scope="module")
def fingerprint(archive):
    return manifest.read_manifest(archive / "manifest.yaml")["fingerprint"]


# ---------------------------------------------------------------------------
# (1) a run, (13) the stamps, (14) the archive-only rule
# ---------------------------------------------------------------------------
class TestRun:
    def test_main_returns_zero_and_writes_every_output(self, out_a):
        assert sorted(p.name for p in out_a.iterdir()) == sorted(OUTPUT_FILES)

    def test_module_constants(self, gen):
        # 1.0 → 1.1: the flow table is a new output set (PR 3, E-08 follow-up; Q5).
        assert gen.GENERATOR_VERSION == "1.1"
        assert gen.MIN_CELL_STUDENTS == 5
        # "derive": 2, the governance filter's stage (PR #238's hand-off, taken
        # in PR 3's light form: the newest dnd derive run completed, its version
        # within bounds, its config carrying the roles_set_sha256 key; Q6).
        assert gen.MIN_STAGE_VERSION == {"derive": 2, "reconstruct": 5, "score": 3, "analyze": 9}
        assert gen.ROOT == ROOT

    def test_every_output_carries_the_run_id_and_the_fingerprint(self, out_a, archive, fingerprint):
        for path in list(out_a.iterdir()) + [archive / "validation" / "checks_classified.csv"]:
            text = path.read_text(encoding="utf-8")
            assert f"run_id: {RUN_ID}" in text, path.name
            assert f"manifest_fingerprint: {fingerprint}" in text, path.name
            assert "generator: paper_tables.py 1.1" in text, path.name  # 1.1 since PR 3 (E-08 follow-up)
        for name in ("tab-items.csv", "tab-validation-by-item.csv", "fig-validation-results.csv",
                     "tab-corpus-flow.csv", "data-corpus-flow-by-offering.csv"):  # the flow CSVs: PR 3
            assert "# items_convention: A" in (out_a / name).read_text(encoding="utf-8")

    def test_no_wall_clock_stamp(self, out_a):
        for path in out_a.iterdir():
            text = path.read_text(encoding="utf-8")
            assert "generated_at" not in text and "built_at" not in text, path.name
            assert not re.search(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", text), path.name

    def test_the_generator_never_opens_a_database(self, gen, archive, tmp_path, capsys):
        """Archive-only (brief §1): no database module in the source outside
        comments, and no database option on the command line."""
        code = "\n".join(line.split("#", 1)[0] for line in SCRIPT.read_text(encoding="utf-8").splitlines())
        assert "sqlite3" not in code
        assert "--db" not in SCRIPT.read_text(encoding="utf-8")
        assert gen.main(["--archive", str(archive), "--db", "x.db", "--out", str(tmp_path / "o")]) == 2
        assert not (tmp_path / "o").exists()

    def test_default_out_is_displays_under_the_archive(self, gen, archive, tmp_path):
        run = copy_archive(archive, tmp_path)
        assert gen.main(["--archive", str(run)]) == 0
        assert sorted(p.name for p in (run / "displays").iterdir()) == sorted(OUTPUT_FILES)
        assert (run / "validation" / "checks_classified.csv").exists()


# ---------------------------------------------------------------------------
# (2) the hand-computed reference
# ---------------------------------------------------------------------------
class TestReference:
    """The Wilson reference, by hand (brief §3.6; z = 1.959964, z² = 3.841459).

    Overall exact agreement: k = 19 agreeing of n = 23 eligible anchors (the
    fixture pin: 30 checks, 23 eligible, 19 agreeing, 4 disagreeing).

        p            = 19 / 23                       = 0.826087
        z²/n         = 3.841459 / 23                 = 0.167020
        z²/(2n)      = 3.841459 / 46                 = 0.083510
        denominator  = 1 + z²/n                      = 1.167020
        centre       = (p + z²/(2n)) / denominator
                     = 0.909597 / 1.167020           = 0.779418
        p(1-p)/n     = 0.826087 × 0.173913 / 23      = 0.006246
        z²/(4n²)     = 3.841459 / 2116               = 0.001815
        half-width   = z · sqrt(0.006246 + 0.001815) / denominator
                     = 1.959964 × 0.089788 / 1.167020 = 0.150795
        [lo, hi]     = [0.779418 - 0.150795, 0.779418 + 0.150795]
                     = [0.628623, 0.930213]

    so the rate is 0.8261 and the interval [0.6286, 0.9302] to four decimals.

    Ready share overall: 9 ready attempts (DemoStudent1, PaperStudent01-05,
    PaperStudentMany, PaperStudentQ2, PaperStudentOld) of 21 → 0.428571 →
    0.4286; by the same formula centre = (0.428571 + 0.091463) / 1.182927 =
    0.439617, half-width = 1.959964 × sqrt(0.011662 + 0.002178) / 1.182927 =
    0.194917 → [0.2447, 0.6345].

    One by-offering cell, Y01-B03-P17 (the paper fixture: 22 checks, 17
    eligible, 13 agreeing): p = 13/17 = 0.764706; z²/n = 0.225968;
    denominator 1.225968; centre = (0.764706 + 0.112984) / 1.225968 =
    0.715916; half-width = 1.959964 × sqrt(0.010584 + 0.003323) / 1.225968 =
    0.188532 → rate 0.7647, interval [0.5274, 0.9044].
    """

    def test_overall_agreement_and_interval(self, out_a):
        rows = by(read_display(out_a / "tab-validation-results.csv")[1], "stratum")
        overall = rows["All offerings"]
        assert (overall["attempts"], overall["eligible_anchors"]) == ("21", "23")
        assert (overall["agreement_rate"], overall["agreement_lo"], overall["agreement_hi"]) == (
            "0.8261", "0.6286", "0.9302"
        )
        assert overall["suppressed"] == "0" and overall["n_students"] == "21"

    def test_overall_ready_share(self, out_a):
        overall = by(read_display(out_a / "tab-validation-results.csv")[1], "stratum")["All offerings"]
        assert (overall["ready_rate"], overall["ready_lo"], overall["ready_hi"]) == (
            "0.4286", "0.2447", "0.6345"
        )

    def test_one_by_offering_cell(self, out_a):
        rows = by(read_display(out_a / "data-validation-by-offering.csv")[1], "offering")
        paper = rows["Y01-B03-P17"]
        assert (paper["attempts"], paper["eligible_anchors"], paper["n_students"]) == ("17", "17", "17")
        assert (paper["agreement_rate"], paper["agreement_lo"], paper["agreement_hi"]) == (
            "0.7647", "0.5274", "0.9044"
        )

    def test_the_interval_is_the_standard_library_wilson(self, gen):
        lo, hi = gen.audit_sample.wilson(19, 23)
        assert (round(lo, 4), round(hi, 4)) == (0.6286, 0.9302)
        assert gen._rate(19, 23) == ("0.8261", "0.6286", "0.9302")
        assert gen._rate(0, 0) == ("", "", "")

    def test_the_principal_flag(self, out_a):
        # invalid_state on 6 of the 21 attempts (DemoStudent2, DupCard,
        # Offboard, OffboardSlot, UnknownCard, UnknownZone); check_mismatch 4.
        overall = by(read_display(out_a / "tab-validation-results.csv")[1], "stratum")["All offerings"]
        assert (overall["principal_flag"], overall["principal_flag_share"]) == ("invalid_state", "0.2857")


# ---------------------------------------------------------------------------
# (3) determinism
# ---------------------------------------------------------------------------
def test_two_runs_are_byte_identical(gen, archive, out_a, tmp_path):
    again = tmp_path / "again"
    assert gen.main(["--archive", str(archive), "--out", str(again)]) == 0
    assert sorted(p.name for p in again.iterdir()) == sorted(p.name for p in out_a.iterdir())
    for path in out_a.iterdir():
        assert path.read_bytes() == (again / path.name).read_bytes(), path.name


# ---------------------------------------------------------------------------
# (4) refusals: exit 2, nothing written
# ---------------------------------------------------------------------------
def _edit_body(data):
    data["body"]["package_version"] = "edited-by-hand"


def _non_primary(data):
    latest_run(data, "reconstruct")["config"]["gap_seconds"] = 600.0


def _drop_hand(data):
    del data["hand"]


def _not_ready_only(data):
    data["hand"]["configuration"]["readiness"] = "ready_and_limited"


def _old_reconstruct(data):
    latest_run(data, "reconstruct")["config"]["stage_version"] = 4


def _future_analyze(data):
    latest_run(data, "analyze")["config"]["stage_version"] = 99


def _term_missing(data):
    del data["hand"]["corpus"]["files"][0]["term"]


def _exposure_not_boolean(data):
    data["hand"]["corpus"]["files"][2]["development_exposure"] = "yes"


# PR 3 (E-08 follow-up), Q6 in the light form: the newest `dnd derive` run is
# bounded like the other stages (MIN_STAGE_VERSION["derive"] = 2, the
# governance filter's stage) and its recorded config must carry the
# roles_set_sha256 key, null on an archive with an empty roles table (the
# module archive), which manifest.dump_yaml / read_manifest round-trip as
# `null` → None, so the key survives the write. An archive derived before
# the filter would show Authorized = Raw silently.
def _old_derive(data):
    latest_run(data, "derive")["config"]["stage_version"] = 1


def _derive_without_roles_key(data):
    del latest_run(data, "derive")["config"]["roles_set_sha256"]


class TestRefusals:
    @pytest.mark.parametrize(
        "mutate, refingerprint, message",
        [
            (_edit_body, False, "fingerprint does not match"),
            (_non_primary, True, "gap_seconds=600.0"),
            (_drop_hand, False, "no top-level `hand` block"),
            (_not_ready_only, False, "readiness is 'ready_and_limited'"),
            (_old_reconstruct, True, "stage_version 4 is below the frozen-run minimum 5"),
            (_future_analyze, True, "stage_version 99 is above"),
            (_term_missing, False, "lacks a string term"),
            (_exposure_not_boolean, False, "lacks a boolean development_exposure"),
            (_old_derive, True, "stage_version 1 is below the frozen-run minimum 2"),
            (_derive_without_roles_key, True, "roles_set_sha256"),
        ],
        ids=["tampered-body", "non-primary", "no-hand-block", "not-ready-only",
             "reconstruct-below-5", "analyze-above-current", "term-missing", "exposure-not-boolean",
             "derive-below-2", "derive-without-roles-key"],
    )
    def test_manifest_refusals(self, gen, archive, tmp_path, capsys, mutate, refingerprint, message):
        run = copy_archive(archive, tmp_path)
        rewrite_manifest(run, mutate, refingerprint)
        out = tmp_path / "out"
        assert gen.main(["--archive", str(run), "--out", str(out)]) == 2
        err = capsys.readouterr().err
        assert err.startswith("error: ") and message in err
        assert not out.exists() and not (run / "validation").exists()

    def test_a_non_primary_manifest_fails_only_the_primary_check(self, gen, archive, tmp_path, capsys):
        """The copy is re-fingerprinted, so the fingerprint check passes and
        the refusal names the configuration key, not the hash."""
        run = copy_archive(archive, tmp_path)
        rewrite_manifest(run, _non_primary, refingerprint=True)
        assert gen.main(["--archive", str(run), "--out", str(tmp_path / "out")]) == 2
        err = capsys.readouterr().err
        assert "fingerprint" not in err
        assert "not the frozen primary configuration" in err and "frozen primary 1800.0" in err

    def test_a_directory_without_a_manifest_is_not_an_archive(self, gen, tmp_path, capsys):
        empty = tmp_path / "not-an-archive"
        empty.mkdir()
        assert gen.main(["--archive", str(empty), "--out", str(tmp_path / "out")]) == 2
        assert "not an archive: no manifest.yaml" in capsys.readouterr().err
        assert not (tmp_path / "out").exists()

    def test_a_missing_export_is_refused(self, gen, archive, tmp_path, capsys):
        run = copy_archive(archive, tmp_path)
        (run / "exports" / "boards.csv").unlink()
        assert gen.main(["--archive", str(run), "--out", str(tmp_path / "out")]) == 2
        assert "missing export(s) under exports/: boards.csv" in capsys.readouterr().err
        assert not (tmp_path / "out").exists()

    def test_a_seeded_pass_is_refused(self, gen, archive, tmp_path, capsys):
        run = copy_archive(archive, tmp_path)
        path = run / "exports" / "checks.csv"
        rows = list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))
        rows[0]["seeded"] = "1"
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        assert gen.main(["--archive", str(run), "--out", str(tmp_path / "out")]) == 2
        assert "seeded replay marker" in capsys.readouterr().err
        assert not (tmp_path / "out").exists()


# ---------------------------------------------------------------------------
# (5) the output guard, reused as it stands
# ---------------------------------------------------------------------------
class TestOutputGuard:
    def test_out_inside_the_repository_is_refused(self, gen, archive, capsys):
        out = ROOT / "data" / "paper-tables-guard-probe"
        assert not out.exists()
        assert gen.main(["--archive", str(archive), "--out", str(out)]) == 2
        assert "inside the repository but not under data/private/" in capsys.readouterr().err
        assert not out.exists()

    def test_allow_anywhere_lifts_the_guard(self, gen, archive, tmp_path, monkeypatch):
        """The guard reads audit_sample.ROOT; a stand-in repository under
        tmp_path keeps the accepted run out of the real checkout."""
        fake_repo = tmp_path / "repo"
        (fake_repo / "src" / "proteus").mkdir(parents=True)
        monkeypatch.setattr(gen.audit_sample, "ROOT", fake_repo)
        out = fake_repo / "displays"
        assert gen.main(["--archive", str(archive), "--out", str(out)]) == 2
        assert not out.exists()
        assert gen.main(["--archive", str(archive), "--out", str(out), "--allow-anywhere"]) == 0
        assert sorted(p.name for p in out.iterdir()) == sorted(OUTPUT_FILES)
        # data/private/ under the repository needs no override.
        private = fake_repo / "data" / "private" / "run"
        assert gen.main(["--archive", str(archive), "--out", str(private)]) == 0

    def test_the_validation_destination_is_guarded_too(self, gen, archive, tmp_path, monkeypatch, capsys):
        """PR #236 Copilot review (2026-10-19): ``<archive>/validation/
        checks_classified.csv`` carries event- and attempt-level identifiers,
        so an archive inside the repository (not under data/private/) is
        refused even when ``--out`` is outside it, before either directory
        is created."""
        fake_repo = tmp_path / "repo"
        (fake_repo / "src" / "proteus").mkdir(parents=True)
        monkeypatch.setattr(gen.audit_sample, "ROOT", fake_repo)
        run = copy_archive(archive, fake_repo / "archives")
        out = tmp_path / "outside"
        assert gen.main(["--archive", str(run), "--out", str(out)]) == 2
        err = capsys.readouterr().err
        assert "validation" in err and "inside the repository but not under data/private/" in err
        assert not out.exists()
        assert not (run / "validation").exists()
        assert not (run / "displays").exists()
        assert gen.main(["--archive", str(run), "--out", str(out), "--allow-anywhere"]) == 0
        assert (run / "validation" / "checks_classified.csv").exists()
        assert sorted(p.name for p in out.iterdir()) == sorted(OUTPUT_FILES)
        # An archive under data/private/ passes without the flag.
        private = copy_archive(archive, fake_repo / "data" / "private")
        assert gen.main(["--archive", str(private), "--out", str(tmp_path / "outside2")]) == 0
        assert (private / "validation" / "checks_classified.csv").exists()


# ---------------------------------------------------------------------------
# (6) the cell guard: students, never checks or attempts
# ---------------------------------------------------------------------------
class TestCellGuard:
    def test_by_offering(self, out_a, gen):
        rows = by(read_display(out_a / "data-validation-by-offering.csv")[1], "offering")
        assert list(rows) == ["Y00-B03-P11", "Y00-B03-P13", "Y01-B03-P17"]
        for pseud in ("Y00-B03-P11", "Y00-B03-P13"):  # two students each
            row = rows[pseud]
            assert row["suppressed"] == "1"
            assert all(row[c] == "" for c in gen.VALIDATION_MEASURES), pseud
        assert rows["Y01-B03-P17"]["suppressed"] == "0"

    def test_by_item_under_a(self, out_a, gen):
        rows = by(read_display(out_a / "tab-validation-by-item.csv")[1], "item")
        assert list(rows) == [Q1, Q2, Q2_OLD]
        assert rows[Q1]["suppressed"] == "0" and rows[Q1]["n_students"] == "17"
        # q2: two students (PaperStudentMany's six checks and PaperStudentQ2's
        # one: seven checks, two students); q2-old: one student.
        for item in (Q2, Q2_OLD):
            assert rows[item]["suppressed"] == "1"
            assert all(rows[item][c] == "" for c in gen.VALIDATION_MEASURES), item
        items = by(read_display(out_a / "tab-items.csv")[1], "item")
        for item in (Q2, Q2_OLD):
            assert items[item]["suppressed"] == "1"
            assert all(items[item][c] == "" for c in gen.ITEMS_COUNT_COLUMNS), item
        fig = by(read_display(out_a / "fig-validation-results.csv")[1], "item")
        for item in (Q2, Q2_OLD):
            assert fig[item]["suppressed"] == "1"
            assert all(fig[item][c] == "" for c in gen.FIG_MEASURES), item

    def test_a_cell_of_many_checks_from_one_student_is_suppressed(self, gen, archive):
        """The guard counts students: q2 under A has seven checks from two
        students, q2 under B eight from three (both under five). The counts
        are read before the guard through the module, since the displays
        suppress them."""
        arc_a = gen.Archive(archive, "A")
        rows = {r["item"]: r for r in gen.item_rows_unguarded(arc_a)}
        assert (rows[Q2]["students"], rows[Q2]["attempts"], rows[Q2]["checks"]) == (2, 2, 7)
        assert (rows[Q2_OLD]["students"], rows[Q2_OLD]["attempts"], rows[Q2_OLD]["checks"]) == (1, 1, 1)
        assert (rows[Q1]["students"], rows[Q1]["attempts"], rows[Q1]["checks"]) == (17, 17, 21)
        arc_b = gen.Archive(archive, "B")
        pooled = {r["item"]: r for r in gen.item_rows_unguarded(arc_b)}
        assert (pooled[Q2]["students"], pooled[Q2]["attempts"], pooled[Q2]["checks"]) == (3, 3, 8)
        assert gen.suppressed(rows[Q2]["_measures"]) and gen.suppressed(pooled[Q2]["_measures"])
        assert not gen.suppressed(rows[Q1]["_measures"])

    def test_the_guard_counts_students_not_attempts(self, gen):
        """Every fixture student has exactly one attempt (21 students, 21
        attempts), so no display can tell a students guard from an attempts
        guard; the module API can. Ruling R5 and the 2026-10-14 governance
        entry, item 4: the threshold counts students, never checks or attempts
        (twenty checks -- or attempts -- from one student is a cell of one),
        and exactly five students is the first cell shown."""
        def attempts(student_ids):
            return [{"student_id": s, "readiness": qc.READY, "qc_flags_json": "[]"} for s in student_ids]

        one_student = gen.measures(attempts(["s"] * 6), [])
        assert (one_student["n_students"], one_student["attempts"]) == (1, 6)
        assert gen.suppressed(one_student)
        four_students = gen.measures(attempts([f"s{i % 4}" for i in range(20)]), [])
        assert (four_students["n_students"], four_students["attempts"]) == (4, 20)
        assert gen.suppressed(four_students)
        five_students = gen.measures(attempts([f"s{i}" for i in range(5)]), [])
        assert (five_students["n_students"], five_students["attempts"]) == (5, 5)
        assert not gen.suppressed(five_students)
        # Every display reaches the guard through the same function.
        for fields, columns in ((gen.validation_fields, gen.VALIDATION_MEASURES),
                                (gen.fig_fields, gen.FIG_MEASURES)):
            hidden = fields(one_student)
            assert hidden["suppressed"] == "1" and all(hidden[c] == "" for c in columns)
            shown = fields(five_students)
            assert shown["suppressed"] == "0" and shown["attempts"] == "5"

    def test_strata_rows_and_the_note(self, out_a):
        rows = by(read_display(out_a / "tab-validation-results.csv")[1], "stratum")
        assert list(rows) == [
            "All offerings", "Fall 2025 (2 offerings)", "Spring 2026 (1 offering)",
            "Offerings not used in development (2)", "Development offering (1)",
        ]
        # Fall 2025 and the unexposed offerings are the same four students.
        for label in ("Fall 2025 (2 offerings)", "Offerings not used in development (2)"):
            assert rows[label]["suppressed"] == "1" and rows[label]["n_students"] == ""
        assert rows["Spring 2026 (1 offering)"]["suppressed"] == "0"
        assert rows["Development offering (1)"]["agreement_rate"] == "0.7647"
        note = (out_a / "tab-validation-results-note.ptx").read_text(encoding="utf-8")
        assert "Per-offering counts appear only where every cell contains at least five students" in note
        assert "fewer than five students" in note
        for name in ("Fall 2025 (2 offerings)", "Offerings not used in development (2)",
                     "Y00-B03-P11", "Y00-B03-P13"):
            assert name in note
        assert "Y01-B03-P17" not in note

    def test_a_suppressed_cell_never_prints_its_student_count(self, out_a):
        for name in ("tab-validation-results.csv", "tab-validation-by-item.csv",
                     "data-validation-by-offering.csv",
                     "tab-corpus-flow.csv", "data-corpus-flow-by-offering.csv"):  # the flow CSVs: PR 3
            for row in read_display(out_a / name)[1]:
                if row["suppressed"] == "1":
                    assert row["n_students"] == "", (name, row)
        for row in read_display(out_a / "tab-items.csv")[1]:
            if row["suppressed"] == "1":
                assert row["students"] == "", row


# ---------------------------------------------------------------------------
# (7) the items convention
# ---------------------------------------------------------------------------
class TestItemsConvention:
    def test_convention_a_lists_logged_identifiers_with_a_board(self, out_a):
        rows = read_display(out_a / "tab-items.csv")[1]
        assert [r["item"] for r in rows] == [Q1, Q2, Q2_OLD]
        q1, q2, old = rows
        assert (q1["P"], q1["R"], q1["decoy_cards"], q1["decoy_zones"]) == ("4", "3", "1", "1")
        assert (q1["students"], q1["attempts"], q1["checks"], q1["eligible_anchors"]) == ("17", "17", "21", "14")
        assert (q1["correct_checks_feedback"], q1["correct_checks_scored"]) == ("6", "6")
        assert (q1["section"], q1["source_version"], q1["feedback_policy"]) == ("1.1", "hash", "legacy")
        assert (q1["canonical_question_id"], q1["is_alias"]) == (Q1, "0")
        # The alias row: the successor board's covariates, its own identity.
        assert (old["P"], old["R"], old["decoy_cards"], old["decoy_zones"]) == ("4", "3", "1", "1")
        assert (old["canonical_question_id"], old["is_alias"], old["section"]) == (Q2, "1", "1.2")
        assert (q2["canonical_question_id"], q2["is_alias"], q2["section"]) == (Q2, "0", "1.2")

    def test_convention_b_pools_the_alias(self, out_b):
        rows = read_display(out_b / "tab-items.csv")[1]
        assert [r["item"] for r in rows] == [Q1, Q2]
        assert all(r["is_alias"] == "0" for r in rows)
        assert [r["item"] for r in read_display(out_b / "tab-validation-by-item.csv")[1]] == [Q1, Q2]
        assert [r["item"] for r in read_display(out_b / "fig-validation-results.csv")[1]] == [Q1, Q2]
        assert "# items_convention: B" in (out_b / "tab-items.csv").read_text(encoding="utf-8")

    def test_the_board_less_identifier_is_in_neither_and_counted(self, out_a, out_b):
        for out in (out_a, out_b):
            for name in ("tab-items.csv", "tab-validation-by-item.csv", "fig-validation-results.csv"):
                assert Q3 not in {r["item"] for r in read_display(out / name)[1]}, (out, name)
            assert "Logged identifiers without a board: 1." in (out / "tab-items-note.ptx").read_text(encoding="utf-8")

    def test_the_notes_state_the_convention(self, out_a, out_b):
        a = (out_a / "tab-items-note.ptx").read_text(encoding="utf-8")
        assert ("3 logged identifiers correspond to 2 canonical items; 1 identifier(s) aliased to "
                "successor(s); no rows are lost by the alias.") in a
        b = (out_b / "tab-items-note.ptx").read_text(encoding="utf-8")
        assert "2 items, logged under 3 identifiers." in b
        for note in (a, b):
            assert "Source versions: hash." in note
            assert "Understanding Linear Algebra" not in note  # the credit line is the number fill's
        assert "fewer than five students): demo-book__demo-shapes-q2, demo-book__demo-shapes-q2-old." in a
        assert "fewer than five students): demo-book__demo-shapes-q2." in b

    def test_the_board_less_checks_still_enter_the_agreement_strata(self, out_a):
        # q3's one disagreeing anchor is in the 23 overall, not in any item row.
        overall = by(read_display(out_a / "tab-validation-results.csv")[1], "stratum")["All offerings"]
        assert overall["eligible_anchors"] == "23"
        items = read_display(out_a / "fig-validation-results.csv")[1]
        assert items[0]["item"] == Q1 and items[0]["eligible_anchors"] == "14"


# ---------------------------------------------------------------------------
# (8) the diagnostics (R7 (a))
# ---------------------------------------------------------------------------
class TestDiagnostics:
    def test_pooled_counts(self, out_a, gen):
        rows = read_display(out_a / "data-anchor-diagnostics.csv")[1]
        assert [(r["scope"], r["offering"]) for r in rows] == [
            ("pooled", ""), ("offering", "Y00-B03-P11"), ("offering", "Y00-B03-P13"), ("offering", "Y01-B03-P17"),
        ]
        pooled = rows[0]
        # 21 students pooled: the pooled row passes the cell guard it now
        # shares with the offering rows (PR #236 Copilot review, 2026-10-19).
        assert pooled["n_disagreeing"] == "4" and pooled["suppressed"] == "0"
        assert [pooled[c] for c in gen.CATEGORY_ORDER] == ["1", "1", "1", "1"]
        assert gen.CATEGORY_ORDER == (
            "source-map problem", "ambiguous ordering", "skipped event", "unexplained disagreement",
        )
        # The two-student offerings are suppressed; the paper offering holds all four.
        assert rows[1]["suppressed"] == "1" and rows[1]["n_disagreeing"] == ""
        assert rows[3]["suppressed"] == "0" and rows[3]["n_disagreeing"] == "4"

    def test_each_planted_disagreement_takes_its_category(self, out_a, archive):
        classified = read_display(archive / "validation" / "checks_classified.csv")[1]
        attempts = by(list(csv.DictReader((archive / "exports" / "attempt_metrics.csv").open(encoding="utf-8"))), "attempt_id")
        category = {attempts[r["attempt_id"]]["student_id"]: r["category"] for r in classified}
        assert category == {
            "PaperStudentDrop": "unexplained disagreement",
            "PaperStudentTie": "ambiguous ordering",
            "PaperStudentOffboard": "skipped event",
            "PaperStudentNoBoard": "source-map problem",
        }
        rows = by(classified, "attempt_id")
        no_board = rows[next(a for a, r in attempts.items() if r["student_id"] == "PaperStudentNoBoard")]
        assert (no_board["d"], no_board["question_id"], no_board["offering"]) == ("not computed", Q3, "Y01-B03-P17")
        assert "missing_source_map" in no_board["evidence"]
        for student in ("PaperStudentDrop", "PaperStudentTie", "PaperStudentOffboard"):
            row = rows[next(a for a, r in attempts.items() if r["student_id"] == student)]
            assert row["d"] == "1" and row["evidence"].endswith("d=1")

    def test_the_classification_is_internal(self, out_a, archive):
        assert (archive / "validation" / "checks_classified.csv").exists()
        assert not any(p.name == "checks_classified.csv" for p in out_a.iterdir())
        assert not (out_a / "validation").exists()


# ---------------------------------------------------------------------------
# (9) privacy: pseudonyms only, no event-, attempt- or student-level rows
# ---------------------------------------------------------------------------
class TestPrivacy:
    def test_no_forbidden_column(self, out_a):
        for path in out_a.glob("*.csv"):
            assert not FORBIDDEN_COLUMNS & set(columns_of(path)), (path.name, columns_of(path))

    def test_no_import_sha256_and_no_student_id(self, out_a, fingerprint):
        import_hashes = {_sha(FIXTURES / name) for name in FIXTURE_FILES}
        students = set()
        for name in FIXTURE_FILES:
            with open(FIXTURES / name, newline="", encoding="utf-8") as fh:
                students |= {r["Anon Student Id"] for r in csv.DictReader(fh, delimiter="\t")}
        assert len(students) == 21
        for path in out_a.iterdir():
            text = path.read_text(encoding="utf-8")
            assert not import_hashes & set(SHA256.findall(text)), path.name
            # The one 64-hex string a display may carry is the manifest fingerprint.
            assert set(SHA256.findall(text)) <= {fingerprint}, path.name
            for student in students:
                assert student not in text, (path.name, student)
            for name in FIXTURE_FILES:
                assert name not in text, path.name

    def test_no_attempt_level_row_count(self, out_a):
        items, offerings, strata = 3, 3, 5
        bound = max(items, offerings, strata) + 8
        # The flow files are fixed grids, not bounded lists: six rows by five
        # columns per table: 30 pooled cells, 30 per offering (PR 3; Q3).
        grid = {"tab-corpus-flow.csv": 30, "data-corpus-flow-by-offering.csv": 30 * offerings}
        for path in out_a.glob("*.csv"):
            if path.name in grid:
                assert len(read_display(path)[1]) == grid[path.name], path.name
            else:
                assert len(read_display(path)[1]) <= bound, path.name


# ---------------------------------------------------------------------------
# (10) the PreTeXt fragments (R2)
# ---------------------------------------------------------------------------
class TestFragments:
    def test_tab_items_fragment(self, out_a, fingerprint):
        text = (out_a / "tab-items.ptx").read_text(encoding="utf-8")
        assert text.startswith(f"<!-- run_id: {RUN_ID}; manifest_fingerprint: {fingerprint}; "
                               "generator: paper_tables.py 1.1; items_convention: A -->\n<table")  # 1.1 since PR 3
        root = ET.fromstring(text)
        assert root.tag == "table" and root.get("{http://www.w3.org/XML/1998/namespace}id") == "tab-items"
        assert root.find("title").text == "Items in the study corpus with design covariates and counts."
        tabular = root.find("tabular")
        assert tabular.get("halign") == "center"
        cols = tabular.findall("col")
        assert [c.get("width") for c in cols] == ["16%", "12%", "8%", "8%", "10%", "10%", "12%", "12%", "12%"]
        assert cols[0].get("halign") == "left" and all(c.get("halign") is None for c in cols[1:])
        header = tabular.find("row[@header='yes']")
        labels = ["".join(c.itertext()) for c in header.findall("cell")]
        assert labels == ["Item", "Section", "P", "R", "Decoy-cards", "Decoy-zones", "Students", "Attempts", "Checks"]
        assert header.findall("cell")[2].find("m").text == "P"
        assert [ln.text for ln in header.findall("cell")[4].findall("line")] == ["Decoy-", "cards"]
        rows = read_display(out_a / "tab-items.csv")[1]
        expected = []
        for r in rows:
            counts = [EM_DASH] * 3 if r["suppressed"] == "1" else [r["students"], r["attempts"], r["checks"]]
            expected.append([r["item"], r["section"], r["P"], r["R"], r["decoy_cards"], r["decoy_zones"], *counts])
        assert _data_rows(root) == expected

    def test_tab_validation_results_fragment(self, out_a, fingerprint):
        text = (out_a / "tab-validation-results.ptx").read_text(encoding="utf-8")
        assert text.startswith(f"<!-- run_id: {RUN_ID}; manifest_fingerprint: {fingerprint}; "
                               "generator: paper_tables.py 1.1 -->\n<table")  # 1.1 since PR 3
        assert '<col width="12%" />' in text  # the manuscript's spelling
        root = ET.fromstring(text)
        assert root.get("{http://www.w3.org/XML/1998/namespace}id") == "tab-validation-results"
        assert root.find("title").text == "Anchor validation and readiness results by stratum."
        tabular = root.find("tabular")
        cols = tabular.findall("col")
        assert [c.get("width") for c in cols] == ["20%"] + ["12%"] * 5
        assert cols[0].get("halign") == "left"
        header = tabular.find("row[@header='yes']")
        assert ["".join(c.itertext()) for c in header.findall("cell")] == [
            "Stratum", "Attempts", "Eligible-anchors", "Exact-agreement", "Ready", "Principal-flag",
        ]
        rows = read_display(out_a / "tab-validation-results.csv")[1]
        expected = []
        for r in rows:
            if r["suppressed"] == "1":
                expected.append([r["stratum"]] + [EM_DASH] * 5)
            else:
                expected.append([
                    r["stratum"], r["attempts"], r["eligible_anchors"],
                    f"{r['agreement_rate']} [{r['agreement_lo']}, {r['agreement_hi']}]",
                    f"{r['ready_rate']} [{r['ready_lo']}, {r['ready_hi']}]",
                    f"{r['principal_flag']} ({r['principal_flag_share']})",
                ])
        assert _data_rows(root) == expected
        assert expected[0][3] == "0.8261 [0.6286, 0.9302]"

    def test_notes_are_paragraph_fragments(self, out_a):
        for name in ("tab-items-note.ptx", "tab-validation-results-note.ptx",
                     "tab-corpus-flow-note.ptx"):  # the flow note: PR 3
            root = ET.fromstring((out_a / name).read_text(encoding="utf-8"))
            assert root.tag == "p" and root.text.strip()

    def test_the_manuscript_is_never_written(self, out_a):
        source = SCRIPT.read_text(encoding="utf-8")
        assert "paper1-manuscript" not in source
        assert all(p.parent == out_a for p in out_a.iterdir())


# ---------------------------------------------------------------------------
# (11) the cause counts, (12) the mismatch distance
# ---------------------------------------------------------------------------
class TestCausesAndDistance:
    def test_seven_rows_in_cause_order(self, out_a, archive):
        comments, rows = read_display(out_a / "data-anchor-causes-seven.csv")
        assert [r["cause"] for r in rows] == list(qc.ANCHOR_CAUSE_ORDER)
        assert [r["n_checks"] for r in rows] == ["0", "0", "2", "1", "2", "1", "1"]
        assert all(r["suppressed"] == "0" for r in rows)  # 21 students pooled
        assert rows[2]["share_of_all_checks"] == "0.0667"  # 2 / 30
        checks = list(csv.DictReader((archive / "exports" / "checks.csv").open(encoding="utf-8")))
        assert sum(int(r["n_checks"]) for r in rows) == sum(1 for c in checks if c["eligible"] == "0") == 7
        assert "# n_all_checks: 30" in comments and "# n_eligible: 23" in comments
        assert "# n_never_reconstructed: 0" in comments

    def test_mismatch_distance(self, out_a):
        [summary] = read_display(out_a / "data-mismatch-distance.csv")[1]
        assert summary == {
            "n_disagreeing": "4", "n_computed": "3", "n_not_computed": "1",
            "median_d": "1", "share_d_le_1": "1.0000", "suppressed": "0",
        }
        hist = read_display(out_a / "data-mismatch-distance-hist.csv")[1]
        assert [(r["d"], r["n"], r["suppressed"]) for r in hist] == [("1", "3", "0")]

    def test_sample_reporting(self, out_a):
        comments, data = read_display(out_a / "data-sample-reporting.csv")
        rows = {r["quantity"]: r["value"] for r in data}
        # rq3_subset_attempts mirrors contrast.read_population (PR #236 Copilot
        # review, 2026-10-19): ready, no excluded flag, first_check_correct
        # recorded, eventual_correct = 1. six attempts (DemoStudent1,
        # PaperStudent01, PaperStudent02, PaperStudentMany, PaperStudentOld,
        # PaperStudentQ2), not the nine ready attempts with an eligible anchor
        # (PaperStudent03-05 are ready, anchored and never correct).
        assert rows == {
            "n_students": "21", "n_attempts": "21", "n_checks": "30",
            "checks_no_move_logging": "0", "checks_resumed_attempt": "0",
            "attempts_with_eligible_anchor": "15", "rq3_subset_attempts": "6",
        }
        assert all(r["suppressed"] == "0" for r in data)
        definitions = next(c for c in comments if c.startswith("# definitions: "))
        assert "contrast.read_population" in definitions
        assert "first_check_correct recorded" in definitions and "eventual_correct = 1" in definitions
        assert "the pooled cohort is guarded like any cell" in definitions

    def test_every_pooled_file_is_shown_on_twenty_one_students(self, out_a, gen):
        """The pooled displays carry the cell guard's column and, at 21
        students, show every value (the guard's other branch is
        ``TestPooledGuard``)."""
        for name in POOLED_FILES:
            comments, rows = read_display(out_a / name)
            assert "suppressed" in columns_of(out_a / name), name
            assert rows and all(r["suppressed"] == "0" for r in rows), name
            definitions = next(c for c in comments if c.startswith("# definitions: "))
            assert "the pooled cohort is guarded like any cell" in definitions, name
        comments = read_display(out_a / "data-anchor-causes-six.csv")[0]
        assert "# n_all_checks: 30" in comments and "# n_eligible: 23" in comments
        assert "# n_never_reconstructed: 0" in comments

    def test_figure_rows_sorted_by_eligible_anchor_count(self, out_a):
        rows = read_display(out_a / "fig-validation-results.csv")[1]
        assert [r["item"] for r in rows] == [Q1, Q2, Q2_OLD]
        q1 = rows[0]
        assert (q1["eligible_anchors"], q1["agreeing"], q1["agreement_rate"]) == ("14", "11", "0.7857")
        assert (q1["attempts"], q1["n_ready"], q1["n_limited"], q1["n_exclude"]) == ("17", "6", "5", "6")
        assert q1["section"] == "1.1"


class TestRQ3Subset:
    """F3: ``rq3_subset_attempts`` is ``contrast.read_population``'s rule read
    off the export, not "ready with at least one eligible anchor"."""

    RQ3_STUDENTS = frozenset({
        "DemoStudent1", "PaperStudent01", "PaperStudent02",
        "PaperStudentMany", "PaperStudentOld", "PaperStudentQ2",
    })

    def test_equals_read_population_on_the_same_build(self, out_a, gen):
        conn = db.connect(":memory:")
        try:
            db.bootstrap(conn)
            build_database(conn)
            population = contrast.read_population(conn)
        finally:
            conn.close()
        rows = {r["quantity"]: r["value"] for r in read_display(out_a / "data-sample-reporting.csv")[1]}
        assert rows["rq3_subset_attempts"] == str(len(population)) == "6"
        assert {r.student_id for r in population} == self.RQ3_STUDENTS

    def test_the_predicate_on_the_export(self, gen, archive):
        arc = gen.Archive(archive, "A")
        admitted = gen.rq3_admitted(arc.attempts)
        assert {a["student_id"] for a in admitted} == self.RQ3_STUDENTS
        # The old reading (ready and anchored) admitted nine: the three
        # never-correct PaperStudent03-05 are ready, anchored and excluded.
        eligible = {c["attempt_id"] for c in arc.checks if c.get("eligible") == "1"}
        ready_anchored = [
            a for a in arc.attempts
            if a["readiness"] == qc.READY and a["attempt_id"] in eligible
        ]
        assert len(ready_anchored) == 9
        assert {a["student_id"] for a in ready_anchored} - self.RQ3_STUDENTS == {
            "PaperStudent03", "PaperStudent04", "PaperStudent05",
        }
        assert all(a["eventual_correct"] == "0" for a in ready_anchored if a["student_id"] not in self.RQ3_STUDENTS)
        # The configuration is the harness's own: a wider readiness admits more.
        wider = gen.rq3_admitted(arc.attempts, gen.ContrastConfig(readiness=(qc.READY, qc.LIMITED)))
        assert len(wider) >= len(admitted)
        none = gen.rq3_admitted(arc.attempts, gen.ContrastConfig(exclude_flags=("replay_ok",)))
        assert none == []

    def test_each_clause_of_the_predicate(self, gen):
        """The four clauses on hand-built export rows: the fixture has no
        ready, eventually-correct attempt without a scored first check, so
        the first_check_correct clause is exercised here."""
        base = {
            "readiness": qc.READY, "qc_flags_json": '["replay_ok"]',
            "first_check_correct": "0", "eventual_correct": "1",
        }
        assert gen.rq3_admitted([base]) == [base]
        for change in (
            {"readiness": qc.LIMITED},            # readiness not admitted
            {"qc_flags_json": '["check_mismatch"]'},  # see exclude_flags below
            {"first_check_correct": ""},          # no scored check (NULL in the table)
            {"eventual_correct": "0"},            # never correct
        ):
            row = {**base, **change}
            config = (
                gen.ContrastConfig(exclude_flags=("check_mismatch",))
                if "qc_flags_json" in change else None
            )
            assert gen.rq3_admitted([row], config) == [], change
        # An empty or absent flag list never excludes.
        assert gen.rq3_admitted([{**base, "qc_flags_json": ""}]) == [{**base, "qc_flags_json": ""}]


def _offboard_only_attempt(conn) -> None:
    """One more events row under golden_dnd.tab's import: DemoStudent1's
    second attempt on q1 (a new session, two hours later), a single drop onto
    slot 7 of the R = 3 board. Skipped in replay, so the attempt has X0 only
    and no configuration with an event."""
    [(import_id,)] = conn.execute(
        "SELECT import_id FROM raw_imports WHERE file_hash = ?", (_sha(FIXTURES / "golden_dnd.tab"),)
    ).fetchall()
    event = Event(
        student_id="DemoStudent1", session_id="101", problem_name=Q1,
        selection="dragNdrop-drop", action=f"{Q1}_drag1 -> {Q1}_drop7", input="",
        feedback_text="", feedback_classification="", problem_view="1",
        timestamp="2026-02-06 12:00:00.000000", course_id="university",
        class_id="1", week_no="8", chapter=None, subchapter=None,
    )
    assert write_events(conn, import_id, [event]) == (1, 0)


@pytest.fixture(scope="module")
def offboard_archive(tmp_path_factory):
    return build_fixture_archive(tmp_path_factory.mktemp("offboard"), post_ingest=_offboard_only_attempt)


class TestOffBoardOnlyAttempt:
    """F2: an attempt whose every move was skipped carries its import's hash
    (read off the attempt window), so the archive loads and runs."""

    def test_the_attempt_is_skipped_only_and_keyed(self, offboard_archive):
        attempts = list(csv.DictReader((offboard_archive / "exports" / "attempt_metrics.csv").open(encoding="utf-8")))
        configs = list(csv.DictReader((offboard_archive / "exports" / "configurations.csv").open(encoding="utf-8")))
        assert len(attempts) == 22
        [second] = [a for a in attempts if a["student_id"] == "DemoStudent1" and a["attempt_index"] == "2"]
        assert second["n_moves"] == "0" and second["session_id"] == "101"
        own = [c for c in configs if c["attempt_id"] == second["attempt_id"]]
        assert [(c["seq"], c["event_id"]) for c in own] == [("0", "")]  # X0 only, no event
        expected = _sha(FIXTURES / "golden_dnd.tab")
        assert second["import_sha256"] == expected
        assert own[0]["import_sha256"] == expected
        assert all(a["import_sha256"] for a in attempts) and all(c["import_sha256"] for c in configs)

    def test_the_generator_accepts_the_archive(self, gen, offboard_archive, tmp_path, capsys):
        out = tmp_path / "out"
        assert gen.main(["--archive", str(offboard_archive), "--out", str(out)]) == 0, capsys.readouterr().err
        assert sorted(p.name for p in out.iterdir()) == sorted(OUTPUT_FILES)
        rows = {r["quantity"]: r["value"] for r in read_display(out / "data-sample-reporting.csv")[1]}
        assert (rows["n_students"], rows["n_attempts"]) == ("21", "22")


SMALL_FILES = ("golden_dnd.tab", "golden_dnd_training_anomalies.tab")


@pytest.fixture(scope="module")
def small_archive(tmp_path_factory):
    """The two-fixture archive (four students, two offerings of two; its own
    archive directory)."""
    return build_fixture_archive(tmp_path_factory.mktemp("small"), files=SMALL_FILES)


@pytest.fixture(scope="module")
def small_out(gen, small_archive, tmp_path_factory):
    """The displays of the two-fixture archive under convention A."""
    out = tmp_path_factory.mktemp("small-displays") / "A"
    assert gen.main(["--archive", str(small_archive), "--out", str(out)]) == 0
    return out


@pytest.fixture(scope="module")
def small_out_b(gen, small_archive, tmp_path_factory):
    """The same displays under convention B."""
    out = tmp_path_factory.mktemp("small-displays") / "B"
    assert gen.main([
        "--archive", str(small_archive), "--out", str(out), "--items-convention", "B",
    ]) == 0
    return out


def _flow_note_prose(note: str) -> str:
    """The note before its guard sentence (whose offering names carry
    digits of their own)."""
    prose, sep, _ = note.partition("Per-offering counts appear")
    assert sep
    return prose


class TestPooledGuard:
    """F4: the pooled cohort is a cell under the guard. Two fixtures, four
    students in two offerings of two: every pooled display is suppressed."""

    def test_four_students_pooled(self, small_out, gen):
        strata = by(read_display(small_out / "tab-validation-results.csv")[1], "stratum")
        overall = strata["All offerings"]
        assert overall["suppressed"] == "1"
        assert all(overall[c] == "" for c in gen.VALIDATION_MEASURES)
        offerings = read_display(small_out / "data-validation-by-offering.csv")[1]
        assert [r["offering"] for r in offerings] == ["Y00-B03-P11", "Y00-B03-P13"]
        assert all(r["suppressed"] == "1" for r in offerings)

    def test_sample_reporting_is_blank(self, small_out):
        rows = read_display(small_out / "data-sample-reporting.csv")[1]
        assert [r["quantity"] for r in rows] == [
            "n_students", "n_attempts", "n_checks", "checks_no_move_logging",
            "checks_resumed_attempt", "attempts_with_eligible_anchor", "rq3_subset_attempts",
        ]
        assert all(r["value"] == "" and r["suppressed"] == "1" for r in rows)

    def test_cause_files_are_blank(self, small_out):
        for name in ("data-anchor-causes-seven.csv", "data-anchor-causes-six.csv"):
            comments, rows = read_display(small_out / name)
            assert rows and all(r["suppressed"] == "1" for r in rows), name
            assert all(r["n_checks"] == "" and r["share_of_all_checks"] == "" for r in rows), name
            assert "# n_all_checks: " in comments and "# n_eligible: " in comments, name
            assert "# n_never_reconstructed: " in comments, name
            assert not any(re.fullmatch(r"# n_(all_checks|eligible|never_reconstructed): \d+", c) for c in comments), name
        seven = read_display(small_out / "data-anchor-causes-seven.csv")[1]
        assert [r["cause"] for r in seven] == list(qc.ANCHOR_CAUSE_ORDER)  # the rows stay

    def test_distance_files_are_blank(self, small_out):
        [summary] = read_display(small_out / "data-mismatch-distance.csv")[1]
        assert summary == {
            "n_disagreeing": "", "n_computed": "", "n_not_computed": "",
            "median_d": "", "share_d_le_1": "", "suppressed": "1",
        }
        assert read_display(small_out / "data-mismatch-distance-hist.csv")[1] == [
            {"d": "", "n": "", "suppressed": "1"},
        ]

    def test_pooled_diagnostics_row_is_suppressed(self, small_out, gen):
        rows = read_display(small_out / "data-anchor-diagnostics.csv")[1]
        assert rows[0]["scope"] == "pooled"
        assert rows[0]["suppressed"] == "1" and rows[0]["n_disagreeing"] == ""
        assert all(rows[0][c] == "" for c in gen.CATEGORY_ORDER)
        assert all(r["suppressed"] == "1" for r in rows[1:])

    def test_no_pooled_file_prints_a_digit_value(self, small_out):
        # Every column but the row keys and labels (which carry digits of
        # their own: rq3_subset_attempts, the prose labels) is blank.
        # The flow table's column and step keys carry digits of their own (PR 3).
        labels = {"suppressed", "quantity", "cause", "components", "label", "column", "step"}
        for name in POOLED_FILES:
            for row in read_display(small_out / name)[1]:
                for key, value in row.items():
                    if key not in labels:
                        assert value == "", (name, key, value)

    def test_corpus_flow_is_blank(self, small_out):
        """PR 3: four students, so every valued cell of both flow files is
        suppressed (blank value, blank student count), the fragment's data
        cells are all dashes and the note names every table."""
        for name, n in (("tab-corpus-flow.csv", 30), ("data-corpus-flow-by-offering.csv", 60)):
            rows = read_display(small_out / name)[1]
            assert len(rows) == n, name
            for r in rows:
                expected = NO_VALUE if (r["quantity"], r["column"]) in J10_CELLS else HIDDEN
                assert triple(r) == expected, (name, r["quantity"], r["column"])
        root = ET.fromstring((small_out / "tab-corpus-flow.ptx").read_text(encoding="utf-8"))
        assert _data_rows(root) == [[label] + [EM_DASH] * 5 for label in FLOW_LABELS]
        note = ET.fromstring((small_out / "tab-corpus-flow-note.ptx").read_text(encoding="utf-8")).text
        assert note.rstrip().endswith(
            "suppressed (fewer than five students): pooled (every cell), "
            "Y00-B03-P11 (every cell), Y00-B03-P13 (every cell)."
        )
        # The note's numbers obey the same guard (PR 3 Copilot round): every
        # flow-backed count of S1-S5 is the dash; the alias count (a count of
        # identifiers, not a cell) is the only digit left before S6.
        for fragment in (
            "Items are logged identifiers (convention A): — logged identifiers at Raw correspond "
            "to — canonical items after the identifier alias, — to — at Ready;",
            "item traces are student-identifier pairs (— at Raw; — student-item pairs after the "
            "alias)",
            "Governance removed — rows of instructor, researcher and test accounts",
            "source alignment removed — attempts on a logged identifier with no board",
            "Event parsing removed — rows (— checks) whose payload did not parse; state validity "
            "removed — rows (— checks) whose payload",
        ):
            assert fragment in note, fragment[:40]
        prose = re.sub(r"\d+ identifier\(s\) aliased", "", _flow_note_prose(note))
        assert not re.search(r"\d", prose)

    def test_corpus_flow_note_is_blank_under_b(self, small_out_b):
        """The same under convention B, whose S1 and traces clause read the
        canonical cells: no digit at all before the guard sentence."""
        note = _note_text(small_out_b / "tab-corpus-flow-note.ptx")
        for fragment in (
            "Items are canonical items (convention B): — items at Raw, logged under — "
            "identifiers; — at Ready.",
            "item traces are student-item pairs after the alias (— at Raw; — student-identifier "
            "pairs before it)",
            "Governance removed — rows", "source alignment removed — attempts",
            "Event parsing removed — rows (— checks)", "state validity removed — rows (— checks)",
        ):
            assert fragment in note, fragment[:40]
        assert not re.search(r"\d", _flow_note_prose(note))
        assert note.rstrip().endswith(
            "pooled (every cell), Y00-B03-P11 (every cell), Y00-B03-P13 (every cell)."
        )


# ---------------------------------------------------------------------------
# (16) tab-corpus-flow from the ledger's flow export (E-08's one-commit
# follow-up, ruling R8 (ii); PR 3 on PR #239's `flow` dataset)
# ---------------------------------------------------------------------------
# The attrition fixture as a fourth, one-import archive: the roles fixture
# imported and the regime row added on build_database's post_ingest hook, so
# governance (step 1) and the regime-dated check are in play. Not exposed;
# the pseudonym is the roles fixture's own made-up column value (ruling Q1).
ATTRITION = "golden_dnd_attrition.tab"
ATTRITION_PSEUDONYM = "Y99-B03-P99"
PSEUDONYM[ATTRITION] = ATTRITION_PSEUDONYM
TERM[ATTRITION] = "Spring 2026"
# The rulings file and the regime row _attrition_setup adds: free text the
# archive may carry (the regime reason sits in the manifest's logging_regimes
# rows; the rulings file's note column is discarded at read, R13) and no
# display may (the privacy test's needles).
ATTRITION_ROLES = "account_roles_fixture.csv"
ATTRITION_REGIME_REASON = "fixture: one check logged before the component logged moves"

FLOW_FILES = ("tab-corpus-flow.csv", "data-corpus-flow-by-offering.csv")
# The manuscript's rows and columns, and the ledger step each column reads
# (R9 (b): Raw 0, Authorized 1, Source-aligned 3, Reconstructed 5, Ready 6;
# steps 2 and 4 fold into the column that follows them).
FLOW_COLUMN_NAMES = ("Raw", "Authorized", "Source-aligned", "Reconstructed", "Ready")
FLOW_STEPS = ("0", "1", "3", "5", "6")
# The three cells with no value (J10): attempts exist from reconstruction on.
J10_CELLS = frozenset({("Attempts", "Raw"), ("Attempts", "Authorized"), ("Attempts", "Source-aligned")})
NO_VALUE = ("", "", "0")  # (value, n_students, suppressed) of a J10 cell: blank, unsuppressed
HIDDEN = ("", "", "1")    # of a suppressed cell: blank, its student count withheld too

# The attrition archive's thirty cells, (value, n_students, suppressed) by row
# and column: the ledger's own pins (test_dnd_attrition_ledger.FLOW_POOLED,
# rows_out and n_students at steps 0 / 1 / 3 / 5 / 6), each re-derived here
# from golden_dnd_attrition.tab, 71 rows: 44 drops, 23 checks, 1 reset, 1
# page row (the staff account's) and AttritionSA's shortanswer + mChoice pair;
# sixteen ids, every one with a DnD row. Under the roles fixture
# (AttritionStaff1 ruled instructor, exclude) and the regime row 2026-06-02.
# * Students: 16; -1 Staff1 at governance; -1 NoBoard at source alignment (q9
#   has no board, no alias); -5 at readiness (Malformed, DupCard, Regime,
#   Limited, NoCheck, Resumed keeps attempt 1). n_students = the value.
# * Items (convention A, items_logged; items_canonical is identical, no
#   alias): q1 and q9; q9 lost at step 3; q1 has ready attempts (J9).
#   n_students = the surviving students of the step (16, 15, 14, 14, 9).
# * Item traces: every student on exactly one item, so the Students row.
# * Attempts: no value before Reconstructed (J10). 16 attempts (Resumed holds
#   two); -1 NoBoard (missing_source_map) at step 3; none at steps 4-5 (R9 b);
#   -6 at readiness (exclude DupCard and Resumed #2; limited Malformed, Regime,
#   Limited, NoCheck) → 9. n_students 14 (fifteen students less NoBoard), 9.
# * Events: 71; -5 Staff1 (page, 3 drops, check) → 66; -2 shortanswer +
#   mChoice → 64; -3 NoBoard (2 drops, check) → 61; -2 at parsing (Malformed's
#   truncated check, Regime's `not json` check) → 59; -2 at validity (DupCard's
#   and Resumed #2's duplicate-card checks) → 57; -13 at readiness, the rows the
#   six non-ready attempts still hold (Malformed 4, DupCard 2, Resumed #2 1,
#   Regime 0, Limited 4, NoCheck 2) → 44 = the nine ready attempts' rows
#   (RuledKept 4, Clean1-3 12, Revised 6, SA 2, Resumed #1 4, Many 9,
#   Takeback 7). n_students 16, 15, 14, 13 (Regime's only row went at step 4), 9.
# * Checks: 23 dragNdrop rows; -1 Staff1 → 22; -1 NoBoard → 21; -2 at parsing
#   → 19; -2 at validity → 17; -2 at readiness (Malformed's agreeing check,
#   Limited's) → 15. n_students 15 (everyone but NoCheck), 14, 13, 11 (Regime
#   went at step 4, DupCard at 5), 9.
ATTRITION_FLOW = {
    "Students": (("16", "16", "0"), ("15", "15", "0"), ("14", "14", "0"), ("14", "14", "0"), ("9", "9", "0")),
    "Items": (("2", "16", "0"), ("2", "15", "0"), ("1", "14", "0"), ("1", "14", "0"), ("1", "9", "0")),
    "Item traces": (("16", "16", "0"), ("15", "15", "0"), ("14", "14", "0"), ("14", "14", "0"), ("9", "9", "0")),
    "Attempts": (NO_VALUE, NO_VALUE, NO_VALUE, ("15", "14", "0"), ("9", "9", "0")),
    "Events": (("71", "16", "0"), ("66", "15", "0"), ("61", "14", "0"), ("57", "13", "0"), ("44", "9", "0")),
    "Checks": (("23", "15", "0"), ("22", "14", "0"), ("21", "13", "0"), ("17", "11", "0"), ("15", "9", "0")),
}

# The module archive's cells (golden_dnd.tab P11 14 rows, the anomalies
# fixture P13 16, the paper fixture P17 79: 109 rows, 30 checks, 20 page rows,
# one shortanswer; 21 students each on one item; logged identifiers q1, q2,
# q2-old (aliased to q2) and the board-less q3; no roles loaded, so
# governance withholds nothing and Authorized = Raw in every row).
# * Students: -1 PaperStudentNoBoard at step 3; DemoStudent2 loses every DnD
#   row by step 5 (the garbage-action drop and the truncated check at step 4,
#   the other-question_drag1 check and the off-board drag9 move at step 5) but
#   keeps its attempt (R9 b; Q15), so 20 at Reconstructed; ready: DemoStudent1,
#   PaperStudent01-05, Many, Q2, Old.
# * Items: four logged, q3 lost at step 3, ready attempts on q1, q2 and q2-old
#   (J9); canonical q1, q2, q3 → q1, q2 under B.
# * Item traces: no student spans an alias pair, so one trace per student
#   under both readings (the Students row).
# * Attempts: 21, -1 missing_source_map (NoBoard), none at steps 4-5, 9 ready.
# * Events: 109; step 2 -20 page, -1 shortanswer → 88; step 3 -3 (NoBoard's
#   two drops and check) → 85; step 4 -3 (DemoStudent2's garbage-action drop
#   and truncated check, PaperStudentMalformed's check) → 82; step 5 -7 (the
#   five decode-cause checks: DemoStudent2's other-question_drag1, DupCard,
#   UnknownCard, UnknownZone, OffboardSlot; two off-board moves: DemoStudent2's
#   drag9, PaperStudentOffboard's drop4 on the R = 3 board) → 75; Ready = the
#   nine ready attempts' rows, all surviving (a ready attempt has no off-board
#   move and no bad check): DemoStudent1 8 + PaperStudent01 4 + 02 4 + 03 3 +
#   04 3 + 05 4 + Many 12 + Q2 4 + Old 4 = 46.
# * Checks: 30; -1 NoBoard's (eligible, lost with its attempt) → 29; -2
#   malformed (DemoStudent2's truncated, PaperStudentMalformed's) → 27; -5
#   decode causes (duplicate_card 1, unknown_card 2, unknown_zone 1,
#   offboard_slot 1. The PR #236 pin "0, 0, 2, 1, 2, 1, 1") → 22; Ready =
#   DemoStudent1 2 + PaperStudent01-05 5 + Many 6 + Q2 1 + Old 1 = 15.
MODULE_FLOW_A = {
    "Students": ("21", "21", "20", "20", "9"),
    "Items": ("4", "4", "3", "3", "3"),
    "Item traces": ("21", "21", "20", "20", "9"),
    "Attempts": ("", "", "", "20", "9"),
    "Events": ("109", "109", "85", "75", "46"),
    "Checks": ("30", "30", "29", "22", "15"),
}
MODULE_FLOW_B = {**MODULE_FLOW_A, "Items": ("3", "3", "2", "2", "2")}
# The paper offering alone (17 students, 79 rows, 22 checks): its 17 page rows
# at step 2 → 62; -3 NoBoard (two drops and a check; the one attempt lost) at
# step 3 → 59 rows, 21 checks; step 4 -1 (PaperStudentMalformed's check) → 58;
# step 5 -5 (four decode-cause checks and PaperStudentOffboard's drop4 move)
# → 53 rows, 16 checks; Ready = 46 - DemoStudent1's 8 rows = 38, 15 -
# DemoStudent1's 2 checks = 13; 8 students at Ready (PaperStudent01-05, Many,
# Q2, Old).
P17_FLOW = {
    "Students": ("17", "17", "16", "16", "8"),
    "Attempts": ("", "", "", "16", "8"),
    "Events": ("79", "79", "59", "53", "38"),
    "Checks": ("22", "22", "21", "16", "13"),
}


def _attrition_setup(conn) -> None:
    """test_dnd_attrition_fixture.attrition's order on build_database's hook:
    the roles import before derive (R1 a), the regime row after the ingest,
    keyed by the file's sha256; the board is build_database's Q1 (q9 stays
    board-less; Q2 is registered unused, harmless, arc.items() lists logged
    identifiers with rows only); no Q2_OLD event, so no alias."""
    account_roles.import_roles(conn, FIXTURES / ATTRITION_ROLES)
    logging_regimes.add_regime(
        conn, _sha(FIXTURES / ATTRITION), "2026-06-02", reason=ATTRITION_REGIME_REASON,
    )


@pytest.fixture(scope="module")
def attrition_archive(tmp_path_factory):
    return build_fixture_archive(
        tmp_path_factory.mktemp("attrition"), files=(ATTRITION,), post_ingest=_attrition_setup,
    )


@pytest.fixture(scope="module")
def attrition_out(gen, attrition_archive, tmp_path_factory):
    out = tmp_path_factory.mktemp("attrition-displays") / "A"
    assert gen.main(["--archive", str(attrition_archive), "--out", str(out)]) == 0
    return out


@pytest.fixture(scope="module")
def attrition_fingerprint(attrition_archive):
    return manifest.read_manifest(attrition_archive / "manifest.yaml")["fingerprint"]


def flow_index(rows: list[dict]) -> dict[tuple[str, str, str], dict]:
    """{(offering, quantity, column): row} of a flow CSV; the pooled file's
    offering is ''. Every key occurs once."""
    index: dict[tuple[str, str, str], dict] = {}
    for r in rows:
        key = (r.get("offering", ""), r["quantity"], r["column"])
        assert key not in index, key
        index[key] = r
    return index


def triple(r: dict) -> tuple[str, str, str]:
    return (r["value"], r["n_students"], r["suppressed"])


def _assert_no_needle(name: str, text: str, needles) -> None:
    """Count the private values in `text`, then drop the text and the
    needles before asserting, so a failure names the file and a count only
    (M6: pytest prints a failing frame's arguments)."""
    leaked = sum(1 for needle in needles if needle and needle in text)
    del text, needles
    assert leaked == 0, f"{leaked} private value(s) in {name}"


def _note_text(path: Path) -> str:
    root = ET.fromstring(path.read_text(encoding="utf-8"))
    assert root.tag == "p" and root.text.strip()
    return root.text


class TestCorpusFlow:
    """tab-corpus-flow: every cell pinned on the attrition archive (the
    complete pin), the fragment, the note and the by-offering file; then
    the module archive's cells under both conventions, its by-offering
    guard and the manuscript match."""

    # -- the attrition archive: every cell -------------------------------------------
    def test_every_cell_of_the_pooled_table(self, attrition_out):
        path = attrition_out / "tab-corpus-flow.csv"
        assert columns_of(path) == ["quantity", "column", "step", "value", "n_students", "suppressed"]
        comments, rows = read_display(path)
        # The manuscript's row order, then its column order (30 cells).
        assert [(r["quantity"], r["column"]) for r in rows] == [
            (q, c) for q in FLOW_LABELS for c in FLOW_COLUMN_NAMES
        ]
        index = flow_index(rows)
        for label, cells in ATTRITION_FLOW.items():
            for column, step, expected in zip(FLOW_COLUMN_NAMES, FLOW_STEPS, cells):
                row = index[("", label, column)]
                assert row["step"] == step, (label, column)
                assert triple(row) == expected, (label, column)
        # Nine students at Ready: no cell is suppressed; the only blank values
        # are the three J10 cells.
        assert all(r["suppressed"] == "0" for r in rows)
        assert {(r["quantity"], r["column"]) for r in rows if r["value"] == ""} == J10_CELLS
        definitions = next(c for c in comments if c.startswith("# definitions: "))
        for clause in ("Raw (ledger step 0, raw)", "Authorized (step 1, governance)",
                       "Source-aligned (step 3, source_alignment", "Reconstructed (step 5, state_validity",
                       "Ready (step 6, readiness)", "ruling J10",
                       "the pooled cohort is guarded like any cell"):
            assert clause in definitions, clause

    def test_fragment(self, attrition_out, attrition_fingerprint):
        text = (attrition_out / "tab-corpus-flow.ptx").read_text(encoding="utf-8")
        assert text.startswith(
            f"<!-- run_id: {RUN_ID}; manifest_fingerprint: {attrition_fingerprint}; "
            "generator: paper_tables.py 1.1; items_convention: A -->\n<table"
        )
        # The manuscript's spellings of the column specifications.
        assert '<col width="16%" halign="right"/>' in text and '<col width="12%" />' in text
        root = ET.fromstring(text)
        assert root.tag == "table" and root.get(XML_ID) == "tab-corpus-flow"
        assert root.find("title").text == "Corpus flow from raw records to primary analysis."
        tabular = root.find("tabular")
        assert tabular.get("halign") == "center"
        cols = tabular.findall("col")
        assert [c.get("width") for c in cols] == ["16%", "12%", "14%", "14%", "14%", "12%"]
        assert cols[0].get("halign") == "right" and all(c.get("halign") is None for c in cols[1:])
        header = tabular.find("row[@header='yes']")
        assert ["".join(c.itertext()) for c in header.findall("cell")] == [
            "Quantity", "Raw", "Author-ized", "Source-aligned", "Recon-structed", "Ready",
        ]
        assert [ln.text for ln in header.findall("cell")[2].findall("line")] == ["Author-", "ized"]
        # Every data cell is the CSV's value, an em dash where the value is
        # blank (a J10 cell and a suppressed cell render alike; Q4).
        rows = read_display(attrition_out / "tab-corpus-flow.csv")[1]
        expected = [
            [label] + [EM_DASH if r["value"] == "" else r["value"] for r in rows if r["quantity"] == label]
            for label in FLOW_LABELS
        ]
        assert _data_rows(root) == expected
        assert expected[0] == ["Students", "16", "15", "14", "14", "9"]
        assert expected[3] == ["Attempts", EM_DASH, EM_DASH, EM_DASH, "15", "9"]
        assert expected[4] == ["Events", "71", "66", "61", "57", "44"]

    def test_note(self, attrition_out):
        note = _note_text(attrition_out / "tab-corpus-flow-note.ptx")
        for sentence in (
            # S1 (convention A): items_logged / items_canonical at Raw and at
            # Ready, and the alias count tab-items-note prints (0: no alias).
            "Items are logged identifiers (convention A): 2 logged identifiers at Raw correspond "
            "to 2 canonical items after the identifier alias, 1 to 1 at Ready; 0 identifier(s) "
            "aliased to successor(s).",
            # S2 (R11 a): the traces definition, 16 under both readings.
            "Students are distinct platform accounts with at least one card-to-zone row (a drop, "
            "a check or a reset); item traces are student-identifier pairs (16 at Raw; 16 "
            "student-item pairs after the alias); events are every row of the imported exports; "
            "checks are the rows that carry a checked configuration.",
            # S3: the Raw-to-Authorized difference of the Events row (71 - 66);
            # the account count stays the manuscript's own fact (Q7).
            "Governance removed 5 rows of instructor, researcher and test accounts before any "
            "analysis count (the Raw-to-Authorized difference of the Events row).",
            # S4: the step-3 attempt loss (NoBoard) lives here, not in the table (Q2).
            "Attempts are counted from reconstruction onward; source alignment removed 1 attempt "
            "on a logged identifier with no board after the alias (missing_source_map) before the "
            "Reconstructed count.",
            # S5 (R9 b; J8): steps 4 and 5 lose rows and checks, never an attempt.
            "Event parsing removed 2 rows (2 checks) whose payload did not parse; state validity "
            "removed 2 rows (2 checks) whose payload did not decode to a unique, board-valid "
            "configuration or named an off-board card or slot;",
            "so no attempt is lost at either step. These losses are decode outcomes of each "
            "check's verbatim payload, re-decoded at read time; the cause counts of "
            "data-anchor-causes are the stored causes, which put a structural cause first.",
            # S6: the guard sentence, nothing suppressed.
            "Per-offering counts appear only where every cell contains at least five students; "
            "no cell is suppressed.",
        ):
            assert sentence in note, sentence[:48]
        assert "suppressed (fewer than five students)" not in note

    def test_by_offering_equals_pooled_on_one_import(self, attrition_out):
        path = attrition_out / "data-corpus-flow-by-offering.csv"
        assert columns_of(path) == [
            "offering", "quantity", "column", "step", "value", "n_students", "suppressed",
        ]
        comments, rows = read_display(path)
        assert len(rows) == 30 and {r["offering"] for r in rows} == {ATTRITION_PSEUDONYM}
        pooled = flow_index(read_display(attrition_out / "tab-corpus-flow.csv")[1])
        for r in rows:
            twin = pooled[("", r["quantity"], r["column"])]
            assert (triple(r), r["step"]) == (triple(twin), twin["step"]), (r["quantity"], r["column"])
        definitions = next(c for c in comments if c.startswith("# definitions: "))
        assert "offering = the pseudonym of the ledger's import_sha256 key" in definitions

    def test_privacy_on_the_attrition_archive(self, attrition_out, attrition_fingerprint):
        """The TestPrivacy loop on this archive: no student id, no fixture
        or rulings file name, no path, no sha256 but the fingerprint (the
        import's, the roles file's and the regime set's are all 64-hex), and
        none of the free text the setup feeds in (the rulings file's note
        cells, the regime reason). The pseudonym is the by-offering key (M5)
        and appears only as itself. The ids and the notes are read from the
        fixtures and never named here; every leak check reduces to a count
        before asserting, so a failure names the file and a number, never a
        slice of the file (M6)."""
        with open(FIXTURES / ATTRITION, newline="", encoding="utf-8") as fh:
            ids = {r["Anon Student Id"] for r in csv.DictReader(fh, delimiter="\t")}
        n_ids = len(ids)
        assert n_ids == 16
        with open(FIXTURES / ATTRITION_ROLES, newline="", encoding="utf-8") as fh:
            notes = [r["note"] for r in csv.DictReader(ln for ln in fh if not ln.startswith("#"))]
        assert len(notes) == 2 and all(notes)
        needles = [*ids, *notes, ATTRITION_REGIME_REASON, ATTRITION, ATTRITION_ROLES,
                   _sha(FIXTURES / ATTRITION), str(FIXTURES), str(FIXTURES.resolve())]
        assert sorted(p.name for p in attrition_out.iterdir()) == sorted(OUTPUT_FILES)
        for path in attrition_out.iterdir():
            text = path.read_text(encoding="utf-8")
            foreign = len(set(SHA256.findall(text)) - {attrition_fingerprint})
            assert foreign == 0, path.name
            bare = text.count("Y99") - text.count(ATTRITION_PSEUDONYM)
            assert bare == 0, path.name
            _assert_no_needle(path.name, text, needles)
            assert not FORBIDDEN_COLUMNS & set(columns_of(path)), path.name

    # -- the module archive: the cells under both conventions --------------------------
    @pytest.mark.parametrize("convention", ("A", "B"))
    def test_module_archive_cells(self, out_a, out_b, convention):
        out = out_a if convention == "A" else out_b
        table = MODULE_FLOW_A if convention == "A" else MODULE_FLOW_B
        rows = read_display(out / "tab-corpus-flow.csv")[1]
        assert len(rows) == 30
        index = flow_index(rows)
        for label, values in table.items():
            assert [index[("", label, c)]["value"] for c in FLOW_COLUMN_NAMES] == list(values), label
        assert [index[("", "Attempts", c)]["step"] for c in FLOW_COLUMN_NAMES] == list(FLOW_STEPS)
        # 21 students pooled: every valued cell shown, the J10 cells blank.
        assert all(r["suppressed"] == "0" for r in rows)
        assert {(r["quantity"], r["column"]) for r in rows if r["value"] == ""} == J10_CELLS
        # No roles loaded: governance withholds nothing.
        for label in FLOW_LABELS:
            assert index[("", label, "Raw")]["value"] == index[("", label, "Authorized")]["value"], label
        # The Items and Item traces rows depend on the convention, so the stamp
        # carries it on all four flow outputs: the CSVs' header comment and
        # the fragments' one-line XML comment.
        for name in FLOW_FILES:
            assert f"# items_convention: {convention}" in (out / name).read_text(encoding="utf-8"), name
        for name in ("tab-corpus-flow.ptx", "tab-corpus-flow-note.ptx"):
            first = (out / name).read_text(encoding="utf-8").splitlines()[0]
            assert first.endswith(f"; items_convention: {convention} -->"), name

    def test_module_archive_notes(self, out_a, out_b):
        a = _note_text(out_a / "tab-corpus-flow-note.ptx")
        b = _note_text(out_b / "tab-corpus-flow-note.ptx")
        # S1 under A (O-123's form) and under B; items_logged 4 / 3 at Raw /
        # Ready, items_canonical 3 / 2, one alias (q2-old → q2).
        assert ("Items are logged identifiers (convention A): 4 logged identifiers at Raw correspond "
                "to 3 canonical items after the identifier alias, 3 to 2 at Ready; 1 identifier(s) "
                "aliased to successor(s).") in a
        assert "Items are canonical items (convention B): 3 items at Raw, logged under 4 identifiers; 2 at Ready." in b
        # S2's traces clause follows the convention; 21 under both readings.
        assert "item traces are student-identifier pairs (21 at Raw; 21 student-item pairs after the alias)" in a
        assert "item traces are student-item pairs after the alias (21 at Raw; 21 student-identifier pairs before it)" in b
        for note in (a, b):
            # S3-S5 from the module archive's losses: none at governance, NoBoard
            # at step 3, 3 rows / 2 checks at step 4, 7 rows / 5 checks at step 5.
            assert "Governance removed 0 rows of instructor, researcher and test accounts" in note
            assert "source alignment removed 1 attempt on a logged identifier with no board" in note
            assert "Event parsing removed 3 rows (2 checks)" in note
            assert "state validity removed 7 rows (5 checks)" in note
            # S6: the two two-student offerings, every valued cell; the paper
            # offering and the pooled table are shown and unnamed (Q12).
            assert note.rstrip().endswith(
                "Per-offering counts appear only where every cell contains at least five students; "
                "suppressed (fewer than five students): Y00-B03-P11 (every cell), Y00-B03-P13 (every cell)."
            )
            assert "Y01-B03-P17" not in note
            assert "pooled (every cell)" not in note and "pooled:" not in note

    def test_by_offering_guard_on_the_module_archive(self, out_a):
        rows = read_display(out_a / "data-corpus-flow-by-offering.csv")[1]
        assert len(rows) == 90
        assert [r["offering"] for r in rows[::30]] == ["Y00-B03-P11", "Y00-B03-P13", "Y01-B03-P17"]
        index = flow_index(rows)
        for pseud in ("Y00-B03-P11", "Y00-B03-P13"):  # two students each
            for label in FLOW_LABELS:
                for column in FLOW_COLUMN_NAMES:
                    expected = NO_VALUE if (label, column) in J10_CELLS else HIDDEN
                    assert triple(index[(pseud, label, column)]) == expected, (pseud, label, column)
        # The paper offering is shown in every valued cell: 17 students at Raw,
        # 8 at Ready (PaperStudent01-05, Many, Q2, Old).
        for label in FLOW_LABELS:
            for column in FLOW_COLUMN_NAMES:
                row = index[("Y01-B03-P17", label, column)]
                if (label, column) in J10_CELLS:
                    assert triple(row) == NO_VALUE, (label, column)
                else:
                    assert row["suppressed"] == "0" and row["value"] != "", (label, column)
                    assert int(row["n_students"]) >= 5, (label, column)
        for label, values in P17_FLOW.items():
            assert [index[("Y01-B03-P17", label, c)]["value"] for c in FLOW_COLUMN_NAMES] == list(values), label
        assert index[("Y01-B03-P17", "Students", "Raw")]["n_students"] == "17"
        assert index[("Y01-B03-P17", "Students", "Ready")]["n_students"] == "8"

    # -- the per-cell guard: one cell of a shown table hidden (Q4, Q12) ----------------
    def test_the_guard_names_one_cell_of_a_shown_table(self, gen, archive, tmp_path):
        """The per-cell branch, the one a small offering with five students
        at Raw and fewer at Ready takes, which no fixture archive reaches
        (every table there is all shown or all hidden): a copy of the module
        archive whose flow export reads 4 students on the paper offering's
        Students at Ready (the ledger's 8) and 3 on the pooled Checks at
        Ready (the ledger's 9). The attempts' rows_out, which the
        cross-check reads, are untouched, so the archive is accepted."""
        run = copy_archive(archive, tmp_path)
        paper = _sha(FIXTURES / "golden_dnd_paper_tables.tab")

        def edit(rows):
            students = next(
                r for r in rows
                if r["breakdown"] == "by_offering" and r["key"] == paper
                and r["quantity"] == "students" and r["step"] == "6"
            )
            checks = _flow_row(rows, "pooled", "checks", "6")
            assert (students["n_students"], checks["n_students"]) == ("8", "9")
            students["n_students"], checks["n_students"] = "4", "3"
            return rows

        _rewrite_flow(run, edit)
        out = tmp_path / "out"
        assert gen.main(["--archive", str(run), "--out", str(out)]) == 0
        pooled = flow_index(read_display(out / "tab-corpus-flow.csv")[1])
        assert {k for k, r in pooled.items() if r["suppressed"] == "1"} == {("", "Checks", "Ready")}
        assert triple(pooled[("", "Checks", "Ready")]) == HIDDEN
        assert [pooled[("", "Checks", c)]["value"] for c in FLOW_COLUMN_NAMES] == ["30", "30", "29", "22", ""]
        offerings = flow_index(read_display(out / "data-corpus-flow-by-offering.csv")[1])
        paper_rows = {k: r for k, r in offerings.items() if k[0] == "Y01-B03-P17"}
        assert {k for k, r in paper_rows.items() if r["suppressed"] == "1"} == {
            ("Y01-B03-P17", "Students", "Ready"),
        }
        assert triple(paper_rows[("Y01-B03-P17", "Students", "Ready")]) == HIDDEN
        for (_, label, column), r in paper_rows.items():
            if (label, column) not in J10_CELLS and (label, column) != ("Students", "Ready"):
                assert r["suppressed"] == "0" and r["value"] != "", (label, column)
        for pseud in ("Y00-B03-P11", "Y00-B03-P13"):  # hidden as before
            assert all(
                r["suppressed"] == "1"
                for k, r in offerings.items() if k[0] == pseud and (k[1], k[2]) not in J10_CELLS
            )
        data = _data_rows(ET.fromstring((out / "tab-corpus-flow.ptx").read_text(encoding="utf-8")))
        assert data[0] == ["Students", "21", "21", "20", "20", "9"]
        assert data[5] == ["Checks", "30", "30", "29", "22", EM_DASH]
        # The names: the pooled table's cell first, then each offering's in
        # order (per cell where the table is otherwise shown).
        note = _note_text(out / "tab-corpus-flow-note.ptx")
        assert note.rstrip().endswith(
            "Per-offering counts appear only where every cell contains at least five students; "
            "suppressed (fewer than five students): pooled: Checks at Ready, "
            "Y00-B03-P11 (every cell), Y00-B03-P13 (every cell), Y01-B03-P17: Students at Ready."
        )

    @pytest.mark.parametrize("convention", ("A", "B"))
    def test_the_note_redacts_what_the_guard_hides(self, gen, archive, tmp_path, convention):
        """PR 3 Copilot round: the note's numbers take the table's per-cell
        guard. A copy of the module archive whose pooled flow reads four
        students on items_canonical at Ready (S1's Ready clause, a table
        cell under B only), events at Authorized (S3's governance loss, the
        difference of the Raw and Authorized cells), attempts at step 3
        (S4's loss, a cell the table folds) and checks at step 5 (S5's
        state-validity checks, while its rows stay): those four numbers are
        the dash, every other number prints as before, and the table hides
        exactly the cells it reads among them."""
        run = copy_archive(archive, tmp_path / convention)

        def edit(rows):
            for quantity, step in (
                ("items_canonical", "6"), ("events", "1"), ("attempts", "3"), ("checks", "5"),
            ):
                row = _flow_row(rows, "pooled", quantity, step)
                assert int(row["n_students"]) >= 5
                row["n_students"] = "4"
            return rows

        _rewrite_flow(run, edit)
        out = tmp_path / f"out-{convention}"
        assert gen.main([
            "--archive", str(run), "--out", str(out), "--items-convention", convention,
        ]) == 0
        note = _note_text(out / "tab-corpus-flow-note.ptx")
        if convention == "A":
            assert ("Items are logged identifiers (convention A): 4 logged identifiers at Raw "
                    "correspond to 3 canonical items after the identifier alias, 3 to — at Ready; "
                    "1 identifier(s) aliased to successor(s).") in note
            assert ("item traces are student-identifier pairs (21 at Raw; 21 student-item pairs "
                    "after the alias)") in note
        else:
            assert ("Items are canonical items (convention B): 3 items at Raw, logged under 4 "
                    "identifiers; — at Ready.") in note
            assert ("item traces are student-item pairs after the alias (21 at Raw; 21 "
                    "student-identifier pairs before it)") in note
        assert "Governance removed — rows of instructor, researcher and test accounts" in note
        assert "source alignment removed — attempts on a logged identifier with no board" in note
        assert "Event parsing removed 3 rows (2 checks)" in note
        assert "state validity removed 7 rows (— checks)" in note
        pooled = flow_index(read_display(out / "tab-corpus-flow.csv")[1])
        hidden = {k for k, r in pooled.items() if r["suppressed"] == "1"}
        expected = {("", "Events", "Authorized"), ("", "Checks", "Reconstructed")}
        if convention == "B":
            expected.add(("", "Items", "Ready"))
        assert hidden == expected
        names = ("pooled: Items at Ready, " if convention == "B" else "") + (
            "pooled: Events at Authorized, pooled: Checks at Reconstructed, "
            "Y00-B03-P11 (every cell), Y00-B03-P13 (every cell)."
        )
        assert note.rstrip().endswith("suppressed (fewer than five students): " + names)


class TestFlowCell:
    """The cell guard and the column mapping through the module API (the
    pattern of test_the_guard_counts_students_not_attempts)."""

    def test_a_valued_cell_under_five_students_is_blank_and_marked(self, gen):
        assert gen.flow_cell({"rows_out": "7", "n_students": "4"}, False) == {
            "value": "", "n_students": "", "suppressed": "1",
        }
        assert gen.flow_cell({"rows_out": "7", "n_students": "5"}, False) == {
            "value": "7", "n_students": "5", "suppressed": "0",
        }
        # Twenty rows from one student is a cell of one.
        assert gen.flow_cell({"rows_out": "20", "n_students": "1"}, False) == {
            "value": "", "n_students": "", "suppressed": "1",
        }

    def test_a_short_record_reads_as_an_empty_count(self, gen):
        # PR 3 review round 2: csv.DictReader hands back None, not "", for a
        # field a short record lacks; refused as an empty count, no TypeError.
        with pytest.raises(gen.Refusal, match=r"empty n_students on a valued cell"):
            gen.flow_cell({"rows_out": "7", "n_students": None, "quantity": "students", "step": "0"}, False)
        with pytest.raises(gen.Refusal, match=r"empty rows_out on a valued cell"):
            gen.flow_cell({"rows_out": None, "n_students": "9", "quantity": "events", "step": "0"}, False)

    def test_a_no_value_cell_is_blank_and_unsuppressed(self, gen):
        assert gen.flow_cell(None, True) == {"value": "", "n_students": "", "suppressed": "0"}
        # A J10 row of the export (empty counts) is never read.
        assert gen.flow_cell({"rows_out": "", "n_students": ""}, True) == {
            "value": "", "n_students": "", "suppressed": "0",
        }

    def test_the_column_mapping_and_the_rows(self, gen):
        assert gen.FLOW_COLUMNS == (
            ("Raw", 0), ("Authorized", 1), ("Source-aligned", 3), ("Reconstructed", 5), ("Ready", 6),
        )
        assert gen.FLOW_NO_VALUE == J10_CELLS
        assert gen.FLOW_CSV_COLUMNS == ("quantity", "column", "step", "value", "n_students", "suppressed")
        assert gen.FLOW_BY_OFFERING_COLUMNS == ("offering", *gen.FLOW_CSV_COLUMNS)
        assert [label for label, _ in gen.FLOW_ROWS["A"]] == list(FLOW_LABELS)
        assert [q for _, q in gen.FLOW_ROWS["A"]] == [
            "students", "items_logged", "item_traces_logged", "attempts", "events", "checks",
        ]
        assert [q for _, q in gen.FLOW_ROWS["B"]] == [
            "students", "items_canonical", "item_traces_canonical", "attempts", "events", "checks",
        ]
        assert gen.REQUIRED_EXPORTS == (
            "checks", "attempt_metrics", "configurations", "boards", "item_summary", "check_scores", "flow",
        )

    def test_the_guard_sentence_names_a_cell_or_the_table(self, gen):
        """_flow_suppressed_names (ruling Q12): nothing hidden names nothing;
        every valued cell hidden names the table once; some hidden names each
        cell in table order; a J10 cell is never named."""
        shown = {"value": "7", "n_students": "5", "suppressed": "0"}
        hidden = {"value": "", "n_students": "", "suppressed": "1"}
        no_value = {"value": "", "n_students": "", "suppressed": "0"}

        def table(hidden_cells):
            return [
                {"quantity": q, "column": c, **(
                    no_value if (q, c) in J10_CELLS else hidden if (q, c) in hidden_cells else shown
                )}
                for q in FLOW_LABELS for c in FLOW_COLUMN_NAMES
            ]

        every = {(q, c) for q in FLOW_LABELS for c in FLOW_COLUMN_NAMES} - J10_CELLS
        assert gen._flow_suppressed_names("pooled", table(set())) == []
        assert gen._flow_suppressed_names("Y00-B03-P11", table(every)) == ["Y00-B03-P11 (every cell)"]
        assert gen._flow_suppressed_names("pooled", table({("Checks", "Raw"), ("Students", "Ready")})) == [
            "pooled: Students at Ready", "pooled: Checks at Raw",
        ]


def _rewrite_flow(run: Path, edit) -> None:
    """Rewrite a copied archive's exports/flow.csv: `edit(rows)` returns the
    rows to write (the same dicts, changed or filtered)."""
    path = run / "exports" / "flow.csv"
    rows = list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))
    columns = list(rows[0])
    rows = edit(rows)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _flow_row(rows: list[dict], breakdown: str, quantity: str, step: str) -> dict:
    """The one export row of (breakdown, quantity, step): the pooled row, or
    the by_offering row of the first import the file lists."""
    key = next(r["key"] for r in rows if r["breakdown"] == breakdown)
    [row] = [
        r for r in rows
        if r["breakdown"] == breakdown and r["key"] == key
        and r["quantity"] == quantity and r["step"] == step
    ]
    return row


def _set_flow_cell(breakdown: str, quantity: str, step: str, column: str, value: str):
    """An edit for _rewrite_flow: one cell of one row rewritten."""
    def edit(rows):
        _flow_row(rows, breakdown, quantity, step)[column] = value
        return rows
    return edit


def _truncate_record(run: Path, quantity: str, step: str) -> None:
    """Rewrite a copied archive's exports/flow.csv with the pooled (quantity,
    step) record cut short of its last field, n_students, csv.DictReader
    then reads that field as None, not "" (PR 3 review round 2)."""
    path = run / "exports" / "flow.csv"
    lines = path.read_text(encoding="utf-8").splitlines()
    rows = list(csv.DictReader(lines))
    [index] = [
        i for i, r in enumerate(rows)
        if r["breakdown"] == "pooled" and r["quantity"] == quantity and r["step"] == step
    ]
    line = lines[index + 1]  # one record per line: the header is line 0
    assert line.endswith("," + rows[index]["n_students"])
    lines[index + 1] = line.rsplit(",", 1)[0]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _without_an_offering_row(rows):
    row = _flow_row(rows, "by_offering", "students", "0")
    return [r for r in rows if r is not row]


def _with_a_repeated_row(breakdown: str):
    def edit(rows):
        return rows + [dict(_flow_row(rows, breakdown, "students", "0"))]
    return edit


# A malformed flow export, each refusal pinned by its whole line (PR 3 review
# round): named by its (quantity, step), never by a student or an offering.
MALFORMED_FLOW = (
    ("empty-n-students", _set_flow_cell("pooled", "students", "0", "n_students", ""),
     "exports/flow.csv: empty n_students on a valued cell (students, step 0)"),
    ("empty-n-students-at-reconstructed", _set_flow_cell("pooled", "events", "5", "n_students", ""),
     "exports/flow.csv: empty n_students on a valued cell (events, step 5)"),
    ("non-count-n-students", _set_flow_cell("pooled", "students", "0", "n_students", "x"),
     "exports/flow.csv: n_students 'x' is not a count (students, step 0)"),
    ("empty-rows-out", _set_flow_cell("pooled", "events", "0", "rows_out", ""),
     "exports/flow.csv: empty rows_out on a valued cell (events, step 0)"),
    ("non-count-rows-out", _set_flow_cell("pooled", "events", "0", "rows_out", "x"),
     "exports/flow.csv: rows_out 'x' is not a count (events, step 0)"),
    ("missing-offering-row", _without_an_offering_row,
     "exports/flow.csv lacks the by_offering row (students, step 0) for one offering"),
    ("unknown-breakdown", _set_flow_cell("pooled", "students", "0", "breakdown", "by_item"),
     "exports/flow.csv: unknown breakdown 'by_item'"),
    ("non-numeric-step", _set_flow_cell("pooled", "students", "0", "step", "x"),
     "exports/flow.csv: step 'x' is not a step number"),
    ("repeated-pooled-row", _with_a_repeated_row("pooled"),
     "exports/flow.csv: duplicate row (students, step 0)"),
    ("repeated-offering-row", _with_a_repeated_row("by_offering"),
     "exports/flow.csv: duplicate row (students, step 0) for one offering "
     "(a repeated row, or two imports under one pseudonym)"),
)


class TestFlowRefusals:
    """A missing, incomplete, disagreeing, foreign or malformed flow.csv:
    exit 2, the message on stderr, nothing written."""

    def _refused(self, gen, run: Path, tmp_path: Path, capsys, message: str) -> None:
        out = tmp_path / "out"
        assert gen.main(["--archive", str(run), "--out", str(out)]) == 2
        err = capsys.readouterr().err
        assert err.startswith("error: ") and message in err
        assert not out.exists() and not (run / "validation").exists()

    def test_a_missing_flow_export(self, gen, archive, tmp_path, capsys):
        run = copy_archive(archive, tmp_path)
        (run / "exports" / "flow.csv").unlink()
        self._refused(gen, run, tmp_path, capsys, "missing export(s) under exports/: flow.csv")

    def test_an_incomplete_pooled_grid(self, gen, archive, tmp_path, capsys):
        run = copy_archive(archive, tmp_path)
        _rewrite_flow(run, lambda rows: [
            r for r in rows
            if not (r["breakdown"] == "pooled" and r["quantity"] == "students" and r["step"] == "0")
        ])
        self._refused(gen, run, tmp_path, capsys, "exports/flow.csv lacks the pooled row (students, step 0)")

    def test_a_flow_that_disagrees_with_attempt_metrics(self, gen, attrition_archive, tmp_path, capsys):
        # Ready attempts: 9 in the ledger and in attempt_metrics; the ledger's
        # cell edited to 8 (Reconstructed, checked first, still agrees at 15).
        run = copy_archive(attrition_archive, tmp_path)

        def edit(rows):
            [row] = [r for r in rows if r["breakdown"] == "pooled" and r["quantity"] == "attempts" and r["step"] == "6"]
            assert row["rows_out"] == "9"
            row["rows_out"] = "8"
            return rows

        _rewrite_flow(run, edit)
        self._refused(
            gen, run, tmp_path, capsys,
            "exports/flow.csv disagrees with attempt_metrics.csv: Ready attempts 8 in the flow, "
            "9 in attempt_metrics",
        )

    def test_a_foreign_offering_key(self, gen, archive, tmp_path, capsys):
        run = copy_archive(archive, tmp_path)

        def edit(rows):
            for r in rows:
                if r["breakdown"] == "by_offering":
                    r["key"] = "0" * 64
            return rows

        _rewrite_flow(run, edit)
        self._refused(gen, run, tmp_path, capsys, "not among the manifest's imports")

    def test_a_truncated_flow_record(self, gen, archive, tmp_path, capsys):
        """A record cut short of its last field (PR 3 review round 2): the
        reader hands back None for n_students, refused as an empty count
        (exit 2 and the message, not a TypeError traceback)."""
        run = copy_archive(archive, tmp_path)
        _truncate_record(run, "students", "0")
        self._refused(
            gen, run, tmp_path, capsys,
            "exports/flow.csv: empty n_students on a valued cell (students, step 0)",
        )

    @pytest.mark.parametrize(
        "edit, message", [(e, m) for _, e, m in MALFORMED_FLOW], ids=[i for i, _, _ in MALFORMED_FLOW],
    )
    def test_a_malformed_flow_export(self, gen, archive, tmp_path, capsys, edit, message):
        """Every refusal of a malformed export, by its whole line: a blank or
        non-count value on a cell the table shows (spec §2.1 (d): refused,
        never rendered as a dash), an offering's missing row, an unknown
        breakdown, a non-numeric step, and a repeated row (a duplicated line,
        or two imports under one pseudonym, whose counts cannot be pooled),
        refused rather than letting the later row replace the earlier one."""
        run = copy_archive(archive, tmp_path)
        _rewrite_flow(run, edit)
        out = tmp_path / "out"
        assert gen.main(["--archive", str(run), "--out", str(out)]) == 2
        assert capsys.readouterr().err.strip() == f"error: {message}"
        assert not out.exists() and not (run / "validation").exists()
