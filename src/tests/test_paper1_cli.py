"""
The Paper 1 package's console launchers and the export inventory's UI
cross-check (the PR #247 Copilot review round, 2026-10-27).

Two findings, both real: the console entry points of the three
inventory/diagnosis modules called ``main()`` directly, bypassing the
``__main__`` guards that mask every path (and, for the item inventory, every
mapped surname) in an unexpected exception; and ``inventory_exports.preview_check``
imported the UI's ``preview_upload`` unconditionally, which the public pipeline
(no ``proteus_ui``) cannot satisfy, nor could a private console-script run
once the package stopped putting the checkout on ``sys.path``. Each module now
has one launcher, ``cli()``, behind both its entry point and ``python -m``; the
cross-check degrades to a note.
"""

from __future__ import annotations

import sys
from collections import Counter

import pytest

from proteus.core.ingest.readers import datashop
from proteus.paper1 import _root, diagnose_mismatches, inventory_exports, inventory_items

SECRET = "/home/someone/data/private/some-export.tab"
LAUNCHERS = [
    pytest.param(inventory_exports, "inventory_exports: ", 2, id="inventory_exports"),
    pytest.param(inventory_items, "inventory_items: ", 2, id="inventory_items"),
    pytest.param(diagnose_mismatches, "", 1, id="diagnose_mismatches"),
]


class TestLaunchers:
    @pytest.mark.parametrize("module, prefix, code", LAUNCHERS)
    def test_an_unexpected_exception_is_masked_and_exits_nonzero(
        self, monkeypatch, capsys, module, prefix, code
    ):
        def boom():
            raise RuntimeError(f"cannot read {SECRET}")

        monkeypatch.setattr(module, "main", boom)
        assert module.cli() == code
        err = capsys.readouterr().err
        assert err.startswith(f"{prefix}RuntimeError: ")
        assert SECRET not in err and "data/private" not in err and "someone" not in err
        assert "<masked" in err

    @pytest.mark.parametrize("module, prefix, code", LAUNCHERS)
    def test_a_clean_run_returns_zero(self, monkeypatch, module, prefix, code):
        monkeypatch.setattr(module, "main", lambda: None)
        assert module.cli() == 0

    @pytest.mark.parametrize("module, prefix, code", LAUNCHERS)
    def test_systemexit_passes_through_unchanged(self, monkeypatch, module, prefix, code):
        # argparse errors (already masked by _MaskedParser) and explicit
        # refusals keep their own exit status.
        def refuse():
            raise SystemExit(3)

        monkeypatch.setattr(module, "main", refuse)
        with pytest.raises(SystemExit) as info:
            module.cli()
        assert info.value.code == 3

    def test_every_entry_point_names_a_launcher(self):
        import tomllib

        scripts = tomllib.loads((_root.find_root() / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
        for name in ("inventory_exports", "inventory_items", "diagnose_mismatches"):
            assert scripts[f"proteus-{name.replace('_', '-')}"] == f"proteus.paper1.{name}:cli"


class _StubMask:
    def name(self, path):
        return "Y00-B00-P00"


class TestPreviewCheck:
    def _inspected(self, path):
        events = list(datashop.read(path))
        return {"rows": len(events), "sel": Counter(e.selection for e in events)}

    def test_without_the_ui_the_cross_check_is_a_note(self, monkeypatch, golden_core_tab):
        for name in list(sys.modules):
            if name == "proteus_ui" or name.startswith("proteus_ui."):
                monkeypatch.delitem(sys.modules, name)
        monkeypatch.setitem(sys.modules, "proteus_ui", None)  # every import of it raises
        monkeypatch.setitem(inventory_exports.INSPECTED, golden_core_tab, self._inspected(golden_core_tab))
        lines: list[str] = []
        inventory_exports.preview_check([golden_core_tab], _StubMask(), lines.append)
        assert lines == [inventory_exports.PREVIEW_CHECK_NOT_RUN]

    def test_with_the_ui_the_cross_check_runs_from_a_console_script_path(self, monkeypatch, golden_core_tab):
        # The private checkout: proteus_ui is a folder beside src/, not an
        # installed package, so the launcher must find it from the checkout.
        if not (_root.find_root() / "proteus_ui").is_dir():
            pytest.skip("proteus_ui is not in this tree (the public export)")
        pytest.importorskip("pandas")
        monkeypatch.setitem(inventory_exports.INSPECTED, golden_core_tab, self._inspected(golden_core_tab))
        lines: list[str] = []
        inventory_exports.preview_check([golden_core_tab], _StubMask(), lines.append)
        assert lines[0].startswith("| file | n_events agrees |")
        assert any(line.startswith("| Y00-B00-P00 | yes | yes |") for line in lines), lines
