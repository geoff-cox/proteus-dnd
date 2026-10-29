"""
Tests for the descriptor UI surface (checklist item B4.12).

Each service declares its ordered ``analyses`` and supported ``filter_dimensions``
on its ``ServiceDescriptor``; one generic shell renders them. These tests pin the
declared contract (order, structure, valid dimensions) so the shell can stay
generic.
"""

from __future__ import annotations

import pytest

from proteus.core.registry import Analysis, FILTER_DIMENSIONS
from proteus.core import registry

# Service-conditional cases (cc-task-public-export PR 1, ruling R14): keyed on
# registry discovery, so the same file runs in the private tree (sa + dnd) and
# in the public export (dnd only).
SA = "sa" in {s.name for s in registry.available_services()}
requires_sa = pytest.mark.skipif(
    not SA, reason="the Short Answer service is not in this tree (R14)"
)


def _descriptor(name: str):
    return next(s for s in registry.available_services() if s.name == name)


class TestAnalysesDeclared:
    @requires_sa
    def test_sa_declares_ordered_analyses(self):
        sa = _descriptor("sa")
        keys = [a.key for a in sa.analyses]
        # Question-first, mirroring DnD: a landing + catalog selector, then the
        # canonical five analyses screen → extract → match → dispose → export
        # (CP3.6). Ingest is the shared Upload page.
        assert keys == [
            "overview", "questions", "screening", "extraction",
            "matching", "disposition", "export",
        ]
        assert all(isinstance(a, Analysis) for a in sa.analyses)

    def test_dnd_declares_attempts_then_scoring(self):
        dnd = _descriptor("dnd")
        keys = [a.key for a in dnd.analyses]
        # Explore is appended last (EX-6): the deepest, most-derived surface.
        assert keys == ["overview", "questions", "attempts", "scoring", "explore"]
        # The two analyses proper are in the checklist's order: attempts first.
        assert keys.index("attempts") < keys.index("scoring")

    def test_every_analysis_has_title_and_page(self):
        for svc in registry.available_services():
            name = svc.name
            for a in svc.analyses:
                assert a.title, f"{name}:{a.key} missing a title"
                # All current analyses are routable (have a shell page).
                assert a.page and a.page.startswith("pages/"), a

    def test_analysis_keys_unique_within_service(self):
        for svc in registry.available_services():
            keys = [a.key for a in svc.analyses]
            assert len(keys) == len(set(keys)), f"{svc.name} has duplicate analysis keys"

    def test_default_is_empty_tuple(self):
        # A descriptor that declares nothing defaults to (), not None.
        d = registry.ServiceDescriptor(
            name="x", title="X", selection_values=frozenset(),
            register_cli=lambda sub: None, status=lambda conn: {},
        )
        assert d.analyses == ()
        assert d.filter_dimensions == ()


class TestFilterDimensions:
    def test_every_service_declares_valid_dimensions(self):
        for svc in registry.available_services():
            dims = svc.filter_dimensions
            assert dims, f"{svc.name} declares no filter dimensions"
            # Every declared dimension is one of the canonical core set.
            assert set(dims) <= FILTER_DIMENSIONS, dims

    def test_dnd_declares_the_full_shared_set(self):
        assert _descriptor("dnd").filter_dimensions == (
            "course", "class", "week", "date"
        )
