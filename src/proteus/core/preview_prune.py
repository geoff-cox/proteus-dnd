"""
Residual-chrome post-processor for the question previewer (checklist item PV-3).

The PV-1 preview publication file (portable, boulder theme) already strips most
chrome: no masthead theme, no table of contents, stripped navigation. A small,
defensive prune pass removes what's left so the embedded preview shows only the
question and its interactive markup. This runs in ``core.preview.render_preview``
after the build and before caching (PV-4); the embed component does no HTML
surgery of its own.

What this removes (and *only* this — each as an isolated operation):

* ``a.assistive``            the skip-to-content link
* ``#ptx-masthead``          the book masthead
* inside ``#ptx-navbar``:    ``.toc-toggle`` and ``.treebuttons`` — the
                             ``.nav-other-controls`` block (the dark-mode button)
                             is deliberately KEPT
* ``#ptx-sidebar``           the table-of-contents sidebar
* ``#ptx-content-footer``    the in-content prev/next footer
* ``#ptx-page-footer``       the page footer
* every ``.autopermalink``   including the paragraph-level ¶ inside the question

What it must NOT touch: the entire ``<head>``; the heading *elements and their
title text* (``<h2>``/``<h3>``) — we never remove or retitle a heading (the
``question_id`` is surfaced in the expander label instead). The one deliberate
reach into a heading is the ``.autopermalink`` ¶ PreTeXt nests inside it, which
is removed with all the others (see the removal list above). Also untouched: the
``#latex-macros`` div, and the ``ul[data-component="dragndrop"]`` subtree
(draggables, dropzones, displaymath) plus the ``#light-dark-button``. The
``dragndrop-data`` JSON payload — a *sibling* ``<script>`` after the ``</ul>``,
not part of that subtree — also survives, simply because no prune selector
targets it, and its ``match`` array is the answer key: that is deliberate
(A20/BUG-20 owner decision, 2026-08-22) — DnD previews are analyst-facing and
the coder-blinding guarantee covers SA components only
(``preview._strip_solutions``).

Robustness contract (this is a requirement, not a nicety):

1. Each selector removal runs in its own ``try/except``: a miss or error is
   logged and skipped, never raised.
2. The pruned tree is re-serialized only if it still parses; on any catastrophic
   failure the original ``html`` is returned unchanged. The worst case is a
   preview with full chrome, never a broken page.

The selectors are coupled to PreTeXt's output structure and will drift on
upgrade; the PV-3 "fired-prune" tests turn that drift into a loud CI failure
rather than a silent revert to full chrome, and the PV-7 dev harnesses are
re-run on every ``pretext`` bump.
"""

from __future__ import annotations

import logging

from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

__all__ = ["prune_preview_html", "PRUNE_SELECTORS"]

# (description, CSS selector) for each chrome removal, scoped exactly as the
# checklist specifies. Order is immaterial — each runs in isolation against the
# live tree. The ``.toc-toggle`` / ``.treebuttons`` selectors are scoped to
# ``#ptx-navbar`` so the sibling ``.nav-other-controls`` (dark-mode button) is
# never caught. Keep this list in lock-step with the fired-prune tests.
PRUNE_SELECTORS: tuple[tuple[str, str], ...] = (
    ("skip-to-content link", "a.assistive"),
    ("masthead", "#ptx-masthead"),
    ("navbar toc toggle", "#ptx-navbar .toc-toggle"),
    ("navbar tree buttons", "#ptx-navbar .treebuttons"),
    ("toc sidebar", "#ptx-sidebar"),
    ("content footer", "#ptx-content-footer"),
    ("page footer", "#ptx-page-footer"),
    ("autopermalinks", ".autopermalink"),
)


def prune_preview_html(html: str) -> str:
    """
    Strip residual visible chrome from a built portable PreTeXt preview page,
    leaving the question and its interactive markup intact.

    Defensive by contract: a selector that misses or errors is logged and
    skipped; if parsing or re-serialization fails catastrophically the original
    ``html`` is returned unchanged. Returning full chrome always beats returning
    a broken page.
    """
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:  # noqa: BLE001 — html.parser is lenient; never raise here
        log.warning("preview prune: HTML did not parse; returning unchanged", exc_info=True)
        return html

    for description, selector in PRUNE_SELECTORS:
        # Isolated per selector: a bad selector or bs4 hiccup removes nothing for
        # that one entry and the rest of the pass still runs.
        try:
            removed = 0
            for node in soup.select(selector):
                node.decompose()
                removed += 1
            log.debug("preview prune: removed %d node(s) for %s (%s)", removed, description, selector)
        except Exception:  # noqa: BLE001 — one selector must not abort the pass
            log.warning(
                "preview prune: selector %r (%s) failed; skipping", selector, description, exc_info=True
            )
            continue

    # Serialize, then settle to a parse/serialize fixed point with one extra
    # round-trip. decompose() leaves adjacent whitespace text nodes that
    # html.parser coalesces on the next parse, so a single re-parse makes the
    # pass idempotent (prune(prune(x)) == prune(x)). That inner parse also
    # doubles as the "still parses?" guard: if the pruned tree fails to parse the
    # exception is caught and the original `html` is returned unchanged — full
    # chrome always beats a broken page.
    try:
        result = str(BeautifulSoup(str(soup), "html.parser"))
    except Exception:  # noqa: BLE001 — fall back to the unpruned input
        log.warning("preview prune: re-serialization failed; returning original", exc_info=True)
        return html
    return result
