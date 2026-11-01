"""Paper 1, the manuscript-table generator (expansion memo E-08).

Companion to ``paper1-frozen-run-procedure.md`` (Stage 3 rows 3.1-3.4 and
3.7; Stage 6.1) and the seventh of the Paper 1 scripts, the package
``proteus.paper1`` since 2026-10-27, ``docs/dnd/scripts/`` before (owner
ruling R1 (a) of ``cc-task-paper-tables.md``, Checkpoint 1, 2026-10-18, and
R1 (a) of ``cc-task-public-export.md``). It regenerates, from
one command and one archive, every number-bearing RQ1/RQ2 display of the
manuscript, ``tab-corpus-flow`` included since the attrition ledger's ``flow``
export (PR #239; ruling R8 (ii): this generator's one-commit follow-up to
PR #236), except the sensitivity supplement (R6: a later brief) and the
figure images (the Stage 6.1 displays brief).

The generator reads **only the archive**, ``<archive-root>/<run-id>/`` in the
procedure's layout: ``manifest.yaml`` and ``exports/<dataset>.csv``. It never
opens a database, takes no database option, and never reads an export outside
the archive. The run id is the archive directory's name.

Usage::

    proteus-paper-tables \\
        --archive <archive-root>/<run-id> [--out <dir>] \\
        [--items-convention A|B] [--allow-anywhere]

``--out`` defaults to ``<archive>/displays/``. Both places the generator
writes, the output directory and ``<archive>/validation/`` (the per-check
classification, event- and attempt-level identifiers), pass
``audit_sample.guard_output_dir`` unchanged, before either directory is
created: inside the repository each must lie under ``data/private/`` unless
``--allow-anywhere`` is given (a rehearsal override, not for the frozen run).

Refusals (exit status 2, nothing written, one line on stderr):

1. no ``manifest.yaml`` in ``--archive``, not an archive;
2. the stored ``fingerprint`` is not ``manifest.fingerprint(body)``, a
   tampered or hand-edited body;
3. no top-level ``hand`` block (ruling R11 (a), below), or
   ``hand.configuration.readiness`` is not ``ready_only``, or an import of
   ``body.imports[]`` has no ``hand.corpus.files`` entry with a string
   ``term`` and a boolean ``development_exposure``;
4. the configuration is not the frozen primary (ruling R13 (a)): for each of
   reconstruct, score and analyze the *newest* ``body.pipeline_runs`` entry of
   the stage (by ``run_id``, mirroring ``sensitivity._latest_run`` /
   ``assert_primary_configuration``) must be ``completed`` with a recorded
   configuration equal, key for key, to the frozen primary composed from the
   harness's own symbols (``SegmentationConfig().gap_seconds``,
   ``policy.LEGACY``, ``qc.DEFAULT_IDLE_THRESHOLD_S``,
   ``ProcessConfig().cadence_threshold``, the alias and regime set hashes the
   manifest's ``services.dnd`` section carries); ``stage_version`` is bounded,
   not strict, at least ``MIN_STAGE_VERSION`` (the frozen-run minimum, the
   procedure's Stage 1.3 line "2 / 5 / 3 / 9",
   ``paper1-frozen-run-procedure.md:100``) and at most the harness's current
   ``STAGE_VERSION`` (an archive from newer code than this generator is
   refused); ``seeded`` must be empty on every ``checks`` and
   ``configurations`` row (seeding off is part of the primary); and the
   newest ``dnd derive`` run must be ``completed`` with its ``stage_version``
   bounded the same way (at least 2: the governance filter, dnd/0016,
   PR #238) and its recorded configuration carrying the ``roles_set_sha256``
   key, null with an empty roles table, which is allowed; derive's other
   keys are a pass's scope, not a frozen primary, so there is no key-for-key
   comparison for that stage (PR #238's hand-off to this follow-up, taken in
   the light form);
5. a missing ``exports/<dataset>.csv`` for any of ``checks``,
   ``attempt_metrics``, ``configurations``, ``boards``, ``item_summary``,
   ``check_scores``, ``flow``; or an ``exports/flow.csv`` that is not this
   manifest's or not the ledger's, a ``by_offering`` row keyed outside
   ``body.imports[]``, an unknown ``breakdown``, a non-numeric ``step``, a
   repeated ``(breakdown, key, quantity, step)`` row (a duplicated line, or
   two imports under one pseudonym, whose student counts cannot be pooled),
   a pooled ``(quantity, step)`` cell missing from the 8 × 9 grid the
   ledger emits, an offering's cell missing from its grid, or a blank or
   non-count ``rows_out`` / ``n_students`` on a cell the table shows;
6. the output guard above, on ``--out`` and on ``<archive>/validation/``
   alike (``audit_sample.die``: exit status 2, nothing written);
7. ``exports/flow.csv`` does not describe this archive's attempts
   (``_check_flow``; ruling Q13: the two counts the manuscript cites most):
   its pooled Reconstructed attempts (step 5, ``rows_out``) must equal the
   ``attempt_metrics`` rows without ``missing_source_map`` (ruling R9 (b):
   attempts lose rows at step 3 only before readiness) and its Ready
   attempts (step 6) the rows with ``readiness == ready``.

**The hand block (ruling R11 (a)).** ``manifest.yaml`` carries, beside
``fingerprint`` / ``generated_at`` / ``body``, a sibling top-level key ``hand``
written in the emitter's own subset (``manifest.dump_yaml``: quoted keys,
block style, no comments), so ``read_manifest`` parses it and the fingerprint
(over ``body`` only) still verifies: ``hand.schema_version`` 1;
``hand.corpus.files``, a list of ``{pseudonym, sha256, term,
development_exposure}``; ``hand.configuration.readiness`` ``"ready_only"``;
and optionally ``hand.items``, a list of ``{question_id, section}`` supplying
the Section column of ``tab-items`` and the figure's item labels (no export
stores a section number). Every row's ``import_sha256`` maps to its pseudonym
through ``body.imports[]`` and to its term and development status through
the hand block. Displays carry pseudonyms, never a sha256.

**Definitions (settled; the frozen decisions).**

* *Eligible anchors* are ``checks`` rows with ``eligible == "1"``; *exact
  agreement* is ``matches_input == "1"`` among them. The denominator of every
  agreement rate is the eligible anchors of every admitted attempt regardless
  of readiness (``paper1-decisions.md`` 2026-10-05, "the eligible anchors of
  non-resumed attempts"; the procedure's Stage 4 definitions line, "agreement
  = agreeing eligible anchors over eligible anchors across every admitted
  attempt"; ``sec-empirical-study.ptx`` 445-448, "agreement is computed over
  eligible anchors before readiness is assigned"). ``eligible`` is
  three-valued: ``"1"``, ``"0"`` (stored not comparable, with a cause) and
  ``""`` (the check never reconstructed).
* *Attempts in a stratum* are the ``attempt_metrics`` rows of the stratum,
  readiness-blind and with resumed attempts included, the generator's
  reading, stated in every CSV header comment and reported to the owner.
* *Students in a cell* are the distinct ``student_id`` values among the
  stratum's ``attempt_metrics`` rows.
* *Strata*: overall; by term (the hand block); by offering (the pseudonym);
  by development status (the hand block: exposed against unexposed); by item
  (the logged ``question_id`` under items convention A, the
  ``canonical_question_id`` under B).
* *Items convention* (ruling R3, default A; entry O-123): A lists one row per
  logged identifier that has a board, its own ``boards`` row or, through
  ``boards.aliases``, its successor's; an alias row takes the successor
  board's covariates and its own logged counts. B lists one row per canonical
  identifier with the counts pooled over its aliases. A logged identifier
  with no board (board-less) is excluded from ``tab-items`` under both
  conventions and counted in the table note; its checks still enter every
  agreement stratum.
* *Readiness share* is attempts with ``readiness == qc.READY`` over the
  stratum's attempts, with a Wilson interval.
* *Principal flag* (the manuscript's undefined column): the most frequent QC
  flag other than ``replay_ok`` among the stratum's attempts, ties broken by
  ``qc.FLAG_ORDER``, with its attempt share, the generator's provisional
  definition, reported to the owner.
* *Mismatch distance* ``d`` of a disagreeing eligible anchor (``eligible``
  1, ``matches_input`` 0): the replayed state is ``configurations.s_json`` on
  the row with the same ``event_id``; the decoded state is
  ``decode.decode_configuration(json.loads(configuration_json), question_id,
  n_cards, n_zones)`` with the board found by ``canonical_question_id`` in
  ``boards``; ``d`` is the number of card positions that differ (the memo
  script's ``card_distance``, ``diagnose_mismatches.py``). Without a board
  ``d`` is "not computed", never inferred, and counted.
  ``check_scores.distance`` (the distance to the key) is never used.
* *Wilson intervals* are ``audit_sample.wilson`` (standard library, z =
  1.959964); rates and bounds are printed to four decimals.
* *Cell guard* (ruling R5 (a); ``paper1-decisions.md`` 1291-1294, the
  2026-10-14 governance entry, item 4): every cell whose count is students,
  by offering, by term, by development status, by item, the pooled rows of
  the validation table too, with fewer than ``MIN_CELL_STUDENTS`` distinct
  students is suppressed: its numeric fields are emitted empty, the column
  ``suppressed`` reads 1, the note names it as "fewer than five students",
  and its student count is never printed. The threshold counts students,
  never checks or attempts. The pooled cohort is one more cell under the
  same guard (PR #236 Copilot review, 2026-10-19): the pooled measures are
  computed once (``measures(arc.attempts, arc.checks)``) and every pooled
  display, ``data-sample-reporting.csv``, the pooled row of
  ``data-anchor-diagnostics.csv``, ``data-anchor-causes-seven.csv`` and
  ``-six.csv`` (their ``n_all_checks`` / ``n_eligible`` /
  ``n_never_reconstructed`` header values too), ``data-mismatch-distance.csv``
  and ``-hist.csv``, carries a ``suppressed`` column and blanks its counts
  when the corpus holds fewer than five students.
* *RQ3 subset* (``data-sample-reporting.csv``): the attempts
  ``contrast.read_population`` admits, mirrored from the export's columns
  (``rq3_admitted``): ``readiness`` in ``ContrastConfig().readiness``
  (``ready``), no decoded ``qc_flags_json`` flag in
  ``ContrastConfig().exclude_flags`` (none by default), ``first_check_correct``
  recorded (a scored check) and ``eventual_correct == "1"``, the harness's
  own symbols, ruling R13 (a)'s posture. On the fixture archive that is 6
  attempts, not the 9 ready ones with an eligible anchor (PR #236 Copilot
  review, 2026-10-19).
* *Diagnostic categories* (ruling R7 (a); procedure row 3.4; the vocabulary
  of ``sec-reconstruction.ptx`` 265-268): each disagreeing eligible anchor
  takes the first applicable of *source-map problem* (the attempt's
  ``qc_flags_json`` holds ``missing_source_map``, or the item has no board),
  *ambiguous ordering* (``row_order_ambiguous``), *skipped event*
  (``invalid_state`` or ``parse_error`` and the check's own payload re-decodes
  cleanly, the structural flag masks a decode cause, so the payload is
  re-decoded rather than ``anchor_cause`` read), else *unexplained
  disagreement*. An attempt-level flag attributes to every disagreeing anchor
  of that attempt: exact for source-map problem, a conservative
  over-attribution for the other two.
* *Corpus flow* (``tab-corpus-flow``; ruling R8 (ii), from the attrition
  ledger's ``flow`` export, PR #239): the manuscript's five columns read the
  ledger's pooled ``rows_out`` at steps 0 (Raw), 1 (Authorized), 3
  (Source-aligned; step 2, question type, folded in), 5 (Reconstructed;
  step 4, event parsing, folded in) and 6 (Ready), ruling R9 (b). The
  Attempts row has no value before Reconstructed (ruling J10: blank,
  ``suppressed`` 0, the manuscript's "—"); the step-3 attempt loss is stated
  in the note, never in the table. The Items and Item traces rows follow the
  items convention (logged identifiers under A, canonical identifiers under
  B). Every cell is guarded by its own row's ``n_students``, the pooled
  table and the by-offering file alike; a suppressed cell and a no-value
  cell both render "—" in the fragment and are told apart by the CSV's
  ``suppressed`` column and by the note, which names the suppressed cells.
  The note's own numbers take the same guard (the Copilot round on PR #240):
  a count prints only where its cell is shown, a loss only where the cells
  either side of it are, else the dash. The by-offering file is keyed by the pseudonym of the ledger's
  ``import_sha256``.

Outputs under ``--out`` (every CSV opens with ``# run_id``,
``# manifest_fingerprint``, ``# generator``, ``# items_convention`` where the
convention matters, and one ``# definitions`` line; rows sorted
deterministically; no wall-clock stamp anywhere, so two runs on one archive
are byte-identical): ``tab-items.csv`` + ``.ptx`` + ``-note.ptx``,
``tab-validation-results.csv`` + ``.ptx`` + ``-note.ptx``,
``tab-validation-by-item.csv``, ``data-validation-by-offering.csv``,
``fig-validation-results.csv``, ``data-anchor-causes-seven.csv`` and
``-six.csv``, ``data-mismatch-distance.csv`` and ``-hist.csv``,
``data-anchor-diagnostics.csv``, ``data-sample-reporting.csv`` (every pooled
file with a ``suppressed`` column, the cell guard above),
``tab-corpus-flow.csv`` + ``.ptx`` + ``-note.ptx`` and
``data-corpus-flow-by-offering.csv`` (from ``exports/flow.csv``). The PreTeXt
fragments (ruling R2) are one XML comment stamp followed by one whole
``<table>`` element in the manuscript's markup, every cell byte-derived from
the CSV row; the manuscript itself is never written. The per-check
classification behind the diagnostics goes to
``<archive>/validation/checks_classified.csv``, internal, never under
``--out``, never committed, and guarded like ``--out``.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape


from proteus.core import manifest
from proteus.paper1 import audit_sample  # guard_output_dir, wilson
from proteus.paper1._root import find_root
from proteus.services.dragndrop import decode, policy, qc
from proteus.services.dragndrop import derive as derive_mod
from proteus.services.dragndrop import process as process_mod
from proteus.services.dragndrop import reconstruct as reconstruct_mod
from proteus.services.dragndrop import scoring as scoring_mod
from proteus.services.dragndrop.contrast import ContrastConfig

ROOT = find_root()  # the checkout (proteus.paper1._root)

# 1.0: PR #236; 1.1: tab-corpus-flow from the ledger's flow export (ruling R8 ii).
GENERATOR_VERSION = "1.1"

# The frozen-run minimum stage versions, the procedure's Stage 1.3 line
# "2 / 5 / 3 / 9" (derive / reconstruct / score / analyze;
# paper1-frozen-run-procedure.md:100; derive 2 is the governance filter,
# dnd/0016, PR #238). An archive below them predates a frozen rule; one above
# the harness's current STAGE_VERSION comes from newer code than this
# generator and is refused too.
MIN_STAGE_VERSION = {"derive": 2, "reconstruct": 5, "score": 3, "analyze": 9}
CURRENT_STAGE_VERSION = {
    "derive": derive_mod.STAGE_VERSION,
    "reconstruct": reconstruct_mod.STAGE_VERSION,
    "score": scoring_mod.STAGE_VERSION,
    "analyze": process_mod.STAGE_VERSION,
}

# The small-cohort rule: cells under five students suppressed
# (paper1-decisions.md, the 2026-10-14 governance entry, item 4; ruling R5 (a)).
MIN_CELL_STUDENTS = 5

REQUIRED_EXPORTS = (
    "checks", "attempt_metrics", "configurations", "boards", "item_summary", "check_scores", "flow",
)
HAND_SCHEMA_VERSION = 1
READINESS_READY_ONLY = "ready_only"
CONVENTIONS = ("A", "B")
SUPPRESSED_MARK = "—"  # an em dash in a suppressed PreTeXt cell
NOT_COMPUTED = "not computed"

# Row 3.4's vocabulary (sec-reconstruction.ptx:265-268), in this order.
CATEGORY_SOURCE_MAP = "source-map problem"
CATEGORY_AMBIGUOUS = "ambiguous ordering"
CATEGORY_SKIPPED = "skipped event"
CATEGORY_UNEXPLAINED = "unexplained disagreement"
CATEGORY_ORDER = (
    CATEGORY_SOURCE_MAP, CATEGORY_AMBIGUOUS, CATEGORY_SKIPPED, CATEGORY_UNEXPLAINED,
)

# The Results prose's six causes (sec-results.ptx:96-102, labels verbatim):
# key, the stored causes it sums, label. The two unknown causes merge.
SIX_CAUSES = (
    (qc.CAUSE_NO_MOVE_LOGGING, (qc.CAUSE_NO_MOVE_LOGGING,),
     "logged before the date their offering first recorded a move"),
    (qc.CAUSE_RESUMED_ATTEMPT, (qc.CAUSE_RESUMED_ATTEMPT,),
     "in attempts that resumed an earlier attempt on the same item"),
    (qc.CAUSE_MALFORMED, (qc.CAUSE_MALFORMED,), "malformed payloads"),
    (qc.CAUSE_DUPLICATE_CARD, (qc.CAUSE_DUPLICATE_CARD,), "with a card listed in two zones"),
    ("unknown_card_or_zone", (qc.CAUSE_UNKNOWN_CARD, qc.CAUSE_UNKNOWN_ZONE),
     "with an undecodable card or zone label"),
    (qc.CAUSE_OFFBOARD_SLOT, (qc.CAUSE_OFFBOARD_SLOT,), "with a slot index outside the board"),
)

ITEMS_COLUMNS = (
    "item", "section", "P", "R", "decoy_cards", "decoy_zones",
    "students", "attempts", "checks",
    "eligible_anchors", "correct_checks_feedback", "correct_checks_scored",
    "source_version", "feedback_policy", "canonical_question_id", "is_alias", "suppressed",
)
ITEMS_COUNT_COLUMNS = (
    "students", "attempts", "checks", "eligible_anchors",
    "correct_checks_feedback", "correct_checks_scored",
)
ITEMS_PTX_COLUMNS = (
    "item", "section", "P", "R", "decoy_cards", "decoy_zones", "students", "attempts", "checks",
)
VALIDATION_MEASURES = (
    "attempts", "eligible_anchors", "agreement_rate", "agreement_lo", "agreement_hi",
    "ready_rate", "ready_lo", "ready_hi", "principal_flag", "principal_flag_share", "n_students",
)
VALIDATION_COLUMNS = ("stratum", *VALIDATION_MEASURES, "suppressed")
BY_ITEM_COLUMNS = ("item", "canonical_question_id", "is_alias", *VALIDATION_MEASURES, "suppressed")
BY_OFFERING_COLUMNS = ("offering", *VALIDATION_MEASURES, "suppressed")
FIG_MEASURES = (
    "eligible_anchors", "agreeing", "agreement_rate", "lo", "hi",
    "attempts", "n_ready", "n_limited", "n_exclude",
)
FIG_COLUMNS = ("item", "section", *FIG_MEASURES, "suppressed")
DIAGNOSTICS_COLUMNS = ("scope", "offering", "n_disagreeing", *CATEGORY_ORDER, "suppressed")
# The pooled count files (the cell guard applies to the pooled cohort too).
SAMPLE_COLUMNS = ("quantity", "value", "suppressed")
CAUSES_SEVEN_COLUMNS = ("cause", "n_checks", "share_of_all_checks", "suppressed")
CAUSES_SIX_COLUMNS = ("cause", "components", "label", "n_checks", "share_of_all_checks", "suppressed")
DISTANCE_COLUMNS = (
    "n_disagreeing", "n_computed", "n_not_computed", "median_d", "share_d_le_1", "suppressed",
)
DISTANCE_HIST_COLUMNS = ("d", "n", "suppressed")
CLASSIFIED_COLUMNS = (
    "event_id", "attempt_id", "question_id", "canonical_question_id", "offering",
    "category", "evidence", "d",
)

# tab-corpus-flow: the manuscript's five columns and the ledger step each one reads
# (ruling R9 b; sec-empirical-study.ptx:186-250). Source-aligned folds step 2
# (question_type) in; Reconstructed folds step 4 (event_parsing) in.
FLOW_COLUMNS = (
    ("Raw", 0), ("Authorized", 1), ("Source-aligned", 3), ("Reconstructed", 5), ("Ready", 6),
)
# The six rows in the manuscript's order; the items and traces quantities follow the
# items convention (R3 / O-123: logged identifiers under A, canonical under B).
FLOW_ROWS = {
    "A": (("Students", "students"), ("Items", "items_logged"),
          ("Item traces", "item_traces_logged"), ("Attempts", "attempts"),
          ("Events", "events"), ("Checks", "checks")),
    "B": (("Students", "students"), ("Items", "items_canonical"),
          ("Item traces", "item_traces_canonical"), ("Attempts", "attempts"),
          ("Events", "events"), ("Checks", "checks")),
}
# Attempts exist from reconstruction onward: no value in the first three columns
# (ruling J10; the manuscript's "—" cells). The ledger's step-3 attempt count is
# reported in the note as the source-alignment loss, never in the table (Q2).
FLOW_NO_VALUE = frozenset({("Attempts", "Raw"), ("Attempts", "Authorized"), ("Attempts", "Source-aligned")})
FLOW_CSV_COLUMNS = ("quantity", "column", "step", "value", "n_students", "suppressed")
FLOW_BY_OFFERING_COLUMNS = ("offering", *FLOW_CSV_COLUMNS)
FLOW_STEPS = tuple(range(9))
FLOW_QUANTITIES = ("students", "items_logged", "items_canonical", "item_traces_logged",
                   "item_traces_canonical", "attempts", "events", "checks")

# The cell guard's one sentence, stated once and reused by every display.
_GUARD_TEXT = (
    f"cells with fewer than {MIN_CELL_STUDENTS} distinct students are suppressed "
    "(numeric fields empty, suppressed = 1)"
)

DEFINITIONS = {
    "attempts": (
        "attempts = attempt_metrics rows of the stratum (readiness-blind, resumed included); "
        "n_students = distinct student_id among them"
    ),
    "agreement": (
        "eligible_anchors = checks rows with eligible = 1 of every admitted attempt regardless "
        "of readiness; agreement = matches_input = 1 among them; intervals are 95% Wilson "
        "(z = 1.959964), rates and bounds to 4 decimals"
    ),
    "ready": "ready_rate = attempts with readiness = ready over the stratum's attempts",
    "principal": (
        "principal_flag = the most frequent QC flag other than replay_ok among the stratum's "
        "attempts (ties broken by qc.FLAG_ORDER), principal_flag_share = its attempt share"
    ),
    "guard": _GUARD_TEXT,
    "pooled_guard": "the pooled cohort is guarded like any cell: " + _GUARD_TEXT,
    "rq3": (
        "rq3_subset_attempts = the attempts contrast.read_population admits, mirrored from "
        "the export: readiness in ContrastConfig().readiness, no qc_flags_json flag in "
        "ContrastConfig().exclude_flags, first_check_correct recorded (a scored check) and "
        "eventual_correct = 1"
    ),
}


class Refusal(Exception):
    """A refusal: one line on stderr, exit status 2, nothing written."""


def _card_distance(a, b) -> int:
    # diagnose_mismatches.card_distance (PR #213): the number of card
    # positions on which two state vectors differ.
    return sum(1 for x, y in zip(a, b) if x != y)


def _fmt4(x: float) -> str:
    return f"{x:.4f}"


def _rate(k: int, n: int) -> tuple[str, str, str]:
    """(rate, lo, hi) to four decimals, or three empty strings when n = 0."""
    if n == 0:
        return "", "", ""
    lo, hi = audit_sample.wilson(k, n)
    return _fmt4(k / n), _fmt4(lo), _fmt4(hi)


def _flags(attempt: dict | None) -> set[str]:
    if attempt is None:
        return set()
    try:
        return set(json.loads(attempt.get("qc_flags_json") or "[]"))
    except ValueError:
        return set()


def _principal_flag(attempts: list[dict]) -> tuple[str, int]:
    """The most frequent flag other than replay_ok, ties by qc.FLAG_ORDER."""
    counts: Counter = Counter()
    for a in attempts:
        for flag in _flags(a):
            if flag != "replay_ok":
                counts[flag] += 1
    if not counts:
        return "", 0
    order = {flag: i for i, flag in enumerate(qc.FLAG_ORDER)}
    best = max(counts.values())
    tied = [f for f, n in counts.items() if n == best]
    tied.sort(key=lambda f: (order.get(f, len(order)), f))
    return tied[0], best


def _read_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


# ---------------------------------------------------------------------------
# The archive: manifest checks and exports
# ---------------------------------------------------------------------------
class Archive:
    """Everything the generator reads, loaded once and checked (refusals
    1-5 and 7); nothing is written while this is built."""

    def __init__(self, archive: Path, convention: str) -> None:
        self.path = archive
        self.run_id = archive.name
        self.convention = convention
        data = self._read_manifest()
        self.body: dict = data["body"]
        self.fingerprint: str = data["fingerprint"]
        self.hand: dict = self._check_hand(data)
        # import sha256 -> pseudonym (body.imports[]); pseudonym -> term, exposure (hand).
        self.pseudonym_of: dict[str, str] = {
            str(i["sha256"]): str(i["pseudonym"]) for i in self.body.get("imports", [])
        }
        self.offerings: list[str] = sorted(set(self.pseudonym_of.values()))
        self.term_of: dict[str, str] = {}
        self.exposed: dict[str, bool] = {}
        for entry in self.hand["corpus"]["files"]:
            pseud = self.pseudonym_of.get(str(entry.get("sha256")))
            if pseud is not None:
                self.term_of[pseud] = entry["term"]
                self.exposed[pseud] = entry["development_exposure"]
        # Terms in the hand block's own order of first appearance (the owner's
        # chronological order; the manuscript lists Fall 2025 before Spring 2026).
        self.terms: list[str] = []
        for entry in self.hand["corpus"]["files"]:
            if entry["term"] not in self.terms:
                self.terms.append(entry["term"])
        self.section_of: dict[str, str] = {
            str(i.get("question_id")): str(i.get("section", ""))
            for i in self.hand.get("items", []) or []
            if isinstance(i, dict) and i.get("question_id")
        }
        self._check_primary()
        self._load_exports()
        self._check_flow()

    # -- refusals 1 and 2 --------------------------------------------------
    def _read_manifest(self) -> dict:
        path = self.path / "manifest.yaml"
        if not path.is_file():
            raise Refusal(f"{self.path} is not an archive: no manifest.yaml")
        try:
            data = manifest.read_manifest(path)
        except Exception as exc:  # noqa: BLE001 - any parse failure is a refusal
            raise Refusal(f"manifest.yaml could not be read: {exc}") from exc
        body = data.get("body")
        stored = data.get("fingerprint")
        if not isinstance(body, dict) or not isinstance(stored, str):
            raise Refusal("manifest.yaml has no body or no fingerprint")
        if stored != manifest.fingerprint(body):
            raise Refusal(
                "the manifest's stored fingerprint does not match its body "
                "(tampered or hand-edited body); refusing"
            )
        return data

    # -- refusal 3 ---------------------------------------------------------
    def _check_hand(self, data: dict) -> dict:
        hand = data.get("hand")
        if not isinstance(hand, dict):
            raise Refusal("manifest.yaml has no top-level `hand` block (ruling R11 (a))")
        version = hand.get("schema_version", HAND_SCHEMA_VERSION)
        if version != HAND_SCHEMA_VERSION:
            raise Refusal(
                f"hand.schema_version {version!r} is not {HAND_SCHEMA_VERSION}; "
                "this generator reads schema version 1 only"
            )
        readiness = (hand.get("configuration") or {}).get("readiness")
        if readiness != READINESS_READY_ONLY:
            raise Refusal(
                f"hand.configuration.readiness is {readiness!r}, not "
                f"{READINESS_READY_ONLY!r}: not the frozen primary configuration"
            )
        files = (hand.get("corpus") or {}).get("files")
        if not isinstance(files, list):
            raise Refusal("hand.corpus.files is missing or not a list")
        by_sha: dict[str, dict] = {}
        for entry in files:
            if isinstance(entry, dict) and entry.get("sha256"):
                by_sha[str(entry["sha256"])] = entry
        for imp in data["body"].get("imports", []):
            sha = str(imp.get("sha256"))
            entry = by_sha.get(sha)
            if entry is None:
                raise Refusal(
                    f"import {imp.get('pseudonym')!r} has no hand.corpus.files entry"
                )
            if not isinstance(entry.get("term"), str) or not entry["term"]:
                raise Refusal(
                    f"hand.corpus.files entry for {imp.get('pseudonym')!r} lacks a string term"
                )
            if not isinstance(entry.get("development_exposure"), bool):
                raise Refusal(
                    f"hand.corpus.files entry for {imp.get('pseudonym')!r} lacks a boolean "
                    "development_exposure"
                )
            if entry.get("pseudonym") != imp.get("pseudonym"):
                raise Refusal(
                    f"hand.corpus.files names import {sha[:12]}… {entry.get('pseudonym')!r} "
                    f"but the manifest body names it {imp.get('pseudonym')!r}"
                )
        return hand

    # -- refusal 4 ---------------------------------------------------------
    def _expected_primary(self) -> dict[str, dict]:
        """The frozen primary per stage, composed from the harness's symbols
        (the composition of sensitivity._PRIMARY_RECONSTRUCT / _SCORE /
        _ANALYZE with their data keys; the hashes come from the manifest's
        services.dnd section, aliases/logging_regimes.manifest_section)."""
        dnd = (self.body.get("services") or {}).get("dnd") or {}
        alias_sha = (dnd.get("question_aliases") or {}).get("set_sha256")
        regime_sha = (dnd.get("logging_regimes") or {}).get("set_sha256")
        return {
            "reconstruct": {
                "question_id": None,
                "gap_seconds": reconstruct_mod.SegmentationConfig().gap_seconds,
                "split_on_session_change": True,
                "split_on_page_view": True,
                "split_on_reset": False,
                "feedback_policy": policy.LEGACY,
                "idle_threshold_s": qc.DEFAULT_IDLE_THRESHOLD_S,
                "seed_resumed_attempts": False,
                "alias_set_sha256": alias_sha,
                "regime_set_sha256": regime_sha,
            },
            "score": {"question_id": None, "alias_set_sha256": alias_sha},
            "analyze": {
                "question_id": None,
                "cadence_threshold": process_mod.ProcessConfig().cadence_threshold,
                "idle_threshold_s": qc.DEFAULT_IDLE_THRESHOLD_S,
                "exclude_noop_moves": True,
                "truncate_at_first_correct": False,
                "alias_set_sha256": alias_sha,
            },
        }

    def _newest_run_config(self, stage: str) -> dict:
        """The recorded configuration of the *newest* ``dnd <stage>`` run (by
        ``run_id``, mirroring ``sensitivity._latest_run``), after the checks
        every stage shares: the run exists, is ``completed``, records a
        configuration, and its ``stage_version`` lies between
        ``MIN_STAGE_VERSION`` and ``CURRENT_STAGE_VERSION``."""
        runs = self.body.get("pipeline_runs") or []
        candidates = [
            r for r in runs
            if isinstance(r, dict) and r.get("service") == "dnd" and r.get("stage") == stage
        ]
        if not candidates:
            raise Refusal(f"the manifest records no `dnd {stage}` run")
        latest = max(candidates, key=lambda r: int(r.get("run_id") or 0))
        if latest.get("status") != "completed":
            raise Refusal(
                f"the newest `dnd {stage}` run is {latest.get('status')!r}, not completed"
            )
        config = latest.get("config")
        if not isinstance(config, dict):
            raise Refusal(f"the newest `dnd {stage}` run has no recorded configuration")
        version = config.get("stage_version")
        if not isinstance(version, int):
            raise Refusal(f"the newest `dnd {stage}` run records no stage_version")
        if version < MIN_STAGE_VERSION[stage]:
            raise Refusal(
                f"`dnd {stage}` stage_version {version} is below the frozen-run minimum "
                f"{MIN_STAGE_VERSION[stage]} (paper1-frozen-run-procedure.md Stage 1.3)"
            )
        if version > CURRENT_STAGE_VERSION[stage]:
            raise Refusal(
                f"`dnd {stage}` stage_version {version} is above this generator's harness "
                f"({CURRENT_STAGE_VERSION[stage]}): the archive comes from newer code"
            )
        return config

    def _check_primary(self) -> None:
        # The derive clause (PR #238's hand-off, taken in the light form): the
        # newest derive run ran under the governance filter (stage_version 2,
        # dnd/0016) and recorded the roles set it withheld under, null with
        # an empty roles table, which is allowed (the manifest's YAML subset
        # round-trips a null value with its key). Derive's other keys
        # (import_id, question_id) are a pass's scope, not a frozen primary,
        # so there is no key-for-key comparison for this stage.
        derive_config = self._newest_run_config("derive")
        if "roles_set_sha256" not in derive_config:
            raise Refusal(
                "the newest `dnd derive` run records no roles_set_sha256: the archive was "
                "derived without the governance filter (dnd/0016; derive stage_version 2)"
            )
        for stage, expected in self._expected_primary().items():
            config = self._newest_run_config(stage)
            recorded = {k: v for k, v in config.items() if k != "stage_version"}
            if recorded != expected:
                differences = []
                for key in sorted(set(expected) | set(recorded)):
                    if key in recorded and key in expected and recorded[key] == expected[key]:
                        continue
                    if key not in recorded:
                        differences.append(f"{key} not recorded (frozen primary {expected[key]!r})")
                    elif key not in expected:
                        differences.append(f"{key}={recorded[key]!r} is not part of the frozen primary")
                    else:
                        differences.append(f"{key}={recorded[key]!r} (frozen primary {expected[key]!r})")
                raise Refusal(
                    f"the newest `dnd {stage}` run is not the frozen primary configuration: "
                    + "; ".join(differences)
                )

    # -- refusal 5, then the rows -------------------------------------------
    def _load_exports(self) -> None:
        exports = self.path / "exports"
        missing = [n for n in REQUIRED_EXPORTS if not (exports / f"{n}.csv").is_file()]
        if missing:
            raise Refusal(
                "missing export(s) under exports/: " + ", ".join(f"{n}.csv" for n in missing)
            )
        self.checks = _read_csv(exports / "checks.csv")
        self.attempts = _read_csv(exports / "attempt_metrics.csv")
        configurations = _read_csv(exports / "configurations.csv")
        self.boards: dict[str, dict] = {
            b["question_id"]: b for b in _read_csv(exports / "boards.csv")
        }
        self.item_summary = _read_csv(exports / "item_summary.csv")
        self.scores = _read_csv(exports / "check_scores.csv")
        for name, rows in (("checks", self.checks), ("configurations", configurations)):
            if any(r.get("seeded", "") != "" for r in rows):
                raise Refusal(
                    f"exports/{name}.csv carries a seeded replay marker: the pass seeded "
                    "resumed attempts (D3.2 knob 10), which is not the primary configuration"
                )
        for name, rows in (("checks", self.checks), ("attempt_metrics", self.attempts)):
            for r in rows:
                pseud = self.pseudonym_of.get(r.get("import_sha256", ""))
                if pseud is None:
                    raise Refusal(
                        f"exports/{name}.csv has a row whose import_sha256 is not among the "
                        "manifest's imports: the exports are not this manifest's"
                    )
                r["offering"] = pseud
        self.configs_by_event: dict[str, dict] = {
            c["event_id"]: c for c in configurations if c.get("is_check") == "1" and c.get("event_id")
        }
        self.attempts_by_id: dict[str, dict] = {a["attempt_id"]: a for a in self.attempts}
        # alias -> the board it resolves to (boards.aliases, ';'-joined).
        self.successor: dict[str, str] = {}
        for b in self.boards.values():
            for alias in (b.get("aliases") or "").split(";"):
                if alias:
                    self.successor[alias] = b["question_id"]
        # The ledger's flow export (ruling R8 ii): keyed by (breakdown,
        # offering, quantity, step), the offering a pseudonym, a by_offering
        # key outside the manifest's imports means the export is not this
        # manifest's, as for checks and attempt_metrics above. A repeated key
        # (a duplicated line, or two imports under one pseudonym, whose
        # student counts cannot be pooled) is refused rather than letting the
        # later row replace the earlier one silently (PR 3 review round).
        self.flow = _read_csv(exports / "flow.csv")
        self.flow_by_key: dict[tuple[str, str, str, int], dict] = {}
        for r in self.flow:
            if r.get("breakdown") == "by_offering":
                pseud = self.pseudonym_of.get(r.get("key", ""))
                if pseud is None:
                    raise Refusal(
                        "exports/flow.csv has a by_offering row whose key is not among the "
                        "manifest's imports: the exports are not this manifest's"
                    )
                r["offering"] = pseud
            elif r.get("breakdown") == "pooled":
                r["offering"] = ""
            else:
                raise Refusal(f"exports/flow.csv: unknown breakdown {r.get('breakdown')!r}")
            try:
                step = int(r.get("step") or "")  # a short record reads None
            except ValueError:
                raise Refusal(f"exports/flow.csv: step {r.get('step')!r} is not a step number") from None
            key = (r["breakdown"], r["offering"], r["quantity"], step)
            if key in self.flow_by_key:
                raise Refusal(
                    f"exports/flow.csv: duplicate row ({r['quantity']}, step {step})"
                    + (" for one offering (a repeated row, or two imports under one pseudonym)"
                       if r["offering"] else "")
                )
            self.flow_by_key[key] = r
        # The ledger emits every (step, quantity) pooled cell, J10's empty
        # ones included, so the 8 × 9 pooled grid is complete by construction.
        for quantity in FLOW_QUANTITIES:
            for step in FLOW_STEPS:
                if ("pooled", "", quantity, step) not in self.flow_by_key:
                    raise Refusal(f"exports/flow.csv lacks the pooled row ({quantity}, step {step})")

    # -- refusal 7 -----------------------------------------------------------
    def _check_flow(self) -> None:
        """The flow export must describe this archive's attempts (ruling
        Q13: the two counts the manuscript cites most). Reconstructed
        attempts, the pooled step-5 ``rows_out``, are the ``attempt_metrics``
        rows without ``missing_source_map`` (ruling R9 (b): attempts lose
        rows at step 3 only before readiness); Ready attempts, step 6, the
        rows with ``readiness == qc.READY``."""
        expected = (
            ("Reconstructed", 5, sum(1 for a in self.attempts if "missing_source_map" not in _flags(a))),
            ("Ready", 6, sum(1 for a in self.attempts if a.get("readiness") == qc.READY)),
        )
        for column, step, n_metrics in expected:
            n_flow = _flow_count(self.flow_by_key[("pooled", "", "attempts", step)], "rows_out")
            if n_flow != n_metrics:
                raise Refusal(
                    f"exports/flow.csv disagrees with attempt_metrics.csv: {column} attempts "
                    f"{n_flow} in the flow, {n_metrics} in attempt_metrics"
                )

    # -- item resolution --------------------------------------------------------
    def board_for(self, logged_id: str) -> dict | None:
        board = self.boards.get(logged_id)
        if board is None and logged_id in self.successor:
            board = self.boards.get(self.successor[logged_id])
        return board

    def logged_identifiers(self) -> list[str]:
        return sorted(
            {a["question_id"] for a in self.attempts} | {c["question_id"] for c in self.checks}
        )

    def logged_with_board(self) -> tuple[list[str], list[str]]:
        """The logged identifiers with a board (their own or, through the
        alias, their successor's) and the board-less ones, both sorted."""
        logged = self.logged_identifiers()
        with_board = [q for q in logged if self.board_for(q) is not None]
        boardless = [q for q in logged if self.board_for(q) is None]
        return with_board, boardless

    def items(self) -> tuple[list[tuple[str, str, bool]], list[str]]:
        """Under the convention: [(item, board question_id, is_alias)] and
        the board-less logged identifiers."""
        with_board, boardless = self.logged_with_board()
        if self.convention == "A":
            rows = [(q, self.board_for(q)["question_id"], q not in self.boards) for q in with_board]
        else:
            canonical = sorted({self.board_for(q)["question_id"] for q in with_board})
            rows = [(c, c, False) for c in canonical]
        return rows, boardless

    def item_selector(self, item: str):
        key = "question_id" if self.convention == "A" else "canonical_question_id"
        return lambda row: row.get(key) == item

    def section(self, item: str, board_id: str) -> str:
        return self.section_of.get(item) or self.section_of.get(board_id) or ""


# ---------------------------------------------------------------------------
# Measures
# ---------------------------------------------------------------------------
def measures(attempts: list[dict], checks: list[dict]) -> dict:
    """The unguarded numbers of one stratum."""
    eligible = [c for c in checks if c.get("eligible") == "1"]
    readiness = Counter(a.get("readiness") for a in attempts)
    flag, flag_n = _principal_flag(attempts)
    return {
        "n_students": len({a["student_id"] for a in attempts}),
        "attempts": len(attempts),
        "checks": len(checks),
        "eligible_anchors": len(eligible),
        "agreeing": sum(1 for c in eligible if c.get("matches_input") == "1"),
        "n_ready": readiness.get(qc.READY, 0),
        "n_limited": readiness.get(qc.LIMITED, 0),
        "n_exclude": readiness.get(qc.EXCLUDE, 0),
        "principal_flag": flag,
        "principal_flag_n": flag_n,
    }


def suppressed(m: dict) -> bool:
    """The cell guard: students, never checks or attempts."""
    return m["n_students"] < MIN_CELL_STUDENTS


def rq3_admitted(attempts: list[dict], config: ContrastConfig | None = None) -> list[dict]:
    """The attempts ``contrast.read_population`` would admit, read off the
    ``attempt_metrics`` export: readiness in ``config.readiness``, no decoded
    ``qc_flags_json`` flag in ``config.exclude_flags``, ``first_check_correct``
    recorded (its ``_POPULATION_SQL``: ``first_check_correct IS NOT NULL``)
    and ``eventual_correct = 1``."""
    config = config or ContrastConfig()
    admitted = set(config.readiness)
    excluded = set(config.exclude_flags)
    return [
        a for a in attempts
        if a.get("readiness") in admitted
        and not (_flags(a) & excluded)
        and a.get("first_check_correct", "") != ""
        and a.get("eventual_correct") == "1"
    ]


def validation_fields(m: dict) -> dict:
    """The VALIDATION_MEASURES + suppressed of one stratum, as strings."""
    if suppressed(m):
        return {**{c: "" for c in VALIDATION_MEASURES}, "suppressed": "1"}
    rate, lo, hi = _rate(m["agreeing"], m["eligible_anchors"])
    ready, rlo, rhi = _rate(m["n_ready"], m["attempts"])
    share = _fmt4(m["principal_flag_n"] / m["attempts"]) if m["principal_flag"] else ""
    return {
        "attempts": str(m["attempts"]),
        "eligible_anchors": str(m["eligible_anchors"]),
        "agreement_rate": rate, "agreement_lo": lo, "agreement_hi": hi,
        "ready_rate": ready, "ready_lo": rlo, "ready_hi": rhi,
        "principal_flag": m["principal_flag"],
        "principal_flag_share": share,
        "n_students": str(m["n_students"]),
        "suppressed": "0",
    }


def fig_fields(m: dict) -> dict:
    if suppressed(m):
        return {**{c: "" for c in FIG_MEASURES}, "suppressed": "1"}
    rate, lo, hi = _rate(m["agreeing"], m["eligible_anchors"])
    return {
        "eligible_anchors": str(m["eligible_anchors"]), "agreeing": str(m["agreeing"]),
        "agreement_rate": rate, "lo": lo, "hi": hi,
        "attempts": str(m["attempts"]), "n_ready": str(m["n_ready"]),
        "n_limited": str(m["n_limited"]), "n_exclude": str(m["n_exclude"]),
        "suppressed": "0",
    }


def item_rows_unguarded(arc: Archive) -> list[dict]:
    """tab-items before the cell guard: every count as an int, plus the
    measures dict under ``_measures`` (used by the by-item and figure rows)."""
    rows = []
    for item, board_id, is_alias in arc.items()[0]:
        board = arc.boards[board_id]
        select = arc.item_selector(item)
        attempts = [a for a in arc.attempts if select(a)]
        checks = [c for c in arc.checks if select(c)]
        scores = [s for s in arc.scores if select(s)]
        m = measures(attempts, checks)
        rows.append({
            "item": item,
            "section": arc.section(item, board_id),
            "P": board["n_cards"],
            "R": board["n_zones"],
            "decoy_cards": board["num_decoy_cards"],
            "decoy_zones": board["num_decoy_slots"],
            "students": m["n_students"],
            "attempts": m["attempts"],
            "checks": m["checks"],
            "eligible_anchors": m["eligible_anchors"],
            "correct_checks_feedback": sum(1 for c in checks if c.get("feedback") == "Correct"),
            "correct_checks_scored": sum(1 for s in scores if s.get("all_or_nothing") == "1"),
            "source_version": board["source_hash"],
            "feedback_policy": ";".join(sorted({a.get("feedback_policy", "") for a in attempts})),
            "canonical_question_id": board_id,
            "is_alias": "1" if is_alias else "0",
            "_measures": m,
        })
    return rows


def item_rows(arc: Archive) -> list[dict]:
    """tab-items after the cell guard, every value a string."""
    out = []
    for raw in item_rows_unguarded(arc):
        row = {c: str(raw[c]) for c in ITEMS_COLUMNS if c != "suppressed"}
        if suppressed(raw["_measures"]):
            for c in ITEMS_COUNT_COLUMNS:
                row[c] = ""
            row["suppressed"] = "1"
        else:
            row["suppressed"] = "0"
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# tab-corpus-flow: the cells of one table (ruling R8 ii)
# ---------------------------------------------------------------------------
def _flow_count(row: dict, column: str) -> int:
    """One count of a flow row; a blank or non-numeric count on a cell the
    table shows is a malformed export, refused by its (quantity, step),
    never by a student. A record cut short of the column reads it as None
    (``csv.DictReader``'s restval), refused as an empty count like a blank
    one (PR 3 review round 2), never a TypeError past the refusal."""
    text = row.get(column) or ""
    if text == "":
        raise Refusal(
            f"exports/flow.csv: empty {column} on a valued cell "
            f"({row.get('quantity')}, step {row.get('step')})"
        )
    try:
        return int(text)
    except ValueError:
        raise Refusal(
            f"exports/flow.csv: {column} {text!r} is not a count "
            f"({row.get('quantity')}, step {row.get('step')})"
        ) from None


def flow_cell(row: dict | None, no_value: bool) -> dict:
    """value / n_students / suppressed of one cell. A no-value cell (J10) is
    blank and unsuppressed; a valued cell with fewer than five students is
    blank and suppressed and never prints its student count."""
    if no_value:
        return {"value": "", "n_students": "", "suppressed": "0"}
    n = _flow_count(row, "n_students")
    if suppressed({"n_students": n}):  # the same guard as every display
        return {"value": "", "n_students": "", "suppressed": "1"}
    # The ledger writes every count as an int, so this is the export's own
    # text on a well-formed file; a blank or non-count value on a shown cell
    # is refused, never rendered as a dash.
    return {"value": str(_flow_count(row, "rows_out")), "n_students": str(n), "suppressed": "0"}


def flow_rows(arc: Archive, offering: str | None = None) -> list[dict]:
    """The 30 cells of one table, pooled (offering None) or one offering,
    in the manuscript's row order then column order; every value a string."""
    breakdown, key = ("pooled", "") if offering is None else ("by_offering", offering)
    out = []
    for label, quantity in FLOW_ROWS[arc.convention]:
        for column, step in FLOW_COLUMNS:
            row = arc.flow_by_key.get((breakdown, key, quantity, step))
            if row is None:
                raise Refusal(
                    f"exports/flow.csv lacks the {breakdown} row ({quantity}, step {step})"
                    + (" for one offering" if offering else "")
                )
            out.append({"quantity": label, "column": column, "step": str(step),
                        **flow_cell(row, (label, column) in FLOW_NO_VALUE)})
    return out


# ---------------------------------------------------------------------------
# Disagreements: distance and diagnostic category
# ---------------------------------------------------------------------------
def classify_disagreements(arc: Archive) -> list[dict]:
    """One row per disagreeing eligible anchor: its distance d and its
    diagnostic category (first applicable), with the evidence."""
    order = {flag: i for i, flag in enumerate(qc.FLAG_ORDER)}
    out = []
    for c in arc.checks:
        if c.get("eligible") != "1" or c.get("matches_input") != "0":
            continue
        attempt = arc.attempts_by_id.get(c.get("attempt_id", ""))
        flags = _flags(attempt)
        board = arc.boards.get(c.get("canonical_question_id", ""))
        config = arc.configs_by_event.get(c.get("event_id", ""))
        d: object = NOT_COMPUTED
        redecodes_clean = None
        try:
            payload = json.loads(c.get("configuration_json") or "")
        except ValueError:
            payload = None
        if board is not None and config is not None and isinstance(payload, dict):
            try:
                replayed = tuple(json.loads(config.get("s_json") or ""))
            except ValueError:
                replayed = None
            decoded = decode.decode_configuration(
                payload, c["question_id"], int(board["n_cards"]), int(board["n_zones"])
            )
            redecodes_clean = decoded.cause is None
            if replayed is not None:
                d = _card_distance(replayed, decoded.s)
        if "missing_source_map" in flags or board is None:
            category = CATEGORY_SOURCE_MAP
        elif "row_order_ambiguous" in flags:
            category = CATEGORY_AMBIGUOUS
        elif ({"invalid_state", "parse_error"} & flags) and redecodes_clean:
            category = CATEGORY_SKIPPED
        else:
            category = CATEGORY_UNEXPLAINED
        flag_text = ";".join(sorted(flags, key=lambda f: (order.get(f, len(order)), f)))
        out.append({
            "event_id": c.get("event_id", ""),
            "attempt_id": c.get("attempt_id", ""),
            "question_id": c.get("question_id", ""),
            "canonical_question_id": c.get("canonical_question_id", ""),
            "offering": c.get("offering", ""),
            "category": category,
            "evidence": f"flags={flag_text} d={d}",
            "d": str(d),
            "_d": d,
        })
    out.sort(key=lambda r: (r["offering"], r["question_id"], r["attempt_id"], r["event_id"]))
    return out


# ---------------------------------------------------------------------------
# Output text
# ---------------------------------------------------------------------------
def _csv_text(stamp: list[str], columns, rows: list[dict]) -> str:
    buf = io.StringIO()
    for line in stamp:
        buf.write(f"# {line}\n")
    writer = csv.DictWriter(buf, fieldnames=list(columns), extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def _xml_comment(stamp: list[str]) -> str:
    text = "; ".join(stamp).replace("--", "- -")
    return f"<!-- {text} -->\n"


def _cell(value: str) -> str:
    return f"<cell><p>{_xml_escape(value)}</p></cell>"


def items_ptx_cells(row: dict) -> list[str]:
    """The nine cells of one tab-items row, from its CSV row."""
    cells = []
    for col in ITEMS_PTX_COLUMNS:
        if col in ITEMS_COUNT_COLUMNS and row["suppressed"] == "1":
            cells.append(SUPPRESSED_MARK)
        else:
            cells.append(row[col])
    return cells


def validation_ptx_cells(row: dict) -> list[str]:
    """The six cells of one tab-validation-results row, from its CSV row."""
    if row["suppressed"] == "1":
        return [row["stratum"]] + [SUPPRESSED_MARK] * 5
    agreement = (
        f"{row['agreement_rate']} [{row['agreement_lo']}, {row['agreement_hi']}]"
        if row["agreement_rate"] else ""
    )
    ready = (
        f"{row['ready_rate']} [{row['ready_lo']}, {row['ready_hi']}]" if row["ready_rate"] else ""
    )
    principal = (
        f"{row['principal_flag']} ({row['principal_flag_share']})" if row["principal_flag"] else ""
    )
    return [row["stratum"], row["attempts"], row["eligible_anchors"], agreement, ready, principal]


def items_ptx(stamp: list[str], rows: list[dict]) -> str:
    # The manuscript's markup, sec-empirical-study.ptx:262-287, verbatim.
    lines = [
        _xml_comment(stamp).rstrip("\n"),
        '<table xml:id="tab-items">',
        "  <title>Items in the study corpus with design covariates and counts.</title>",
        '  <tabular halign="center">',
        '    <col width="16%" halign="left"/>',
        '    <col width="12%"/>',
        '    <col width="8%"/>',
        '    <col width="8%"/>',
        '    <col width="10%"/>',
        '    <col width="10%"/>',
        '    <col width="12%"/>',
        '    <col width="12%"/>',
        '    <col width="12%"/>',
        '    <row header="yes">',
        "      <cell>Item</cell>",
        "      <cell>Section</cell>",
        "      <cell><m>P</m></cell>",
        "      <cell><m>R</m></cell>",
        "      <cell><line>Decoy-</line><line>cards</line></cell>",
        "      <cell><line>Decoy-</line><line>zones</line></cell>",
        "      <cell>Students</cell>",
        "      <cell>Attempts</cell>",
        "      <cell>Checks</cell>",
        "    </row>",
    ]
    for row in rows:
        lines.append("    <row>" + "".join(_cell(v) for v in items_ptx_cells(row)) + "</row>")
    lines += ["  </tabular>", "</table>"]
    return "\n".join(lines) + "\n"


def validation_ptx(stamp: list[str], rows: list[dict]) -> str:
    # The manuscript's markup, sec-results.ptx:148-206, verbatim.
    lines = [
        _xml_comment(stamp).rstrip("\n"),
        '<table xml:id="tab-validation-results">',
        "  <title>Anchor validation and readiness results by stratum.</title>",
        '  <tabular halign="center">',
        '    <col width="20%" halign="left"/>',
        '    <col width="12%" />',
        '    <col width="12%" />',
        '    <col width="12%" />',
        '    <col width="12%" />',
        '    <col width="12%" />',
        '    <row header="yes">',
        "      <cell>Stratum</cell>",
        "      <cell>Attempts</cell>",
        "      <cell><line>Eligible-</line><line>anchors</line></cell>",
        "      <cell><line>Exact-</line><line>agreement</line></cell>",
        "      <cell>Ready</cell>",
        "      <cell><line>Principal-</line><line>flag</line></cell>",
        "    </row>",
    ]
    for row in rows:
        lines.append("    <row>" + "".join(_cell(v) for v in validation_ptx_cells(row)) + "</row>")
    lines += ["  </tabular>", "</table>"]
    return "\n".join(lines) + "\n"


def flow_ptx_cells(rows_of_one_quantity: list[dict]) -> list[str]:
    """The six cells of one tab-corpus-flow row: the label, then the five
    values with the mark wherever the value is blank, a J10 cell and a
    suppressed cell render alike, the manuscript's own "—"; the CSV tells
    them apart by ``suppressed`` and the note names the suppressed ones."""
    return [rows_of_one_quantity[0]["quantity"]] + [
        r["value"] if r["value"] != "" else SUPPRESSED_MARK for r in rows_of_one_quantity
    ]


def corpus_flow_ptx(stamp: list[str], rows: list[dict]) -> str:
    # The manuscript's markup, sec-empirical-study.ptx:186-250, verbatim.
    lines = [
        _xml_comment(stamp).rstrip("\n"),
        '<table xml:id="tab-corpus-flow">',
        "  <title>Corpus flow from raw records to primary analysis.</title>",
        '  <tabular halign="center">',
        '    <col width="16%" halign="right"/>',
        '    <col width="12%" />',
        '    <col width="14%" />',
        '    <col width="14%" />',
        '    <col width="14%" />',
        '    <col width="12%" />',
        '    <row header="yes">',
        "      <cell>Quantity</cell>",
        "      <cell>Raw</cell>",
        "      <cell><line>Author-</line><line>ized</line></cell>",
        "      <cell><line>Source-</line><line>aligned</line></cell>",
        "      <cell><line>Recon-</line><line>structed</line></cell>",
        "      <cell>Ready</cell>",
        "    </row>",
    ]
    width = len(FLOW_COLUMNS)
    for i in range(0, len(rows), width):
        lines.append(
            "    <row>" + "".join(_cell(v) for v in flow_ptx_cells(rows[i:i + width])) + "</row>"
        )
    lines += ["  </tabular>", "</table>"]
    return "\n".join(lines) + "\n"


def _guard_sentence(names: list[str]) -> str:
    base = "Per-offering counts appear only where every cell contains at least five students"
    if names:
        return base + "; suppressed (fewer than five students): " + ", ".join(names) + "."
    return base + "; no cell is suppressed."


def note_ptx(stamp: list[str], sentences: list[str]) -> str:
    body = " ".join(s for s in sentences if s)
    return _xml_comment(stamp) + f"<p>\n  {_xml_escape(body)}\n</p>\n"


def _flow_suppressed_names(table: str, rows: list[dict]) -> list[str]:
    """The guard sentence's names for one flow table (``"pooled"`` or a
    pseudonym): ``"<table> (every cell)"`` when every valued cell of the
    table is suppressed, else one ``"<table>: <quantity> at <column>"`` per
    suppressed cell (ruling Q12). A no-value cell (J10) is never named."""
    valued = [r for r in rows if (r["quantity"], r["column"]) not in FLOW_NO_VALUE]
    hidden = [r for r in valued if r["suppressed"] == "1"]
    if hidden and len(hidden) == len(valued):
        return [f"{table} (every cell)"]
    return [f"{table}: {r['quantity']} at {r['column']}" for r in hidden]


def flow_note_sentences(
    arc: Archive, n_alias: int, pooled: list[dict], by_offering: list[dict]
) -> list[str]:
    """The six sentences of ``tab-corpus-flow-note.ptx``, from the pooled
    flow rows: S1 the items convention (O-123's form; ``n_alias`` is the
    number ``tab-items-note.ptx`` prints), S2 the definitions (ruling R11
    (a); the traces clause follows the convention), S3 the governance rows
    count, the "[n] rows" slot of exclusion (1) in ``subsec-analysis-unit``;
    the account count is the manuscript's own fact and is not restated
    (Q7), S4 attempts and source alignment (why the Attempts row begins at
    Reconstructed: J10, Q2), S5 the step-4 / step-5 clause (rulings R9 (b)
    and J8) and S6 the guard sentence, naming the pooled table's suppressed
    cells then each offering's. Every number of S1-S5 is a pooled ledger
    cell under the table's own guard (the Copilot round on PR #240): a
    cell's rows_out prints only when the cell is shown, a loss only when the
    cells either side of it are (a loss is their difference, so printing it
    beside a hidden cell would give that cell away), else the table's own
    dash, with S6 naming the suppressed cells. The alias count is a count of
    identifiers, not a cell, and prints as ``tab-items-note.ptx`` prints
    it."""

    def row(quantity: str, step: int) -> dict:
        return arc.flow_by_key[("pooled", "", quantity, step)]

    def shown(quantity: str, step: int) -> bool:  # the table's guard, per cell
        return not suppressed({"n_students": _flow_count(row(quantity, step), "n_students")})

    def p(quantity: str, step: int) -> str:  # the pooled rows_out, or the dash
        if shown(quantity, step):
            return str(_flow_count(row(quantity, step), "rows_out"))
        return SUPPRESSED_MARK

    def lost(quantity: str, step: int) -> str:  # the pooled rows_lost, or the dash
        # The step-3 attempts loss has no cell before it (J10: the Attempts
        # row begins at Reconstructed); every other loss sits between two.
        before = (quantity == "attempts" and step == 3) or shown(quantity, step - 1)
        if before and shown(quantity, step):
            return str(_flow_count(row(quantity, step), "rows_lost"))
        return SUPPRESSED_MARK

    def plural(count: str, noun: str) -> str:
        return f"{count} {noun}" if count == "1" else f"{count} {noun}s"

    if arc.convention == "A":
        s1 = (
            f"Items are logged identifiers (convention A): {p('items_logged', 0)} logged "
            f"identifiers at Raw correspond to {p('items_canonical', 0)} canonical items after "
            f"the identifier alias, {p('items_logged', 6)} to {p('items_canonical', 6)} at Ready; "
            f"{n_alias} identifier(s) aliased to successor(s)."
        )
        traces = (
            f"item traces are student-identifier pairs ({p('item_traces_logged', 0)} at Raw; "
            f"{p('item_traces_canonical', 0)} student-item pairs after the alias)"
        )
    else:
        s1 = (
            f"Items are canonical items (convention B): {p('items_canonical', 0)} items at Raw, "
            f"logged under {p('items_logged', 0)} identifiers; {p('items_canonical', 6)} at Ready."
        )
        traces = (
            f"item traces are student-item pairs after the alias ({p('item_traces_canonical', 0)} "
            f"at Raw; {p('item_traces_logged', 0)} student-identifier pairs before it)"
        )
    s2 = (
        "Students are distinct platform accounts with at least one card-to-zone row (a drop, a "
        f"check or a reset); {traces}; events are every row of the imported exports; checks are "
        "the rows that carry a checked configuration."
    )
    s3 = (
        f"Governance removed {plural(lost('events', 1), 'row')} of instructor, researcher and "
        "test accounts before any analysis count (the Raw-to-Authorized difference of the Events "
        "row)."
    )
    s4 = (
        "Attempts are counted from reconstruction onward; source alignment removed "
        f"{plural(lost('attempts', 3), 'attempt')} on a logged identifier with no board after "
        "the alias (missing_source_map) before the Reconstructed count."
    )
    s5 = (
        f"Event parsing removed {plural(lost('events', 4), 'row')} ({lost('checks', 4)} checks) "
        f"whose payload did not parse; state validity removed {plural(lost('events', 5), 'row')} "
        f"({lost('checks', 5)} checks) whose payload did not decode to a unique, board-valid "
        "configuration or named an off-board card or slot; the attempts carrying them are "
        "retained (parse_error as limited, invalid_state counted at readiness) so no attempt "
        "is lost at either step. These losses are decode outcomes of each check's verbatim "
        "payload, re-decoded at read time; the cause counts of data-anchor-causes are the "
        "stored causes, which put a structural cause first."
    )
    names = _flow_suppressed_names("pooled", pooled)
    for pseud in arc.offerings:
        names += _flow_suppressed_names(pseud, [r for r in by_offering if r["offering"] == pseud])
    return [s1, s2, s3, s4, s5, _guard_sentence(names)]


# ---------------------------------------------------------------------------
# The build
# ---------------------------------------------------------------------------
def build_outputs(arc: Archive) -> tuple[dict[str, str], str]:
    """Every display as text, keyed by file name, plus the internal
    per-check classification (``validation/checks_classified.csv``)."""
    base = [
        f"run_id: {arc.run_id}",
        f"manifest_fingerprint: {arc.fingerprint}",
        f"generator: paper_tables.py {GENERATOR_VERSION}",
    ]
    with_conv = base + [f"items_convention: {arc.convention}"]
    out: dict[str, str] = {}

    # -- tab-items -----------------------------------------------------------
    items = item_rows(arc)
    item_keys, boardless = arc.items()
    items_defs = (
        "item = logged identifier (convention A) or canonical identifier (B) with a board; "
        + DEFINITIONS["attempts"].replace("n_students", "students")
        + "; checks = all checks rows of the item; eligible_anchors = checks with eligible = 1; "
        "correct_checks_feedback = checks.feedback = Correct; correct_checks_scored = "
        "check_scores.all_or_nothing = 1; source_version = boards.source_hash; feedback_policy = "
        "the set of attempt_metrics.feedback_policy values; " + DEFINITIONS["guard"]
    )
    out["tab-items.csv"] = _csv_text(with_conv + [f"definitions: {items_defs}"], ITEMS_COLUMNS, items)
    out["tab-items.ptx"] = items_ptx(with_conv, items)

    # The convention sentence counts the same identifiers under A and B
    # (O-123's form): the logged identifiers with a board, the canonical
    # items they resolve to, and the aliases among them.
    with_board, _ = arc.logged_with_board()
    n_logged = len(with_board)
    n_canonical = len({arc.board_for(q)["question_id"] for q in with_board})
    n_alias = sum(1 for q in with_board if q not in arc.boards)
    if arc.convention == "A":
        convention_sentence = (
            f"{n_logged} logged identifiers correspond to {n_canonical} canonical items; "
            f"{n_alias} identifier(s) aliased to successor(s); no rows are lost by the alias."
        )
    else:
        convention_sentence = f"{n_canonical} items, logged under {n_logged} identifiers."
    versions = sorted({arc.boards[b]["source_hash"] for _, b, _ in item_keys})
    out["tab-items-note.ptx"] = note_ptx(with_conv, [
        convention_sentence,
        f"Logged identifiers without a board: {len(boardless)}.",
        "Source versions: " + (", ".join(versions) if versions else "none") + ".",
        _guard_sentence([r["item"] for r in items if r["suppressed"] == "1"]),
    ])

    # -- tab-validation-results -----------------------------------------------
    validation_defs = "; ".join(
        DEFINITIONS[k] for k in ("attempts", "agreement", "ready", "principal", "guard")
    )
    strata: list[tuple[str, list[dict], list[dict]]] = [
        ("All offerings", arc.attempts, arc.checks),
    ]
    for term in arc.terms:
        offerings = {p for p, t in arc.term_of.items() if t == term}
        strata.append((
            f"{term} ({_plural(len(offerings), 'offering')})",
            [a for a in arc.attempts if a["offering"] in offerings],
            [c for c in arc.checks if c["offering"] in offerings],
        ))
    unexposed = {p for p, e in arc.exposed.items() if not e}
    exposed = {p for p, e in arc.exposed.items() if e}
    strata.append((
        f"Offerings not used in development ({len(unexposed)})",
        [a for a in arc.attempts if a["offering"] in unexposed],
        [c for c in arc.checks if c["offering"] in unexposed],
    ))
    strata.append((
        f"Development offering ({len(exposed)})",
        [a for a in arc.attempts if a["offering"] in exposed],
        [c for c in arc.checks if c["offering"] in exposed],
    ))
    validation_rows = [
        {"stratum": label, **validation_fields(measures(attempts, checks))}
        for label, attempts, checks in strata
    ]
    out["tab-validation-results.csv"] = _csv_text(
        base + [f"definitions: {validation_defs}"], VALIDATION_COLUMNS, validation_rows
    )
    out["tab-validation-results.ptx"] = validation_ptx(base, validation_rows)

    # -- by item, by offering, the figure data -----------------------------------
    by_item_rows, fig_unsorted = [], []
    for raw in item_rows_unguarded(arc):
        m = raw["_measures"]
        by_item_rows.append({
            "item": raw["item"], "canonical_question_id": raw["canonical_question_id"],
            "is_alias": raw["is_alias"], **validation_fields(m),
        })
        fig_unsorted.append((m, {"item": raw["item"], "section": raw["section"], **fig_fields(m)}))
    out["tab-validation-by-item.csv"] = _csv_text(
        with_conv + [f"definitions: {validation_defs}"], BY_ITEM_COLUMNS, by_item_rows
    )

    offering_rows = []
    for pseud in arc.offerings:
        m = measures(
            [a for a in arc.attempts if a["offering"] == pseud],
            [c for c in arc.checks if c["offering"] == pseud],
        )
        offering_rows.append({"offering": pseud, **validation_fields(m)})
    out["data-validation-by-offering.csv"] = _csv_text(
        base + [f"definitions: {validation_defs}"], BY_OFFERING_COLUMNS, offering_rows
    )
    out["tab-validation-results-note.ptx"] = note_ptx(base, [
        _guard_sentence(
            [r["stratum"] for r in validation_rows if r["suppressed"] == "1"]
            + [r["offering"] for r in offering_rows if r["suppressed"] == "1"]
        ),
    ])

    # Items sorted by eligible-anchor count descending (ruling R4, the spec at
    # sec-results.ptx:218-226), ties by item; suppressed rows carry no count
    # and follow, by item, so their position reveals nothing.
    shown = sorted(
        (r for m, r in fig_unsorted if r["suppressed"] == "0"),
        key=lambda r: (-int(r["eligible_anchors"]), r["item"]),
    )
    hidden = sorted((r for m, r in fig_unsorted if r["suppressed"] == "1"), key=lambda r: r["item"])
    out["fig-validation-results.csv"] = _csv_text(
        with_conv + [
            "definitions: one row per item, sorted by eligible-anchor count descending (ties by "
            "item), suppressed rows last by item; " + DEFINITIONS["agreement"] + "; "
            + DEFINITIONS["attempts"] + "; n_ready / n_limited / n_exclude = attempts by "
            "readiness; " + DEFINITIONS["guard"]
        ],
        FIG_COLUMNS, shown + hidden,
    )

    # -- the pooled cohort is one more cell under the guard ----------------------
    # Computed once; every pooled display below reads this one verdict (PR #236
    # Copilot review, 2026-10-19).
    pooled = measures(arc.attempts, arc.checks)
    pooled_hidden = suppressed(pooled)
    pooled_mark = "1" if pooled_hidden else "0"

    def guarded(value: object) -> str:
        return "" if pooled_hidden else str(value)

    # -- anchor causes: seven rows, and the prose's six -------------------------
    n_all_checks = sum(1 for c in arc.checks if c.get("attempt_id"))
    n_eligible = sum(1 for c in arc.checks if c.get("eligible") == "1")
    n_never = sum(1 for c in arc.checks if c.get("eligible", "") == "")
    causes = Counter(c.get("anchor_cause", "") for c in arc.checks if c.get("eligible") == "0")
    universe = [
        f"n_all_checks: {guarded(n_all_checks)}", f"n_eligible: {guarded(n_eligible)}",
        f"n_never_reconstructed: {guarded(n_never)}",
        (
            "definitions: n_all_checks = checks rows with a non-empty attempt_id; n_eligible = "
            "eligible = 1; n_never_reconstructed = eligible empty; the cause rows partition the "
            "eligible = 0 checks (qc.ANCHOR_CAUSE_ORDER, precedence order); share_of_all_checks = "
            "n_checks / n_all_checks to 4 decimals; " + DEFINITIONS["pooled_guard"]
        ),
    ]

    def share(n: int) -> str:
        return _fmt4(n / n_all_checks) if n_all_checks else ""

    out["data-anchor-causes-seven.csv"] = _csv_text(
        base + universe, CAUSES_SEVEN_COLUMNS,
        [{"cause": cause, "n_checks": guarded(causes.get(cause, 0)),
          "share_of_all_checks": guarded(share(causes.get(cause, 0))), "suppressed": pooled_mark}
         for cause in qc.ANCHOR_CAUSE_ORDER],
    )
    six_rows = []
    for key, components, label in SIX_CAUSES:
        n = sum(causes.get(c, 0) for c in components)
        six_rows.append({"cause": key, "components": ";".join(components), "label": label,
                         "n_checks": guarded(n), "share_of_all_checks": guarded(share(n)),
                         "suppressed": pooled_mark})
    out["data-anchor-causes-six.csv"] = _csv_text(
        base + universe[:3] + [
            (
                "definitions: the Results prose's six causes (sec-results.ptx:96-102, labels "
                "verbatim); unknown_card_or_zone sums unknown_card and unknown_zone; "
                "share_of_all_checks = n_checks / n_all_checks to 4 decimals; "
                + DEFINITIONS["pooled_guard"]
            )
        ],
        CAUSES_SIX_COLUMNS, six_rows,
    )

    # -- mismatch distance and the diagnostics ----------------------------------
    classified = classify_disagreements(arc)
    computed = [r["_d"] for r in classified if r["_d"] != NOT_COMPUTED]
    distance_defs = (
        "definitions: disagreeing = eligible = 1 and matches_input = 0; d = card positions on "
        "which configurations.s_json (same event_id) and the decoded configuration_json differ "
        "(decode.decode_configuration with the board of canonical_question_id); not computed "
        "when the item has no board; median_d and share_d_le_1 over the computed d; "
        + DEFINITIONS["pooled_guard"]
    )
    out["data-mismatch-distance.csv"] = _csv_text(
        base + [distance_defs], DISTANCE_COLUMNS,
        [{
            "n_disagreeing": guarded(len(classified)),
            "n_computed": guarded(len(computed)),
            "n_not_computed": guarded(len(classified) - len(computed)),
            "median_d": guarded(f"{statistics.median(computed):g}" if computed else ""),
            "share_d_le_1": guarded(
                _fmt4(sum(1 for d in computed if d <= 1) / len(computed)) if computed else ""
            ),
            "suppressed": pooled_mark,
        }],
    )
    hist = Counter(computed)
    # A suppressed histogram is one blank row: the d values themselves are
    # counts' keys, so they are withheld with the counts.
    hist_rows = (
        [{"d": "", "n": "", "suppressed": "1"}] if pooled_hidden
        else [{"d": str(d), "n": str(hist[d]), "suppressed": "0"} for d in sorted(hist)]
    )
    out["data-mismatch-distance-hist.csv"] = _csv_text(
        base + [distance_defs + "; a suppressed histogram is one blank row (the d keys are "
                "withheld with their counts)"],
        DISTANCE_HIST_COLUMNS, hist_rows,
    )

    def diagnostics_row(scope: str, offering: str, rows: list[dict], guard: dict | None) -> dict:
        counts = Counter(r["category"] for r in rows)
        row = {"scope": scope, "offering": offering}
        if guard is not None and suppressed(guard):
            row.update({"n_disagreeing": ""}, **{c: "" for c in CATEGORY_ORDER}, suppressed="1")
        else:
            row["n_disagreeing"] = str(len(rows))
            row.update({c: str(counts.get(c, 0)) for c in CATEGORY_ORDER})
            row["suppressed"] = "0"
        return row

    diagnostics = [diagnostics_row("pooled", "", classified, pooled)]
    for pseud in arc.offerings:
        m = measures([a for a in arc.attempts if a["offering"] == pseud], [])
        diagnostics.append(diagnostics_row(
            "offering", pseud, [r for r in classified if r["offering"] == pseud], m,
        ))
    out["data-anchor-diagnostics.csv"] = _csv_text(
        base + [
            "definitions: one diagnostic category per disagreeing eligible anchor, first "
            "applicable: source-map problem (the attempt holds missing_source_map, or the item "
            "has no board); ambiguous ordering (row_order_ambiguous); skipped event "
            "(invalid_state or parse_error, and the check's own payload re-decodes cleanly); "
            "else unexplained disagreement (sec-reconstruction.ptx:265-268). Attribution: an "
            "attempt-level flag attributes to every disagreeing anchor of that attempt, exact "
            "for source-map problem, a conservative over-attribution for the other two. "
            "By-offering rows: " + DEFINITIONS["guard"] + "; the pooled row: "
            + DEFINITIONS["pooled_guard"]
        ],
        DIAGNOSTICS_COLUMNS, diagnostics,
    )

    # -- tab-corpus-flow (row 3.7; ruling R8 ii, from the ledger's flow export) ---------
    flow_defs = (
        "definitions: one row per cell of the manuscript's table; column = Raw (ledger step 0, "
        "raw), Authorized (step 1, governance), Source-aligned (step 3, source_alignment; step 2 "
        "question_type folded in), Reconstructed (step 5, state_validity; step 4 event_parsing "
        "folded in), Ready (step 6, readiness); value = the flow's rows_out at that step; "
        "attempts have no value before Reconstructed (blank, suppressed = 0; ruling J10 and the "
        "manuscript's cells); items and item traces are logged identifiers under convention A, "
        "canonical identifiers under B; n_students = the ledger's n_students of the cell; "
        + DEFINITIONS["pooled_guard"]
    )
    pooled_flow = flow_rows(arc)
    out["tab-corpus-flow.csv"] = _csv_text(with_conv + [flow_defs], FLOW_CSV_COLUMNS, pooled_flow)
    out["tab-corpus-flow.ptx"] = corpus_flow_ptx(with_conv, pooled_flow)
    offering_flow = [{"offering": p, **cell} for p in arc.offerings for cell in flow_rows(arc, p)]
    out["data-corpus-flow-by-offering.csv"] = _csv_text(
        with_conv + [flow_defs + "; offering = the pseudonym of the ledger's import_sha256 key"],
        FLOW_BY_OFFERING_COLUMNS, offering_flow,
    )
    out["tab-corpus-flow-note.ptx"] = note_ptx(
        with_conv, flow_note_sentences(arc, n_alias, pooled_flow, offering_flow)
    )

    # -- the flow-independent sample numbers (row 3.7; ruling R8 (ii)) ------------
    eligible_attempts = {c["attempt_id"] for c in arc.checks if c.get("eligible") == "1"}
    sample = [
        ("n_students", pooled["n_students"]),
        ("n_attempts", len(arc.attempts)),
        ("n_checks", len(arc.checks)),
        (f"checks_{qc.CAUSE_NO_MOVE_LOGGING}", causes.get(qc.CAUSE_NO_MOVE_LOGGING, 0)),
        (f"checks_{qc.CAUSE_RESUMED_ATTEMPT}", causes.get(qc.CAUSE_RESUMED_ATTEMPT, 0)),
        ("attempts_with_eligible_anchor", len(eligible_attempts)),
        ("rq3_subset_attempts", len(rq3_admitted(arc.attempts))),
    ]
    out["data-sample-reporting.csv"] = _csv_text(
        base + [
            (
                "definitions: pooled only; n_students = distinct student_id in attempt_metrics; "
                "n_attempts = attempt_metrics rows; n_checks = checks rows; checks_<cause> = "
                "checks stored with that structural cause; attempts_with_eligible_anchor = "
                "attempts with at least one eligible = 1 check; " + DEFINITIONS["rq3"] + "; "
                + DEFINITIONS["pooled_guard"]
            )
        ],
        SAMPLE_COLUMNS,
        [{"quantity": q, "value": guarded(v), "suppressed": pooled_mark} for q, v in sample],
    )

    classified_text = _csv_text(
        base + [
            "internal working file (procedure §3.7): the per-check classification behind "
            "data-anchor-diagnostics.csv; never under displays/, never committed"
        ],
        CLASSIFIED_COLUMNS, classified,
    )
    return out, classified_text


def _write(path: Path, text: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        fh.write(text)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="paper_tables.py", description=__doc__.split("\n\n")[0],
    )
    parser.add_argument("--archive", type=Path, required=True,
                        help="the run directory <archive-root>/<run-id> (manifest.yaml + exports/)")
    parser.add_argument("--out", type=Path, default=None,
                        help="output directory (default: <archive>/displays/)")
    parser.add_argument("--items-convention", choices=CONVENTIONS, default="A",
                        help="A: one row per logged identifier with a board (default; O-123); "
                             "B: one row per canonical identifier, counts pooled over aliases")
    parser.add_argument("--allow-anywhere", action="store_true",
                        help="lift the output guard on --out and on <archive>/validation/ "
                             "(rehearsals only; never the frozen run)")
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    archive = Path(args.archive).resolve()
    out = Path(args.out).resolve() if args.out is not None else archive / "displays"
    validation = archive / "validation"
    arc = Archive(archive, args.items_convention)  # refusals 1-5 and 7
    # Refusal 6 (audit_sample.die exits 2 itself): both destinations, before
    # either directory exists, the classification under validation/ carries
    # event- and attempt-level identifiers, so it is guarded like --out.
    audit_sample.guard_output_dir(out, args.allow_anywhere)
    audit_sample.guard_output_dir(validation, args.allow_anywhere)
    outputs, classified = build_outputs(arc)
    out.mkdir(parents=True, exist_ok=True)
    for name in sorted(outputs):
        _write(out / name, outputs[name])
    validation.mkdir(parents=True, exist_ok=True)
    _write(validation / "checks_classified.csv", classified)
    print(f"paper_tables.py {GENERATOR_VERSION}: run {arc.run_id}, fingerprint "
          f"{arc.fingerprint[:12]}…, convention {arc.convention}: {len(outputs)} files under {out}; "
          f"classification under {validation}")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parse_args(argv)
        return run(args)
    except Refusal as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except SystemExit as exc:  # audit_sample.die (the guard) and argparse
        code = exc.code
        return code if isinstance(code, int) else 2


if __name__ == "__main__":
    sys.exit(main())
