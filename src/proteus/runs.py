"""
Compatibility shim — the pipeline-run helpers moved to ``proteus.core.runs``
as part of the multi-service refactor (plan item A1.1).

Importing from ``proteus.runs`` continues to work unchanged; new code should
import from ``proteus.core.runs``. This shim is intentionally thin and will be
removed once all call sites have been migrated.
"""

from __future__ import annotations

from proteus.core.runs import (
    DEFAULT_CODEBOOK_LABEL,
    DEFAULT_VOCAB_LABEL,
    complete_run,
    ensure_codebook_version,
    ensure_vocabulary_version,
    start_run,
)

__all__ = [
    "DEFAULT_CODEBOOK_LABEL",
    "DEFAULT_VOCAB_LABEL",
    "complete_run",
    "ensure_codebook_version",
    "ensure_vocabulary_version",
    "start_run",
]
