"""
The fixture archive the manuscript-table generator's tests run on, shared by
``test_paper_tables.py`` (the generator's own tests) and
``docs/tests/test_manuscript_sources.py`` (the private-only tests that compare
its displays with the manuscript sources; ``cc-task-public-export`` PR 2,
ruling R13). A plain module beside the tests, importable from both folders
because ``pyproject.toml`` puts ``src/tests`` on the path.

``build_fixture_archive(root)`` writes ``<root>/fixture-run/``: every
``export.DATASETS`` entry and the manifest of one ``build_database`` run on the
three DnD golden fixtures — the archive layout the frozen-run procedure
describes, built from hand-made data only.
"""

from __future__ import annotations

import csv
import hashlib
import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

from proteus import db
from proteus.core import ingest as core_ingest
from proteus.core import manifest
from proteus.services.dragndrop import aliases, board, export
from proteus.services.dragndrop.derive import derive_events
from proteus.services.dragndrop.process import analyze_all
from proteus.services.dragndrop.reconstruct import reconstruct_all
from proteus.services.dragndrop.scoring import score_checks

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "src" / "tests" / "fixtures"

# The three imports, their pseudonyms and the hand block's terms (the two
# 2026-02/04 files one term, the paper fixture the other); only the paper
# fixture is development-exposed.
FIXTURE_FILES = ("golden_dnd.tab", "golden_dnd_training_anomalies.tab", "golden_dnd_paper_tables.tab")
PSEUDONYM = {
    "golden_dnd.tab": "Y00-B03-P11",
    "golden_dnd_training_anomalies.tab": "Y00-B03-P13",
    "golden_dnd_paper_tables.tab": "Y01-B03-P17",
}
TERM = {
    "golden_dnd.tab": "Fall 2025",
    "golden_dnd_training_anomalies.tab": "Fall 2025",
    "golden_dnd_paper_tables.tab": "Spring 2026",
}
EXPOSED = {"golden_dnd_paper_tables.tab"}
RUN_ID = "fixture-run"

Q1 = "demo-book__demo-shapes-q1"
Q2 = "demo-book__demo-shapes-q2"
Q2_OLD = "demo-book__demo-shapes-q2-old"
Q3 = "demo-book__demo-shapes-q3"
SECTION = {Q1: "1.1", Q2: "1.2"}

OUTPUT_FILES = (
    "tab-items.csv", "tab-items.ptx", "tab-items-note.ptx",
    "tab-validation-results.csv", "tab-validation-results.ptx", "tab-validation-results-note.ptx",
    "tab-validation-by-item.csv",
    "data-validation-by-offering.csv",
    "fig-validation-results.csv",
    "data-anchor-causes-seven.csv", "data-anchor-causes-six.csv",
    "data-mismatch-distance.csv", "data-mismatch-distance-hist.csv",
    "data-anchor-diagnostics.csv",
    "data-sample-reporting.csv",
    # tab-corpus-flow from the ledger's flow export (E-08 follow-up, ruling R8 ii; PR 3).
    "tab-corpus-flow.csv", "tab-corpus-flow.ptx", "tab-corpus-flow-note.ptx",
    "data-corpus-flow-by-offering.csv",
)
# The pooled count files: one cell under the guard, every row marked (PR #236
# Copilot review, 2026-10-19); the pooled flow table joins them (PR 3: its
# cells are guarded one by one, each by the ledger row's own n_students).
POOLED_FILES = (
    "data-sample-reporting.csv",
    "data-anchor-causes-seven.csv", "data-anchor-causes-six.csv",
    "data-mismatch-distance.csv", "data-mismatch-distance-hist.csv",
    "tab-corpus-flow.csv",
)
# Columns that would make a display row an event-, attempt- or student-level
# record; none may appear under --out.
FORBIDDEN_COLUMNS = {
    "event_id", "student_id", "attempt_id", "session_id", "timestamp", "started_at",
    "ended_at", "config_id", "segment_id", "prior_attempt_id", "seq", "attempt_index",
}
SHA256 = re.compile(r"\b[0-9a-f]{64}\b")
EM_DASH = "—"
XML_ID = "{http://www.w3.org/XML/1998/namespace}id"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _register_board(conn, question_id: str) -> None:
    """conftest.four_shapes_board's ``register`` body (a function-scoped
    fixture cannot serve a module-scoped one), on conftest.FOUR_SHAPES_XML."""
    from conftest import FOUR_SHAPES_XML

    conn.execute(
        "INSERT OR IGNORE INTO textbooks (textbook_id, git_repo_url, git_ref, "
        "book_xml_id, build_dir) VALUES (1, 'r', 'v', 'demo-book', 'b')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO questions (question_id, textbook_id, xml_id, "
        "metadata_json, source) VALUES (?, 1, 'x', '{}', 'auto_extracted')",
        (question_id,),
    )
    spec = board.board_from_source(question_id, FOUR_SHAPES_XML)
    board.write_board(conn, spec, "hash")
    conn.commit()


def hand_block(files=FIXTURE_FILES) -> dict:
    return {
        "schema_version": 1,
        "corpus": {
            "files": [
                {
                    "pseudonym": PSEUDONYM[name],
                    "sha256": _sha(FIXTURES / name),
                    "term": TERM[name],
                    "development_exposure": name in EXPOSED,
                }
                for name in files
            ]
        },
        "configuration": {"readiness": "ready_only"},
        "items": [{"question_id": q, "section": s} for q, s in SECTION.items()],
    }


def build_database(conn, files=FIXTURE_FILES, post_ingest=None) -> None:
    """One in-memory build on a bootstrapped connection: ingest the fixture
    files, run ``post_ingest(conn)`` if given (rows added under an existing
    import, before derive), derive, the two boards, the alias before
    reconstruction, reconstruct → score → analyze."""
    for name in files:
        core_ingest.ingest_file(conn, FIXTURES / name)
    if post_ingest is not None:
        post_ingest(conn)
    derive_events(conn)
    _register_board(conn, Q1)
    _register_board(conn, Q2)
    # The alias needs a logged event under the retired identifier (the paper
    # fixture's); an archive built without that file has none to alias.
    if conn.execute("SELECT 1 FROM events WHERE problem_name = ? LIMIT 1", (Q2_OLD,)).fetchone():
        aliases.add_alias(conn, Q2_OLD, Q2, reason="renamed in source (fixture)")
    reconstruct_all(conn)
    score_checks(conn)
    analyze_all(conn)


def build_fixture_archive(root: Path, files=FIXTURE_FILES, post_ingest=None) -> Path:
    """``<root>/fixture-run/``: every ``export.DATASETS`` entry (the eight stage
    views and the ledger's three, ``flow.csv`` among them) and the manifest (the E-03
    tool's body, fingerprinted, plus the hand block) of one ``build_database``
    run on ``files`` (default: the three fixtures — the module archive)."""
    run = root / RUN_ID
    (run / "exports").mkdir(parents=True)
    conn = db.connect(":memory:")
    try:
        db.bootstrap(conn)
        build_database(conn, files, post_ingest)
        for name in export.DATASETS:
            with open(run / "exports" / f"{name}.csv", "w", newline="", encoding="utf-8") as fh:
                export.export_dataset(conn, name, fh)
        m = manifest.build_manifest(
            conn, pseudonyms={_sha(FIXTURES / name): PSEUDONYM[name] for name in files},
        )
    finally:
        conn.close()
    m["hand"] = hand_block(files)
    manifest.write_manifest(m, run / "manifest.yaml")
    return run


def read_display(path: Path) -> tuple[list[str], list[dict]]:
    """(the header comment lines, the data rows) of one generated CSV."""
    text = path.read_text(encoding="utf-8")
    comments = [ln for ln in text.splitlines() if ln.startswith("# ")]
    body = "\n".join(ln for ln in text.splitlines() if not ln.startswith("# "))
    return comments, list(csv.DictReader(body.splitlines()))


def columns_of(path: Path) -> list[str]:
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("# "):
            return next(csv.reader([line]))
    return []


def by(rows: list[dict], key: str) -> dict[str, dict]:
    return {r[key]: r for r in rows}


def copy_archive(archive: Path, dest: Path) -> Path:
    """A fresh copy of the fixture archive without anything a run added."""
    run = dest / RUN_ID
    shutil.copytree(archive, run, ignore=shutil.ignore_patterns("validation", "displays"))
    return run


def rewrite_manifest(run: Path, mutate, refingerprint: bool) -> None:
    path = run / "manifest.yaml"
    data = manifest.read_manifest(path)
    mutate(data)
    if refingerprint:
        data["fingerprint"] = manifest.fingerprint(data["body"])
    manifest.write_manifest(data, path)


def latest_run(data: dict, stage: str) -> dict:
    runs = [r for r in data["body"]["pipeline_runs"] if r["stage"] == stage]
    return max(runs, key=lambda r: r["run_id"])


def _data_rows(root: ET.Element) -> list[list[str]]:
    rows = []
    for row in root.iter("row"):
        if row.get("header") == "yes":
            continue
        cells = []
        for cell in row.findall("cell"):
            p = cell.find("p")
            assert p is not None and len(list(p)) == 0, "a data cell is <cell><p>text</p></cell>"
            cells.append(p.text or "")
        rows.append(cells)
    return rows


# The flow table's row labels (the ledger's quantities, in table order).
FLOW_LABELS = ("Students", "Items", "Item traces", "Attempts", "Events", "Checks")
