"""
Command-line interface to the PROTEUS pipeline.

Run with:

    python -m proteus.cli --help

Root subcommands (core, service-agnostic):

    init     Create a fresh database with the schema applied.
    reset    Delete and re-create the database (asks for
             confirmation unless --yes is passed).
    migrate  Apply pending forward-only schema migrations — core first, then
             each registered service's (--dry-run to preview).
    ingest   Import a DataShop .tab export — the supported format — into the
             generic events table (--derive also runs each service's derive
             step); the Runestone useinfo .csv reader remains a dormant seam.
    reingest-events
             Backfill the generic events table from an already-imported
             export file (the original file is required).
    status   Print a summary of DB contents: core counts plus each available
             service's contribution.
    delete-import
             Delete an import and its full derived cascade (events plus every
             service's rows), after printing the blast radius.
    sync-catalog
             Populate the question catalog from events: one questions row per
             distinct problem (page views excluded), with an inferred type.
    textbook add
             Register a public PreTeXt textbook (book id, git URL, build dir)
             in the registry for source sync and preview.
    sync-questions
             Walk a built PreTeXt textbook tree and cache one standalone
             HTML preview per short-answer question.
    sync-sources
             Clone a registered book (--book) and extract its question source,
             or walk a local PreTeXt source tree (--source-dir): cache one .ptx
             snippet per question (and questions.source_xml/.title), then run
             each registered service's source handler.

Service namespaces (one per question-type service the registry finds by
scanning proteus/services/ — a tree carries the namespaces of the services it
ships, and only those):

    dnd <stage> …  Drag-and-Drop commands: derive, sync-boards, validate,
                   reconstruct, score, analyze, export, alias, regime, roles,
                   sensitivity, contrast.
    sa <stage> …   Short Answer commands: derive, screen, extract, match,
                   dispose, vocab, codebook, export.

The registry imports each service inside try/except: a service whose import
fails prints a warning and loses only its own namespace — the root commands
and every other service keep working (plan §2.2 failure isolation). A service
that is absent altogether is simply not found: no namespace, no warning.

Every service command exists only under its namespace. The un-namespaced SA
aliases of item A2.3 (`proteus screen`, …) were retired by FU-1; a legacy
spelling is an argparse error, not a deprecation note.

Database location defaults to <repo>/data/proteus.db (the repository's data/
directory, git-ignored); override with --db. The default is resolved through
proteus.core.db.default_db_path() — the single source of truth shared with the
Streamlit app.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

from proteus import db
from proteus.core import catalog as catalog_mod
from proteus.core import ingest as core_ingest
from proteus.core import manifest as manifest_mod
from proteus.core import migrate as migrate_mod
from proteus.core import registry
from proteus.core import textbooks as textbooks_mod

log = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    services, failures = registry.discover()
    for f in failures:
        print(
            f"warning: service package {f.package!r} failed to load "
            f"({f.error}); its commands are disabled",
            file=sys.stderr,
        )

    parser = _build_parser(services)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    # --db uses a None sentinel (not a constant default) so we can tell an
    # explicit --db from the default. Only the default path resolves through
    # the shared helper and triggers the legacy notice; a custom --db (or
    # :memory:) is left untouched. We ensure the data/ dir only for commands
    # that actually create a fresh DB there (init/reset) — read/inspect
    # commands that bail on a missing DB must not create it as a side effect.
    if args.db is None:
        creates_db = args.func in (cmd_init, cmd_reset)
        args.db = db.default_db_path(create_parent=creates_db)
        _maybe_warn_legacy_db(args.db)

    return args.func(args)


def _maybe_warn_legacy_db(default_path: Path) -> None:
    """
    Non-destructive pointer for users with a database at the *old* default
    location (DB-3, Decision 2). Before this refactor the default was a
    repo-root ``./proteus.db``; now it is ``<repo>/data/proteus.db``. If a
    legacy ``./proteus.db`` is present and the new default does not yet exist,
    point the user at the new location and the move command — without moving or
    deleting anything.

    Only called when the default is in effect (no explicit ``--db``), so a
    custom path stays silent. It also falls silent as soon as the new default
    exists (e.g. after the first ``init``), so it does not nag once migrated.
    The suggested command is self-contained (``mkdir -p`` then ``mv``) so it
    works whether or not ``data/`` exists yet — the notice itself creates
    nothing.
    """
    legacy = Path("proteus.db")
    if legacy.exists() and not default_path.exists():
        print(
            f"note: found a legacy database at ./{legacy} but the default is "
            f"now {default_path}. Nothing was moved. To keep using it, run:\n"
            f"  mkdir -p {default_path.parent} && mv ./{legacy} {default_path}",
            file=sys.stderr,
        )


def _build_parser(
    services: tuple[registry.ServiceDescriptor, ...] | None = None,
) -> argparse.ArgumentParser:
    if services is None:
        services = registry.available_services()

    parser = argparse.ArgumentParser(
        prog="python -m proteus.cli",
        description="PROTEUS automation pipeline CLI.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help=(
            f"Path to the SQLite database "
            f"(default: {db.default_db_path()})"
        ),
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )

    sub = parser.add_subparsers(required=True, dest="command")

    p_init = sub.add_parser("init", help="Create a fresh database.")
    p_init.set_defaults(func=cmd_init)

    p_reset = sub.add_parser(
        "reset", help="Delete and re-create the database."
    )
    p_reset.add_argument(
        "--yes", action="store_true",
        help="Skip the confirmation prompt.",
    )
    p_reset.set_defaults(func=cmd_reset)

    p_migrate = sub.add_parser(
        "migrate",
        help="Apply pending forward-only schema migrations to the database.",
    )
    p_migrate.add_argument(
        "--dry-run",
        action="store_true",
        help="List the migrations that would be applied without running them.",
    )
    p_migrate.set_defaults(func=cmd_migrate)

    p_reingest = sub.add_parser(
        "reingest-events",
        help=(
            "Backfill the generic events table from an already-imported export "
            "file (original file required; does not touch responses)."
        ),
    )
    p_reingest.add_argument(
        "file", type=Path, help="Path to the original export file."
    )
    p_reingest.add_argument(
        "--format",
        choices=["datashop_tab"],
        help="Override format auto-detection.",
    )
    p_reingest.set_defaults(func=cmd_reingest_events)

    p_ingest = sub.add_parser(
        "ingest",
        help=(
            "Import an export file's every row into the shared events table "
            "(add --derive to also materialize each service's rows). "
            "Supported format: DataShop .tab."
        ),
    )
    p_ingest.add_argument("file", type=Path, help="Path to the export file.")
    p_ingest.add_argument(
        "--derive",
        action="store_true",
        help=(
            "After ingest, run every available service's derive step over "
            "the new import (the old one-command UX)."
        ),
    )
    p_ingest.add_argument(
        "--question",
        help=(
            "With --derive: restrict the derive step to this question_id. "
            "Events always capture the whole file."
        ),
    )
    p_ingest.add_argument(
        "--format",
        choices=["datashop_tab", "runestone_useinfo_csv"],
        help="Override format auto-detection.",
    )
    p_ingest.set_defaults(func=cmd_ingest)

    p_status = sub.add_parser("status", help="Print a summary of DB contents.")
    p_status.set_defaults(func=cmd_status)

    p_delete = sub.add_parser(
        "delete-import",
        help=(
            "Delete an import and every derived row for it across services "
            "(events, DnD and SA tables), after showing the blast radius."
        ),
    )
    p_delete.add_argument(
        "import_id", type=int, help="The raw_imports import_id to delete."
    )
    p_delete.add_argument(
        "--yes", action="store_true", help="Skip the confirmation prompt."
    )
    p_delete.set_defaults(func=cmd_delete_import)

    p_catalog = sub.add_parser(
        "sync-catalog",
        help=(
            "Populate the question catalog from events: one questions row per "
            "distinct problem (page views excluded), with an inferred type. "
            "Idempotent; also run automatically at the end of ingest."
        ),
    )
    p_catalog.set_defaults(func=cmd_sync_catalog)

    p_textbook = sub.add_parser(
        "textbook", help="Manage the textbook registry (SP-1)."
    )
    tb_sub = p_textbook.add_subparsers(dest="textbook_command", metavar="<command>")
    p_tb_add = tb_sub.add_parser(
        "add", help="Register a public PreTeXt textbook for source sync/preview."
    )
    p_tb_add.add_argument("book_xml_id", help="The book's xml id, e.g. 'ula-proteus'.")
    p_tb_add.add_argument(
        "git_url", help="Public git repo URL (https:// or file://; no credentials)."
    )
    p_tb_add.add_argument("build_dir", help="Local path to the book's built HTML.")
    p_tb_add.add_argument(
        "--ref", default=None,
        help="Optional git ref (branch/tag/SHA); the default branch if omitted.",
    )
    p_tb_add.add_argument("--notes", default=None, help="Optional free-text note.")
    p_tb_add.set_defaults(func=cmd_textbook_add)
    p_textbook.set_defaults(func=_cmd_textbook_no_subcommand)

    p_sync = sub.add_parser(
        "sync-questions",
        help=(
            "Walk a built PreTeXt textbook tree and cache one standalone "
            "HTML preview per short-answer question."
        ),
    )
    p_sync.add_argument(
        "--build-dir",
        type=Path,
        required=True,
        help="Path to the built PreTeXt HTML directory to walk.",
    )
    p_sync.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help=(
            "Directory to write per-question standalone HTML files into. "
            "Files land in <output-dir>/<textbook-id>/<xml_id>.html."
        ),
    )
    p_sync.add_argument(
        "--textbook-id",
        type=int,
        required=True,
        help="textbook_id (from the textbooks table) the build belongs to.",
    )
    p_sync.set_defaults(func=cmd_sync_questions)

    p_sources = sub.add_parser(
        "sync-sources",
        help=(
            "Clone a registered book and extract its question source — or walk "
            "a local PreTeXt source tree — caching one .ptx snippet per "
            "question (plus questions.source_xml and questions.title) and "
            "running each service's source handler (e.g. DnD board building). "
            "Re-running also backfills titles on rows synced before the "
            "questions.title column existed (migration 0014)."
        ),
    )
    p_sources.add_argument(
        "--book",
        default=None,
        help=(
            "Registered book_xml_id to clone (its public repo) and sync (SP-2). "
            "Mutually exclusive with --source-dir."
        ),
    )
    p_sources.add_argument(
        "--source-dir",
        type=Path,
        default=None,
        help=(
            "Walk this local PreTeXt source directory directly (no clone); "
            "requires --textbook-id."
        ),
    )
    p_sources.add_argument(
        "--textbook-id",
        type=int,
        default=None,
        help="textbook_id the source belongs to (required with --source-dir).",
    )
    p_sources.add_argument(
        "--clone-root",
        type=Path,
        default=None,
        help="Where to clone books with --book (default: data/textbook_clones).",
    )
    p_sources.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=(
            "Directory for the per-question snippet cache; files land in "
            "<output-dir>/<book_xml_id>/<question_id>.ptx "
            "(default: data/question_sources)."
        ),
    )
    p_sources.set_defaults(func=cmd_sync_sources)

    p_manifest = sub.add_parser(
        "manifest",
        help=(
            "Write the run manifest: imports by SHA-256 and pseudonym (never a "
            "path), textbook refs, source pins, migration state, every "
            "pipeline run's configuration, per-table counts, and one "
            "fingerprint (E-03). `manifest diff A B` compares two manifests."
        ),
    )
    p_manifest.add_argument(
        "-o", "--output", type=Path,
        help="Where to write the YAML manifest (default: stdout).",
    )
    p_manifest.add_argument(
        "--pseudonyms", type=Path,
        help=(
            "CSV with `sha256` and `pseudonym` columns naming the imports; "
            "unmapped imports are named by their hash."
        ),
    )
    p_manifest.set_defaults(func=cmd_manifest)
    manifest_sub = p_manifest.add_subparsers(dest="manifest_command")
    p_manifest_diff = manifest_sub.add_parser(
        "diff", help="Report every leaf that differs between two manifests."
    )
    p_manifest_diff.add_argument("a", type=Path)
    p_manifest_diff.add_argument("b", type=Path)
    p_manifest_diff.set_defaults(func=cmd_manifest_diff)

    # Per-service namespaces, delegated to each descriptor (plan §2.2). A
    # service that failed to import is simply absent here; main() has already
    # printed the warning.
    for svc in services:
        p_svc = sub.add_parser(
            svc.name, help=f"{svc.title} service commands."
        )
        svc_sub = p_svc.add_subparsers(required=True, dest=f"{svc.name}_command")
        svc.register_cli(svc_sub)

    return parser


def cmd_init(args: argparse.Namespace) -> int:
    if args.db.exists():
        print(
            f"Database already exists at {args.db}; "
            f"use 'reset' to recreate it.",
            file=sys.stderr,
        )
        return 1
    conn = db.connect(args.db)
    try:
        db.bootstrap(conn)
    finally:
        conn.close()
    print(f"Created fresh database at {args.db}")
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    if args.db.exists() and not args.yes:
        try:
            confirm = input(
                f"This will DELETE {args.db} and create a fresh database. "
                f"Type 'yes' to continue: "
            )
        except EOFError:
            confirm = ""
        if confirm.strip().lower() != "yes":
            print("Aborted.")
            return 1
    db.reset(args.db)
    print(f"Reset database at {args.db}")
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    if not args.db.exists():
        print(
            f"No database at {args.db}; run 'init' first.", file=sys.stderr,
        )
        return 1

    conn = db.connect(args.db)
    try:
        before = migrate_mod.current_version(conn)
        try:
            result = migrate_mod.migrate(
                conn,
                service_sources=registry.migration_sources(),
                dry_run=args.dry_run,
            )
        except migrate_mod.MigrationError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
    finally:
        conn.close()

    if args.dry_run:
        if not result.pending:
            print(f"Database at schema version {before}; no migrations pending.")
        else:
            print(f"Database at schema version {before}; pending migrations:")
            for m in result.pending:
                print(f"  - {m.name} ({m.source})")
        return 0

    if not result.applied:
        print(f"Database already up to date at schema version {before}.")
    else:
        print(f"Applied {len(result.applied)} migration(s):")
        for m in result.applied:
            print(f"  - {m.name} ({m.source})")
        print(f"Database now at schema version {result.final_version}.")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    """
    Shared event ingest (plan §2.3 / item A3.3): parse every row of the file
    into `events`, then — with --derive — run each available service's derive
    over the new import. Responses (and any future service's tables) are only
    written by derive steps, never by ingest itself.
    """
    if not args.db.exists():
        print(
            f"No database at {args.db}; run 'init' first.",
            file=sys.stderr,
        )
        return 1
    if args.question and not args.derive:
        print(
            "ERROR: --question filters the derive step; pass --derive too "
            "(events always capture the whole file).",
            file=sys.stderr,
        )
        return 1

    conn = db.connect(args.db)
    try:
        # B2/ROB-02: a pre-migration database has no events table; without
        # this guard (the one cmd_sync_catalog already carries) the ingest
        # dies in a raw OperationalError traceback. Realistic trigger: an
        # upgraded checkout whose DB hasn't seen `proteus migrate` yet.
        if not _table_exists(conn, "events"):
            print(
                "No events table; this database predates the current "
                "schema — run `proteus migrate` first.",
                file=sys.stderr,
            )
            return 1
        try:
            result = core_ingest.ingest_file(
                conn,
                file_path=args.file,
                file_format=args.format,
            )
        except FileExistsError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        except (ValueError, FileNotFoundError) as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1

        breakdown = ", ".join(
            f"{sel}={n}" for sel, n in sorted(result.per_selection.items())
        )
        print(
            f"Ingest complete:\n"
            f"  import_id:  {result.import_id}\n"
            f"  file:       {result.file_path}\n"
            f"  format:     {result.file_format}\n"
            f"  inserted:   {result.inserted}\n"
            f"  skipped:    {result.skipped}  (already in events)\n"
            f"  total seen: {result.total_seen}\n"
            f"  selections: {breakdown}"
        )

        if args.derive:
            # B1/ROB-01: one service's derive failure must not take the
            # others down — the F6 isolation property the registry
            # promises, and the tolerance cmd_delete_import already shows
            # per service. The events are already committed above; each
            # derive is independent and idempotent, so the sound move is
            # to report, keep going, and exit non-zero at the end.
            derive_failures = 0
            for svc in registry.available_services():
                if svc.derive is None:
                    continue
                try:
                    derive_result = svc.derive(
                        conn,
                        import_id=result.import_id,
                        question_id=args.question,
                    )
                except Exception as e:
                    # A derive that died mid-write can leave an open
                    # transaction on the shared connection; clear it so
                    # the next service starts clean (rollback is a no-op
                    # when nothing is open).
                    conn.rollback()
                    derive_failures += 1
                    print(f"  FAILED ({svc.name}): {e}", file=sys.stderr)
                    continue
                print(
                    f"  {svc.name} derive: inserted={derive_result.inserted}, "
                    f"skipped={derive_result.skipped}, "
                    f"malformed={derive_result.malformed}"
                )
            if derive_failures:
                print(
                    f"{derive_failures} derive step(s) failed; the ingest "
                    "and the other services' derives are committed. Fix "
                    "the failure and re-run the service's own derive "
                    "command (e.g. `proteus sa derive`) — derive is "
                    "idempotent over already-derived rows.",
                    file=sys.stderr,
                )
                return 1
    finally:
        conn.close()
    return 0


def cmd_reingest_events(args: argparse.Namespace) -> int:
    if not args.db.exists():
        print(
            f"No database at {args.db}; run 'init' first.", file=sys.stderr,
        )
        return 1

    conn = db.connect(args.db)
    try:
        # B2/ROB-02: same pre-migration guard as cmd_ingest — see there.
        if not _table_exists(conn, "events"):
            print(
                "No events table; this database predates the current "
                "schema — run `proteus migrate` first.",
                file=sys.stderr,
            )
            return 1
        try:
            result = core_ingest.backfill_events(
                conn, args.file, file_format=args.format
            )
        except FileNotFoundError as e:
            print(f"ERROR: file not found: {e}", file=sys.stderr)
            return 1
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
    finally:
        conn.close()

    breakdown = ", ".join(
        f"{sel}={n}" for sel, n in sorted(result.per_selection.items())
    )
    print(
        f"Reingest-events complete:\n"
        f"  import_id:  {result.import_id}\n"
        f"  file:       {result.file_path}\n"
        f"  inserted:   {result.inserted}\n"
        f"  skipped:    {result.skipped}  (already in events)\n"
        f"  total seen: {result.total_seen}\n"
        f"  selections: {breakdown}"
    )
    return 0


def cmd_sync_catalog(args: argparse.Namespace) -> int:
    """
    Reconcile the question catalog from events (item CD-2): one questions row
    per distinct non-page problem, with an inferred type. Idempotent.
    """
    if not args.db.exists():
        print(f"No database at {args.db}; run 'init' first.", file=sys.stderr)
        return 1

    conn = db.connect(args.db)
    try:
        if not _table_exists(conn, "events"):
            print(
                "No events table; run `proteus migrate` and ingest a file first.",
                file=sys.stderr,
            )
            return 1
        result = catalog_mod.sync_catalog(conn)
    finally:
        conn.close()

    by_type = ", ".join(f"{t}={n}" for t, n in sorted(result.by_type.items()))
    print(
        f"Catalog sync complete:\n"
        f"  questions:   {result.total}\n"
        f"  created:     {result.created}\n"
        f"  serviced:    {result.serviced}\n"
        f"  unserviced:  {result.unserviced}\n"
        f"  by type:     {by_type}"
    )
    return 0


def _cmd_textbook_no_subcommand(args: argparse.Namespace) -> int:
    print(
        "ERROR: `textbook` needs a subcommand (e.g. `textbook add <book> "
        "<git_url> <build_dir>`).",
        file=sys.stderr,
    )
    return 1


def cmd_textbook_add(args: argparse.Namespace) -> int:
    """Register a public PreTeXt textbook in the registry (item SP-1)."""
    if not args.db.exists():
        print(f"No database at {args.db}; run 'init' first.", file=sys.stderr)
        return 1

    conn = db.connect(args.db)
    try:
        try:
            textbook_id = textbooks_mod.add_textbook(
                conn,
                book_xml_id=args.book_xml_id,
                git_repo_url=args.git_url,
                build_dir=args.build_dir,
                git_ref=args.ref,
                notes=args.notes,
            )
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
    finally:
        conn.close()

    print(
        f"Registered textbook {args.book_xml_id!r} (textbook_id={textbook_id}).\n"
        f"  repo:  {args.git_url}\n"
        f"  ref:   {args.ref or textbooks_mod.DEFAULT_GIT_REF}\n"
        f"  build: {args.build_dir}"
    )
    return 0


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def cmd_status(args: argparse.Namespace) -> int:
    """
    Aggregated status (item A2.4): core counts (imports, events, runs) plus
    each available service's status() contribution. Degrades gracefully — a
    pre-migration database or an unavailable service yields a hint line, not
    a crash.
    """
    if not args.db.exists():
        print(f"No database at {args.db}.", file=sys.stderr)
        return 1

    services, failures = registry.discover()
    conn = db.connect(args.db)
    try:
        imports = conn.execute(
            "SELECT count(*) AS n FROM raw_imports"
        ).fetchone()["n"]

        if _table_exists(conn, "events"):
            events = str(
                conn.execute("SELECT count(*) AS n FROM events").fetchone()["n"]
            )
        else:
            events = "— (events table missing; run `proteus migrate`)"

        # Per-service run counts need pipeline_runs.service (migration 0005);
        # fall back to the plain total on an older database.
        run_count = conn.execute(
            "SELECT count(*) AS n FROM pipeline_runs"
        ).fetchone()["n"]
        try:
            by_service = conn.execute(
                "SELECT service, count(*) AS n FROM pipeline_runs "
                "GROUP BY service ORDER BY service"
            ).fetchall()
            if by_service:
                bits = ", ".join(f"{r['service']}={r['n']}" for r in by_service)
                runs_line = f"{run_count} ({bits})"
            else:
                runs_line = str(run_count)
        except sqlite3.OperationalError:
            runs_line = str(run_count)

        print(
            f"Database: {args.db}\n"
            f"  imports:   {imports}\n"
            f"  events:    {events}\n"
            f"  runs:      {runs_line}"
        )

        for svc in services:
            print(f"{svc.title} ({svc.name}):")
            for label, value in svc.status(conn).items():
                print(f"  {label + ':':<11}{value}")
        for f in failures:
            print(f"service package {f.package!r}: UNAVAILABLE ({f.error})")
    finally:
        conn.close()
    return 0


def cmd_delete_import(args: argparse.Namespace) -> int:
    """
    Delete an import and its full derived cascade (item B4.14). Prints the
    blast radius — core rows plus each service's rows, per table — then asks
    for confirmation (unless --yes) before committing the atomic delete.
    """
    if not args.db.exists():
        print(f"No database at {args.db}; run 'init' first.", file=sys.stderr)
        return 1

    from proteus.core import imports as imports_mod

    conn = db.connect(args.db)
    try:
        radius = imports_mod.blast_radius(conn, args.import_id)
        if not radius.exists:
            print(f"No import #{args.import_id}.", file=sys.stderr)
            return 1

        print(f"Deleting import #{args.import_id} will remove:")
        for label, n in radius.core_counts.items():
            print(f"  core.{label:<14} {n}")
        for svc in radius.services:
            if svc.error is not None:
                print(f"  {svc.name}: ERROR computing blast radius ({svc.error})")
                continue
            for table, n in svc.counts.items():
                print(f"  {svc.name}.{table:<14} {n}")
        print(f"  total rows: {radius.total}")

        if not args.yes:
            try:
                confirm = input(
                    f"Type 'yes' to permanently delete import "
                    f"#{args.import_id}: "
                )
            except EOFError:
                confirm = ""
            if confirm.strip().lower() != "yes":
                print("Aborted.")
                return 1

        result = imports_mod.delete_import(conn, args.import_id)
    finally:
        conn.close()

    print(f"Deleted import #{args.import_id} ({result.total} rows).")
    return 0


def cmd_manifest(args: argparse.Namespace) -> int:
    if not args.db.exists():
        print(f"No database at {args.db}.", file=sys.stderr)
        return 1
    pseudonyms = manifest_mod.load_pseudonyms(args.pseudonyms)
    conn = db.connect(args.db)
    try:
        manifest = manifest_mod.build_manifest(
            conn, pseudonyms=pseudonyms, db_path=args.db
        )
    finally:
        conn.close()
    if args.output is None:
        sys.stdout.write(manifest_mod.dump_yaml(manifest))
    else:
        manifest_mod.write_manifest(manifest, args.output)
        print(
            f"Wrote manifest {manifest['fingerprint'][:16]}… to {args.output}",
            file=sys.stderr,
        )
    return 0


def cmd_manifest_diff(args: argparse.Namespace) -> int:
    a = manifest_mod.read_manifest(args.a)
    b = manifest_mod.read_manifest(args.b)
    lines = manifest_mod.diff_manifests(a, b)
    if not lines:
        print("manifests identical (fingerprints "
              f"{a.get('fingerprint', '?')[:16]}… / {b.get('fingerprint', '?')[:16]}…)")
        return 0
    for line in lines:
        print(line)
    return 1


def cmd_sync_sources(args: argparse.Namespace) -> int:
    if not args.db.exists():
        print(
            f"No database at {args.db}; run 'init' first.", file=sys.stderr,
        )
        return 1

    # Exactly one mode: --book (clone from the registry) or --source-dir (walk
    # a local tree). --textbook-id is required with --source-dir.
    if bool(args.book) == bool(args.source_dir):
        print(
            "ERROR: pass exactly one of --book (clone a registered book) or "
            "--source-dir (walk a local tree).",
            file=sys.stderr,
        )
        return 1
    if args.source_dir and args.textbook_id is None:
        print("ERROR: --source-dir requires --textbook-id.", file=sys.stderr)
        return 1

    from proteus.core import sources
    from proteus.core.clone import CloneError

    output_kwargs = {} if args.output_dir is None else {"output_dir": args.output_dir}

    conn = db.connect(args.db)
    try:
        try:
            if args.book:
                clone_kwargs = (
                    {} if args.clone_root is None else {"clone_root": args.clone_root}
                )
                result = sources.sync_book_sources(
                    conn, args.book, **clone_kwargs, **output_kwargs
                )
                label = f"book:           {args.book}"
            else:
                result = sources.sync_question_sources(
                    conn,
                    source_dir=args.source_dir,
                    textbook_id=args.textbook_id,
                    **output_kwargs,
                )
                label = f"textbook_id:    {args.textbook_id}"
        except (ValueError, OSError, CloneError) as e:
            log.debug("sync-sources raised", exc_info=True)
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
    finally:
        conn.close()

    print(
        f"Source sync complete:\n"
        f"  {label}\n"
        f"  extracted:     {result.extracted}\n"
        f"  updated:       {result.updated}\n"
        f"  unchanged:     {result.unchanged}\n"
        f"  skipped files: {len(result.skipped_files)}"
    )
    if result.titles_backfilled:
        # The migration-0014 backfill (FU-3): rows synced before the
        # questions.title column existed, repopulated by this re-sync.
        print(
            f"  titles backfilled: {result.titles_backfilled} "
            "(questions.title filled on rows whose snippet was unchanged)"
        )
    for svc_name, counts in result.handled.items():
        bits = ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "0"
        print(f"  {svc_name} source handler: {bits}")
    return 0


def cmd_sync_questions(args: argparse.Namespace) -> int:
    if not args.db.exists():
        print(
            f"No database at {args.db}; run 'init' first.", file=sys.stderr,
        )
        return 1

    # Imported here, not at module load: sync pulls in bs4, and a missing or
    # broken third-party dep must not take down the whole CLI (F6). The
    # command itself still fails loudly if the import fails.
    # TODO(A1.6): sync-questions generalizes into core and dispatches to each
    # registered service's source_handler; it stays a root command.
    from proteus.sync import questions as sync_questions_mod

    conn = db.connect(args.db)
    try:
        try:
            result = sync_questions_mod.sync_questions(
                conn,
                build_dir=args.build_dir,
                output_dir=args.output_dir,
                textbook_id=args.textbook_id,
            )
        except (ValueError, OSError) as e:
            log.debug("sync_questions raised", exc_info=True)
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
    finally:
        conn.close()

    print(
        f"Sync complete:\n"
        f"  textbook_id:                  {args.textbook_id}\n"
        f"  extracted:                    {result.extracted}\n"
        f"  updated:                      {result.updated}\n"
        f"  unchanged:                    {result.unchanged}\n"
        f"  answered_but_not_catalogued:  {len(result.missing)}\n"
        f"  not_yet_answered:             {len(result.orphan)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
