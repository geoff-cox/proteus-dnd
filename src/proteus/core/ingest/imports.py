"""
Shared raw-import registry helpers (core).

``file_hash`` and ``register_import`` are question-type-agnostic: every
service's ingest registers the same single ``raw_imports`` row per file and
dedupes by the file's SHA-256 (plan §1.3, "file hashing / raw_imports
registry" are shared core infrastructure). They live here so no service owns
them; the SA ingest re-exports them for backward compatibility.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path


def file_hash(path: Path) -> str:
    """SHA-256 of a file's contents, full hex."""
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def register_import(
    conn: sqlite3.Connection,
    file_path: Path,
    file_format: str,
    row_count: int,
    notes: str | None = None,
) -> int:
    """
    Record a raw_imports row and return its import_id. Refuses to register a
    file that has already been imported (UNIQUE on file_hash); callers that
    want to add to an existing import look it up by hash instead.
    """
    fhash = file_hash(file_path)
    cur = conn.execute(
        "INSERT INTO raw_imports (file_path, file_hash, file_format, "
        "row_count, notes) VALUES (?, ?, ?, ?, ?)",
        (str(Path(file_path).resolve()), fhash, file_format, row_count, notes),
    )
    return cur.lastrowid
