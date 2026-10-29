"""
Item A1.1: db.py and runs.py moved to proteus.core with compatibility shims at
the old import paths. These tests pin that the shims re-export the *same*
objects, so old and new import paths cannot silently diverge while call sites
are migrated.
"""

from __future__ import annotations

import proteus.core.db as core_db
import proteus.core.runs as core_runs
from proteus import db as shim_db
from proteus import runs as shim_runs


class TestDbShim:
    def test_shim_reexports_same_objects(self):
        assert shim_db.connect is core_db.connect
        assert shim_db.bootstrap is core_db.bootstrap
        assert shim_db.reset is core_db.reset
        assert shim_db.transaction is core_db.transaction

    def test_from_import_paths_work(self):
        from proteus.core.db import connect as core_connect
        from proteus.db import connect as shim_connect

        assert shim_connect is core_connect

    def test_shim_still_bootstraps(self):
        conn = shim_db.connect(":memory:")
        try:
            shim_db.bootstrap(conn)
            head = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
            assert head >= 3
        finally:
            conn.close()


class TestRunsShim:
    def test_shim_reexports_same_objects(self):
        assert shim_runs.start_run is core_runs.start_run
        assert shim_runs.complete_run is core_runs.complete_run
        assert shim_runs.ensure_vocabulary_version is core_runs.ensure_vocabulary_version
        assert shim_runs.ensure_codebook_version is core_runs.ensure_codebook_version
        assert shim_runs.DEFAULT_VOCAB_LABEL == core_runs.DEFAULT_VOCAB_LABEL
        assert shim_runs.DEFAULT_CODEBOOK_LABEL == core_runs.DEFAULT_CODEBOOK_LABEL
