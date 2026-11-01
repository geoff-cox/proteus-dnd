"""
Tests for the residual-chrome post-processor (checklist item PV-3).

Three guarantees, exercised against a representative portable-build fixture
(``fixtures/preview/built_cardsort.html``):

* **Fired-prune**: every removed selector is present in the raw fixture and
  absent after pruning. This is the loud-on-drift guard: if a ``pretext`` bump
  renames one of these, the assertion fails in CI instead of silently reverting
  the preview to full chrome.
* **Survival**: the interactive cardsort markup, the displaymath, ``#latex-macros``,
  the dark-mode button, the headings, and the whole ``<head>`` are untouched.
* **Robustness**: each removal is isolated, the pass is idempotent, math/script
  payloads round-trip intact, and a catastrophic failure returns the input
  unchanged rather than a broken page.
"""

from __future__ import annotations

import json
from pathlib import Path

from bs4 import BeautifulSoup

from proteus.core import preview_prune
from proteus.core.preview_prune import PRUNE_SELECTORS, prune_preview_html

FIXTURE = Path(__file__).parent / "fixtures" / "preview" / "built_cardsort.html"


def _raw() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def _pruned_soup() -> BeautifulSoup:
    return BeautifulSoup(prune_preview_html(_raw()), "html.parser")


class TestFiredPrune:
    def test_every_removed_selector_is_present_before_pruning(self):
        # Without this, a fired-prune assertion could pass vacuously (the fixture
        # never had the chrome). Each selector must actually fire.
        raw = BeautifulSoup(_raw(), "html.parser")
        for description, selector in PRUNE_SELECTORS:
            assert raw.select(selector), f"fixture lacks {description} ({selector})"

    def test_every_removed_selector_is_absent_after_pruning(self):
        soup = _pruned_soup()
        for description, selector in PRUNE_SELECTORS:
            assert not soup.select(selector), f"{description} ({selector}) survived the prune"

    def test_paragraph_level_autopermalink_inside_question_is_removed(self):
        # The ¶ permalink sits inside the question paragraph, not just on the
        # section heading (all ).autopermalink go.
        soup = _pruned_soup()
        assert soup.select("#p-intro .autopermalink") == []
        # ...but the paragraph itself stays.
        assert soup.select_one("#p-intro") is not None


class TestAnswerKeyPolicy:
    """A20/BUG-20 (owner decision, 2026-08-22): a built cardsort page's
    ``dragndrop-data`` JSON carries the answer key (the ``match`` array), and
    the prune pass deliberately leaves it in place. DnD previews are
    analyst-facing, and the coder-blinding guarantee covers SA components
    only (``preview._strip_solutions``). This pins the policy against the
    BUILT HTML (the solution-strip tests cover source XML only) so a
    future neutralization must be a deliberate change, never drift."""

    def test_answer_key_payload_survives_the_prune(self):
        raw = BeautifulSoup(_raw(), "html.parser")
        payload = raw.select_one('script[data-component="dragndrop-data"]')
        assert payload is not None, "fixture lacks the dragndrop-data payload"
        assert '"match"' in payload.string  # the key ships in the raw build

        pruned = _pruned_soup().select_one(
            'script[data-component="dragndrop-data"]'
        )
        assert pruned is not None
        assert json.loads(pruned.string)["match"] == [0, 1, 2]


class TestSurvival:
    def test_cardsort_subtree_survives(self):
        soup = _pruned_soup()
        assert soup.select_one('ul[data-component="dragndrop"]') is not None

    def test_three_draggable_dropzone_pairs_survive(self):
        soup = _pruned_soup()
        assert len(soup.select("li.draggable")) == 3
        assert len(soup.select("li.dropzone")) == 3

    def test_displaymath_and_latex_macros_survive(self):
        soup = _pruned_soup()
        assert soup.select_one("div.displaymath") is not None
        assert soup.select_one("#latex-macros") is not None

    def test_dark_mode_button_and_its_container_survive(self):
        # .toc-toggle / .treebuttons are pruned from the navbar, but the navbar
        # itself and its .nav-other-controls (holding #light-dark-button) stay.
        soup = _pruned_soup()
        assert soup.select_one("#ptx-navbar") is not None
        assert soup.select_one("#ptx-navbar .nav-other-controls") is not None
        assert soup.select_one("#light-dark-button") is not None

    def test_headings_are_untouched(self):
        soup = _pruned_soup()
        assert soup.select_one("h2") is not None
        assert soup.select_one("h3") is not None
        # The wrapper "Section 1 Question" heading is intentionally left as-is.
        assert "Section 1 Question" in soup.get_text()

    def test_head_is_untouched(self):
        soup = _pruned_soup()
        head = soup.select_one("head")
        assert head is not None
        assert head.select_one("title") is not None
        # The CDN MathJax + CSS refs in <head> are exactly what self-containment
        # depends on; the prune never touches them.
        assert head.select_one('script[src*="mathjax"]') is not None
        assert head.select_one('link[href*="cdn"]') is not None


class TestRoundTripSafety:
    def test_still_parses_and_keeps_one_html_document(self):
        out = prune_preview_html(_raw())
        soup = BeautifulSoup(out, "html.parser")
        assert soup.select_one("html") is not None
        assert soup.select_one("body") is not None

    def test_math_delimiters_survive_intact(self):
        out = prune_preview_html(_raw())
        assert r"\(" in out and r"\)" in out  # inline math
        assert r"\[" in out and r"\]" in out  # display math
        assert r"\begin{equation*}" in out

    def test_script_payload_survives_intact(self):
        out = prune_preview_html(_raw())
        # The dragndrop data <script> and the head MathJax <script> are intact.
        assert '"premises"' in out
        assert "tex-mml-chtml.js" in out


class TestIdempotence:
    def test_prune_of_prune_equals_prune(self):
        once = prune_preview_html(_raw())
        twice = prune_preview_html(once)
        assert twice == once


class TestRobustness:
    def test_page_without_chrome_is_safe(self):
        # Every selector misses; the pass must not raise and must keep content.
        html = "<html><body><p id='q'>Just a question.</p></body></html>"
        out = prune_preview_html(html)
        assert "Just a question." in out

    def test_garbage_input_does_not_raise(self):
        # Not even close to HTML; html.parser is lenient, the pass returns a
        # string without raising.
        out = prune_preview_html("<<< not really html >>> & < >")
        assert isinstance(out, str)

    def test_one_failing_selector_does_not_abort_the_pass(self, monkeypatch):
        # A bad selector (soupsieve raises) is logged and skipped; the remaining
        # valid selectors still fire. Order puts the bad one first.
        monkeypatch.setattr(
            preview_prune,
            "PRUNE_SELECTORS",
            (("bad selector", "::::nonsense"), ("masthead", "#ptx-masthead")),
        )
        soup = BeautifulSoup(prune_preview_html(_raw()), "html.parser")
        # No raise, and the valid selector after the bad one still removed it.
        assert soup.select_one("#ptx-masthead") is None

    def test_returns_original_unchanged_on_catastrophic_parse_failure(self, monkeypatch):
        # If BeautifulSoup itself blows up, return the input verbatim (never a
        # partially-mangled page).
        sentinel = "<html><body>UNTOUCHED</body></html>"

        def boom(*_a, **_k):
            raise RuntimeError("parser exploded")

        monkeypatch.setattr(preview_prune, "BeautifulSoup", boom)
        assert prune_preview_html(sentinel) == sentinel
