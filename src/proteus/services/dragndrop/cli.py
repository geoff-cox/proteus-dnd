"""
CLI registration and handlers for the DnD service (plan §2.5).

`register_cli` attaches the DnD stage commands to a subparsers action,
normally the `proteus dnd` namespace. The service is new, so there are no
deprecated root aliases (item A2.3 is SA-only history).

Commands land with their checklist items: `derive` (B1.3); `sync-boards`
and `validate` (B2.2/B2.3/B2.4); reconstruct/score/analyze/export follow in
B3; `alias` (dnd/0014, the P4 identifier alias), `regime` (dnd/0015, the
logging regimes), `roles` (dnd/0016, the staff-account rulings applied at
derive, no command prints an account id) and the Paper 1 frozen-run
tooling (`sensitivity`, `contrast`) came with their task briefs.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from proteus.core import db
from proteus.services.dragndrop import (
    account_roles as roles_mod,
)
from proteus.services.dragndrop import (
    aliases as aliases_mod,
)
from proteus.services.dragndrop import (
    board,
    validate,
)
from proteus.services.dragndrop import (
    contrast as contrast_mod,
)
from proteus.services.dragndrop import (
    derive as derive_mod,
)
from proteus.services.dragndrop import (
    export as export_mod,
)
from proteus.services.dragndrop import (
    logging_regimes as regimes_mod,
)
from proteus.services.dragndrop import (
    process as process_mod,
)
from proteus.services.dragndrop import (
    reconstruct as reconstruct_mod,
)
from proteus.services.dragndrop import (
    scoring as scoring_mod,
)
from proteus.services.dragndrop import (
    sensitivity as sensitivity_mod,
)

log = logging.getLogger(__name__)


def register_cli(sub: argparse._SubParsersAction) -> None:
    """Attach the DnD stage commands to `sub` (the `proteus dnd` namespace)."""
    p_derive = sub.add_parser(
        "derive",
        help=(
            "Run Stage 1: materialize typed dnd_events from ingested "
            "dragNdrop*/page events. Rows of an account ruled excluded "
            "(`dnd roles`, dnd/0016) are withheld and counted; load the "
            "rulings before the first derive."
        ),
    )
    p_derive.add_argument(
        "--question",
        help=(
            "If given, only derive events for this question_id (page rows "
            "carry the page's name, so they are excluded by this filter)."
        ),
    )
    p_derive.add_argument(
        "--import-id",
        type=int,
        help="If given, only derive events belonging to this import.",
    )
    p_derive.set_defaults(func=cmd_derive)

    p_boards = sub.add_parser(
        "sync-boards",
        help=(
            "(Re)build board specs (cards, zones, key, decoys) from already-"
            "synced question source, re-runs the DnD source handler over "
            "questions with source_xml."
        ),
    )
    p_boards.add_argument(
        "--question",
        help="If given, only rebuild the board for this question_id.",
    )
    p_boards.set_defaults(func=cmd_sync_boards)

    p_validate = sub.add_parser(
        "validate",
        help=(
            "Cross-validate boards against the data: the key inferred from "
            "Correct checks must match the source-derived key (B2.3), and "
            "every drag#/drop# label seen must exist on its board (B2.4). "
            "Exits 1 if anything fails."
        ),
    )
    p_validate.add_argument(
        "--question",
        help="If given, only validate this question_id.",
    )
    p_validate.set_defaults(func=cmd_validate)

    p_reconstruct = sub.add_parser(
        "reconstruct",
        help=(
            "Replay moves from the initial board into per-action "
            "configurations, segment them into attempts, and cross-check each "
            "check against its logged Input (B3.1/B3.2). Re-run `dnd score` / "
            "`dnd analyze` afterwards."
        ),
    )
    p_reconstruct.add_argument(
        "--question",
        help="If given, only reconstruct this question_id.",
    )
    p_reconstruct.add_argument(
        "--gap-seconds",
        type=float,
        default=reconstruct_mod.SegmentationConfig().gap_seconds,
        help=(
            "Idle gap (seconds) that starts a new attempt "
            "(default %(default)gs; plan §7 Open Question #4)."
        ),
    )
    p_reconstruct.add_argument(
        "--no-split-session",
        action="store_true",
        help="Do not start a new attempt on a session change.",
    )
    p_reconstruct.add_argument(
        "--no-split-page",
        action="store_true",
        help="Do not start a new attempt when a page view intervenes.",
    )
    p_reconstruct.add_argument(
        "--split-on-reset",
        action="store_true",
        help="Treat every reset as an attempt boundary (off by default).",
    )
    p_reconstruct.add_argument(
        "--feedback-policy",
        choices=list(reconstruct_mod.policy.VALID_POLICIES),
        default=reconstruct_mod.policy.LEGACY,
        help=(
            "Platform feedback-policy era for the per-check disclosure "
            "classification (FP-3). Default %(default)s; pass `layered` for "
            "data collected under the mid-2026 gated policy."
        ),
    )
    p_reconstruct.add_argument(
        "--seed-resumed",
        action="store_true",
        help=(
            "D3.2 knob 10 (seeded replay): start a resumed attempt at the "
            "prior attempt's last decodable checked configuration instead of "
            "s_0 (s_0 when the prior attempt has none) and compare its checks; "
            "off by default, the primary stores them not comparable "
            "(resumed_attempt)."
        ),
    )
    p_reconstruct.set_defaults(func=cmd_reconstruct)

    p_score = sub.add_parser(
        "score",
        help=(
            "Score every checked configuration against its board's key "
            "(σ, distance, per-card, baseline-corrected g, label-free σ̃, "
            "decoy leaks; all variants side by side, B3.3)."
        ),
    )
    p_score.add_argument(
        "--question",
        help="If given, only score this question_id.",
    )
    p_score.set_defaults(func=cmd_score)

    p_analyze = sub.add_parser(
        "analyze",
        help=(
            "Compute per-attempt process metrics (move/takeback/reset/check "
            "tallies, time to first check, convergence curve, grader-as-oracle "
            "probe; B3.5)."
        ),
    )
    p_analyze.add_argument(
        "--question",
        help="If given, only analyze this question_id's attempts.",
    )
    p_analyze.set_defaults(func=cmd_analyze)

    p_export = sub.add_parser(
        "export",
        help="Dump a DnD stage view to CSV for review or sharing (B3.7).",
    )
    p_export.add_argument(
        "dataset",
        choices=export_mod.dataset_names(),
        # Generated from the export registry, never hand-listed: this help
        # named four of the eight datasets for as long as the other four had
        # been shippable. Each dataset carries its own one-clause summary.
        help="Which view to export. " + "; ".join(
            f"'{name}': {spec.summary}"
            for name, spec in export_mod.DATASETS.items()
        ) + ".",
    )
    p_export.add_argument(
        "-o", "--output",
        type=Path,
        help="File to write to. Defaults to stdout.",
    )
    p_export.add_argument(
        "--question",
        help="Filter to one question_id.",
    )
    p_export.set_defaults(func=cmd_export)

    p_alias = sub.add_parser(
        "alias",
        help=(
            "Question-identifier aliases at board binding (dnd/0014): a retired "
            "identifier resolves to its successor's board; raw rows are never "
            "rewritten. Subcommands: add, remove, list, import."
        ),
    )
    alias_sub = p_alias.add_subparsers(dest="alias_command", metavar="<command>")
    p_alias_add = alias_sub.add_parser(
        "add", help="Register one alias: <alias> resolves to <canonical>."
    )
    p_alias_add.add_argument(
        "alias_question_id",
        help="The retired identifier, spelled as logged (<book_xml_id>__<xml_id>).",
    )
    p_alias_add.add_argument(
        "canonical_question_id",
        help="Its successor: the identifier whose board it binds to.",
    )
    p_alias_add.add_argument("--reason", default=None, help="Why (free text).")
    p_alias_add.add_argument(
        "--source-commit", default=None,
        help="The rename commit in the book's source.",
    )
    p_alias_add.add_argument(
        "--recorded-on", default=None, help="ISO date (default: today, UTC)."
    )
    p_alias_add.add_argument(
        "--replace-board", action="store_true",
        help=(
            "Retire the alias identifier's own board (and clear its results) "
            "instead of refusing the ambiguity."
        ),
    )
    p_alias_add.set_defaults(func=cmd_alias_add)
    p_alias_remove = alias_sub.add_parser(
        "remove",
        help=(
            "Remove one alias. Results already computed under it are left as "
            "they are; re-run reconstruct / score / analyze."
        ),
    )
    p_alias_remove.add_argument("alias_question_id")
    p_alias_remove.set_defaults(func=cmd_alias_remove)
    p_alias_list = alias_sub.add_parser(
        "list", help="List the registered aliases and the alias set's sha256."
    )
    p_alias_list.set_defaults(func=cmd_alias_list)
    p_alias_import = alias_sub.add_parser(
        "import",
        help=(
            "Load an alias file: CSV with the header alias_question_id, "
            "canonical_question_id, reason, source_commit, recorded_on; '#' "
            "comment lines allowed. Idempotent; refuses chains, self-aliases and "
            "alias identifiers that have their own board; prints the set's sha256."
        ),
    )
    p_alias_import.add_argument("file", type=Path, help="The alias file.")
    p_alias_import.add_argument(
        "--replace-board", action="store_true",
        help="Retire the board of any alias identifier that has one.",
    )
    p_alias_import.set_defaults(func=cmd_alias_import)
    p_alias.set_defaults(func=_cmd_alias_no_subcommand)

    p_regime = sub.add_parser(
        "regime",
        help=(
            "Logging regimes (dnd/0015): the calendar date from which an "
            "export's component logged moves; a check logged before it is "
            "stored not comparable (no_move_logging). Keyed by the export's "
            "sha256. Subcommands: import, list, remove."
        ),
    )
    regime_sub = p_regime.add_subparsers(dest="regime_command", metavar="<command>")
    p_regime_import = regime_sub.add_parser(
        "import",
        help=(
            "Load a regime file: CSV with the header file_sha256, pseudonym, "
            "move_logging_from, reason, source_memo, recorded_on; '#' comment "
            "lines allowed. Idempotent; refuses a sha256 no import carries "
            "(reports the count of known imports, never a hash); prints the "
            "set's sha256."
        ),
    )
    p_regime_import.add_argument("file", type=Path, help="The regime file.")
    p_regime_import.set_defaults(func=cmd_regime_import)
    p_regime_list = regime_sub.add_parser(
        "list", help="List the recorded regimes and the regime set's sha256."
    )
    p_regime_list.set_defaults(func=cmd_regime_list)
    p_regime_remove = regime_sub.add_parser(
        "remove",
        help=(
            "Remove one regime row by its export's sha256. Results already "
            "computed under it are left as they are; re-run reconstruct."
        ),
    )
    p_regime_remove.add_argument("file_sha256")
    p_regime_remove.set_defaults(func=cmd_regime_remove)
    p_regime.set_defaults(func=_cmd_regime_no_subcommand)

    p_roles = sub.add_parser(
        "roles",
        help=(
            "Account roles (dnd/0016): the staff-account rulings. Rows of an "
            "account ruled excluded are withheld at derive and never reach "
            "dnd_events (events keeps every row); load the rulings file before "
            "the first `ingest --derive`. Subcommands: import, list, remove. "
            "No account id is ever printed."
        ),
    )
    roles_sub = p_roles.add_subparsers(dest="roles_command", metavar="<command>")
    p_roles_import = roles_sub.add_parser(
        "import",
        help=(
            "Load a rulings file: CSV with the header pseudonym, "
            "anon_student_id, raw_rows, role, exclude, ruled_by, ruled_on, "
            "note; '#' comment lines allowed; `note` is never stored. "
            "Idempotent; refuses an id already ruled otherwise and an excluded "
            "id that already has derived rows (counts only); prints the counts "
            "and the set's sha256, never an id."
        ),
    )
    p_roles_import.add_argument("file", type=Path, help="The rulings file.")
    p_roles_import.set_defaults(func=cmd_roles_import)
    p_roles_list = roles_sub.add_parser(
        "list",
        help=(
            "List the rulings: an ordinal, the offering pseudonym, role, "
            "exclude flag, raw_rows claim, ruling date and author, file name, "
            "and the set's sha256. No id."
        ),
    )
    p_roles_list.set_defaults(func=cmd_roles_list)
    p_roles_remove = roles_sub.add_parser(
        "remove",
        help=(
            "Remove one ruling by its account id (the argument is never "
            "echoed). Rows already withheld are not restored; re-run "
            "`dnd derive`, which inserts them."
        ),
    )
    p_roles_remove.add_argument(
        "anon_student_id", help="The account id, exactly as the export logs it."
    )
    p_roles_remove.set_defaults(func=cmd_roles_remove)
    p_roles.set_defaults(func=_cmd_roles_no_subcommand)

    # --- The Paper 1 frozen-run tooling (cc-task-frozen-run-tooling) -----
    p_sens = sub.add_parser(
        "sensitivity",
        help=(
            "RQ4 sensitivity harness (E-01): the frozen D3.2 primary + ten "
            "alternatives, each on a scratch copy, with deltas in the order "
            "of tab-sensitivity-results; and the bracket-evidence report."
        ),
    )
    sens_sub = p_sens.add_subparsers(required=True, dest="sensitivity_command")
    p_sens_run = sens_sub.add_parser(
        "run",
        help=(
            "Clone the primary database once per configuration, re-run the "
            "stages the knob changes, export, and write deltas.csv."
        ),
    )
    p_sens_run.add_argument(
        "--primary-db", type=Path, required=True,
        help=(
            "The primary-configuration database (reconstruct/score/analyze "
            "already run with the frozen defaults). Never the canonical "
            "database unless --allow-canonical."
        ),
    )
    p_sens_run.add_argument(
        "--out", type=Path, required=True,
        help="Output directory: <out>/<knob>/ per configuration + deltas.csv.",
    )
    p_sens_run.add_argument(
        "--knob", action="append", dest="knobs",
        choices=[k.key for k in sensitivity_mod.KNOB_TABLE],
        help=(
            "Run only this frozen alternative (repeatable). Default: all ten. "
            "Anything outside the table is refused here; the Python API labels "
            "it post_hoc."
        ),
    )
    p_sens_run.add_argument(
        "--estimate", action="store_true",
        help=(
            "Fill the primary-RQ3 column (difference in group medians of "
            "n_effective_moves_pre). Off by default: the contrast runner's "
            "counts-only posture."
        ),
    )
    p_sens_run.add_argument(
        "--allow-canonical", action="store_true",
        help="Permit the canonical data/proteus.db as the primary (refused otherwise).",
    )
    p_sens_run.set_defaults(func=cmd_sensitivity_run)
    p_sens_ev = sens_sub.add_parser(
        "evidence",
        help=(
            "Gap / session / page-view / reset distributions that justify the "
            "D3.2 brackets (counts only), read from --db."
        ),
    )
    p_sens_ev.add_argument(
        "-o", "--output", type=Path, help="Write the Markdown report here (default: stdout)."
    )
    p_sens_ev.add_argument(
        "--allow-canonical", action="store_true",
        help="Permit the canonical data/proteus.db (refused otherwise).",
    )
    p_sens_ev.set_defaults(func=cmd_sensitivity_evidence)

    p_contrast = sub.add_parser(
        "contrast",
        help=(
            "RQ3 endpoint-equivalent process contrast (E-15): counts-only by "
            "default (group sizes, items with >= 5 per arm, reporting level); "
            "--estimate adds the three blocks with student-clustered bootstrap "
            "intervals and the representative attempts."
        ),
    )
    p_contrast.add_argument(
        "--out", type=Path, required=True, help="Output directory for the result files."
    )
    p_contrast.add_argument(
        "--estimate", action="store_true",
        help="Compute the estimates (needs --seed); default is counts only.",
    )
    p_contrast.add_argument("--seed", type=int, help="Bootstrap seed (required with --estimate).")
    p_contrast.add_argument(
        "--B", type=int, default=contrast_mod.DEFAULT_B, dest="B",
        help="Bootstrap replicates (default %(default)s).",
    )
    p_contrast.add_argument(
        "--include-limited", action="store_true",
        help=(
            "D3.2 knob 7: admit limited attempts too; each contributes a "
            "feature only when its flags permit it (the QC rulebook's matrix)."
        ),
    )
    p_contrast.add_argument(
        "--exclude-flag", action="append", dest="exclude_flags", default=[],
        choices=list(reconstruct_mod.qc.FLAG_ORDER),
        help="D3.2 knob 5 and kin: drop attempts carrying this flag (repeatable).",
    )
    p_contrast.add_argument(
        "--allow-canonical", action="store_true",
        help="Permit the canonical data/proteus.db (refused otherwise).",
    )
    p_contrast.set_defaults(func=cmd_contrast)


def cmd_derive(args: argparse.Namespace) -> int:
    if not args.db.exists():
        print(
            f"No database at {args.db}; run 'init' first.",
            file=sys.stderr,
        )
        return 1

    conn = db.connect(args.db)
    try:
        try:
            result = derive_mod.derive_events(
                conn,
                import_id=args.import_id,
                question_id=args.question,
            )
        except roles_mod.RoleError as e:
            # R7: an excluded account already has derived rows (the rulings
            # were loaded after a derive). Counts, roles and pseudonyms only.
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
    finally:
        conn.close()

    print(
        f"Derive complete:\n"
        f"  seen:      {result.seen}  (dragNdrop/page events)\n"
        f"  inserted:  {result.inserted}\n"
        f"  skipped:   {result.skipped}  (already in database)\n"
        f"  malformed: {result.malformed}  (written with parse_ok=0)\n"
        f"  excluded:  {result.excluded}  (rows of excluded accounts withheld; see dnd roles)\n"
        f"  run:       #{result.run_id}  (pipeline_runs provenance)"
    )
    return 0


def cmd_sync_boards(args: argparse.Namespace) -> int:
    if not args.db.exists():
        print(
            f"No database at {args.db}; run 'init' first.",
            file=sys.stderr,
        )
        return 1

    conn = db.connect(args.db)
    try:
        sql = "SELECT * FROM questions WHERE source_xml IS NOT NULL"
        params: list[object] = []
        if args.question:
            sql += " AND question_id = ?"
            params.append(args.question)

        counts: dict[str, int] = {}
        for row in conn.execute(sql, params).fetchall():
            try:
                status = board.sync_board_for_question(conn, row)
            except ValueError as e:
                log.warning("sync-boards: %s", e)
                status = "error"
            counts[status] = counts.get(status, 0) + 1
        conn.commit()
        n_boards = conn.execute(
            "SELECT count(*) AS n FROM dnd_boards"
        ).fetchone()["n"]
    finally:
        conn.close()

    bits = ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "0"
    print(
        f"Board sync complete:\n"
        f"  questions with source: {sum(counts.values())} ({bits})\n"
        f"  boards registered:     {n_boards}"
    )
    return 1 if counts.get("error") else 0


def cmd_validate(args: argparse.Namespace) -> int:
    if not args.db.exists():
        print(
            f"No database at {args.db}; run 'init' first.",
            file=sys.stderr,
        )
        return 1

    conn = db.connect(args.db)
    try:
        results = validate.validate_all(conn, question_id=args.question)
    finally:
        conn.close()

    if not results:
        print("Nothing to validate: no boards and no derived DnD events.")
        return 0

    failures = 0
    for r in results:
        ok = "ok" if r.ok else "FAIL"
        line = f"[{ok}] {r.question_id}: key={r.key_status}"
        if r.canonical_question_id:
            # dnd/0014: validated against the canonical identifier's board.
            line += f" (alias of {r.canonical_question_id})"
        if r.key_status == validate.KEY_MATCH:
            line += f" ({r.n_correct_checks} Correct check(s))"
        elif r.key_status in (validate.KEY_MISMATCH, validate.KEY_AMBIGUOUS):
            line += (
                f" — source {list(r.source_key)}, inferred "
                f"{[list(k) for k in r.inferred_keys]}"
            )
        if r.n_ineligible_correct_checks:
            line += (
                f" [{r.n_ineligible_correct_checks} non-eligible Correct "
                f"check(s) ignored]"
            )
        print(line)
        if r.label_gaps:
            print(f"       label gaps ({len(r.label_gaps)}): {list(r.label_gaps)}")
        if not r.ok:
            failures += 1

    print(f"Validated {len(results)} question(s); {failures} failing.")
    return 1 if failures else 0


def cmd_reconstruct(args: argparse.Namespace) -> int:
    if not args.db.exists():
        print(f"No database at {args.db}; run 'init' first.", file=sys.stderr)
        return 1

    config = reconstruct_mod.SegmentationConfig(
        gap_seconds=args.gap_seconds,
        split_on_session_change=not args.no_split_session,
        split_on_page_view=not args.no_split_page,
        split_on_reset=args.split_on_reset,
    )
    conn = db.connect(args.db)
    try:
        try:
            result = reconstruct_mod.reconstruct_all(
                conn, question_id=args.question, config=config,
                feedback_policy=args.feedback_policy,
                seed_resumed_attempts=args.seed_resumed,
            )
        except regimes_mod.RegimeError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
    finally:
        conn.close()

    seeding = (
        f"seeded {result.seeded_attempts} of {result.resumed_attempts} at the prior "
        f"attempt's last decodable checked configuration, {result.resumed_attempts - result.seeded_attempts} "
        "from s_0"
        if args.seed_resumed
        else "every check not already no_move_logging stored resumed_attempt; --seed-resumed is D3.2 knob 10"
    )
    print(
        f"Reconstruct complete:\n"
        f"  questions:       {result.questions}\n"
        f"  attempts:        {result.attempts}\n"
        f"  resumed:         {result.resumed_attempts}  (prior_attempt_id set; {seeding})\n"
        f"  configurations:  {result.configurations}\n"
        f"  checks:          {result.checks}\n"
        f"  mismatches:      {result.mismatches}  (replay ≠ logged Input, eligible anchors)\n"
        f"  not comparable:  {result.non_eligible}  (non-eligible checks, stored with their cause)\n"
        f"  no move logging: {result.no_move_logging_checks}  (checks dated before the import's regime date; never compared)\n"
        f"  resumed attempt: {result.resumed_attempt_checks}  (checks of resumed attempts not already no_move_logging; never compared)\n"
        f"  noop moves:      {result.noop_moves}  (flagged noop_move; excluded from move counts)\n"
        f"  skipped actions: {result.skipped_actions}  (malformed / off-board)\n"
        f"  ambiguous:       {result.ambiguous_traces}  (traces with (timestamp, problem_view) ties)\n"
        f"  feedback policy: {args.feedback_policy}  (per-check disclosure classification)\n"
        f"  run:             #{result.run_id}  (pipeline_runs provenance)"
    )
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    if not args.db.exists():
        print(f"No database at {args.db}; run 'init' first.", file=sys.stderr)
        return 1

    conn = db.connect(args.db)
    try:
        result = scoring_mod.score_checks(conn, question_id=args.question)
    finally:
        conn.close()

    print(
        f"Score complete:\n"
        f"  questions: {result.questions}  (with a board)\n"
        f"  scored:    {result.scored}  checks\n"
        f"  correct:   {result.correct}  (all-or-nothing)\n"
        f"  skipped:   {result.skipped}  (unparseable Input)\n"
        f"  run:       #{result.run_id}  (pipeline_runs provenance)"
    )
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    if not args.db.exists():
        print(f"No database at {args.db}; run 'init' first.", file=sys.stderr)
        return 1

    conn = db.connect(args.db)
    try:
        result = process_mod.analyze_all(conn, question_id=args.question)
    finally:
        conn.close()

    print(
        f"Analyze complete:\n"
        f"  attempts:         {result.attempts}\n"
        f"  with convergence: {result.with_convergence}  (question has a board)\n"
        f"  run:              #{result.run_id}  (pipeline_runs provenance)"
    )
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    if not args.db.exists():
        print(f"No database at {args.db}.", file=sys.stderr)
        return 1

    conn = db.connect(args.db)
    try:
        if args.output is None:
            export_mod.export_dataset(
                conn, args.dataset, sys.stdout, question_id=args.question
            )
        else:
            with args.output.open("w", encoding="utf-8", newline="") as f:
                n = export_mod.export_dataset(
                    conn, args.dataset, f, question_id=args.question
                )
            print(f"Wrote {n} row(s) to {args.output}", file=sys.stderr)
    finally:
        conn.close()
    return 0


def _cmd_alias_no_subcommand(args: argparse.Namespace) -> int:
    print(
        "ERROR: `dnd alias` needs a subcommand: add, remove, list or import.",
        file=sys.stderr,
    )
    return 2


def _format_alias(a: aliases_mod.Alias) -> str:
    bits = []
    if a.reason:
        bits.append(a.reason)
    if a.source_commit:
        bits.append(f"commit {a.source_commit}")
    if a.recorded_on:
        bits.append(f"recorded {a.recorded_on}")
    if a.imported_from:
        bits.append(f"from {a.imported_from}")
    if a.replaced_board_source_hash:
        bits.append(f"retired its own board {a.replaced_board_source_hash[:12]}")
    tail = f"  [{'; '.join(bits)}]" if bits else ""
    return f"  {a.alias_question_id} -> {a.canonical_question_id}{tail}"


def cmd_alias_add(args: argparse.Namespace) -> int:
    if not args.db.exists():
        print(f"No database at {args.db}; run 'init' first.", file=sys.stderr)
        return 1
    conn = db.connect(args.db)
    try:
        try:
            status = aliases_mod.add_alias(
                conn, args.alias_question_id, args.canonical_question_id,
                reason=args.reason, source_commit=args.source_commit,
                recorded_on=args.recorded_on, replace_board=args.replace_board,
            )
        except aliases_mod.AliasError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        digest = aliases_mod.alias_set_sha256(conn)
    finally:
        conn.close()
    print(
        f"Alias {status}: {args.alias_question_id} -> {args.canonical_question_id}\n"
        f"  alias set sha256: {digest}"
    )
    return 0


def cmd_alias_remove(args: argparse.Namespace) -> int:
    if not args.db.exists():
        print(f"No database at {args.db}; run 'init' first.", file=sys.stderr)
        return 1
    conn = db.connect(args.db)
    try:
        removed = aliases_mod.remove_alias(conn, args.alias_question_id)
        digest = aliases_mod.alias_set_sha256(conn)
    finally:
        conn.close()
    if not removed:
        print(f"No alias {args.alias_question_id!r} is registered.", file=sys.stderr)
        return 1
    print(
        f"Alias removed: {args.alias_question_id}\n"
        f"  alias set sha256: {digest}\n"
        "  results computed under it are unchanged, re-run reconstruct / score / analyze"
    )
    return 0


def cmd_alias_list(args: argparse.Namespace) -> int:
    if not args.db.exists():
        print(f"No database at {args.db}; run 'init' first.", file=sys.stderr)
        return 1
    conn = db.connect(args.db)
    try:
        rows = aliases_mod.list_aliases(conn)
        digest = aliases_mod.alias_set_sha256(conn)
    finally:
        conn.close()
    print(f"Question-identifier aliases: {len(rows)}  (alias set sha256: {digest})")
    for a in rows:
        print(_format_alias(a))
    return 0


def cmd_alias_import(args: argparse.Namespace) -> int:
    if not args.db.exists():
        print(f"No database at {args.db}; run 'init' first.", file=sys.stderr)
        return 1
    if not args.file.exists():
        print(f"No alias file at {args.file}.", file=sys.stderr)
        return 1
    conn = db.connect(args.db)
    try:
        try:
            result = aliases_mod.import_aliases(
                conn, args.file, replace_board=args.replace_board
            )
        except aliases_mod.AliasError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
    finally:
        conn.close()
    print(
        f"Alias import complete:\n"
        f"  file:      {args.file.name}  (sha256 {result.file_sha256})\n"
        f"  added:     {result.added}\n"
        f"  unchanged: {result.unchanged}\n"
        f"  aliases:   {len(result.rows)}  (alias set sha256: {result.set_sha256})"
    )
    for a in result.rows:
        print(_format_alias(a))
    return 0


def _cmd_regime_no_subcommand(args: argparse.Namespace) -> int:
    print(
        "ERROR: `dnd regime` needs a subcommand: import, list or remove.",
        file=sys.stderr,
    )
    return 2


def _format_regime(g: regimes_mod.Regime) -> str:
    bits = []
    if g.reason:
        bits.append(g.reason)
    if g.source_memo:
        bits.append(f"memo {g.source_memo}")
    if g.recorded_on:
        bits.append(f"recorded {g.recorded_on}")
    if g.imported_from:
        bits.append(f"from {g.imported_from}")
    tail = f"  [{'; '.join(bits)}]" if bits else ""
    return f"  {g.file_sha256[:12]}… logs moves from {g.move_logging_from}{tail}"


def cmd_regime_import(args: argparse.Namespace) -> int:
    if not args.db.exists():
        print(f"No database at {args.db}; run 'init' first.", file=sys.stderr)
        return 1
    if not args.file.exists():
        print(f"No regime file at {args.file}.", file=sys.stderr)
        return 1
    conn = db.connect(args.db)
    try:
        try:
            result = regimes_mod.import_regimes(conn, args.file)
        except regimes_mod.RegimeError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
    finally:
        conn.close()
    print(
        f"Regime import complete:\n"
        f"  file:      {args.file.name}  (sha256 {result.file_sha256})\n"
        f"  added:     {result.added}\n"
        f"  unchanged: {result.unchanged}\n"
        f"  regimes:   {len(result.rows)}  (regime set sha256: {result.set_sha256})"
    )
    for g in result.rows:
        print(_format_regime(g))
    return 0


def cmd_regime_list(args: argparse.Namespace) -> int:
    if not args.db.exists():
        print(f"No database at {args.db}; run 'init' first.", file=sys.stderr)
        return 1
    conn = db.connect(args.db)
    try:
        rows = regimes_mod.list_regimes(conn)
        digest = regimes_mod.regime_set_sha256(conn)
    finally:
        conn.close()
    print(f"Logging regimes: {len(rows)}  (regime set sha256: {digest})")
    for g in rows:
        print(_format_regime(g))
    return 0


def cmd_regime_remove(args: argparse.Namespace) -> int:
    if not args.db.exists():
        print(f"No database at {args.db}; run 'init' first.", file=sys.stderr)
        return 1
    conn = db.connect(args.db)
    try:
        removed = regimes_mod.remove_regime(conn, args.file_sha256)
        digest = regimes_mod.regime_set_sha256(conn)
    finally:
        conn.close()
    if not removed:
        print("No regime row carries that sha256.", file=sys.stderr)
        return 1
    print(
        f"Regime removed: {args.file_sha256[:12]}…\n"
        f"  regime set sha256: {digest}\n"
        "  results computed under it are unchanged, re-run reconstruct"
    )
    return 0


# --- Account roles (dnd/0016). No handler prints an account id (M6). -----
def _cmd_roles_no_subcommand(args: argparse.Namespace) -> int:
    print(
        "ERROR: `dnd roles` needs a subcommand: import, list or remove.",
        file=sys.stderr,
    )
    return 2


def _short_sha(digest: str | None) -> str:
    return f"{digest[:12]}…" if digest else "none"


def _format_role(ordinal: int, a: roles_mod.AccountRole) -> str:
    """One `dnd roles list` line: the ordinal, pseudonym, role, exclude,
    raw_rows, ruled_on, ruled_by and the file name, never the id."""
    exclude = "yes" if a.exclude else "no"
    raw_rows = "-" if a.raw_rows is None else str(a.raw_rows)
    return (
        f"  {ordinal:>3}. {a.pseudonym or '-':<14} {a.role:<12} "
        f"exclude={exclude:<4} raw_rows={raw_rows:<6} "
        f"ruled {a.ruled_on or '-'} by {a.ruled_by or '-'}  "
        f"from {a.imported_from or '-'}"
    )


def cmd_roles_import(args: argparse.Namespace) -> int:
    if not args.db.exists():
        print(f"No database at {args.db}; run 'init' first.", file=sys.stderr)
        return 1
    if not args.file.exists():
        print(f"No rulings file at {args.file}.", file=sys.stderr)
        return 1
    conn = db.connect(args.db)
    try:
        try:
            result = roles_mod.import_roles(conn, args.file)
        except roles_mod.RoleError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
    finally:
        conn.close()
    excluded = sum(1 for a in result.rows if a.exclude)
    print(
        f"Account-role import complete:\n"
        f"  file:      {args.file.name}  (sha256 {result.file_sha256})\n"
        f"  added:     {result.added}\n"
        f"  unchanged: {result.unchanged}\n"
        f"  ruled:     {len(result.rows)}  (excluded: {excluded})\n"
        f"  roles set sha256: {_short_sha(result.set_sha256)}\n"
        "  (no account id is printed; `dnd roles list` names rows by offering pseudonym)"
    )
    return 0


def cmd_roles_list(args: argparse.Namespace) -> int:
    if not args.db.exists():
        print(f"No database at {args.db}; run 'init' first.", file=sys.stderr)
        return 1
    conn = db.connect(args.db)
    try:
        rows = roles_mod.list_roles(conn)
        digest = roles_mod.roles_set_sha256(conn)
    finally:
        conn.close()
    excluded = sum(1 for a in rows if a.exclude)
    print(
        f"Account roles: {len(rows)} ruled, {excluded} excluded  "
        f"(roles set sha256: {_short_sha(digest)})"
    )
    for ordinal, a in enumerate(rows, start=1):
        print(_format_role(ordinal, a))
    return 0


def cmd_roles_remove(args: argparse.Namespace) -> int:
    if not args.db.exists():
        print(f"No database at {args.db}; run 'init' first.", file=sys.stderr)
        return 1
    conn = db.connect(args.db)
    try:
        try:
            removed = roles_mod.remove_role(conn, args.anon_student_id)
        except roles_mod.RoleError as e:
            # A database predating dnd/0016: the remedy, as `roles import`
            # and `dnd derive` print it, not a traceback.
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
    finally:
        conn.close()
    if not removed:
        print("no such row", file=sys.stderr)
        return 1
    print("removed")
    return 0


def cmd_sensitivity_run(args: argparse.Namespace) -> int:
    if not args.primary_db.exists():
        print(f"No database at {args.primary_db}.", file=sys.stderr)
        return 1
    knobs = (
        [sensitivity_mod.knob_by_key(k) for k in args.knobs]
        if args.knobs else list(sensitivity_mod.KNOB_TABLE)
    )
    try:
        primary, rows = sensitivity_mod.run_all(
            args.primary_db, args.out, knobs,
            estimate=args.estimate, allow_canonical=args.allow_canonical,
        )
    except sensitivity_mod.SensitivityError as e:
        print(f"sensitivity: {e}", file=sys.stderr)
        return 1
    print(
        f"Sensitivity run complete:\n"
        f"  primary:        attempts={primary.n_attempts} "
        f"ready_share={primary.ready_share} agreement={primary.agreement} "
        f"primary_rq3={primary.primary_rq3}\n"
        f"  alternatives:   {len(rows)}  "
        f"(material: {sum(1 for d in rows if d.material == 'yes')} yes, "
        f"{sum(1 for d in rows if d.material == 'pending')} pending)\n"
        f"  rq3 column:     {'estimated' if args.estimate else 'empty (run with --estimate)'}\n"
        f"  deltas:         {args.out / 'deltas.csv'}"
    )
    return 0


def cmd_sensitivity_evidence(args: argparse.Namespace) -> int:
    if not args.db.exists():
        print(f"No database at {args.db}.", file=sys.stderr)
        return 1
    try:
        sensitivity_mod.refuse_canonical(args.db, args.allow_canonical)
    except sensitivity_mod.SensitivityError as e:
        print(f"sensitivity: {e}", file=sys.stderr)
        return 1
    conn = db.connect(args.db)
    try:
        report = sensitivity_mod.render_evidence(sensitivity_mod.evidence(conn))
    finally:
        conn.close()
    if args.output is None:
        sys.stdout.write(report)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        print(f"Wrote the evidence report to {args.output}", file=sys.stderr)
    return 0


def cmd_contrast(args: argparse.Namespace) -> int:
    if not args.db.exists():
        print(f"No database at {args.db}.", file=sys.stderr)
        return 1
    try:
        sensitivity_mod.refuse_canonical(args.db, args.allow_canonical)
    except sensitivity_mod.SensitivityError as e:
        print(f"contrast: {e}", file=sys.stderr)
        return 1
    if args.estimate and args.seed is None:
        print("contrast: --estimate needs --seed (the bootstrap is seeded).", file=sys.stderr)
        return 1
    config = contrast_mod.ContrastConfig(
        readiness=("ready", "limited") if args.include_limited else ("ready",),
        exclude_flags=tuple(args.exclude_flags),
        seed=args.seed,
        B=args.B,
    )
    conn = db.connect(args.db)
    try:
        counts, est = contrast_mod.run(
            conn, args.out, config, estimate_mode=args.estimate
        )
    finally:
        conn.close()
    g = {c.group: c for c in counts.groups}
    print(
        f"Contrast complete ({'estimate' if args.estimate else 'counts only'}):\n"
        f"  {contrast_mod.GROUP_CORRECT}: "
        f"{g[contrast_mod.GROUP_CORRECT].attempts} attempts, "
        f"{g[contrast_mod.GROUP_CORRECT].students} students, "
        f"{g[contrast_mod.GROUP_CORRECT].items} items\n"
        f"  {contrast_mod.GROUP_INCORRECT}: "
        f"{g[contrast_mod.GROUP_INCORRECT].attempts} attempts, "
        f"{g[contrast_mod.GROUP_INCORRECT].students} students, "
        f"{g[contrast_mod.GROUP_INCORRECT].items} items\n"
        f"  items with >= {counts.floor_per_arm} per arm: "
        f"{counts.n_items_clearing_floor}  -> reporting level: {counts.reporting_level}\n"
        f"  files:          {args.out}"
    )
    return 0

