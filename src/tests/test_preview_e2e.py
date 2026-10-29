"""
End-to-end preview smoke test (checklist item PV-7).

Drives one fixture question through the REAL pipeline —
assemble → build (with the PV-1 publication file) → prune → cache — and asserts
the production invariants in one place: self-containment (no *local*
``_static/`` bundle, CDN assets present), the fired-prune set is gone from the
cached page, the interactive cardsort markup survived, and the cache
invalidates on a source change.

Gated: a real build needs the ``pretext`` CLI and Node on PATH (the ``preview``
extra, in a venv) plus outbound access to the Runestone CDN on first build.
Without them every test here skips cleanly — CI installs ``.[dev,ui]`` only, so
it skips there; run it in a venv with the ``preview`` extra, or rely on the
``scripts/preview/`` harness, to exercise it after a ``pretext`` bump.
"""

from __future__ import annotations

import re
import shutil

import pytest
from bs4 import BeautifulSoup

from proteus.core import preview, pretext, textbooks
from proteus.core.preview_prune import PRUNE_SELECTORS

requires_toolchain = pytest.mark.skipif(
    not pretext.pretext_available() or shutil.which("node") is None,
    reason="preview build needs the pretext CLI and Node on PATH (preview extra, venv)",
)

_CDN_RE = re.compile(r"""(?:href|src)\s*=\s*["']https://[^"']+\.(?:css|js)""")

# URL-ish tokens containing "_static": everything between quote/paren/space
# delimiters around an occurrence. PV-2 forbids *local* _static/ references;
# the CDN's own paths legitimately contain the substring (html-static >= 2.x
# embeds it, e.g. .../html-static@2.50/dist/_static/pretext/...), so the
# probe checks each occurrence sits inside an absolute https URL instead of
# banning the substring outright (which broke on the newer CDN layout).
_STATIC_TOKEN_RE = re.compile(r"""[^"'()\s]*_static[^"'()\s]*""")


def _local_static_refs(html: str) -> list[str]:
    """Tokens containing ``_static`` that are not absolute https URLs."""
    return [
        token
        for token in _STATIC_TOKEN_RE.findall(html)
        if not token.startswith("https://")
    ]

QID = "demo-book__e2e-cardsort"
# A realistic synced unit: the full <exercise> sync stores (not a bare snippet),
# with a cardsort plus inline + display math so MathJax is exercised too.
SOURCE = (
    '<exercise xml:id="e2e-cardsort">'
    "<statement>"
    "<p>Match each shape to its category, where each lives in <m>\\mathbb{R}^2</m>.</p>"
    "<me>A x = b</me>"
    "</statement>"
    "<cardsort>"
    "<match><premise>equilateral triangle</premise><response>Triangle</response></match>"
    "<match><premise>square</premise><response>Quadrilateral</response></match>"
    "<match><response>Pentagon</response></match>"
    "</cardsort>"
    "</exercise>"
)


def _seed(conn, source: str = SOURCE) -> None:
    tid = textbooks.add_textbook(
        conn,
        book_xml_id="demo-book",
        git_repo_url="https://example.test/demo.git",
        build_dir="/b",
    )
    conn.execute(
        "INSERT INTO questions (question_id, textbook_id, xml_id, metadata_json, "
        "source, source_xml) VALUES (?, ?, 'e2e', '{}', 'auto_extracted', ?)",
        (QID, tid, source),
    )
    conn.commit()


class TestLocalStaticProbe:
    """The CDN-vs-local classifier behind the PV-2 assertion, exercised
    ungated: the e2e class below skips wherever the preview toolchain is
    absent — CI included — so the distinction needs its own coverage
    (PR #178 review)."""

    # The html-static >= 2.x CDN layout whose paths legitimately carry
    # "_static" — the shape that broke the old substring probe.
    CDN = (
        '<link href="https://cdn.jsdelivr.net/gh/PreTeXtBook/'
        'html-static@2.50/dist/_static/pretext/css/theme-boulder.min.css" '
        'rel="stylesheet" type="text/css"/>'
        '<script type="module">import { startMathJax } from '
        "'https://cdn.jsdelivr.net/gh/PreTeXtBook/html-static@2.50/dist"
        "/_static/pretext/js/dist/mathjax_startup.js';</script>"
    )

    def test_cdn_urls_carrying_the_substring_pass(self):
        assert _local_static_refs(self.CDN) == []

    @pytest.mark.parametrize("local", [
        '<link href="_static/pretext/css/x.css"/>',       # quoted attribute
        '<script src="./_static/js/y.js"></script>',      # ./-prefixed
        "<style>body{background:url(_static/i.png)}</style>",  # CSS url()
    ])
    def test_local_references_flag(self, local):
        assert _local_static_refs(self.CDN + local), local


@pytest.mark.preview
@requires_toolchain
class TestPreviewEndToEnd:
    def test_build_prune_cache_invariants(self, db_conn, tmp_path):
        _seed(db_conn)
        root = tmp_path / "previews"
        result = preview.render_preview(db_conn, QID, cache_root=root)
        assert result.status == "built", result.error
        html = result.html
        assert html is not None

        # Self-containment (PV-2): no *local* _static/ bundle reference (a
        # CDN URL may carry the substring in its own path — see
        # _STATIC_TOKEN_RE); CDN assets present; and no _static/ directory
        # materialized beside the cached page.
        assert not _local_static_refs(html), _local_static_refs(html)
        assert _CDN_RE.search(html), "expected an https CDN css/js reference"
        assert not list(root.rglob("_static")), "local _static/ bundle written"

        # The interactive cardsort survived assemble → build → prune (PV-2/PV-3).
        soup = BeautifulSoup(html, "html.parser")
        assert soup.select_one('ul[data-component="dragndrop"]') is not None

        # Fired-prune (PV-3): every removed selector is gone from the cached page.
        for description, selector in PRUNE_SELECTORS:
            assert not soup.select(selector), f"{description} survived ({selector})"

        # The cache file is byte-for-byte the pruned HTML the caller received (PV-4).
        cached = (root / "demo-book" / f"{QID}.html").read_text(encoding="utf-8")
        assert cached == html

    def test_cache_invalidates_on_source_change(self, db_conn, tmp_path):
        _seed(db_conn)
        root = tmp_path / "previews"
        first = preview.render_preview(db_conn, QID, cache_root=root)
        assert first.status == "built"
        second = preview.render_preview(db_conn, QID, cache_root=root)
        assert second.status == "cached"  # served from cache, no rebuild

        db_conn.execute(
            "UPDATE questions SET source_xml = ? WHERE question_id = ?",
            (SOURCE.replace("Pentagon", "Hexagon"), QID),
        )
        db_conn.commit()
        rebuilt = preview.render_preview(db_conn, QID, cache_root=root)
        assert rebuilt.status == "built"  # source hash changed → rebuilt
