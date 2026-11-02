"""
Compatibility shim: the database layer moved to ``proteus.core.db`` as part
of the multi-service refactor (plan item A1.1).

Importing from ``proteus.db`` continues to work unchanged; new code should
import from ``proteus.core.db``. This shim is intentionally thin and will be
removed once all call sites have been migrated.
"""

from __future__ import annotations

from proteus.core.db import (
    bootstrap,
    connect,
    default_db_path,
    reset,
    transaction,
)

__all__ = ["bootstrap", "connect", "default_db_path", "reset", "transaction"]
