"""The repository root for the Paper 1 scripts (ruling R1 (a) of
``cc-task-public-export``; the Checkpoint 1 report's J10).

History: the scripts computed the root from their own location — ``parents[6]``
until owner decision O-47 (2026-10-18) made it ``parents[5]``, ``parents[3]``
after the 2026-10-24 move to ``docs/dnd/scripts/`` — and two of them walked up
to an ancestor holding both ``src/proteus`` and ``docs/dnd``, which no public
checkout has. Since 2026-10-27 the root is found here, once, for every module.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

ENV_VAR = "PROTEUS_ROOT"
MARKER = "pyproject.toml"


def _nearest_marker(start: Path) -> Path | None:
    for candidate in (start, *start.parents):
        if (candidate / MARKER).is_file():
            return candidate
    return None


def find_root(
    start: Path | None = None,
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    """
    The checkout the scripts run in.

    In order: ``PROTEUS_ROOT`` if set (a copy running outside the tree, or an
    operator pinning the root); else the nearest ancestor of this package
    that holds ``pyproject.toml`` (an editable install, or a venv inside the
    checkout); else the nearest such ancestor of the working directory (a
    wheel installed elsewhere, run from the checkout); else a clean error.
    ``start``, ``cwd`` and ``env`` are test seams.
    """
    environ = os.environ if env is None else env
    override = environ.get(ENV_VAR)
    if override:
        return Path(override).resolve()
    here = (Path(__file__) if start is None else Path(start)).resolve()
    found = _nearest_marker(here.parent if here.is_file() else here)
    if found is None:
        found = _nearest_marker((Path.cwd() if cwd is None else Path(cwd)).resolve())
    if found is None:
        # Path-free on purpose (§2.3a): this runs at import time, before any
        # module's masking launcher, and the package path can carry a home
        # directory. The marker and the override are enough to recover.
        raise SystemExit(
            f"error: repository root not found — no {MARKER} above the package "
            f"or the working directory; set {ENV_VAR} to the checkout"
        )
    return found
