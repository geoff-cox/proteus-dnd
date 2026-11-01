"""
Service registry (plan item A2.1), the expandability mechanism.

Each question-type service ships one ``ServiceDescriptor`` as the module-level
``DESCRIPTOR`` attribute of ``proteus.services.<package>.service``. The
registry imports those modules lazily, each inside ``try/except``: a service
whose import fails (broken dependency, syntax error, missing DESCRIPTOR) is
reported as unavailable and skipped, never raising into the caller. That is
the failure-isolation guarantee of plan §2.2, a broken ``dnd`` can never take
down ``sa`` commands or pages, and vice versa.

Adding question type N+1 = add a package under ``proteus/services/`` whose
``service`` module exposes ``DESCRIPTOR``. The registry finds it by scanning
that folder (``known_service_packages``); there is no list to edit, no edits to
core logic, no edits to other services. A tree that carries only some of
the services (the public export, ``cc-task-public-export`` PR 1) finds exactly
those, with nothing to warn about.

Isolation invariants enforced around this module (lint tests accompany the
refactor):

* core never imports from ``proteus.services.*`` except through this registry;
* no service imports another service's package.
"""

from __future__ import annotations

import argparse
import importlib
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

log = logging.getLogger(__name__)

# The folder the scan reads: proteus/services/, beside this package.
SERVICES_DIR: Path = Path(__file__).resolve().parent.parent / "services"


def known_service_packages(root: Path | None = None) -> tuple[str, ...]:
    """
    The service packages this tree carries: every subdirectory of
    ``proteus/services/`` that is a package (``__init__.py``) and ships a
    ``service`` module (``service.py``), sorted by directory name.

    A filesystem scan, never an import: whether the module really exposes a
    ``ServiceDescriptor`` is decided by ``discover``, inside its try/except,
    so a broken service is still *found* here and then reported as failed
    there (the failure-isolation guarantee), while an absent one is simply not
    found, a tree without ``services/shortanswer/`` lists only ``dragndrop``
    and nothing warns. Sorted by name so the order is deterministic in every
    tree (``dragndrop`` before ``shortanswer``; nothing persisted or reported
    depends on it, the manifest fingerprint sorts its keys). ``root`` is a
    test seam.
    """
    base = SERVICES_DIR if root is None else Path(root)
    if not base.is_dir():
        return ()
    return tuple(
        entry.name
        for entry in sorted(base.iterdir(), key=lambda e: e.name)
        if entry.is_dir()
        and (entry / "__init__.py").is_file()
        and (entry / "service.py").is_file()
    )


# Question-type service packages known to this build, the scan's result at
# import time, in the order the services are displayed and applied. The
# package directory name under proteus/services/; the descriptor's `name`
# supplies the (usually shorter) CLI namespace, e.g. shortanswer -> `sa`.
# Read by ``discover`` at call time (a test may monkeypatch it to simulate a
# tree without a service).
KNOWN_SERVICE_PACKAGES: tuple[str, ...] = known_service_packages()


@dataclass(frozen=True)
class Analysis:
    """One analysis a service offers, declared on its descriptor (item B4.12).

    The service declares its *ordered* analyses; one generic shell renders them
    (the type → question → analyses front door) without naming the service. The
    fields are all data the UI consumer reads, core never interprets them:

    * ``key``  : stable identifier, unique within the service (e.g. ``"attempts"``).
    * ``title``: human label the shell shows.
    * ``icon`` : optional display glyph.
    * ``page`` : optional route hint for the Streamlit shell (a page path such
      as ``"pages/dnd/Attempts.py"``); ``None`` for an analysis with no dedicated
      page yet. Opaque to core; only the UI dereferences it.
    """

    key: str
    title: str
    icon: str = ""
    page: str | None = None


# Canonical row-filter dimension keys a service may declare in
# ``ServiceDescriptor.filter_dimensions`` (item B4.12). They name the shared
# ``events`` columns the row-level filter (B4.10/CD-1) narrows on, no service
# owns them, so the set is defined here, in core, for every service to draw from.
FILTER_DIMENSIONS: frozenset[str] = frozenset({"course", "class", "week", "date"})


@dataclass(frozen=True)
class ServiceDescriptor:
    """
    One question-type service's contract with core (plan §2.2).

    Only the fields a service has grown into need to be populated; the
    remaining ones default to "not provided yet" and are filled in by later
    checklist items (noted per field).
    """

    name: str  # CLI namespace and pipeline_runs.service tag, e.g. "sa"
    title: str  # human-readable, e.g. "Short Answer"
    selection_values: frozenset[str]  # DataShop Selection values it claims
    register_cli: Callable[[argparse._SubParsersAction], None]
    status: Callable[[sqlite3.Connection], dict[str, object]]
    # Datasets the service can export (name -> dataset definition).
    export_datasets: dict = field(default_factory=dict)
    # Service-owned slices of the baseline schema, applied by db.bootstrap
    # after the core schema (item A3.5).
    schema_files: tuple[Path, ...] = ()
    # Service-owned forward migrations, applied by core.migrate after core's
    # (first real use: dnd B1.1).
    migration_dir: Path | None = None
    # Materialize the service's unit-of-analysis tables from `events`.
    # Called as derive(conn, import_id=..., question_id=...) (both filters
    # optional, None = unfiltered) and returns the service's DeriveResult.
    # None = the service has no derive step.
    derive: Callable | None = None
    # PreTeXt question-type element tags the service claims (plan §2.6),
    # e.g. {"cardsort"}. core.sources recognizes the union of these across
    # services when walking a textbook source tree.
    question_types: frozenset[str] = frozenset()
    # Build the service's artifacts from one questions row's source_xml
    # (plan §2.6 / B2.2). Called as source_handler(conn, question_row) for
    # each synced question whose question_type the service claims; returns a
    # short status string ('created', 'unchanged', …) or None. None field =
    # the service derives nothing from question source.
    source_handler: Callable | None = None
    # Delete the service's derived rows for one import, and report the blast
    # radius (item B4.14). Called as delete_import_rows(conn, import_id, *,
    # dry_run=True) and returns an ordered {table: rows} map; dry_run=True
    # counts without deleting (for the confirmation dialog), dry_run=False
    # deletes and returns the removed counts. Registry-driven so core's import
    # delete never names a service table. None = nothing to clean up on delete.
    delete_import_rows: Callable | None = None
    # The service's contribution to the run manifest (expansion memo E-03;
    # core.manifest): called as manifest(conn) and returns a JSON-
    # serializable mapping of provenance facts the database holds beyond
    # table counts, e.g. the DnD identifier-alias set with its content
    # hash. Registry-driven so core names no service table. None = nothing
    # beyond the generic sections.
    manifest: Callable | None = None
    # The ordered analyses this service offers, declared for the descriptor-
    # driven front door (item B4.12): one generic shell renders them in this
    # order, naming no service. Empty = the service declares no analyses yet.
    analyses: tuple[Analysis, ...] = ()
    # The row-filter dimensions this service supports (item B4.12), drawn from
    # FILTER_DIMENSIONS. The shared row filter renders exactly these, in this
    # order, not a hardcoded set. Empty = the service offers no row filter.
    filter_dimensions: tuple[str, ...] = ()


@dataclass(frozen=True)
class FailedService:
    """A service package that could not be loaded."""

    package: str
    error: str  # stringified exception; kept as text so the record is inert


def discover(
    packages: Iterable[str] | None = None,
) -> tuple[tuple[ServiceDescriptor, ...], tuple[FailedService, ...]]:
    """
    Import each service package's descriptor module and return
    ``(available, failed)``.

    Stateless by design: every call re-resolves (module imports are cached by
    Python, so repeat calls are cheap, and a test can inject `packages`).
    Never raises for a broken service, that is the point.
    """
    available: list[ServiceDescriptor] = []
    failed: list[FailedService] = []
    for pkg in KNOWN_SERVICE_PACKAGES if packages is None else packages:
        module_name = f"proteus.services.{pkg}.service"
        try:
            module = importlib.import_module(module_name)
            descriptor = module.DESCRIPTOR
            if not isinstance(descriptor, ServiceDescriptor):
                raise TypeError(
                    f"{module_name}.DESCRIPTOR is not a ServiceDescriptor "
                    f"(got {type(descriptor).__name__})"
                )
        except Exception as exc:  # noqa: BLE001 - isolation boundary
            log.warning("Service package %r unavailable: %s", pkg, exc)
            failed.append(
                FailedService(package=pkg, error=f"{type(exc).__name__}: {exc}")
            )
            continue
        available.append(descriptor)
    return tuple(available), tuple(failed)


def available_services(
    packages: Iterable[str] | None = None,
) -> tuple[ServiceDescriptor, ...]:
    """The descriptors of every service that loaded cleanly."""
    return discover(packages)[0]


def failed_services(
    packages: Iterable[str] | None = None,
) -> tuple[FailedService, ...]:
    """The packages that failed to load, with their errors."""
    return discover(packages)[1]


def migration_sources(
    services: Iterable[ServiceDescriptor] | None = None,
) -> list[tuple[str, Path]]:
    """
    ``(name, migration_dir)`` pairs for services that own migrations, in
    registry order, the ``service_sources`` argument of
    ``core.migrate.migrate``, which applies them after core's (plan §2.4).
    """
    if services is None:
        services = available_services()
    return [(s.name, s.migration_dir) for s in services if s.migration_dir is not None]
