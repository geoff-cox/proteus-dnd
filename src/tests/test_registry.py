"""
Tests for the service registry (plan item A2.1; discovery by scan since
``cc-task-public-export`` PR 1).

The registry is the failure-isolation boundary of the multi-service design:
the service packages are *found* by scanning ``proteus/services/`` (no list to
edit), their descriptors are imported lazily inside try/except, a broken
service is reported and skipped, and discovery never raises. These tests pin
that contract against whatever services this tree carries (the private
monorepo has ``shortanswer`` and ``dragndrop``, the public export only
``dragndrop``), plus the shape of each descriptor that is present.
"""

from __future__ import annotations

import logging
import sys
import types

import pytest

from proteus.core import registry
from proteus.core.registry import FailedService, ServiceDescriptor

KNOWN = registry.known_service_packages()
PRESENT = {d.name for d in registry.available_services()}
requires_sa = pytest.mark.skipif(
    "sa" not in PRESENT, reason="the Short Answer service is not in this tree (R14)"
)


class TestScan:
    def test_scan_lists_the_service_folders_sorted(self):
        # Every subfolder of proteus/services/ that is a package with a
        # `service` module, by name (nothing else, nothing hard-coded).
        expected = sorted(
            p.name
            for p in registry.SERVICES_DIR.iterdir()
            if p.is_dir() and (p / "__init__.py").is_file() and (p / "service.py").is_file()
        )
        assert list(KNOWN) == expected
        assert "dragndrop" in KNOWN  # the one service every tree carries
        assert registry.KNOWN_SERVICE_PACKAGES == KNOWN

    def test_scan_ignores_what_is_not_a_service_package(self, tmp_path):
        services = tmp_path / "services"
        # A real service package.
        (services / "zeta").mkdir(parents=True)
        (services / "zeta" / "__init__.py").write_text("")
        (services / "zeta" / "service.py").write_text("")
        # A package without a service module, a folder that is not a package,
        # a stray file and a cache folder: none of them is a service.
        (services / "alpha").mkdir()
        (services / "alpha" / "__init__.py").write_text("")
        (services / "beta").mkdir()
        (services / "beta" / "service.py").write_text("")
        (services / "gamma.py").write_text("")
        (services / "__pycache__").mkdir()
        assert registry.known_service_packages(services) == ("zeta",)

    def test_scan_of_a_missing_folder_is_empty(self, tmp_path):
        assert registry.known_service_packages(tmp_path / "nowhere") == ()

    def test_every_found_package_loads_and_nothing_warns(self, caplog):
        # In any tree, what the scan finds is what discover() loads: no
        # failures, no warning. The public tree (dnd alone) starts silently.
        with caplog.at_level(logging.WARNING, logger="proteus.core.registry"):
            available, failed = registry.discover()
        assert failed == ()
        assert caplog.records == []
        assert len(available) == len(KNOWN)
        # Registry order is the scan's order.
        names = [d.name for d in available]
        assert names == [d.name for d in registry.available_services()]
        assert registry.failed_services() == ()


@requires_sa
class TestDiscoverSa:
    def test_sa_descriptor_shape(self):
        sa = next(d for d in registry.available_services() if d.name == "sa")
        assert sa.title == "Short Answer"
        assert sa.selection_values == frozenset({"shortanswer"})
        assert callable(sa.register_cli)
        assert callable(sa.status)
        assert "responses" in sa.export_datasets


class TestDiscoverDnd:
    def test_dnd_service_is_available(self):
        available, _ = registry.discover()
        dnd = next(d for d in available if d.name == "dnd")
        assert dnd.title == "Drag-and-Drop"
        assert dnd.selection_values == frozenset(
            {"dragNdrop", "dragNdrop-drop", "dragNdrop-reset"}
        )
        assert callable(dnd.register_cli)
        assert callable(dnd.status)
        assert callable(dnd.derive)
        # The first service-owned migration dir (item B1.1).
        assert dnd.migration_dir is not None


def _break_import_of(monkeypatch, package: str) -> None:
    """Make ``proteus.services.<package>.service`` raise at import, as a
    broken dependency would; every other import is untouched."""
    real_import = registry.importlib.import_module
    target = f"proteus.services.{package}.service"

    def boom(name, *args, **kwargs):
        if name == target:
            raise RuntimeError("boom at import time")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(
        registry, "importlib", types.SimpleNamespace(import_module=boom)
    )


class TestFailureIsolation:
    def test_unknown_package_is_failed_not_fatal(self):
        available, failed = registry.discover(packages=("dragndrop", "nonexistent"))
        assert [d.name for d in available] == ["dnd"]
        assert len(failed) == 1
        assert failed[0].package == "nonexistent"
        assert "ModuleNotFoundError" in failed[0].error

    def test_a_synthetic_broken_package_leaves_every_real_service_available(
        self, monkeypatch
    ):
        # The §2.2 guarantee, keyed on nothing this tree may lack (R21): a
        # package that breaks at import loses only itself; every service the
        # scan found still loads, and discovery never raises.
        _break_import_of(monkeypatch, "synthetic")
        available, failed = registry.discover(packages=KNOWN + ("synthetic",))
        assert [d.name for d in available] == [
            d.name for d in registry.available_services()
        ]
        assert [f.package for f in failed] == ["synthetic"]
        assert "RuntimeError: boom at import time" in failed[0].error

    @pytest.mark.parametrize("victim", KNOWN)
    def test_a_broken_service_leaves_the_others_available(self, monkeypatch, victim):
        # Phase-B form, over the real services: break each one in turn; the
        # rest stay available (with two services this is the old "a broken
        # sa can never take down dnd, and vice versa").
        before = [d.name for d in registry.available_services()]
        _break_import_of(monkeypatch, victim)
        available, failed = registry.discover()
        assert [f.package for f in failed] == [victim]
        assert len(available) == len(before) - 1
        assert set(d.name for d in available) < set(before)

    @requires_sa
    def test_broken_sa_leaves_dnd_available(self, monkeypatch):
        _break_import_of(monkeypatch, "shortanswer")
        available, failed = registry.discover()
        assert [d.name for d in available] == ["dnd"]
        assert [f.package for f in failed] == ["shortanswer"]

    def test_import_error_in_service_module_is_contained(self, monkeypatch):
        # The F6 failure mode in isolation: a service module raising at
        # import is reported and discovery keeps going, never raises.
        _break_import_of(monkeypatch, "dragndrop")
        available, failed = registry.discover(packages=("dragndrop",))
        assert available == ()
        assert [f.package for f in failed] == ["dragndrop"]
        assert "RuntimeError: boom at import time" in failed[0].error

    def test_module_without_descriptor_is_failed(self, monkeypatch):
        fake = types.ModuleType("proteus.services.fake.service")
        monkeypatch.setitem(sys.modules, "proteus.services.fake.service", fake)
        available, failed = registry.discover(packages=("fake",))
        assert available == ()
        assert failed[0].package == "fake"
        assert "AttributeError" in failed[0].error

    def test_wrong_descriptor_type_is_failed(self, monkeypatch):
        fake = types.ModuleType("proteus.services.fake2.service")
        fake.DESCRIPTOR = object()
        monkeypatch.setitem(sys.modules, "proteus.services.fake2.service", fake)
        available, failed = registry.discover(packages=("fake2",))
        assert available == ()
        assert "not a ServiceDescriptor" in failed[0].error


class TestMigrationSources:
    def test_every_service_with_migrations_is_a_source(self):
        # dnd/0001 (item B1.1) was the first service-owned dir; the SA dir
        # landed with CP1.1 (its pre-split history stays in core's
        # migrations). Source order follows the registry's package order.
        sources = dict(registry.migration_sources())
        assert set(sources) == {
            d.name for d in registry.available_services() if d.migration_dir is not None
        }
        assert (sources["dnd"] / "0001_dnd_schema.sql").is_file()
        if "sa" in PRESENT:
            assert (sources["sa"] / "0001_codebook_surface_markers.sql").is_file()

    def test_service_with_migration_dir_is_listed(self, tmp_path):
        desc = ServiceDescriptor(
            name="fake",
            title="Fake",
            selection_values=frozenset(),
            register_cli=lambda sub: None,
            status=lambda conn: {},
            migration_dir=tmp_path,
        )
        assert registry.migration_sources([desc]) == [("fake", tmp_path)]

    def test_failed_service_record_is_inert(self):
        f = FailedService(package="x", error="Boom: y")
        assert isinstance(f.error, str)
