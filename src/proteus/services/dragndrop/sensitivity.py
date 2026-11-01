"""
Sensitivity harness for the Paper 1 frozen run (expansion memo E-01;
``cc-task-frozen-run-tooling``, owner decisions 2026-10-04).

RQ4 is a one-knob-at-a-time design (``paper1-decisions.md`` D3.2, frozen
2026-07-17, amended 2026-10-05): the primary configuration plus ten
alternatives (knobs 1-9 and knob 10, "seeded replay"), each reported as Δ
attempts, Δ ready share, Δ anchor agreement and Δ primary RQ3 difference
with a materiality flag under the P6 thresholds. Hand-running eleven
reconstructions in a database that overwrites its own reconstruction layer
is days of work and invites error; this module runs them.

**Freeze integrity is the governing constraint.** The knob table below is
hard-coded from D3.2; the harness exposes no free-form knob, and a
configuration outside the table is labelled ``post_hoc`` in its result file.
The RQ3 column stays empty unless the harness is run with ``estimate=True``
(the contrast runner's counts-only default); the P6 thresholds are constants
with their freeze dates. The canonical database is refused unless
``allow_canonical`` is passed, the harness works on scratch copies only.

What one run does, per configuration:

1. clone the primary database (the SQLite backup API) into
   ``<out>/<key>/scratch.sqlite``;
2. re-run the stages the knob changes, ``reconstruct → score → analyze`` for
   the reconstruction knobs (knob 10 with ``seed_resumed_attempts``) and for
   δ_max (knob 6, option (a): both stages at the alternative threshold, so
   the ``long_idle_gap`` flag, the ``active_work`` segments and
   ``active_time`` agree), ``analyze`` alone for knobs 3 and 4
   (``ProcessConfig`` flags), nothing for the inclusion knobs 5 and 7, which
   are read-time filters;
3. export the datasets Stages 3.2 and 4.2 read (``checks``,
   ``attempt_metrics``, ``modeling_table``); the knob-10 run also writes
   ``seeded_strata.csv``, the seeded checks (agree / disagree / not
   comparable by cause) beside the resumed attempts that fell back to
   ``s_0`` and the non-resumed attempts, counts only, the numbers the
   Discussion's "seeding removes [n] of [n]" sentence reads;
4. summarize: admitted attempts, ready share, exact agreement among eligible
   anchors (over the truncated span for knob 3), and, with ``estimate``,
   the primary RQ3 quantity from :mod:`contrast`;
5. write ``config.json`` carrying the knob, the stages re-run, the F22
   ``pipeline_runs`` row of every stage (re-run, or inherited from the
   primary) and the stage versions, the package version, the input and
   scratch database hashes and the summary.

``run_all`` then writes ``deltas.csv`` in the column order of
``tab-sensitivity-results`` (Δ attempts, Δ ready share, Δ agreement,
Δ primary RQ3 difference, Material), knobs 1-3 and 10 then 4-7, plus
``deltas.json`` with the thresholds and freeze dates.

Definitions (stated once, used everywhere):

* *admitted attempts*: reconstructed attempts minus those a knob's attempt
  filter removes (knob 5: any carrying ``row_order_ambiguous``);
* *ready share*: ready attempts over admitted attempts;
* *attempts* (the Δ attempts column): admitted attempts whose readiness the
  knob admits to the primary analyses (``ready``; ``ready`` + ``limited``
  under knob 7);
* *agreement*: agreeing eligible anchors over eligible anchors
  (``dnd_configurations`` check rows with no ``anchor_cause`` and a
  comparison), across every admitted attempt regardless of readiness; under
  knob 3 the anchors after an attempt's first correct scored check leave the
  denominator (the truncated span); under knob 10 the checks of resumed
  attempts, stored ``resumed_attempt`` in the primary, are compared from the
  seeded replay and enter it, the row reports the magnitude of the
  mechanism, not fragility of the primary (2026-10-05 entry);
* *primary RQ3 difference*: the difference in group medians of
  ``n_effective_moves_pre`` on the knob's analysis population
  (:func:`contrast.primary_difference`).

Knob 3 under read-time option (i) leaves attempts, flags and readiness
untouched by construction, so its Δ attempts and Δ ready share are zero and
its Δ primary RQ3 difference is zero as well (the pre window precedes every
truncation point); only Δ agreement and the whole-attempt descriptors can
move. The harness reports the zeros rather than hiding the row.

The *evidence* report (``evidence``) emits the gap, session, page-view and
reset distributions that justify the D3.2 brackets, counts and quantiles
only, never an outcome.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import sqlite3
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from proteus.core import db
from proteus.core.ingest.normalize import parse_timestamp
from proteus.services.dragndrop import aliases, logging_regimes, policy, qc
from proteus.services.dragndrop import derive as derive_mod
from proteus.services.dragndrop import export as export_mod
from proteus.services.dragndrop import process as process_mod
from proteus.services.dragndrop import reconstruct as reconstruct_mod
from proteus.services.dragndrop import scoring as scoring_mod

log = logging.getLogger(__name__)

__all__ = [
    "SensitivityError",
    "Knob",
    "PRIMARY",
    "KNOB_TABLE",
    "RECONSTRUCTION",
    "FEATURES_INCLUSION",
    "D32_FROZEN",
    "D32_AMENDED",
    "P6_FROZEN",
    "P6_SUPERSEDED",
    "P6_SIGN_CLAUSE_DROPPED",
    "MATERIAL_AGREEMENT_PP",
    "MATERIAL_RQ3_RELATIVE",
    "MATERIAL_RQ3_FLOOR_MOVES",
    "MATERIAL_ATTEMPTS_RELATIVE",
    "MATERIAL_READY_SHARE_RELATIVE",
    "Summary",
    "Delta",
    "EvidenceReport",
    "knob_by_key",
    "refuse_canonical",
    "clone_database",
    "assert_primary_configuration",
    "run_stages",
    "summarize",
    "run_configuration",
    "material",
    "deltas",
    "write_deltas",
    "run_all",
    "evidence",
    "render_evidence",
]

RECONSTRUCTION = "reconstruction"
FEATURES_INCLUSION = "features_inclusion"

# Freeze dates written into every result file.
D32_FROZEN = "2026-07-17"
D32_AMENDED = "2026-10-05"  # knob 10, seeded replay, in the reconstruction group
P6_FROZEN = "2026-09-27"
P6_SUPERSEDED = "2026-09-28"  # primary quantity; the larger-of rule
P6_SIGN_CLAUSE_DROPPED = "2026-10-03"
KNOB3_OPTION = "(i) read-time: ProcessConfig.truncate_at_first_correct (owner, 2026-10-04)"
KNOB6_OPTION = "(a) reconstruct and analyze re-run at the alternative δ_max (owner, 2026-10-04)"

# P6 materiality thresholds (frozen 2026-09-27; primary-quantity rule
# superseded 2026-09-28; the sign-change clause dropped 2026-10-03). A change
# is material when |Δ agreement| exceeds MATERIAL_AGREEMENT_PP percentage
# points, when |Δ primary RQ3| exceeds the larger of MATERIAL_RQ3_RELATIVE ×
# |primary estimate| and MATERIAL_RQ3_FLOOR_MOVES moves, or when the attempt
# count or the ready share moves by more than MATERIAL_ATTEMPTS_RELATIVE /
# MATERIAL_READY_SHARE_RELATIVE of the primary value.
MATERIAL_AGREEMENT_PP = 2.0
MATERIAL_RQ3_RELATIVE = 0.25
MATERIAL_RQ3_FLOOR_MOVES = 1.0
MATERIAL_ATTEMPTS_RELATIVE = 0.10
MATERIAL_READY_SHARE_RELATIVE = 0.10

_PRIMARY_READINESS: tuple[str, ...] = ("ready",)


class SensitivityError(RuntimeError):
    """A refusal: the canonical database, a non-primary input, an unknown knob."""


@dataclass(frozen=True)
class Knob:
    """One configuration: the primary or one D3.2 alternative."""

    key: str  # result directory and deltas key
    number: int  # D3.2 row (0 = primary)
    group: str  # RECONSTRUCTION | FEATURES_INCLUSION | "primary"
    label: str  # the tab-sensitivity-results row label
    segmentation: Mapping[str, object] = field(default_factory=dict)
    idle_threshold_s: float | None = None
    process: Mapping[str, object] = field(default_factory=dict)
    exclude_flags: tuple[str, ...] = ()
    readiness: tuple[str, ...] = _PRIMARY_READINESS
    truncate_agreement: bool = False
    # Replay options of `reconstruct_all` beyond segmentation (knob 10:
    # `seed_resumed_attempts`); any entry re-runs reconstruct → score → analyze.
    reconstruct: Mapping[str, object] = field(default_factory=dict)

    @property
    def stages(self) -> tuple[str, ...]:
        """The stages this knob re-runs on its scratch copy."""
        if self.segmentation or self.idle_threshold_s is not None or self.reconstruct:
            return ("reconstruct", "score", "analyze")
        if self.process:
            return ("analyze",)
        return ()

    @property
    def seeds(self) -> bool:
        """Whether the knob seeds resumed attempts (knob 10): its run also
        writes ``seeded_strata.csv``."""
        return bool(self.reconstruct.get("seed_resumed_attempts"))


PRIMARY = Knob(key="primary", number=0, group="primary", label="Primary configuration")

# The D3.2 table, hard-coded (frozen 2026-07-17; knob 10 added 2026-10-05):
# primary + 10. Knob 10 sits with the reconstruction group (knobs 1-3, 10) in
# table order, so rows 1-9 keep their numbers. Nothing else is a frozen
# alternative; see `run_configuration` for the post_hoc label.
KNOB_TABLE: tuple[Knob, ...] = (
    Knob("k1-gap-600", 1, RECONSTRUCTION, "Attempt gap 10 min (30 min)",
         segmentation={"gap_seconds": 600.0}),
    Knob("k1-gap-3600", 1, RECONSTRUCTION, "Attempt gap 60 min (30 min)",
         segmentation={"gap_seconds": 3600.0}),
    Knob("k2-split-on-reset", 2, RECONSTRUCTION,
         "Reset opens a new attempt (reset within attempt)",
         segmentation={"split_on_reset": True}),
    Knob("k3-truncate-at-first-correct", 3, RECONSTRUCTION,
         "Truncate at first correct check (retain)",
         process={"truncate_at_first_correct": True}, truncate_agreement=True),
    Knob("k10-seeded-replay", 10, RECONSTRUCTION,
         "Seeded replay: resumed attempt starts at the prior attempt's last "
         "decodable checked configuration, s_0 if none (s_0)",
         reconstruct={"seed_resumed_attempts": True}),
    Knob("k4-count-noop-moves", 4, FEATURES_INCLUSION,
         "No-op drops counted as moves (flagged, excluded)",
         process={"count_noop_moves": True}),
    Knob("k5-exclude-order-ambiguous", 5, FEATURES_INCLUSION,
         "Exclude order-ambiguous attempts (retain, flagged)",
         exclude_flags=("row_order_ambiguous",)),
    Knob("k6-idle-120", 6, FEATURES_INCLUSION, "Idle threshold 120 s (300 s)",
         idle_threshold_s=120.0),
    Knob("k6-idle-600", 6, FEATURES_INCLUSION, "Idle threshold 600 s (300 s)",
         idle_threshold_s=600.0),
    Knob("k7-ready-and-limited", 7, FEATURES_INCLUSION,
         "Ready and limited attempts (ready only)",
         readiness=("ready", "limited")),
)
_TABLE_KEYS = {k.key for k in KNOB_TABLE}


def knob_by_key(key: str) -> Knob:
    for k in KNOB_TABLE:
        if k.key == key:
            return k
    if key == PRIMARY.key:
        return PRIMARY
    raise SensitivityError(
        f"unknown knob {key!r}; the frozen table has {sorted(_TABLE_KEYS)}"
    )


@dataclass(frozen=True)
class Summary:
    """One configuration's quantities behind the Δ columns."""

    key: str
    label: str
    group: str
    post_hoc: bool
    n_reconstructed: int  # admitted reconstructed attempts
    n_ready: int
    n_attempts: int  # admitted to the primary analyses (the Δ attempts column)
    ready_share: float | None
    n_anchors: int
    n_agree: int
    agreement: float | None  # proportion
    primary_rq3: float | None  # None until estimated
    rq3_n_first_check_correct: int | None
    rq3_n_first_check_incorrect_eventually_correct: int | None


@dataclass(frozen=True)
class Delta:
    """One row of deltas.csv."""

    knob: Knob
    alternative: Summary
    primary: Summary
    delta_attempts: int
    delta_ready_share: float | None
    delta_agreement_pp: float | None
    delta_primary_rq3: float | None
    material: str  # "yes" | "no" | "pending"
    material_reasons: tuple[str, ...]


# ---------------------------------------------------------------------------
# Guards and the scratch copy
# ---------------------------------------------------------------------------
def refuse_canonical(path: Path, allow_canonical: bool = False) -> None:
    """Refuse the canonical working database unless explicitly allowed."""
    try:
        canonical = db.default_db_path().resolve()
    except Exception:  # pragma: no cover - defensive: no repo anchor
        return
    if Path(path).resolve() == canonical and not allow_canonical:
        raise SensitivityError(
            f"{path} is the canonical database; the harness works on scratch "
            "copies only (pass allow_canonical / --allow-canonical to override)"
        )


def clone_database(src: Path, dest: Path) -> Path:
    """Copy ``src`` to ``dest`` through the SQLite backup API (consistent even
    while ``src`` is open elsewhere)."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    source = sqlite3.connect(str(src))
    try:
        target = sqlite3.connect(str(dest))
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    return dest


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _latest_run(conn: sqlite3.Connection, stage: str) -> tuple[str, dict | None] | None:
    """The newest ``pipeline_runs`` row of a DnD stage, whatever its status,
    as (status, recorded configuration or None)."""
    row = conn.execute(
        "SELECT status, config_json FROM pipeline_runs WHERE service = 'dnd' "
        "AND stage = ? ORDER BY run_id DESC LIMIT 1",
        (stage,),
    ).fetchone()
    if row is None:
        return None
    config = None
    if row["config_json"]:
        try:
            config = json.loads(row["config_json"])
        except ValueError:
            config = None
    return row["status"], config


# The complete F22 configuration each stage records for a full primary pass:
# the frozen defaults at the current stage versions, unscoped (question_id
# None). A key recorded by a stage but missing here, or the reverse, is a
# mismatch; the harness's primary-reproduction test keeps the two aligned.
# Two keys are data, not defaults: `alias_set_sha256` (dnd/0014), the hash of
# the identifier-alias set the boards were bound through, and
# `regime_set_sha256` (dnd/0015), the hash of the logging-regime set the
# structural causes were read under. The guard appends both at check time
# from the database it inspects, so a run recorded under a different alias or
# regime set than the database now holds is refused.
_PRIMARY_RECONSTRUCT = {
    "stage_version": reconstruct_mod.STAGE_VERSION,
    "question_id": None,
    "gap_seconds": reconstruct_mod.SegmentationConfig().gap_seconds,
    "split_on_session_change": True,
    "split_on_page_view": True,
    "split_on_reset": False,
    "feedback_policy": policy.LEGACY,
    "idle_threshold_s": qc.DEFAULT_IDLE_THRESHOLD_S,
    "seed_resumed_attempts": False,  # knob 10 off: the primary
}
# The data keys, by the stage that records them (score and analyze record
# the alias hash only, they do not read the regime table).
_DATA_KEYS = {
    "reconstruct": ("alias_set_sha256", "regime_set_sha256"),
    "score": ("alias_set_sha256",),
    "analyze": ("alias_set_sha256",),
}
_PRIMARY_SCORE = {
    "stage_version": scoring_mod.STAGE_VERSION,
    "question_id": None,
}
_PRIMARY_ANALYZE = {
    "stage_version": process_mod.STAGE_VERSION,
    "question_id": None,
    "cadence_threshold": process_mod.ProcessConfig().cadence_threshold,
    "idle_threshold_s": qc.DEFAULT_IDLE_THRESHOLD_S,
    "exclude_noop_moves": True,
    "truncate_at_first_correct": False,
}
_PRIMARY_RUNS = (
    ("reconstruct", _PRIMARY_RECONSTRUCT),
    ("score", _PRIMARY_SCORE),
    ("analyze", _PRIMARY_ANALYZE),
)


def assert_primary_configuration(conn: sqlite3.Connection) -> None:
    """
    The primary database must carry the frozen primary configuration: for
    each of reconstruct, score and analyze, the *newest* ``pipeline_runs``
    row (whatever its status) must be a completed, unscoped pass whose
    recorded configuration equals the frozen full-run configuration key for
    key, at the current stage version. The newest row is the one that counts:
    reconstruct commits per question and marks a crashed pass failed, and
    every stage accepts a question scope, so a newer failed or question-scoped
    pass may have rewritten part of the data under another configuration.
    Deltas against such a database would be meaningless.
    """
    data_hashes = {
        "alias_set_sha256": aliases.alias_set_sha256(conn),
        "regime_set_sha256": logging_regimes.regime_set_sha256(conn),
    }
    for stage, frozen in _PRIMARY_RUNS:
        expected = {**frozen, **{k: data_hashes[k] for k in _DATA_KEYS[stage]}}
        latest = _latest_run(conn, stage)
        if latest is None:
            raise SensitivityError(
                f"the primary database has no `dnd {stage}` run; run the "
                "primary stages first"
            )
        status, config = latest
        if status != "completed":
            raise SensitivityError(
                f"the primary database's newest `dnd {stage}` run is {status!r}, "
                "not completed: its data may be partial; re-run the primary stages"
            )
        if config is None:
            raise SensitivityError(
                f"the primary database's newest `dnd {stage}` run has no recorded "
                "configuration (F22); re-run the primary stages"
            )
        if config != expected:
            differences = []
            for key in sorted(set(expected) | set(config)):
                if key in config and key in expected and config[key] == expected[key]:
                    continue  # only the keys that differ are named
                if key == "alias_set_sha256" and key in config and key in expected:
                    differences.append(
                        "alias_set_sha256 differs from the database's current "
                        "alias set (the aliases changed after the run)"
                    )
                elif key == "regime_set_sha256" and key in config and key in expected:
                    differences.append(
                        "regime_set_sha256 differs from the database's current "
                        "logging-regime set (the regimes changed after the run)"
                    )
                elif key not in config:
                    differences.append(f"{key} not recorded (frozen primary {expected[key]!r})")
                elif key not in expected:
                    differences.append(f"{key}={config[key]!r} is not part of the frozen primary")
                else:
                    differences.append(f"{key}={config[key]!r} (frozen primary {expected[key]!r})")
            raise SensitivityError(
                f"the primary database's newest `dnd {stage}` run is not the "
                "frozen primary configuration: " + "; ".join(differences)
            )


# ---------------------------------------------------------------------------
# One configuration
# ---------------------------------------------------------------------------
def run_stages(conn: sqlite3.Connection, knob: Knob) -> dict[str, int]:
    """Re-run the stages ``knob`` changes on an open scratch connection.
    Returns the run ids per stage. Every stage is called through its public
    entry point with the override as the only difference from a plain run."""
    run_ids: dict[str, int] = {}
    if "reconstruct" in knob.stages:
        config = reconstruct_mod.SegmentationConfig(**dict(knob.segmentation))
        kwargs: dict[str, object] = dict(knob.reconstruct)
        if knob.idle_threshold_s is not None:
            kwargs["idle_threshold_s"] = knob.idle_threshold_s
        result = reconstruct_mod.reconstruct_all(conn, config=config, **kwargs)
        run_ids["reconstruct"] = result.run_id
    if "score" in knob.stages:
        run_ids["score"] = scoring_mod.score_checks(conn).run_id
    if "analyze" in knob.stages:
        kwargs = dict(knob.process)
        if knob.idle_threshold_s is not None:
            kwargs["idle_threshold_s"] = knob.idle_threshold_s
        config = process_mod.ProcessConfig(**kwargs)
        run_ids["analyze"] = process_mod.analyze_all(conn, config=config).run_id
    return run_ids


def _admitted_attempts(conn: sqlite3.Connection, knob: Knob) -> dict[int, str]:
    """attempt_id → readiness for the attempts the knob's attempt filter admits."""
    excluded = set(knob.exclude_flags)
    out: dict[int, str] = {}
    for r in conn.execute("SELECT attempt_id, readiness, qc_flags_json FROM dnd_attempts"):
        if excluded:
            try:
                flags = set(json.loads(r["qc_flags_json"] or "[]"))
            except (TypeError, ValueError):
                flags = set()
            if flags & excluded:
                continue
        out[r["attempt_id"]] = r["readiness"]
    return out


def _agreement(conn: sqlite3.Connection, admitted: Mapping[int, str], knob: Knob) -> tuple[int, int]:
    """(eligible anchors, agreeing anchors) over the admitted attempts; the
    truncated span under knob 3."""
    cutoff: dict[int, int] = {}
    if knob.truncate_agreement:
        for r in conn.execute(
            "SELECT c.attempt_id, MIN(c.seq) AS seq FROM dnd_configurations c "
            "JOIN dnd_check_scores s ON s.event_id = c.event_id "
            "WHERE c.is_check = 1 AND s.distance = 0 GROUP BY c.attempt_id"
        ):
            cutoff[r["attempt_id"]] = r["seq"]
    n = agree = 0
    for r in conn.execute(
        "SELECT attempt_id, seq, matches_input FROM dnd_configurations "
        "WHERE is_check = 1 AND anchor_cause IS NULL AND matches_input IS NOT NULL"
    ):
        if r["attempt_id"] not in admitted:
            continue
        if r["attempt_id"] in cutoff and r["seq"] > cutoff[r["attempt_id"]]:
            continue
        n += 1
        agree += 1 if r["matches_input"] else 0
    return n, agree


def summarize(
    conn: sqlite3.Connection, knob: Knob, *, estimate: bool = False, post_hoc: bool = False
) -> Summary:
    """The quantities behind the Δ columns for one configuration."""
    admitted = _admitted_attempts(conn, knob)
    n_reconstructed = len(admitted)
    n_ready = sum(1 for r in admitted.values() if r == "ready")
    n_attempts = sum(1 for r in admitted.values() if r in knob.readiness)
    n_anchors, n_agree = _agreement(conn, admitted, knob)
    rq3 = None
    n_c = n_i = None
    if estimate:
        from proteus.services.dragndrop import contrast

        pd = contrast.primary_difference(
            conn,
            contrast.ContrastConfig(
                readiness=knob.readiness, exclude_flags=knob.exclude_flags
            ),
        )
        rq3 = pd.difference
        n_c = pd.n_first_check_correct
        n_i = pd.n_first_check_incorrect_eventually_correct
    return Summary(
        key=knob.key,
        label=knob.label,
        group=knob.group,
        post_hoc=post_hoc,
        n_reconstructed=n_reconstructed,
        n_ready=n_ready,
        n_attempts=n_attempts,
        ready_share=(n_ready / n_reconstructed) if n_reconstructed else None,
        n_anchors=n_anchors,
        n_agree=n_agree,
        agreement=(n_agree / n_anchors) if n_anchors else None,
        primary_rq3=rq3,
        rq3_n_first_check_correct=n_c,
        rq3_n_first_check_incorrect_eventually_correct=n_i,
    )


_EXPORTS = ("checks", "attempt_metrics", "modeling_table")


def _export_datasets(conn: sqlite3.Connection, out_dir: Path) -> None:
    for name in _EXPORTS:
        with (out_dir / f"{name}.csv").open("w", encoding="utf-8", newline="") as f:
            export_mod.export_dataset(conn, name, f)


SEEDED_STRATA = ("seeded", "resumed_fallback", "non_resumed")
SEEDED_STRATA_FILE = "seeded_strata.csv"


def seeded_strata(conn: sqlite3.Connection) -> list[dict[str, object]]:
    """
    The knob-10 strata, counts only, one row per stratum in
    :data:`SEEDED_STRATA` order: every check of an attempt whose replay was
    seeded at the prior attempt's last decodable checked configuration
    (``seeded = 1``); of a resumed attempt that fell back to ``s_0``
    (``prior_attempt_id`` set, ``seeded = 0``); of a non-resumed attempt.
    Each row: checks, agree, disagree, not comparable, and the not-comparable
    checks by cause (every name of ``qc.ANCHOR_CAUSE_ORDER``, zero where
    none). No proportion, no outcome column: the Discussion's "seeding
    removes [n] of [n]" sentence reads these numbers.
    """
    rows = {
        name: {"stratum": name, "checks": 0, "agree": 0, "disagree": 0, "not_comparable": 0,
               **{f"cause_{c}": 0 for c in qc.ANCHOR_CAUSE_ORDER}}
        for name in SEEDED_STRATA
    }
    for r in conn.execute(
        "SELECT c.matches_input, c.anchor_cause, c.seeded, a.prior_attempt_id "
        "FROM dnd_configurations c JOIN dnd_attempts a ON a.attempt_id = c.attempt_id "
        "WHERE c.is_check = 1"
    ):
        if r["seeded"] == 1:
            stratum = "seeded"
        elif r["prior_attempt_id"] is not None:
            stratum = "resumed_fallback"
        else:
            stratum = "non_resumed"
        row = rows[stratum]
        row["checks"] += 1
        if r["anchor_cause"] is not None:
            row["not_comparable"] += 1
            key = f"cause_{r['anchor_cause']}"
            row[key] = row.get(key, 0) + 1
        elif r["matches_input"] == 1:
            row["agree"] += 1
        elif r["matches_input"] == 0:
            row["disagree"] += 1
    return [rows[name] for name in SEEDED_STRATA]


def write_seeded_strata(conn: sqlite3.Connection, out_dir: Path) -> Path:
    """``seeded_strata.csv`` for a knob-10 run directory."""
    rows = seeded_strata(conn)
    path = Path(out_dir) / SEEDED_STRATA_FILE
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        for row in rows:
            w.writerow(row)
    return path


def _package_version() -> str:
    try:
        from importlib.metadata import version

        return version("proteus-rs-pipeline")
    except Exception:  # pragma: no cover
        import proteus

        return getattr(proteus, "__version__", "unknown")


def _stage_versions() -> dict[str, int]:
    return {
        "derive": derive_mod.STAGE_VERSION,
        "reconstruct": reconstruct_mod.STAGE_VERSION,
        "score": scoring_mod.STAGE_VERSION,
        "analyze": process_mod.STAGE_VERSION,
    }


_STAGES = ("derive", "reconstruct", "score", "analyze")


def _run_rows(conn: sqlite3.Connection, run_ids: Mapping[str, int]) -> list[dict]:
    """One ``pipeline_runs`` row per stage, in pipeline order: the new run
    where the knob re-ran the stage (``source: rerun``), else the newest row
    the scratch copy inherited from the primary (``source: inherited``), so
    ``config.json`` names the effective configuration of every stage, not
    only the re-run ones."""
    out = []
    for stage in _STAGES:
        if stage in run_ids:
            r = conn.execute(
                "SELECT run_id, stage, status, config_json, notes FROM pipeline_runs "
                "WHERE run_id = ?",
                (run_ids[stage],),
            ).fetchone()
            source = "rerun"
        else:
            r = conn.execute(
                "SELECT run_id, stage, status, config_json, notes FROM pipeline_runs "
                "WHERE service = 'dnd' AND stage = ? ORDER BY run_id DESC LIMIT 1",
                (stage,),
            ).fetchone()
            source = "inherited"
        if r is None:
            continue
        out.append(
            {"stage": r["stage"], "run_id": r["run_id"], "status": r["status"],
             "source": source,
             "config": json.loads(r["config_json"]) if r["config_json"] else None,
             "notes": r["notes"]}
        )
    return out


def _summary_dict(s: Summary) -> dict[str, object]:
    return {
        "key": s.key, "label": s.label, "group": s.group, "post_hoc": s.post_hoc,
        "n_reconstructed": s.n_reconstructed, "n_ready": s.n_ready,
        "n_attempts": s.n_attempts, "ready_share": s.ready_share,
        "n_anchors": s.n_anchors, "n_agree": s.n_agree, "agreement": s.agreement,
        "primary_rq3": s.primary_rq3,
        "rq3_n_first_check_correct": s.rq3_n_first_check_correct,
        "rq3_n_first_check_incorrect_eventually_correct": s.rq3_n_first_check_incorrect_eventually_correct,
    }


def run_configuration(
    primary_db: Path,
    out_dir: Path,
    knob: Knob,
    *,
    estimate: bool = False,
    allow_canonical: bool = False,
    primary_sha256: str | None = None,
) -> Summary:
    """
    Clone the primary database into ``<out_dir>/<knob.key>/``, re-run the
    stages the knob changes, export the datasets, summarize, and write
    ``config.json``. The primary itself (``knob is PRIMARY``) is summarized
    on a clone with no stage re-run, it is the run the procedure's Stage 2
    already made, and the harness's primary-configuration test proves the
    harness's own stage invocation reproduces it bit for bit.

    A knob outside :data:`KNOB_TABLE` (and not the primary) is accepted but
    labelled ``post_hoc`` in its ``config.json`` and summary, the manuscript
    rule for anything not in D3.2.
    """
    primary_db = Path(primary_db)
    refuse_canonical(primary_db, allow_canonical)
    post_hoc = knob.key not in _TABLE_KEYS and knob is not PRIMARY
    dest_dir = Path(out_dir) / knob.key
    dest_dir.mkdir(parents=True, exist_ok=True)
    scratch = clone_database(primary_db, dest_dir / "scratch.sqlite")
    conn = db.connect(scratch)
    try:
        if knob is PRIMARY:
            assert_primary_configuration(conn)
        run_ids = run_stages(conn, knob)
        conn.commit()
        _export_datasets(conn, dest_dir)
        if knob.seeds:
            write_seeded_strata(conn, dest_dir)
        summary = summarize(conn, knob, estimate=estimate, post_hoc=post_hoc)
        run_rows = _run_rows(conn, run_ids)
    finally:
        conn.close()
    config = {
        "harness": "dnd sensitivity (E-01)",
        "knob": {
            "key": knob.key, "number": knob.number, "group": knob.group,
            "label": knob.label,
            "segmentation": dict(knob.segmentation),
            "idle_threshold_s": knob.idle_threshold_s,
            "process": dict(knob.process),
            "exclude_flags": list(knob.exclude_flags),
            "readiness": list(knob.readiness),
            "truncate_agreement": knob.truncate_agreement,
            "reconstruct": dict(knob.reconstruct),
        },
        "label": "post_hoc" if post_hoc else ("primary" if knob is PRIMARY else "frozen_alternative"),
        "freeze": {
            "d3_2_knob_table": D32_FROZEN,
            "d3_2_knob_10_added": D32_AMENDED,
            "knob3_option": KNOB3_OPTION,
            "knob6_option": KNOB6_OPTION,
        },
        "stages_rerun": list(knob.stages),
        "pipeline_runs": run_rows,
        "stage_versions": _stage_versions(),
        "package_version": _package_version(),
        "primary_db_sha256": primary_sha256 or _sha256(primary_db),
        "scratch_db_sha256": _sha256(scratch),
        "exports": [f"{name}.csv" for name in _EXPORTS] + ([SEEDED_STRATA_FILE] if knob.seeds else []),
        "summary": _summary_dict(summary),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    (dest_dir / "config.json").write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


# ---------------------------------------------------------------------------
# Deltas and the materiality rule
# ---------------------------------------------------------------------------
def _relative_change(primary_value: float, alternative_value: float) -> float:
    """|alternative - primary| / |primary|; a change from a zero primary is
    unbounded (reported as infinity), no change is 0."""
    if primary_value == alternative_value:
        return 0.0
    if primary_value == 0:
        return float("inf")
    return abs(alternative_value - primary_value) / abs(primary_value)


def material(primary: Summary, alternative: Summary) -> tuple[str, tuple[str, ...]]:
    """The P6 verdict for one alternative: ``yes`` when any threshold is
    crossed, ``pending`` when none is crossed but the RQ3 quantity is not yet
    estimated, else ``no``; with the crossed thresholds named."""
    reasons: list[str] = []
    if _relative_change(primary.n_attempts, alternative.n_attempts) > MATERIAL_ATTEMPTS_RELATIVE:
        reasons.append("attempts")
    if (
        primary.ready_share is not None and alternative.ready_share is not None
        and _relative_change(primary.ready_share, alternative.ready_share) > MATERIAL_READY_SHARE_RELATIVE
    ):
        reasons.append("ready_share")
    if (
        primary.agreement is not None and alternative.agreement is not None
        and abs(alternative.agreement - primary.agreement) * 100.0 > MATERIAL_AGREEMENT_PP
    ):
        reasons.append("agreement")
    rq3_pending = primary.primary_rq3 is None or alternative.primary_rq3 is None
    if not rq3_pending:
        floor = max(MATERIAL_RQ3_RELATIVE * abs(primary.primary_rq3), MATERIAL_RQ3_FLOOR_MOVES)
        if abs(alternative.primary_rq3 - primary.primary_rq3) > floor:
            reasons.append("primary_rq3")
    if reasons:
        verdict = "yes"
    elif rq3_pending:
        verdict = "pending"
    else:
        verdict = "no"
    return verdict, tuple(reasons)


def deltas(primary: Summary, alternatives: Sequence[tuple[Knob, Summary]]) -> list[Delta]:
    out: list[Delta] = []
    for knob, alt in alternatives:
        verdict, reasons = material(primary, alt)
        out.append(
            Delta(
                knob=knob,
                alternative=alt,
                primary=primary,
                delta_attempts=alt.n_attempts - primary.n_attempts,
                delta_ready_share=(
                    alt.ready_share - primary.ready_share
                    if alt.ready_share is not None and primary.ready_share is not None
                    else None
                ),
                delta_agreement_pp=(
                    (alt.agreement - primary.agreement) * 100.0
                    if alt.agreement is not None and primary.agreement is not None
                    else None
                ),
                delta_primary_rq3=(
                    alt.primary_rq3 - primary.primary_rq3
                    if alt.primary_rq3 is not None and primary.primary_rq3 is not None
                    else None
                ),
                material=verdict,
                material_reasons=reasons,
            )
        )
    return out


# The exact display order of tab-sensitivity-results, after the row label.
DELTA_COLUMNS: tuple[str, ...] = (
    "alternative",
    "delta_attempts",
    "delta_ready_share",
    "delta_agreement_pp",
    "delta_primary_rq3_moves",
    "material",
    "material_reasons",
    "group",
    "knob",
    "key",
    "post_hoc",
    "attempts",
    "ready_share",
    "agreement",
    "primary_rq3_moves",
    "primary_attempts",
    "primary_ready_share",
    "primary_agreement",
    "primary_primary_rq3_moves",
)


def _fmt(v: object) -> object:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.6g}"
    return v


def write_deltas(rows: Sequence[Delta], out_dir: Path, *, estimated: bool) -> Path:
    """``deltas.csv`` (knobs 1-3 and 10, then 4-7, in table order) and
    ``deltas.json`` (thresholds, freeze dates, the primary's absolute
    values)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ordered = sorted(
        rows, key=lambda d: (0 if d.knob.group == RECONSTRUCTION else 1, d.knob.number, d.knob.key)
    )
    path = out_dir / "deltas.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(DELTA_COLUMNS), lineterminator="\n")
        w.writeheader()
        for d in ordered:
            a, p = d.alternative, d.primary
            w.writerow({
                "alternative": d.knob.label,
                "delta_attempts": d.delta_attempts,
                "delta_ready_share": _fmt(d.delta_ready_share),
                "delta_agreement_pp": _fmt(d.delta_agreement_pp),
                "delta_primary_rq3_moves": _fmt(d.delta_primary_rq3),
                "material": d.material,
                "material_reasons": ";".join(d.material_reasons),
                "group": d.knob.group,
                "knob": d.knob.number,
                "key": d.knob.key,
                "post_hoc": int(a.post_hoc),
                "attempts": a.n_attempts,
                "ready_share": _fmt(a.ready_share),
                "agreement": _fmt(a.agreement),
                "primary_rq3_moves": _fmt(a.primary_rq3),
                "primary_attempts": p.n_attempts,
                "primary_ready_share": _fmt(p.ready_share),
                "primary_agreement": _fmt(p.agreement),
                "primary_primary_rq3_moves": _fmt(p.primary_rq3),
            })
    meta = {
        "harness": "dnd sensitivity (E-01)",
        "columns": list(DELTA_COLUMNS),
        "groups": {RECONSTRUCTION: "knobs 1-3, 10", FEATURES_INCLUSION: "knobs 4-7"},
        "thresholds": {
            "agreement_pp": MATERIAL_AGREEMENT_PP,
            "rq3_relative": MATERIAL_RQ3_RELATIVE,
            "rq3_floor_moves": MATERIAL_RQ3_FLOOR_MOVES,
            "attempts_relative": MATERIAL_ATTEMPTS_RELATIVE,
            "ready_share_relative": MATERIAL_READY_SHARE_RELATIVE,
            "frozen": P6_FROZEN,
            "superseded": P6_SUPERSEDED,
            "sign_clause_dropped": P6_SIGN_CLAUSE_DROPPED,
        },
        "freeze": {"d3_2_knob_table": D32_FROZEN, "d3_2_knob_10_added": D32_AMENDED,
                   "knob3_option": KNOB3_OPTION, "knob6_option": KNOB6_OPTION},
        "rq3_estimated": estimated,
        "definitions": {
            "attempts": (
                "admitted attempts whose readiness the knob admits to the primary analyses"
            ),
            "ready_share": "ready attempts over admitted reconstructed attempts",
            "agreement": (
                "agreeing eligible anchors over eligible anchors, every admitted "
                "attempt; knob 3 over the truncated span; knob 10 with the checks "
                "of resumed attempts (seeded, or replayed from s_0 when the prior "
                "attempt has no decodable check) in the denominator"
            ),
            "primary_rq3": (
                "difference in group medians of n_effective_moves_pre, "
                "first_check_correct minus first_check_incorrect_eventually_correct"
            ),
        },
        "stage_versions": _stage_versions(),
        "package_version": _package_version(),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    (out_dir / "deltas.json").write_text(
        json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


def run_all(
    primary_db: Path,
    out_dir: Path,
    knobs: Sequence[Knob] = KNOB_TABLE,
    *,
    estimate: bool = False,
    allow_canonical: bool = False,
) -> tuple[Summary, list[Delta]]:
    """The primary plus every requested alternative, then ``deltas.csv``."""
    primary_db = Path(primary_db)
    refuse_canonical(primary_db, allow_canonical)
    primary_sha = _sha256(primary_db)
    primary = run_configuration(
        primary_db, out_dir, PRIMARY, estimate=estimate,
        allow_canonical=allow_canonical, primary_sha256=primary_sha,
    )
    results: list[tuple[Knob, Summary]] = []
    for knob in knobs:
        results.append((knob, run_configuration(
            primary_db, out_dir, knob, estimate=estimate,
            allow_canonical=allow_canonical, primary_sha256=primary_sha,
        )))
    rows = deltas(primary, results)
    write_deltas(rows, out_dir, estimated=estimate)
    return primary, rows


# ---------------------------------------------------------------------------
# Evidence for the brackets (D3.2 "write at Phase 1.2"; counts only)
# ---------------------------------------------------------------------------
_GAP_CUTS_S: tuple[float, ...] = (120.0, 300.0, 600.0, 1800.0, 3600.0)
_QUANTILES: tuple[float, ...] = (50.0, 95.0, 99.0, 99.5, 99.9)


@dataclass(frozen=True)
class EvidenceReport:
    n_timelines: int  # student × question traces with ≥ 1 non-page event
    n_events: int
    n_gaps: int  # consecutive-event gaps within a timeline
    quantiles_s: Mapping[str, float | None]
    max_gap_s: float | None
    above: Mapping[str, int]  # gaps strictly above each cut
    n_session_changes: int  # consecutive events in different sessions
    n_page_views_between: int  # consecutive events with an intervening page view
    n_reset_events: int
    n_timelines_with_reset: int
    n_attempts: int | None  # reconstructed attempts, when reconstruct has run
    n_attempts_with_reset: int | None
    # dnd/0015 (knob 10): resumed attempts and the checks stored with each
    # structural cause in the primary, plus the imports under a regime row.
    n_attempts_resumed: int | None = None
    n_checks_resumed_attempt: int | None = None
    n_checks_no_move_logging: int | None = None
    n_imports_with_regime: int | None = None


def evidence(conn: sqlite3.Connection) -> EvidenceReport:
    """The gap / session / page-view / reset distributions over every
    student × question timeline, read exactly as reconstruct orders them
    (timestamp, numeric problem view, file order). Counts and quantiles
    only; no feedback, grade or outcome column is read."""
    rows = conn.execute(
        "SELECT e.student_id, e.problem_name, e.session_id, e.timestamp, d.kind "
        "FROM dnd_events d JOIN events e USING (event_id) "
        "WHERE d.kind != 'page_view' "
        "ORDER BY e.student_id, e.problem_name, e.timestamp, "
        "CAST(e.problem_view AS INTEGER), e.rowid"
    ).fetchall()
    page_times: dict[str, list[str]] = defaultdict(list)
    for r in conn.execute(
        "SELECT e.student_id, e.timestamp FROM dnd_events d JOIN events e "
        "USING (event_id) WHERE d.kind = 'page_view' AND e.timestamp IS NOT NULL "
        "ORDER BY e.timestamp"
    ):
        page_times[r["student_id"]].append(r["timestamp"])

    timelines: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
    for r in rows:
        timelines[(r["student_id"], r["problem_name"])].append(r)

    gaps: list[float] = []
    n_session = n_page = n_reset = n_tl_reset = 0
    for (student, _), events in timelines.items():
        if any(e["kind"] == "reset" for e in events):
            n_tl_reset += 1
        n_reset += sum(1 for e in events if e["kind"] == "reset")
        pages = page_times.get(student, [])
        for prev, cur in zip(events, events[1:]):
            if prev["session_id"] != cur["session_id"]:
                n_session += 1
            if prev["timestamp"] and cur["timestamp"] and any(
                prev["timestamp"] < pt < cur["timestamp"] for pt in pages
            ):
                n_page += 1
            try:
                gap = (parse_timestamp(cur["timestamp"]) - parse_timestamp(prev["timestamp"])).total_seconds()
            except (TypeError, ValueError):
                continue
            gaps.append(gap)
    gaps.sort()
    from proteus.services.dragndrop.contrast import percentile  # pure helper

    quantiles = {f"p{q:g}": percentile(gaps, q) for q in _QUANTILES}
    above = {f"gt_{int(cut)}s": sum(1 for g in gaps if g > cut) for cut in _GAP_CUTS_S}

    n_attempts = n_attempts_reset = None
    n_resumed = n_resumed_checks = n_regime_checks = n_regime_imports = None
    try:
        n_attempts = conn.execute("SELECT count(*) AS n FROM dnd_attempts").fetchone()["n"]
        n_attempts_reset = conn.execute(
            "SELECT count(DISTINCT c.attempt_id) AS n FROM dnd_configurations c "
            "JOIN dnd_events d ON d.event_id = c.event_id WHERE d.kind = 'reset'"
        ).fetchone()["n"]
        n_resumed = conn.execute(
            "SELECT count(*) AS n FROM dnd_attempts WHERE prior_attempt_id IS NOT NULL"
        ).fetchone()["n"]
        n_resumed_checks = conn.execute(
            "SELECT count(*) AS n FROM dnd_configurations WHERE is_check = 1 "
            "AND anchor_cause = ?", (qc.CAUSE_RESUMED_ATTEMPT,),
        ).fetchone()["n"]
        n_regime_checks = conn.execute(
            "SELECT count(*) AS n FROM dnd_configurations WHERE is_check = 1 "
            "AND anchor_cause = ?", (qc.CAUSE_NO_MOVE_LOGGING,),
        ).fetchone()["n"]
        n_regime_imports = len(logging_regimes.regime_by_import(conn))
    except sqlite3.OperationalError:
        pass
    return EvidenceReport(
        n_timelines=len(timelines),
        n_events=len(rows),
        n_gaps=len(gaps),
        quantiles_s=quantiles,
        max_gap_s=gaps[-1] if gaps else None,
        above=above,
        n_session_changes=n_session,
        n_page_views_between=n_page,
        n_reset_events=n_reset,
        n_timelines_with_reset=n_tl_reset,
        n_attempts=n_attempts,
        n_attempts_with_reset=n_attempts_reset,
        n_attempts_resumed=n_resumed,
        n_checks_resumed_attempt=n_resumed_checks,
        n_checks_no_move_logging=n_regime_checks,
        n_imports_with_regime=n_regime_imports,
    )


def render_evidence(report: EvidenceReport) -> str:
    """The evidence report as Markdown (counts and quantiles only)."""
    def pct(n: int) -> str:
        return f"{100.0 * n / report.n_gaps:.2f}%" if report.n_gaps else "—"

    def secs(v: float | None) -> str:
        return "—" if v is None else f"{v:.1f} s"

    lines = [
        "# Sensitivity brackets, evidence (counts only)",
        "",
        f"Timelines (student × question, non-page events): {report.n_timelines}; "
        f"events: {report.n_events}; consecutive-event gaps: {report.n_gaps}.",
        "",
        "## Gap distribution (knobs 1 and 6)",
        "",
        "| quantile | gap |",
        "|---|---|",
    ]
    for name, value in report.quantiles_s.items():
        lines.append(f"| {name} | {secs(value)} |")
    lines.append(f"| max | {secs(report.max_gap_s)} |")
    lines += ["", "| gaps above | count | share |", "|---|---|---|"]
    for name, n in report.above.items():
        lines.append(f"| {name.replace('gt_', '> ')} | {n} | {pct(n)} |")
    lines += [
        "",
        "## Sitting boundaries (knob 1 companions)",
        "",
        f"- session changes between consecutive events: {report.n_session_changes}",
        f"- page views between consecutive events: {report.n_page_views_between}",
        "",
        "## Resets (knob 2)",
        "",
        f"- reset events: {report.n_reset_events}",
        f"- timelines with at least one reset: {report.n_timelines_with_reset}",
    ]
    if report.n_attempts is not None:
        lines.append(
            f"- reconstructed attempts: {report.n_attempts}; with at least one "
            f"reset: {report.n_attempts_with_reset}"
        )
    if report.n_attempts_resumed is not None:
        lines += [
            "",
            "## Resumed attempts and the logging regime (knob 10; the two structural causes)",
            "",
            f"- resumed attempts (a prior attempt by the same student on the same "
            f"logged identifier): {report.n_attempts_resumed}",
            f"- checks stored resumed_attempt in the primary (compared under knob 10): "
            f"{report.n_checks_resumed_attempt}",
            f"- checks stored no_move_logging (never compared, under either "
            f"configuration): {report.n_checks_no_move_logging}; imports under a "
            f"regime row: {report.n_imports_with_regime}",
        ]
    lines += [
        "",
        f"D3.2 brackets (frozen {D32_FROZEN}; knob 10 added {D32_AMENDED}): knob 1 "
        "gap 600 / 1800 / 3600 s; knob 6 δ_max 120 / 300 / 600 s; knob 10 the "
        "prior attempt's last decodable checked configuration / s_0. No outcome "
        "column was read.",
    ]
    return "\n".join(lines) + "\n"
