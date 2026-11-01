"""
The real PreTeXt HTML builder behind the shared preview pipeline (SP-4).

SP-3 shipped the assemble → build → cache pipeline with a pluggable
``PreviewBuilder`` and a placeholder stub. SP-4 supplies the real builder: it
drops an assembled standalone document into a minimal PreTeXt project and runs
the bundled ``pretext`` CLI to produce web-ready HTML.

HTML output needs only ``pretext`` and its HTML-build dependencies, not the
TeX/PDF stack, so a cardsort question's preview builds with the pinned, minimal
toolchain. The builder shells out to the ``pretext`` console script, so this
module is import-light and the heavy dependency is only exercised where previews
are actually built.

Pinned toolchain: ``pretext == 2.55.0``, one pin, shared by pyproject's
``preview`` extra and the docs build's ``requirements.txt`` (owner decision
O-20, 2026-10-17; PR #210's decision 2, option 2a). History: SP-4 pinned
2.41.2; PV-6 re-pinned to 2.42, the toolchain the preview prune selectors were
validated against (``html-static@2.42`` / runestone ``8.1.2``); O-20 re-pinned
to 2.55.0 after the selectors were re-run against real ``html-static@2.55``
output with no drift.

Footprint confirmed during SP-4 (then pretext 2.41.2; re-confirmed on 2.55.0 at
the O-20 re-pin, same dependency shape):

* Installs cleanly in a fresh venv with up-to-date pip/setuptools (~42 packages,
  ~315 MB). On a system Python with Debian-patched setuptools the transitive PDF
  dependency ``pdfCropMargins`` fails to build its wheel, a packaging quirk, not
  a network issue; install the ``preview`` extra into a venv.
* The full dependency tree pulls PDF / asset-generation tooling (``pdfCropMargins``,
  ``PyMuPDF``, ``pillow``, ``playwright``) that an HTML-only cardsort preview does
  not use, pretext hard-requires them, so they can't be pruned from the extra,
  but no TeX/PDF stack or asset-generation pass runs for an HTML build.
* The HTML build invokes Node/npm and fetches the Runestone Services manifest
  from the PreTeXt CDN at build time (the runtime for interactive components
  such as cardsort). The build host therefore needs Node and outbound access to
  that CDN; on first preview the manifest fetch is the only network step.
  Offline, the CLI's own fallback is broken (open-todos item 25): seed
  ``~/.ptx/<version>/rs_cache/rs_services.xml`` from the bundled
  ``runestone_services.xml`` beside it, renamed.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from importlib import resources
from pathlib import Path

log = logging.getLogger(__name__)

__all__ = [
    "build_html",
    "pretext_available",
    "PretextBuildError",
    "PRETEXT_VERSION",
    "PRETEXT_CMD",
    "DEFAULT_BUILD_TIMEOUT",
]

# The pinned toolchain version, kept in sync with pyproject's `preview` extra and
# the bundled books' requirements.txt, one pin since O-20 (2026-10-17).
# Recorded here per SP-4; re-pinned to 2.42 in PV-6 and to 2.55.0 on O-20.
PRETEXT_VERSION = "2.55.0"

# The PreTeXt CLI invocation; the `pretext` package installs this console script.
PRETEXT_CMD = ("pretext",)

# Ceiling for a single preview build, so a hung toolchain can't wedge a caller.
DEFAULT_BUILD_TIMEOUT = 180

# A minimal ptx-version="2" manifest with one HTML target whose source is
# source/main.ptx (matching the bundled books' project.ptx shape). The target
# carries @publication so the build picks up the PV-1 preview publication file
# (portable mode → self-contained HTML). The manifest's @publication resolves
# under the project's default `publication/` directory, exactly as @source
# resolves under `source/`, so the file is written to publication/publication.ptx.
_PROJECT_MANIFEST = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<project ptx-version="2">\n'
    "  <targets>\n"
    '    <target name="web" format="html" source="main.ptx"'
    ' publication="publication.ptx" />\n'
    "  </targets>\n"
    "</project>\n"
)

# The PV-1 preview publication file, shipped as proteus.core package data and
# loaded the same way as the core schema (resources.files("proteus.core")).
_PUBLICATION_RESOURCE = "preview_assets/preview_publication.ptx"


def _preview_publication_xml() -> str:
    """The PV-1 preview publication file as text, from package data."""
    return (
        resources.files("proteus.core")
        .joinpath(_PUBLICATION_RESOURCE)
        .read_text(encoding="utf-8")
    )


class PretextBuildError(RuntimeError):
    """A ``pretext build`` invocation failed or produced no HTML."""


def pretext_available() -> bool:
    """
    True when the PreTeXt CLI is installed and on PATH. The pinned version
    (:data:`PRETEXT_VERSION`) is enforced at install time via the ``preview``
    extra, not re-checked here (that would cost a subprocess per call).
    """
    return shutil.which(PRETEXT_CMD[0]) is not None


def build_html(standalone_doc: str, *, timeout: float = DEFAULT_BUILD_TIMEOUT) -> str:
    """
    Build an assembled standalone PreTeXt document to web-ready HTML and return
    its primary page. Raises :class:`PretextBuildError` on any failure.

    The build uses the PV-1 preview publication file (portable mode), so the
    returned HTML is self-contained: a single page with CDN-hosted CSS/JS and no
    local ``_static/`` bundle. That is what lets the preview embed as a raw
    srcdoc string with no asset serving, retiring SP-4's asset-serving TODO.
    HTML output needs no TeX/PDF stack, so no asset-generation pass runs. This
    function only builds; ``core.preview.render_preview`` applies the thin PV-3
    prune pass to the returned page and caches it.
    """
    with tempfile.TemporaryDirectory(prefix="proteus-preview-") as tmp:
        project = Path(tmp)
        (project / "project.ptx").write_text(_PROJECT_MANIFEST, encoding="utf-8")
        source = project / "source"
        source.mkdir()
        (source / "main.ptx").write_text(standalone_doc, encoding="utf-8")
        # The manifest's @publication resolves under the project's default
        # `publication/` directory, so the preview publication file must live at
        # publication/publication.ptx (not the project root). This was the one
        # bug in the prototype harness.
        publication = project / "publication"
        publication.mkdir()
        (publication / "publication.ptx").write_text(
            _preview_publication_xml(), encoding="utf-8"
        )
        # pretext hard-fails the build unless the publication file's
        # source/directories/@external and @generated resolve, so create the
        # (empty) directories the PV-1 publication file declares. WHERE they
        # must live is version-dependent: 2.42 resolved them at the project
        # root (siblings of project.ptx and source/ (PR #69)) while 2.48 and
        # later, the pinned 2.55.0 included, resolve them *relative to
        # source/*, failing otherwise with 'the directory ".../source/assets"
        # implied by the value "assets" … does not exist' (re-verified on
        # 2.55.0 at the O-20 re-pin: the root pair alone fails, the source/
        # pair alone passes). A preview has no external or generated assets
        # either way, so create both empty pairs and the build satisfies
        # whichever base the installed CLI uses, cheap insurance that also
        # covers an older CLI on PATH.
        for base in (project, source):
            (base / "assets").mkdir(exist_ok=True)
            (base / "generated-assets").mkdir(exist_ok=True)

        try:
            result = subprocess.run(
                [*PRETEXT_CMD, "build", "web"],
                cwd=project,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except FileNotFoundError as exc:  # CLI not installed / not on PATH
            raise PretextBuildError(
                f"the PreTeXt CLI ({PRETEXT_CMD[0]}) is not installed"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise PretextBuildError(f"pretext build timed out after {timeout}s") from exc

        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip() or (
                f"exit status {result.returncode}"
            )
            raise PretextBuildError(f"pretext build failed: {detail}")

        return _read_primary_html(project / "output")


# PreTeXt emits a top-level index.html that immediately redirects to the real
# first page (a one-page article lands on `preview.html`). Embedded in an iframe
# that redirect navigates to a URL that resolves against the host origin, so we
# must follow it at build time and cache the *content* page, not the stub.
_META_REFRESH_RE = re.compile(
    r"<meta\b[^>]*http-equiv\s*=\s*['\"]refresh['\"][^>]*>", re.IGNORECASE
)
_REFRESH_URL_RE = re.compile(r"url\s*=\s*['\"]?([^'\"\s>;]+)", re.IGNORECASE)


def _meta_refresh_target(html: str) -> str | None:
    """The URL a ``<meta http-equiv="refresh">`` points at, or ``None``."""
    tag = _META_REFRESH_RE.search(html)
    if not tag:
        return None
    url = _REFRESH_URL_RE.search(tag.group(0))
    return url.group(1) if url else None


def _read_primary_html(output_dir: Path) -> str:
    """
    Return the built site's primary *content* page: the shallowest ``index.html``
    (the target's landing page), following one PreTeXt redirect hop to the real
    page it points at, else the largest ``.html``. Robust to per-version
    differences in the output subdirectory's name.
    """
    indexes = sorted(output_dir.rglob("index.html"), key=lambda p: len(p.parts))
    if indexes:
        primary = indexes[0]
        html = primary.read_text(encoding="utf-8")
        target = _meta_refresh_target(html)
        if target:
            # Resolve the redirect target within the index's directory, refusing
            # to escape it (a crafted URL must not read outside the build output).
            base = primary.parent.resolve()
            dest = (base / target).resolve()
            if dest.is_file() and base in (dest, *dest.parents):
                return dest.read_text(encoding="utf-8")
        return html
    htmls = list(output_dir.rglob("*.html"))
    if not htmls:
        raise PretextBuildError("pretext build produced no HTML output")
    return max(htmls, key=lambda p: p.stat().st_size).read_text(encoding="utf-8")
