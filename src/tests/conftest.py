"""
Pytest fixtures shared across the test suite.

Notable fixtures:

    db_conn       Fresh, in-memory, schema-applied SQLite connection. Auto-
                  closes after each test. Use this for any test that
                  touches the database; you don't need to do any setup
                  or teardown beyond declaring the fixture.

    fixtures_dir  Path to tests/fixtures/.

    sample_tab    Path to tests/fixtures/sample.tab.
    sample_csv    Path to tests/fixtures/sample.csv.

    golden_core_tab      Path to tests/fixtures/golden_core.tab, the hand-made
                         DataShop export the core (service-agnostic) tests read:
                         the same selection composition as golden_sa.tab, no
                         real data, so it ships with the public export
                         (cc-task-public-export PR 1, ruling R17).
    sa_legacy_ingest     The SA-era ``proteus.ingest.ingest_file`` (writes
                         ``responses`` directly, no ``events``), for the tests
                         of the backfill path; skips, keyed on registry
                         discovery, in a tree without the SA service (R14).

    golden_sa_tab        Path to tests/fixtures/golden_sa.tab, a byte-faithful
                         real subset of data/S26_ULA_datashop.tab used as the
                         frozen short-answer regression anchor (item A0.1).
    golden_sa_responses  The recorded `SELECT * FROM responses` baseline
                         (list of dict rows), loaded from
                         tests/fixtures/golden_sa_responses.json.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from proteus import db


FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def db_conn():
    """
    Fresh in-memory SQLite connection with schema applied.

    Each test gets its own connection; nothing persists across tests.
    Faster and cleaner than a tmp-file database; identical behaviour
    for everything we test here because in-memory SQLite uses the same
    SQL engine.
    """
    conn = db.connect(":memory:")
    db.bootstrap(conn)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def baseline_v3_script():
    """
    The frozen version-3 baseline as one SQL script: the core schema plus
    each registered service's schema files, in bootstrap order (item A3.5).
    Applying this alone (no migrations) reproduces a previous-release
    database, byte-identical DDL to the single schema.sql the split replaced.
    """
    from pathlib import Path as _Path

    from proteus.core import db as core_db
    from proteus.core import registry

    parts = [core_db.core_schema_sql()]
    for svc in registry.available_services():
        for schema_file in svc.schema_files:
            parts.append(_Path(schema_file).read_text(encoding="utf-8"))
    return "\n".join(parts)


@pytest.fixture
def fixtures_dir():
    return FIXTURES_DIR


@pytest.fixture
def make_git_repo():
    """
    Factory: ``make_git_repo(path, {relpath: content, ...})`` writes the files
    and commits them in a fresh local git repo, returning the repo path. Use
    ``file://{repo}`` as a public clone URL, real git, no network (SP-2 tests).
    """
    import subprocess

    def _make(path, files: dict[str, str]) -> Path:
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        for rel, content in files.items():
            f = path / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(content, encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(path)], check=True)
        subprocess.run(
            ["git", "-C", str(path), "config", "user.email", "t@example.test"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(path), "config", "user.name", "Test"], check=True
        )
        subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
        # -c commit.gpgsign=false: the CI/dev environment may force commit
        # signing via a server that rejects these throwaway test commits.
        subprocess.run(
            ["git", "-C", str(path), "-c", "commit.gpgsign=false",
             "commit", "-q", "-m", "init"],
            check=True,
        )
        return path

    return _make


@pytest.fixture
def sample_tab():
    return FIXTURES_DIR / "sample.tab"


@pytest.fixture
def sample_csv():
    return FIXTURES_DIR / "sample.csv"


@pytest.fixture
def sample_mangled_csv():
    return FIXTURES_DIR / "sample_mangled_timestamps.csv"


@pytest.fixture
def golden_sa_tab():
    """
    Path to the frozen short-answer golden fixture (item A0.1).

    A byte-faithful subset of data/S26_ULA_datashop.tab: 22 `shortanswer`
    rows (including the one non-ASCII response, several trailing-whitespace
    responses, and one malformed row with an empty Problem Name) plus 11
    non-short-answer rows (dragNdrop drop/move/reset, page, mChoice, poll,
    sage) that the SA path must ignore.
    """
    return FIXTURES_DIR / "golden_sa.tab"


@pytest.fixture
def golden_core_tab():
    """
    Path to the hand-made core ingest fixture (cc-task-public-export PR 1,
    ruling R17).

    A DataShop export in the real column layout with golden_sa.tab's selection
    composition, 22 ``shortanswer`` rows (two with trailing whitespace in
    Action, one non-ASCII, one empty, one malformed with an empty Problem
    Name) plus 11 other rows (three dragNdrop drops, two checks and a reset on
    the demo item, two page views, mChoice, poll, sage), Class 1 throughout,
    Week No 8 except one week-10 row, a blank Chapter, and a SubChapter on
    exactly the two page rows, but every student, item and response text is
    invented, so the core tests can read it in a tree that does not carry the
    real-data SA golden.
    """
    return FIXTURES_DIR / "golden_core.tab"


@pytest.fixture
def sa_legacy_ingest():
    """
    The SA-era ingest entry point, ``proteus.ingest.ingest_file``: writes
    ``responses`` directly and no ``events``, the starting point of the
    backfill tests (``reingest-events``, ``backfill_events``).

    The legacy package stays private with the SA service; this fixture skips,
    keyed on registry discovery (ruling R14), in a tree without ``sa``.
    """
    from proteus.core import registry

    if "sa" not in {s.name for s in registry.available_services()}:
        pytest.skip("the Short Answer service (and its legacy ingest) is not in this tree")
    from proteus.ingest import ingest_file

    return ingest_file


@pytest.fixture
def golden_dnd_tab():
    """
    Path to the DnD derive fixture (items B1.4/B3.6).

    A hand-crafted DataShop export in the real column layout that encodes the
    reference page's illustrative four-shape attempt (sec-raw-data,
    tab-raw-data-trace) for question `demo-book__demo-shapes-q1`: four
    placements, an Incorrect check (with the Action column truncated
    mid-JSON exactly as the real export truncates it, and the full JSON in
    Input), a takeback, a Correct check, and a reset. A second student
    contributes the pathological rows: a check whose Input itself is
    truncated (invalid JSON), a check referencing another question's drag
    label (the orphan case), a move with an unparseable Action, and a move
    whose indices fall outside the four-card board (the B2.4 gap case).
    One shortanswer row proves the SA/DnD split of the same file.
    """
    return FIXTURES_DIR / "golden_dnd.tab"


@pytest.fixture
def golden_dnd_worked_example_tab():
    """
    Path to the worked-example fixture (item B3.6).

    Encodes the journal's six-move worked attempt (the retired
    ``sec-worked-example`` walk; this golden fixture is now its frozen record,
    the book's current running example, ``sec-running-example-features``, is a
    shorter five-move walk) for ``demo-book__demo-shapes-q1``: the student
    places the second triangle in the wrong slot and the circle decoy in the
    empty-slot decoy, checks (σ=2, Incorrect, both decoys leaked), then moves
    the triangle to its slot and takes the decoy back to land on the key and
    check again (Correct). Distinct from ``golden_dnd.tab``, which encodes the
    simpler ``sec-raw-data`` trace whose first check is already σ=3.
    """
    return FIXTURES_DIR / "golden_dnd_worked_example.tab"


# The reference page's four-shape cardsort (sec-sample-question): P=4 cards,
# R=3 slots, key s* = (1, 1, 2, 0), the circle a decoy card, Pentagon a decoy
# slot. Shared by the DnD reconstruct/scoring/process/export tests.
FOUR_SHAPES_XML = """
<cardsort>
  <match><premise>equilateral triangle</premise><premise>right triangle</premise><response>Triangle</response></match>
  <match><premise>square</premise><response>Quadrilateral</response></match>
  <match><response>Pentagon</response></match>
  <match><premise>circle</premise></match>
</cardsort>
"""


@pytest.fixture
def four_shapes_board():
    """
    A callable ``register(conn, question_id="demo-book__demo-shapes-q1")`` that
    seeds the textbook/question rows and writes the reference four-shape board
    so downstream DnD stages (reconstruct, score, analyze) have a key to work
    against.
    """
    from proteus.services.dragndrop import board

    def register(conn, question_id="demo-book__demo-shapes-q1"):
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

    return register


@pytest.fixture
def golden_sa_responses():
    """
    The recorded golden baseline: `SELECT * FROM responses ORDER BY
    response_id` after ingesting golden_sa.tab through the current path.

    This is the load-bearing compatibility record (plan §2.3): later the
    SA service's `derive` step must reproduce these rows byte-for-byte.
    Never regenerate it to make a failing test pass, a diff here means a
    real behavior change.
    """
    path = FIXTURES_DIR / "golden_sa_responses.json"
    return json.loads(path.read_text(encoding="utf-8"))
