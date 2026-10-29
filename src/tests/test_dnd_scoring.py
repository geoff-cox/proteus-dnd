"""
Tests for DnD scoring (plan item B3.3).

The pure metrics are pinned to the journal's hand-checkable numbers: the
retired worked attempt's first check ``s4 = (1, 2, 2, 3)`` (σ=2, both decoys
leaked) and the solved second check ``s6 = (1, 1, 2, 0)`` (the key), plus the
swapped-names student ``s = (2, 2, 1, 0)`` for whom the label-free score σ̃
and the labeled σ part ways (worked live in
``subsec-label-free-example``). The writer is exercised end to end on the
golden fixture.
"""

from __future__ import annotations

import json

import pytest

from proteus.core import ingest as core_ingest
from proteus.services.dragndrop import scoring
from proteus.services.dragndrop.derive import derive_events
from proteus.services.dragndrop.reconstruct import reconstruct_all

KEY = (1, 1, 2, 0)  # s* for the four-shape question
R = 3  # slots (the dragzone is zone 0, not counted)
Q = "demo-book__demo-shapes-q1"


class TestWorkedExampleMetrics:
    # s4 = (1, 2, 2, 3): right triangle in the wrong slot, circle in the
    # empty-slot decoy. The retired tab-we-scoring reference gives σ=2, d=2,
    # w=2, u=0, c=(1,0,1,0), G=1/3 — the sec-checked-state-features
    # definitions on the worked first check.
    S4 = (1, 2, 2, 3)
    # s6 = the key exactly.
    S6 = (1, 1, 2, 0)

    def test_sigma_and_distance(self):
        assert scoring.sigma(self.S4, KEY) == 2
        assert scoring.distance(self.S4, KEY) == 2
        assert scoring.sigma(self.S6, KEY) == 4
        assert scoring.distance(self.S6, KEY) == 0

    def test_per_card(self):
        assert scoring.per_card(self.S4, KEY) == (1, 0, 1, 0)
        assert scoring.per_card(self.S6, KEY) == (1, 1, 1, 1)

    def test_grader_readout(self):
        # σ + w + u = P, and d = w + u.
        assert scoring.w(self.S4, KEY) == 2
        assert scoring.u(self.S4, KEY) == 0

    def test_error_profile_components(self):
        # F12: s4's two wrong cards split one apiece — the misfiled right
        # triangle (W_slot) and the leaked circle (a decoy-card leak, NOT a
        # wrong-slot placement) — so d = O + W_slot + L_P = 0 + 1 + 1.
        assert scoring.wrong_slot(self.S4, KEY) == 1
        assert scoring.u(self.S4, KEY) == 0
        assert scoring.wrong_slot(self.S6, KEY) == 0
        # The journal's worked check (1,1,2,3): ε = (0, 0, 1, 1) — the one
        # error is the decoy leaked into the decoy slot; W_slot stays 0.
        assert scoring.wrong_slot((1, 1, 2, 3), KEY) == 0
        assert scoring.u((1, 1, 2, 3), KEY) == 0
        # The empty board: every keyed card is an omission, nothing placed.
        assert scoring.u((0, 0, 0, 0), KEY) == 3
        assert scoring.wrong_slot((0, 0, 0, 0), KEY) == 0

    def test_wrong_slot_matches_the_errors_inventory(self):
        # scoring.wrong_slot is len(errors.misplacements) — the same W_slot,
        # counted flat here and itemized as triples there.
        from proteus.services.dragndrop import errors

        for s in [self.S4, self.S6, (1, 1, 2, 3), (0, 0, 0, 0), (2, 2, 1, 0)]:
            assert scoring.wrong_slot(s, KEY) == len(errors.misplacements(s, KEY))

    def test_additive_decomposition_is_exact(self):
        # d★ = omissions + wrong_slot + decoy_source_leak, on every state.
        for s in [self.S4, self.S6, (1, 1, 2, 3), (0, 0, 0, 0), (2, 2, 1, 0),
                  (3, 3, 3, 3)]:
            assert scoring.distance(s, KEY) == (
                scoring.u(s, KEY)
                + scoring.wrong_slot(s, KEY)
                + scoring.decoy_source_leak(s, KEY)
            ), s

    def test_per_card_score(self):
        assert scoring.per_card_score(self.S4, KEY) == pytest.approx(0.5)
        assert scoring.per_card_score(self.S6, KEY) == pytest.approx(1.0)
        assert scoring.per_card_score((1, 1, 2, 3), KEY) == pytest.approx(0.75)

    def test_baseline_corrected_progress(self):
        # σ(X0) = o = 1 (the lone decoy card sits correctly in the tray).
        assert scoring.sigma_baseline(KEY) == 1
        assert scoring.g(self.S4, KEY) == pytest.approx(1 / 3)
        assert scoring.g(self.S6, KEY) == pytest.approx(1.0)
        assert scoring.g((0, 0, 0, 0), KEY) == pytest.approx(0.0)

    def test_all_or_nothing(self):
        assert scoring.all_or_nothing(self.S4, KEY) == 0
        assert scoring.all_or_nothing(self.S6, KEY) == 1

    def test_both_decoys_leak_at_the_first_check(self):
        # The circle (decoy card) is dropped into the empty-slot decoy z3,
        # tripping both structural decoys at once.
        assert scoring.decoy_source_leak(self.S4, KEY) == 1
        assert scoring.decoy_target_leak(self.S4, KEY, R) == 1
        # The key trips neither.
        assert scoring.decoy_source_leak(self.S6, KEY) == 0
        assert scoring.decoy_target_leak(self.S6, KEY, R) == 0

    def test_label_free_equals_labeled_here(self):
        # The best slot matching is the identity, so σ̃ = σ = 2.
        assert scoring.sigma_tilde(self.S4, KEY, R) == 2
        assert scoring.sigma_tilde(self.S6, KEY, R) == 4

    def test_score_configuration_bundles_everything(self):
        score = scoring.score_configuration(self.S4, KEY, R)
        assert (score.sigma, score.distance, score.sigma_tilde) == (2, 2, 2)
        assert score.per_card == (1, 0, 1, 0)
        assert (score.decoy_source_leak, score.decoy_target_leak) == (1, 1)
        assert score.g == pytest.approx(1 / 3)
        # F12: the flat error-profile components ride the bundle too.
        assert (score.u, score.wrong_slot) == (0, 1)
        assert score.per_card_score == pytest.approx(0.5)


class TestLabelFreeMislabelDiagnostic:
    # Cohort Student 2, s = (2, 2, 1, 0): triangles filed under Quadrilateral
    # and the square under Triangle — perfect grouping, swapped names.
    S = (2, 2, 1, 0)

    def test_labeled_score_is_low(self):
        # Only the correctly trayed circle agrees.
        assert scoring.sigma(self.S, KEY) == 1

    def test_label_free_score_recovers_the_grouping(self):
        # Swapping the two slot names matches the key exactly: σ̃ = P = 4.
        assert scoring.sigma_tilde(self.S, KEY, R) == 4
        # The gap σ̃ − σ = 3 is the mislabel diagnostic.
        assert scoring.sigma_tilde(self.S, KEY, R) - scoring.sigma(self.S, KEY) == 3

    def test_confusion_matrix_and_assignment(self):
        n = scoring.confusion_matrix(self.S, KEY, R)
        # Student slot 2 holds both triangles (key slot 1); student slot 1
        # holds the square (key slot 2).
        assert n == [[0, 1, 0], [2, 0, 0], [0, 0, 0]]
        assert scoring.max_assignment(n) == 3  # the swap matches 1+2 cards


class TestScoreWriter:
    @pytest.fixture
    def scored(self, db_conn, golden_dnd_tab, four_shapes_board):
        core_ingest.ingest_file(db_conn, golden_dnd_tab)
        derive_events(db_conn)
        four_shapes_board(db_conn)
        reconstruct_all(db_conn)
        result = scoring.score_checks(db_conn)
        return db_conn, result

    def test_result_counts(self, scored):
        _, result = scored
        assert result.questions == 1
        # DemoStudent1's two checks + DemoStudent2's orphan check parse_ok=1.
        assert result.scored == 3
        assert result.correct == 1  # only the second (Correct) check is the key
        assert result.skipped == 1  # the truncated-Input check (parse_ok=0)

    def test_first_check_scores_three(self, scored):
        conn, _ = scored
        # DemoStudent1's first (Incorrect) check decodes to (1,1,2,3): three
        # cards right, the circle leaked into z3.
        row = conn.execute(
            "SELECT s.* FROM dnd_check_scores s JOIN dnd_events d "
            "USING (event_id) WHERE d.feedback = 'Incorrect' "
            "AND s.attempt_id IS NOT NULL "
            "ORDER BY s.sigma DESC LIMIT 1"
        ).fetchone()
        assert row["sigma"] == 3
        assert row["distance"] == 1
        assert row["decoy_source_leak"] == 1
        assert row["decoy_target_leak"] == 1
        assert json.loads(row["per_card_json"]) == [1, 1, 1, 0]
        # F12: the journal's worked profile ε = (0, 0, 1, 1) in flat form.
        assert row["omissions"] == 0
        assert row["wrong_slot"] == 0
        assert row["per_card_score"] == pytest.approx(0.75)

    def test_persisted_decomposition_is_exact(self, scored):
        conn, _ = scored
        # F12: distance = omissions + wrong_slot + decoy_source_leak on
        # every persisted row (the orphan check's empty board included:
        # 3 = 3 + 0 + 0).
        rows = conn.execute("SELECT * FROM dnd_check_scores").fetchall()
        assert rows
        for row in rows:
            assert row["distance"] == (
                row["omissions"] + row["wrong_slot"] + row["decoy_source_leak"]
            )
        assert {r["omissions"] for r in rows} == {0, 3}

    def test_correct_check_is_all_or_nothing(self, scored):
        conn, _ = scored
        row = conn.execute(
            "SELECT s.* FROM dnd_check_scores s JOIN dnd_events d "
            "USING (event_id) WHERE d.feedback = 'Correct'"
        ).fetchone()
        assert row["sigma"] == 4
        assert row["all_or_nothing"] == 1
        assert row["g"] == pytest.approx(1.0)

    def test_scores_link_to_attempts(self, scored):
        conn, _ = scored
        # Every scored check that was reconstructed carries its attempt id.
        unlinked = conn.execute(
            "SELECT count(*) AS n FROM dnd_check_scores WHERE attempt_id IS NULL"
        ).fetchone()["n"]
        assert unlinked == 0

    def test_rescore_is_idempotent(self, scored):
        conn, _ = scored
        again = scoring.score_checks(conn)
        assert again.scored == 3
        assert conn.execute(
            "SELECT count(*) AS n FROM dnd_check_scores"
        ).fetchone()["n"] == 3

    def test_scoring_without_a_board_is_skipped(self, db_conn, golden_dnd_tab):
        core_ingest.ingest_file(db_conn, golden_dnd_tab)
        derive_events(db_conn)
        # No board synced: nothing to score against.
        result = scoring.score_checks(db_conn)
        assert result.questions == 0
        assert result.scored == 0
