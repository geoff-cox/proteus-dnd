"""
Isolation-invariant lint (CLAUDE.md rule 4; plan §2.2/§2.4).

Static import rules that keep the multi-service architecture decoupled:

1. Nothing outside ``proteus.services`` may import ``proteus.services.*``
   directly — services are reached only through ``core.registry``, which
   resolves them dynamically (importlib) inside try/except. A static import
   would defeat the failure isolation.
2. No service package may import another service's package.

3. No service table has a foreign key into another service's tables
   (item B1.1): ownership is computed from each descriptor's schema_files and
   migration_dir DDL, everything else is core, and every FK target of a
   service-owned table must belong to the same service or to core.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import proteus
from proteus.core import registry

PROTEUS_ROOT = Path(proteus.__file__).parent
SERVICES_ROOT = PROTEUS_ROOT / "services"

# A3.1 import shims: the old SA module paths re-export the relocated
# implementations, which necessarily means a static import of
# proteus.services.shortanswer from outside services/. These files are the
# documented one-release exception to invariant 1; each consists solely of
# sys.modules aliasing. Remove every entry here together with its shim.
SHIM_EXEMPT = {
    Path("screening/__init__.py"),
    Path("extraction/__init__.py"),
    Path("matching/__init__.py"),
    Path("disposition/__init__.py"),
    Path("codebook.py"),
    Path("vocabulary.py"),
    Path("export.py"),
}

# Client packages: not core, so invariant 1 does not bind them, but each may
# import only the service it is a client of. proteus.paper1 — the Paper 1
# scripts (cc-task-public-export PR 2, ruling R1 (a)) — is the drag-and-drop
# service's client: it reads the archive or a named database through the
# service's own decode/board/reconstruct code and nothing else's.
CLIENT_PACKAGES: dict[str, frozenset[str]] = {
    "paper1": frozenset({"proteus.services.dragndrop"}),
}


def _imported_modules(path: Path) -> set[str]:
    """All module names statically imported by a file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


def _py_files(root: Path):
    return (p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


class TestImportIsolation:
    def test_only_services_import_services(self):
        offenders = []
        for path in _py_files(PROTEUS_ROOT):
            if SERVICES_ROOT in path.parents:
                continue
            rel = path.relative_to(PROTEUS_ROOT)
            if rel in SHIM_EXEMPT:
                continue
            allowed = CLIENT_PACKAGES.get(rel.parts[0], frozenset())
            bad = {
                m
                for m in _imported_modules(path)
                if (m == "proteus.services" or m.startswith("proteus.services."))
                and not any(m == a or m.startswith(a + ".") for a in allowed)
            }
            if bad:
                offenders.append((path.relative_to(PROTEUS_ROOT), sorted(bad)))
        assert offenders == [], (
            "core/root modules must reach services only via core.registry "
            f"(dynamic import); static imports found: {offenders}"
        )

    def test_no_service_imports_another_service(self):
        # The packages the registry's scan finds — the same list discovery
        # runs over, so the lint covers exactly the services this tree carries.
        service_packages = list(registry.known_service_packages())
        assert service_packages, "expected at least one service package"

        offenders = []
        for pkg in service_packages:
            own_prefix = f"proteus.services.{pkg}"
            for path in _py_files(SERVICES_ROOT / pkg):
                for m in _imported_modules(path):
                    if not (
                        m == "proteus.services" or m.startswith("proteus.services.")
                    ):
                        continue  # not a services import at all
                    # A service may import only its own package/submodules —
                    # not another service's, and not the bare services root
                    # (which is where cross-service coupling would sneak in).
                    if m == own_prefix or m.startswith(own_prefix + "."):
                        continue
                    offenders.append((str(path.relative_to(SERVICES_ROOT)), m))
        assert offenders == [], (
            f"a service imported another service's package (or the services "
            f"root): {offenders}"
        )


_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"']?(\w+)", re.IGNORECASE
)


def _tables_created_by(sql: str) -> set[str]:
    return set(_CREATE_TABLE_RE.findall(sql))


def _service_table_owners() -> dict[str, str]:
    """table name -> owning service name, from each descriptor's DDL."""
    owners: dict[str, str] = {}
    for svc in registry.available_services():
        ddl_files = list(svc.schema_files)
        if svc.migration_dir is not None:
            ddl_files.extend(sorted(Path(svc.migration_dir).glob("*.sql")))
        for ddl in ddl_files:
            for table in _tables_created_by(Path(ddl).read_text(encoding="utf-8")):
                owners[table] = svc.name
    return owners


class TestSchemaIsolation:
    def test_no_service_table_fks_into_another_service(self, db_conn):
        owners = _service_table_owners()
        assert owners, "expected at least one service-owned table"

        offenders = []
        for table, owner in owners.items():
            for fk in db_conn.execute(f"PRAGMA foreign_key_list({table})"):
                target = fk["table"]
                target_owner = owners.get(target)  # None = core table
                if target_owner is not None and target_owner != owner:
                    offenders.append((table, target))
        assert offenders == [], (
            f"service tables may FK into core tables or their own service's "
            f"tables only; cross-service FKs found: {offenders}"
        )
