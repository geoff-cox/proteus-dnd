"""
Clone a public textbook repo for source sync (checklist item SP-2).

``sync-sources`` clones the registered book's **public** repo and extracts each
question's raw source from it. The clone is deliberately minimal: a shallow
(``--depth 1``) clone of a credential-free URL, registration already refused
anything else (:func:`proteus.core.textbooks.add_textbook`).

The clone is a clean re-clone each call (it removes any prior copy first), so the
working tree always matches the requested ref, that is the "clone/refresh" of
SP-2. History isn't needed, hence the shallow depth.

``run`` is injectable so the failure paths (git missing, clone error) can be
exercised without a subprocess; the happy path is covered with a real ``file://``
clone so no network is required.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

__all__ = ["clone_repo", "CloneError", "DEFAULT_CLONE_ROOT"]

# Where clones land by default; parallel to data/question_sources.
DEFAULT_CLONE_ROOT = Path("data") / "textbook_clones"


class CloneError(RuntimeError):
    """A textbook repo could not be cloned (git missing, bad URL, blocked)."""


def clone_repo(
    git_repo_url: str,
    dest: str | Path,
    *,
    git_ref: str | None = None,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> Path:
    """
    Shallow-clone ``git_repo_url`` into ``dest`` and return the clone path.

    A clean re-clone: any existing ``dest`` is removed first (a file or symlink
    is unlinked, not rmtree'd, so a symlink's target is never touched). With no
    ``git_ref`` (or ``HEAD``) the default branch is cloned shallowly; an explicit
    ref may be a branch, tag, **or commit SHA**, so a full clone + ``checkout``
    is used (``--branch`` rejects a SHA and a shallow clone can't reach an
    arbitrary one). Raises :class:`CloneError` if git is unavailable or a step
    fails (a blocked network, an unreachable URL, or a missing ref).
    """
    dest = Path(dest)
    _clear_dest(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    pinned = bool(git_ref) and git_ref != "HEAD"
    clone_cmd = ["git", "clone", "--quiet"]
    if not pinned:
        clone_cmd += ["--depth", "1"]
    clone_cmd += [git_repo_url, str(dest)]
    _run_git(run, clone_cmd, f"could not clone {git_repo_url!r}")

    if pinned:
        _run_git(
            run,
            ["git", "-C", str(dest), "checkout", "--quiet", git_ref],
            f"could not check out ref {git_ref!r} of {git_repo_url!r}",
        )

    log.info("Cloned %s into %s", git_repo_url, dest)
    return dest


def _clear_dest(dest: Path) -> None:
    """Remove a prior clone destination. A symlink/file is unlinked (never
    followed into rmtree, which could delete a symlink target's contents)."""
    if dest.is_symlink() or dest.is_file():
        dest.unlink()
    elif dest.is_dir():
        shutil.rmtree(dest)


def _run_git(
    run: Callable[..., subprocess.CompletedProcess], cmd: list[str], context: str
) -> None:
    try:
        run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise CloneError("git is not installed or not on PATH") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip() or (exc.stdout or "").strip()
        raise CloneError(context + (f": {detail}" if detail else "")) from exc
