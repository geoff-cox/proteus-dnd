"""
Tests for the feedback-policy vocabulary + gate/layer logic (item FP-3).

Pure-function tests only: the placement gate and the disclosure-layer
mapping. The end-to-end classification (reconstruct writing
``dnd_configurations.disclosure_layer`` / ``.graded_ordinal`` and
``dnd_attempts.feedback_policy`` from real timelines) is tested beside the
replay in ``test_dnd_reconstruct.TestFeedbackPolicy``, and the export surface
in ``test_dnd_export``.
"""

from __future__ import annotations

from proteus.services.dragndrop import policy


class TestIsGraded:
    def test_legacy_grades_every_check(self):
        # No ℓ₀ needed; any placement is graded.
        assert policy.is_graded(policy.LEGACY, 0, None) is True
        assert policy.is_graded(policy.LEGACY, 0, 3) is True

    def test_layered_gates_on_placement(self):
        # ℓ₀ = 3 non-decoy cards; the gate is placed ≥ ℓ₀.
        assert policy.is_graded(policy.LAYERED, 2, 3) is False
        assert policy.is_graded(policy.LAYERED, 3, 3) is True
        assert policy.is_graded(policy.LAYERED, 4, 3) is True  # a placed decoy counts

    def test_layered_without_board_is_undecidable(self):
        assert policy.is_graded(policy.LAYERED, 3, None) is None


class TestDisclosureLayer:
    def test_legacy_is_always_highlight(self):
        # The legacy single layer is φ3-equivalent — counts + highlighting.
        assert policy.disclosure_layer(policy.LEGACY, True, 1) == "highlight"
        assert policy.disclosure_layer(policy.LEGACY, True, 5) == "highlight"

    def test_layered_refused_when_ungraded(self):
        assert policy.disclosure_layer(policy.LAYERED, False, None) == "refused"

    def test_layered_count_before_the_third_graded_check(self):
        assert policy.disclosure_layer(policy.LAYERED, True, 1) == "count"
        assert policy.disclosure_layer(policy.LAYERED, True, 2) == "count"

    def test_layered_highlight_from_the_third_graded_check(self):
        assert policy.disclosure_layer(policy.LAYERED, True, 3) == "highlight"
        assert policy.disclosure_layer(policy.LAYERED, True, 9) == "highlight"

    def test_undecidable_when_graded_is_none(self):
        assert policy.disclosure_layer(policy.LAYERED, None, None) is None

    def test_layers_and_threshold_are_the_documented_ones(self):
        assert policy.LAYERS == ("refused", "count", "highlight")
        assert policy.HIGHLIGHT_AFTER == 3
        assert policy.VALID_POLICIES == ("legacy", "layered")
