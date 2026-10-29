"""
The cohort-aggregation golden test (build item EX-2).

Pins the EX-1 ``cohort.cohort_data`` aggregation to the hand-verified
four-student cohort (the journal's retired ``subsec-we-cohort`` /
``tab-we-heatmap`` example — this test is its frozen record; the definitions
it exercises are ``subsec-heatmap-definition``,
``subsec-card-entropy-diagnostic``, and the labeling gap of
``subsec-when-label-free-is-appropriate``). Four students each submit one
final checked board over the four-shape question (P=4, R=3, key s* =
(1,1,2,0)):

    S1 = (1,1,2,0)  the key exactly                       σ = 4
    S2 = (2,2,1,0)  grouping perfect, slot names swapped  σ = 1, σ̃ = 4 (gap 3)
    S3 = (1,1,2,3)  decoy circle parked in the empty slot σ = 3
    S4 = (1,2,2,0)  right triangle merged with the square σ = 3

flowed through the real pipeline (ingest → derive → sync-boards → reconstruct →
score) so the persisted σ / σ̃ / s_json the aggregation reads are computed by
the production code, not hand-seeded. Same discipline as ``test_dnd_scoring`` /
``test_dnd_worked_example``: the asserted numbers are the frozen journal
baseline — **if this fails, the aggregation is wrong, not the baseline.**

The four-student cohort is synthesized from the explicit final-state spec below
(the spec *is* the cheatsheet cohort); the moves that reach each state and the
DataShop ``.tab`` rows are generated so the whole read path is exercised end to
end.
"""

from __future__ import annotations

import csv
import json
import math

import pytest

from proteus.core import ingest as core_ingest
from proteus.services.dragndrop import cohort
from proteus.services.dragndrop.derive import derive_events
from proteus.services.dragndrop.reconstruct import reconstruct_all
from proteus.services.dragndrop.scoring import score_checks

Q = "demo-book__demo-shapes-q1"
KEY = (1, 1, 2, 0)

# The cheatsheet cohort: (student, session, final checked state, feedback).
COHORT = [
    ("CohortS1", "401", (1, 1, 2, 0), "Correct"),
    ("CohortS2", "402", (2, 2, 1, 0), "Incorrect"),
    ("CohortS3", "403", (1, 1, 2, 3), "Incorrect"),
    ("CohortS4", "404", (1, 2, 2, 0), "Incorrect"),
]

_HEADER = [
    "Time", "Anon Student Id", "Action", "Problem Name", "Class",
    "Level (Chapter)", "Level (SubChapter)", "Session Id", "Problem View",
    "Selection", "Input", "Feedback Text", "Feedback Classification",
    "CF (Code)", "CF (Week No)", "CF (Institution)",
]


def _input_json(state: tuple[int, ...]) -> str:
    """The check's logged Input: {drop_label: [drag_labels]} for placed cards."""
    placements: dict[int, list[str]] = {}
    for card, zone in enumerate(state, start=1):
        if zone != 0:
            placements.setdefault(zone, []).append(f"{Q}_drag{card}")
    return json.dumps(
        {f"{Q}_drop{zone}": placements[zone] for zone in sorted(placements)}
    )


def _row(time, student, action, session, view, selection, *, inp="", feedback=""):
    return [
        time, student, action, Q, "1", "", "", session, str(view),
        selection, inp, feedback, feedback, "", "8", "university",
    ]


def _write_cohort_tab(path) -> None:
    """Generate the DataShop .tab: each student places its cards then checks."""
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(_HEADER)
        for day, (student, session, state, feedback) in enumerate(COHORT, start=1):
            moves = [(card, zone) for card, zone in enumerate(state, 1) if zone != 0]
            view = 1
            for step, (card, zone) in enumerate(moves):
                time = f"2026-03-0{day} 10:00:{step:02d}.000000"
                action = f"{Q}_drag{card} -> {Q}_drop{zone}"
                writer.writerow(_row(time, student, action, session, view,
                                     "dragNdrop-drop"))
                view += 1
            inp = _input_json(state)
            time = f"2026-03-0{day} 10:00:{len(moves):02d}.000000"
            writer.writerow(_row(time, student, inp, session, view, "dragNdrop",
                                 inp=inp, feedback=feedback))


@pytest.fixture
def cohort_golden(db_conn, four_shapes_board, tmp_path):
    tab = tmp_path / "cohort.tab"
    _write_cohort_tab(tab)
    core_ingest.ingest_file(db_conn, tab)
    derive_events(db_conn)
    four_shapes_board(db_conn)
    reconstruct_all(db_conn)
    score_checks(db_conn)
    return db_conn


def test_cohort_is_four_final_boards(cohort_golden):
    data = cohort.cohort_data(cohort_golden, Q, unit="final")
    assert data.n_unit == 4
    assert data.n_checks == 4
    # The per-student final scores {4,1,3,3} (order-independent).
    assert sorted(data.sigma_unit) == [1, 3, 3, 4]


def test_placement_heatmap_xbar(cohort_golden):
    # X̄[zone][card] (subsec-heatmap-definition), every column summing to 1;
    # the values are the retired tab-we-heatmap's.
    data = cohort.cohort_data(cohort_golden, Q, unit="final")
    expected = [
        [0.0, 0.0, 0.0, 3 / 4],   # tray (zone 0)
        [3 / 4, 1 / 2, 1 / 4, 0.0],  # z1
        [1 / 4, 1 / 2, 3 / 4, 0.0],  # z2
        [0.0, 0.0, 0.0, 1 / 4],   # z3 (decoy slot)
    ]
    for i, row in enumerate(expected):
        for j, value in enumerate(row):
            assert data.heat_frac[i][j] == pytest.approx(value), (i, j)


def test_cohort_mean_score_is_two_point_seventy_five(cohort_golden):
    data = cohort.cohort_data(cohort_golden, Q, unit="final")
    # ⟨X̄, X*⟩ = Σ_j X̄[s*(j), j] = 3/4 + 1/2 + 3/4 + 3/4 = 11/4.
    mean_from_heatmap = sum(
        data.heat_frac[KEY[j - 1]][j - 1] for j in range(1, data.n_cards + 1)
    )
    assert mean_from_heatmap == pytest.approx(2.75)
    # Equivalently the mean of the per-student σ {4,1,3,3}.
    assert sum(data.sigma_unit) / data.n_unit == pytest.approx(2.75)


def test_per_card_difficulty_vector(cohort_golden):
    data = cohort.cohort_data(cohort_golden, Q, unit="final")
    # 1 − X̄[s*(j), j] read at the key row: c1_2 (right triangle) hardest at 1/2.
    difficulty = [d["difficulty"] for d in data.difficulty]
    assert difficulty == pytest.approx([1 / 4, 1 / 2, 1 / 4, 1 / 4])


def test_card_placement_entropy(cohort_golden):
    # F11: H_j over the heatmap columns (g-baseline-card-entropy; natural
    # log, 0·log 0 = 0).
    # c1/c3/c4 split 3/4 vs 1/4 across two zones; c2 splits evenly (ln 2).
    data = cohort.cohort_data(cohort_golden, Q, unit="final")
    h_31 = -(3 / 4) * math.log(3 / 4) - (1 / 4) * math.log(1 / 4)
    entropies = [d["entropy"] for d in data.difficulty]
    assert entropies == pytest.approx([h_31, math.log(2), h_31, h_31])
    assert data.mean_entropy == pytest.approx((3 * h_31 + math.log(2)) / 4)
    assert data.max_entropy == pytest.approx(math.log(2))
    # The diagnostic pairing: c2 is both the hardest card (1/2) and the most
    # dispersed — scattered confusion, not a shared wrong placement.


def test_first_correct_unit_drops_never_correct_attempts(cohort_golden):
    # F16/F26: only S1 ever checks the key, so the first-correct analysis
    # point holds exactly one state — the other three attempts have no state
    # there and are absent (the subsec-heatmap-definition reading), exactly
    # as a checkless attempt is absent from first/final.
    data = cohort.cohort_data(cohort_golden, Q, unit="first_correct")
    assert data.n_unit == 1
    assert data.sigma_unit == [4]
    # The sample is the key itself, so nothing is misplaced at this point.
    assert [d["difficulty"] for d in data.difficulty] == [0.0, 0.0, 0.0, 0.0]
    # The all-checks aggregates keep every attempt in scope.
    assert data.n_checks == 4


def test_occupancy_diagnostics(cohort_golden):
    # F8b: the zone-level diagnostics over the four final boards. Mean
    # occupancy is the heatmap row sum (subsec-linear-summaries-from-heatmaps),
    # so the residuals follow from X̄:
    # ō = (3/4, 3/2, 3/2, 1/4) against o★ = (1, 2, 1, 0).
    data = cohort.cohort_data(cohort_golden, Q, unit="final")
    rows = data.occupancy_rows
    assert [r["key_occupancy"] for r in rows] == [1, 2, 1, 0]
    assert [r["mean_occupancy"] for r in rows] == pytest.approx(
        [3 / 4, 3 / 2, 3 / 2, 1 / 4]
    )
    assert [r["mean_residual"] for r in rows] == pytest.approx(
        [-1 / 4, -1 / 2, 1 / 2, 1 / 4]
    )
    assert sum(r["mean_residual"] for r in rows) == pytest.approx(0.0)
    # Every final board occupies exactly three zones (tray or z3 included).
    assert data.mean_occupied_zones == pytest.approx(3.0)


def test_swapped_names_mislabel_gap(cohort_golden):
    data = cohort.cohort_data(cohort_golden, Q, unit="final")
    # Only S2 (perfect grouping, swapped slot names) has a relabel gain —
    # σ̃ − σ = 4 − 1 = 3 (the labeling gap of
    # subsec-when-label-free-is-appropriate; S2 is the swapped-names response
    # worked in subsec-label-free-example); everyone else's gap is 0.
    gaps = sorted(r["gap"] for r in data.gap_rows)
    assert gaps == [0, 0, 0, 3]
    # And it is S2 specifically: its persisted (σ, σ̃) = (1, 4).
    row = cohort_golden.execute(
        "SELECT cs.sigma, cs.sigma_tilde FROM dnd_check_scores cs "
        "JOIN dnd_attempts a USING (attempt_id) "
        "WHERE a.student_id = 'CohortS2'"
    ).fetchone()
    assert (row["sigma"], row["sigma_tilde"]) == (1, 4)
