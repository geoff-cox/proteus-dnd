"""
Tests for the public-repo clone helper (checklist item SP-2).

The happy path uses a real ``file://`` clone (real git, no network); the failure
paths (git missing, clone error) are covered with an injected ``run`` or a bad
URL so they don't depend on the environment.
"""

from __future__ import annotations

import subprocess

import pytest

from proteus.core.clone import CloneError, clone_repo


class TestCloneRepo:
    def test_clones_a_public_file_url(self, tmp_path, make_git_repo):
        repo = make_git_repo(tmp_path / "repo", {"source/sec.ptx": "<section/>"})
        dest = tmp_path / "clone"

        out = clone_repo(f"file://{repo}", dest)

        assert out == dest
        assert (dest / "source" / "sec.ptx").read_text() == "<section/>"

    def test_reclone_refreshes_to_latest(self, tmp_path, make_git_repo):
        repo = make_git_repo(tmp_path / "repo", {"a.ptx": "v1"})
        dest = tmp_path / "clone"
        clone_repo(f"file://{repo}", dest)

        (repo / "a.ptx").write_text("v2")
        subprocess.run(
            ["git", "-C", str(repo), "-c", "commit.gpgsign=false",
             "commit", "-aqm", "v2"],
            check=True,
        )
        clone_repo(f"file://{repo}", dest)  # clean re-clone

        assert (dest / "a.ptx").read_text() == "v2"

    def test_checks_out_a_specific_commit_sha(self, tmp_path, make_git_repo):
        # An explicit ref may be a commit SHA, full clone + checkout handles it
        # (a shallow `--branch <sha>` would fail).
        repo = make_git_repo(tmp_path / "repo", {"a.ptx": "v1"})
        sha = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        (repo / "a.ptx").write_text("v2")
        subprocess.run(
            ["git", "-C", str(repo), "-c", "commit.gpgsign=false",
             "commit", "-aqm", "v2"],
            check=True,
        )

        clone_repo(f"file://{repo}", tmp_path / "clone", git_ref=sha)

        assert (tmp_path / "clone" / "a.ptx").read_text() == "v1"  # the pinned commit

    def test_reclone_over_a_file_destination(self, tmp_path, make_git_repo):
        # A clean re-clone must handle a dest that is currently a file (rmtree
        # would raise on it).
        repo = make_git_repo(tmp_path / "repo", {"a.ptx": "v1"})
        dest = tmp_path / "clone"
        dest.write_text("i am a file, not a directory")

        clone_repo(f"file://{repo}", dest)

        assert (dest / "a.ptx").read_text() == "v1"

    def test_missing_git_raises_cloneerror(self, tmp_path):
        def fake_run(*args, **kwargs):
            raise FileNotFoundError("git")

        with pytest.raises(CloneError, match="git is not installed"):
            clone_repo("file:///x", tmp_path / "c", run=fake_run)

    def test_clone_failure_raises_cloneerror(self, tmp_path):
        # A path with no repo -> git clone fails -> CloneError (not a raw
        # CalledProcessError escaping).
        with pytest.raises(CloneError, match="could not clone"):
            clone_repo(f"file://{tmp_path}/nope", tmp_path / "c")
