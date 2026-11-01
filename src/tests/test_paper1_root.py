"""
The Paper 1 scripts' repository-root lookup (``proteus.paper1._root``;
``cc-task-public-export`` PR 2, ruling R1 (a) and the Checkpoint 1 report's J10).

History: the scripts computed the root from their own location: ``parents[6]``,
one level too high, until owner decision O-47 (2026-10-18) made it ``parents[5]``;
``parents[3]`` after the 2026-10-24 move to ``docs/dnd/scripts/``. And two of
them walked up to an ancestor holding both ``src/proteus`` and ``docs/dnd``,
which no public checkout has. Since 2026-10-27 every module takes its ``ROOT``
from ``find_root()``: ``PROTEUS_ROOT`` if set, else the nearest ``pyproject.toml``
above the package, else above the working directory, else a clean error. This
pins the lookup and that each module's ``ROOT`` is the checkout, which
``audit_sample.guard_output_dir`` reads as the repository.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from proteus.paper1 import (
    _root,
    audit_sample,
    audit_training_kit,
    diagnose_mismatches,
    inventory_exports,
    inventory_items,
    paper_tables,
)

REPO = Path(__file__).resolve().parents[2]
MODULES = (
    audit_sample, audit_training_kit, paper_tables,
    inventory_exports, inventory_items, diagnose_mismatches,
)


class TestFindRoot:
    def test_the_package_resolves_to_the_checkout(self):
        assert _root.find_root() == REPO
        assert (REPO / "pyproject.toml").is_file()

    def test_env_override_wins(self, tmp_path):
        assert _root.find_root(env={"PROTEUS_ROOT": str(tmp_path)}) == tmp_path.resolve()

    def test_walks_up_from_the_start_to_the_marker(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("")
        deep = tmp_path / "pkg" / "sub"
        deep.mkdir(parents=True)
        (deep / "module.py").write_text("")
        assert _root.find_root(deep / "module.py", env={}) == tmp_path.resolve()
        assert _root.find_root(deep, env={}) == tmp_path.resolve()

    def test_falls_back_to_the_working_directory(self, tmp_path):
        # An installed wheel (no marker above the package) run from a checkout.
        elsewhere = tmp_path / "site-packages" / "proteus" / "paper1"
        elsewhere.mkdir(parents=True)
        checkout = tmp_path / "checkout"
        (checkout / "sub").mkdir(parents=True)
        (checkout / "pyproject.toml").write_text("")
        assert _root.find_root(elsewhere, cwd=checkout / "sub", env={}) == checkout.resolve()

    def test_no_marker_anywhere_is_a_clean_error_naming_no_path(self, tmp_path):
        # The message runs at import time, before any masking launcher (the
        # PR #247 review round): it names the marker and the override, never
        # the package path or the working directory.
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        with pytest.raises(SystemExit, match="PROTEUS_ROOT") as info:
            _root.find_root(tmp_path / "a", cwd=tmp_path / "b", env={})
        message = str(info.value)
        assert "pyproject.toml" in message
        assert str(tmp_path) not in message and "/" not in message


@pytest.mark.parametrize("module", MODULES, ids=[m.__name__.rsplit(".", 1)[1] for m in MODULES])
def test_each_module_root_is_the_checkout(module):
    assert module.ROOT == REPO


def test_the_output_guard_reads_the_checkout_as_the_repository():
    # A sibling of the checkout is outside it, and data/private/ inside it is
    # allowed (the procedure's archive note).
    sibling = REPO.parent / "not-the-checkout"
    assert not sibling.resolve().is_relative_to(audit_sample.ROOT)
    assert (REPO / "data" / "private" / "x").is_relative_to(audit_sample.ROOT / "data" / "private")
