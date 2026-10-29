"""
Tests for the PreTeXt HTML builder behind the preview pipeline (SP-4).

The ``pretext`` CLI is mocked, so these run without the real toolchain: they
verify the project scaffolding, command, output discovery, and error handling.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from proteus.core import pretext

DOC = '<?xml version="1.0"?>\n<pretext><article><title>t</title></article></pretext>\n'


class TestPretextAvailable:
    def test_reflects_path_lookup(self, monkeypatch):
        monkeypatch.setattr(pretext.shutil, "which", lambda name: "/usr/bin/pretext")
        assert pretext.pretext_available() is True
        monkeypatch.setattr(pretext.shutil, "which", lambda name: None)
        assert pretext.pretext_available() is False


class TestBuildHtml:
    def _fake_run(self, *, returncode=0, stdout="", stderr="", output_html="index.html"):
        """A subprocess.run stand-in that scaffolds the expected build output."""

        def run(cmd, cwd, capture_output, text, timeout):
            assert cmd == ["pretext", "build", "web"]
            project = Path(cwd)
            # The builder must have written a minimal project for the CLI.
            assert (project / "project.ptx").is_file()
            assert (project / "source" / "main.ptx").read_text(encoding="utf-8") == DOC
            # PV-2: the preview publication file is written under publication/
            # (where the manifest's @publication resolves) and is the portable
            # boulder file, so the build produces self-contained HTML.
            manifest = (project / "project.ptx").read_text(encoding="utf-8")
            assert 'publication="publication.ptx"' in manifest
            pub = (project / "publication" / "publication.ptx").read_text(
                encoding="utf-8"
            )
            assert 'portable="yes"' in pub
            # PR #69: the empty external/generated dirs the publication file's
            # @external/@generated point at exist at the project root, where
            # pretext 2.42 resolved them, AND under source/, where 2.48 and
            # later — the pinned 2.55.0 included — resolve the same attributes
            # (a build with only the root pair fails there with 'the directory
            # ".../source/assets" implied by the value "assets" … does not
            # exist'). Both bases, so either CLI builds.
            assert (project / "assets").is_dir()
            assert (project / "generated-assets").is_dir()
            assert (project / "source" / "assets").is_dir()
            assert (project / "source" / "generated-assets").is_dir()
            if output_html and returncode == 0:
                out = project / "output" / "web"
                out.mkdir(parents=True, exist_ok=True)
                (out / output_html).write_text(
                    "<html><body>BUILT BY PRETEXT</body></html>", encoding="utf-8"
                )
            return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

        return run

    def test_success_returns_primary_page(self, monkeypatch):
        monkeypatch.setattr(pretext.subprocess, "run", self._fake_run())
        html = pretext.build_html(DOC)
        assert "BUILT BY PRETEXT" in html

    def test_nonzero_exit_raises_with_detail(self, monkeypatch):
        monkeypatch.setattr(
            pretext.subprocess, "run", self._fake_run(returncode=1, stderr="bad xml")
        )
        with pytest.raises(pretext.PretextBuildError, match="bad xml"):
            pretext.build_html(DOC)

    def test_nonzero_exit_without_detail_reports_status(self, monkeypatch):
        monkeypatch.setattr(pretext.subprocess, "run", self._fake_run(returncode=2))
        with pytest.raises(pretext.PretextBuildError, match="exit status 2"):
            pretext.build_html(DOC)

    def test_no_output_raises(self, monkeypatch):
        monkeypatch.setattr(
            pretext.subprocess, "run", self._fake_run(output_html=None)
        )
        with pytest.raises(pretext.PretextBuildError, match="no HTML"):
            pretext.build_html(DOC)

    def test_missing_cli_raises(self, monkeypatch):
        def run(*a, **k):
            raise FileNotFoundError("pretext")

        monkeypatch.setattr(pretext.subprocess, "run", run)
        with pytest.raises(pretext.PretextBuildError, match="not installed"):
            pretext.build_html(DOC)

    def test_timeout_raises(self, monkeypatch):
        def run(cmd, cwd, capture_output, text, timeout):
            raise pretext.subprocess.TimeoutExpired(cmd, timeout)

        monkeypatch.setattr(pretext.subprocess, "run", run)
        with pytest.raises(pretext.PretextBuildError, match="timed out"):
            pretext.build_html(DOC, timeout=1)


class TestPreviewPublication:
    def test_manifest_target_carries_publication_attribute(self):
        # PV-2: the build target points at publication.ptx so portable mode runs.
        assert 'publication="publication.ptx"' in pretext._PROJECT_MANIFEST

    def test_publication_asset_is_portable_boulder_with_required_directories(self):
        # The PV-1 asset loads from package data and is the proven config:
        # portable + boulder. PR #69: it also declares source/directories with
        # @external/@generated — pretext hard-fails without them (2.42 and
        # 2.55.0 alike) — pointing at the empty scratch dirs build_html creates.
        # Parse it (not a substring scan) so the explanatory comment that names
        # <directories> doesn't skew the result.
        import xml.etree.ElementTree as ET

        root = ET.fromstring(pretext._preview_publication_xml().encode("utf-8"))
        assert root.find("html/platform").get("portable") == "yes"
        assert root.find("html/css").get("theme") == "boulder"
        directories = root.find("source/directories")
        assert directories is not None
        assert directories.get("external") and directories.get("generated")


class TestReadPrimaryHtml:
    def test_prefers_shallowest_index(self, tmp_path):
        deep = tmp_path / "web" / "sub"
        deep.mkdir(parents=True)
        (tmp_path / "web" / "index.html").write_text("TOP", encoding="utf-8")
        (deep / "index.html").write_text("DEEP", encoding="utf-8")
        assert pretext._read_primary_html(tmp_path) == "TOP"

    def test_falls_back_to_largest_html(self, tmp_path):
        (tmp_path / "small.html").write_text("x", encoding="utf-8")
        (tmp_path / "big.html").write_text("x" * 100, encoding="utf-8")
        assert pretext._read_primary_html(tmp_path) == "x" * 100

    def test_no_html_raises(self, tmp_path):
        with pytest.raises(pretext.PretextBuildError, match="no HTML"):
            pretext._read_primary_html(tmp_path)

    def test_follows_meta_refresh_to_content_page(self, tmp_path):
        # PreTeXt's index.html is a redirect stub to the real page; we cache the
        # content it points at, not the stub (which would navigate an iframe).
        web = tmp_path / "web"
        web.mkdir()
        (web / "index.html").write_text(
            "<html><head><meta http-equiv=\"refresh\" "
            "content=\"0; URL='preview.html'\"></head><body></body></html>",
            encoding="utf-8",
        )
        (web / "preview.html").write_text("REAL CONTENT", encoding="utf-8")
        assert pretext._read_primary_html(tmp_path) == "REAL CONTENT"

    def test_meta_refresh_to_missing_file_returns_index(self, tmp_path):
        web = tmp_path / "web"
        web.mkdir()
        stub = (
            "<meta http-equiv=\"refresh\" content=\"0; URL='gone.html'\">"
        )
        (web / "index.html").write_text(stub, encoding="utf-8")
        # Target doesn't exist → fall back to the index itself, no crash.
        assert pretext._read_primary_html(tmp_path) == stub

    def test_meta_refresh_escaping_target_refused(self, tmp_path):
        web = tmp_path / "web"
        web.mkdir()
        (tmp_path / "secret.html").write_text("SECRET", encoding="utf-8")
        stub = (
            "<meta http-equiv=\"refresh\" content=\"0; URL='../secret.html'\">"
        )
        (web / "index.html").write_text(stub, encoding="utf-8")
        # A redirect target outside the index's directory must not be read.
        assert pretext._read_primary_html(tmp_path) == stub
