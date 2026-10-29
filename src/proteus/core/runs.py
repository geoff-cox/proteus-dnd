"""
Pipeline-run helpers.

A pipeline_run row groups together the per-stage artifacts produced from a
single end-to-end pass over the data (screenings, chains, matches,
dispositions). Every stage that writes to the database does so under some
run_id, which is how we keep multiple parallel attempts (e.g. comparing
two prompt versions) from overwriting each other.

The schema's pipeline_runs table carries vocabulary_version and
codebook_version references (foreign keys in the frozen baseline; plain
nullable columns since migration 0015), even for stages that don't actually
consult either of them (Stage 2 doesn't). To keep the early stages usable
before the group has finalised a vocabulary and codebook, this module will
create stub 'default' versions on demand. The version tables are owned by
the SA service, so on a database bootstrapped without it they don't exist;
since migration 0015 the run is then recorded with NULL versions instead of
failing (A2/BUG-02 — a broken SA package must not brick another service's
stages).
"""

from __future__ import annotations

import json
import logging
import sqlite3

log = logging.getLogger(__name__)

DEFAULT_VOCAB_LABEL = "default"
DEFAULT_CODEBOOK_LABEL = "default"

# Placeholder for the baseline's SA-specific NOT NULL columns
# (prompt_version, model_id) on runs recorded by non-LLM stage passes —
# their real configuration lives in stage/config_json (migration 0013, F22).
_NOT_APPLICABLE = "n/a"


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def ensure_vocabulary_version(
    conn: sqlite3.Connection,
    label: str = DEFAULT_VOCAB_LABEL,
) -> int:
    """
    Return the version_id for a vocabulary label, creating it if needed.

    Uses INSERT OR IGNORE so two callers racing to create the same label
    can't trip the UNIQUE constraint; one wins the insert, the other
    sees the existing row on the follow-up SELECT.
    """
    conn.execute(
        "INSERT OR IGNORE INTO vocabulary_versions (label, notes) VALUES (?, ?)",
        (label, "Auto-created stub; populate before running Stage 3."),
    )
    row = conn.execute(
        "SELECT version_id FROM vocabulary_versions WHERE label = ?", (label,)
    ).fetchone()
    return row["version_id"]


def ensure_codebook_version(
    conn: sqlite3.Connection,
    label: str = DEFAULT_CODEBOOK_LABEL,
) -> int:
    """
    Return the version_id for a codebook label, creating it if needed.

    Uses INSERT OR IGNORE so two callers racing to create the same label
    can't trip the UNIQUE constraint; one wins the insert, the other
    sees the existing row on the follow-up SELECT.
    """
    conn.execute(
        "INSERT OR IGNORE INTO codebook_versions (label, notes) VALUES (?, ?)",
        (label, "Auto-created stub; populate before running Stage 4."),
    )
    row = conn.execute(
        "SELECT version_id FROM codebook_versions WHERE label = ?", (label,)
    ).fetchone()
    return row["version_id"]


def start_run(
    conn: sqlite3.Connection,
    *,
    prompt_version: str,
    model_id: str,
    vocab_label: str = DEFAULT_VOCAB_LABEL,
    codebook_label: str = DEFAULT_CODEBOOK_LABEL,
    notes: str | None = None,
    service: str = "sa",
) -> int:
    """
    Open a new pipeline_run and return its run_id.

    The vocabulary and codebook versions referenced are looked up by label
    and created if absent, so callers don't have to pre-populate those
    tables just to run an early stage. On a database bootstrapped without
    the SA service the version tables themselves don't exist; the run is
    then recorded with NULL versions (nullable since migration 0015) rather
    than failing on the missing tables (A2/BUG-02).

    `service` tags the run with the owning question-type service (item A3.5;
    the pipeline_runs.service column from migration 0005). The default 'sa'
    matches the column default and covers the pre-A4 UI callers; services
    should pass their descriptor name explicitly.

    Transaction boundaries are the caller's responsibility (mirroring the
    ingest helpers): this function does not commit. Callers typically
    either wrap their flow in `proteus.db.transaction(conn)` or call
    `conn.commit()` explicitly once the run row should be persisted.
    """
    vocab_version = (
        ensure_vocabulary_version(conn, vocab_label)
        if _table_exists(conn, "vocabulary_versions")
        else None
    )
    codebook_version = (
        ensure_codebook_version(conn, codebook_label)
        if _table_exists(conn, "codebook_versions")
        else None
    )
    cur = conn.execute(
        "INSERT INTO pipeline_runs (status, prompt_version, model_id, "
        "vocab_version, codebook_version, notes, service) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "running", prompt_version, model_id, vocab_version,
            codebook_version, notes, service,
        ),
    )
    run_id = cur.lastrowid
    log.info(
        "Started pipeline run %d (service=%s prompt=%s model=%s)",
        run_id, service, prompt_version, model_id,
    )
    return run_id


def start_stage_run(
    conn: sqlite3.Connection,
    *,
    service: str,
    stage: str,
    config: dict[str, object],
    notes: str | None = None,
) -> int:
    """
    Open a pipeline_run for a non-LLM stage pass and return its run_id (F22).

    Run-level provenance for the deterministic stages (the journal's
    subsec-provenance-and-reproducibility): `stage` names the pass within its
    service (e.g. 'reconstruct') and `config` is its configuration — stage
    version, thresholds, boundary rules, exclusion defaults, filters —
    serialized to the `config_json` column with sorted keys so equal
    configurations compare equal as text.

    The baseline's SA-specific NOT NULL columns (prompt_version, model_id —
    the schema's recorded wart) are filled with the 'n/a' placeholder; the
    vocab/codebook FKs get the stub 'default' versions when the SA-owned
    version tables exist, and NULL (nullable since migration 0015) when the
    database was bootstrapped without the SA service (A2/BUG-02).

    Like `start_run`, transaction boundaries are the caller's responsibility:
    this function does not commit.
    """
    vocab_version = (
        ensure_vocabulary_version(conn)
        if _table_exists(conn, "vocabulary_versions")
        else None
    )
    codebook_version = (
        ensure_codebook_version(conn)
        if _table_exists(conn, "codebook_versions")
        else None
    )
    cur = conn.execute(
        "INSERT INTO pipeline_runs (status, prompt_version, model_id, "
        "vocab_version, codebook_version, notes, service, stage, config_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "running", _NOT_APPLICABLE, _NOT_APPLICABLE, vocab_version,
            codebook_version, notes, service, stage,
            json.dumps(config, sort_keys=True),
        ),
    )
    run_id = cur.lastrowid
    log.info(
        "Started pipeline run %d (service=%s stage=%s)", run_id, service, stage
    )
    return run_id


def complete_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    status: str = "completed",
    notes: str | None = None,
) -> None:
    """
    Mark a pipeline_run as finished. Status must satisfy the schema CHECK.

    Like `start_run`, this leaves transaction management to the caller
    and does not commit on its own.
    """
    if status not in {"completed", "failed", "partial"}:
        raise ValueError(f"invalid run status: {status!r}")
    if notes is None:
        conn.execute(
            "UPDATE pipeline_runs SET status = ?, "
            "completed_at = datetime('now') WHERE run_id = ?",
            (status, run_id),
        )
    else:
        conn.execute(
            "UPDATE pipeline_runs SET status = ?, "
            "completed_at = datetime('now'), notes = ? WHERE run_id = ?",
            (status, notes, run_id),
        )
