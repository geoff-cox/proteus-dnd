"""Paper 1, item-inventory task A (§3): source-side cardsort inventory at a pin.

Companion to inventory_exports.py and tabulate_corpus_facts.py. Builds every
board from a *local checkout* of the registered book at an expected commit,
using the pipeline's own code end to end, never a reimplementation:

  - the walk is ``proteus.core.sources.sync_question_sources`` over an
    in-memory bootstrapped connection (``db.connect(':memory:')``, the
    source handlers index rows by name and need the ``sqlite3.Row`` factory
    it installs; task-file-audit amendment B5, corrected per task §3), with
    one textbook row registered from a ``file://`` URL and an explicit
    ``output_dir`` (its default writes under ``data/``, forbidden by §2.1);
    the sync dispatches the service source handlers itself, so boards land
    in ``dnd_boards`` exactly as ``sync-sources`` would store them;
  - the per-question source *path* (which the pipeline does not store) comes
    from a second walk with ``extract_from_file`` over the same sorted file
    list, mirroring the sync's keep-the-first duplicate rule (BUG-03); the
    two walks' cardsort id sets are asserted identical;
  - F18 covariates come from ``export._board_covariates`` (callable outside
    the export: it reads only ``n_cards`` / ``n_zones`` / ``key_json`` off
    the row, plus the configured policy defaults).

No database file is created; the only writes are the snippet cache under
--work-dir and the two output files. Deterministic: rows sort by
question_id; re-running at the same commit reproduces the outputs.

§2.2/§2.3 discipline: no authored card/zone text is emitted, ids, indices,
keys, counts, and hashes only. Output paths are checkout-relative; every
diagnostic passes the predecessor's Mask (mapped surnames) and _mask_paths
(path shapes), and both outputs pass Mask.assert_clean before they are
written (§2.3a). `source_hash` is the **board** hash (`dnd_boards
.source_hash` via `board.source_fingerprint`), not `questions.source_hash`
(§2.4a), named to match the Task A schema and `dnd export boards`.

Usage:
    proteus-inventory-items \
        --checkout /tmp/<session>/ula-fork \
        --commit <sha> --book ula-proteus \
        --map data/private/offering-pseudonyms.csv \
        --csv /tmp/<session>/items.csv --md /tmp/<session>/items.md \
        --work-dir /tmp/<session>/inv-work [--scope S26] \
        [--extra-question-id <logged id with no board at this pin> ...] \
        [--db /tmp/<session>/db/stage.db]   # adds pooled per-item attempt counts

For --book ula-proteus the known logged-only row (task §3's 41st) is appended
automatically; --extra-question-id adds further ids. An extra id that has a
board at the pin being inventoried is skipped, so era pins stay correct.
"""

import argparse
import csv
import io
import json
import logging
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from proteus.core import db, registry, sources, textbooks
from proteus.paper1._root import find_root
from proteus.paper1.inventory_exports import Mask, _MaskedParser, _mask_paths, load_map
from proteus.services.dragndrop import export as dnd_export
from proteus.services.dragndrop.board import load_board

ROOT = find_root()  # the checkout (proteus.paper1._root)

# Task §3's required 41st row: the logged problem with no board at the
# current pin, a textbook identifier (task §0), safe to commit. Appended
# for the matching --book unless the id has a board at the inventoried pin.
KNOWN_LOGGED_ONLY = {
    "ula-proteus": ["ula-proteus__ula_proteus_Section_1_2_RREF_using_EROs_v1"],
}

CSV_COLUMNS = [
    "question_id", "xml_id", "section_id", "source_path", "source_commit",
    "blob_sha", "source_hash", "present_at_pin", "P", "R", "key_json",
    "decoy_cards", "decoy_zones", "match_blocks", "empty_match_blocks",
    "num_slots", "state_space_size", "num_decoy_cards", "num_decoy_slots",
    "key_occupancy", "feedback_policy", "check_cost", "scope", "attempts",
]


_MASK: Mask | None = None


def _clean(text: str) -> str:
    """§2.3a: every printed diagnostic passes both filters, path shapes
    always, mapped surnames once the map is loaded."""
    text = _mask_paths(text)
    return _MASK(text) if _MASK is not None else text


def _die(msg: str) -> None:
    sys.stderr.write(_clean(msg) + "\n")
    raise SystemExit(2)


def _git(checkout: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(checkout), *args],
        capture_output=True, text=True, check=True,
    )
    return proc.stdout.strip()


def _match_counts(source_xml: str) -> tuple[int, int]:
    """(match blocks, empty match blocks) of the snippet's first <cardsort>,
    the same element board_from_source reads (it warns on and ignores the
    empty ones)."""
    root = ET.fromstring(source_xml)
    cardsort = root if root.tag == "cardsort" else root.find(".//cardsort")
    if cardsort is None:
        return 0, 0
    matches = cardsort.findall("match")
    empty = sum(
        1 for m in matches
        if not m.findall("premise") and not m.findall("response")
    )
    return len(matches), empty


def _path_map(checkout: Path, claimed_types: frozenset[str]) -> dict[str, str]:
    """question_id (bare xml_id) -> checkout-relative path, mirroring the
    sync walk: same glob, same sort, same keep-the-first rule (BUG-03)."""
    paths = sorted(
        p for pattern in ("*.ptx", "*.xml") for p in checkout.rglob(pattern)
    )
    out: dict[str, str] = {}
    for path in paths:
        extracted, parsed_ok = sources.extract_from_file(path, claimed_types)
        if not parsed_ok:
            continue
        for q in extracted:
            out.setdefault(q.xml_id, str(path.relative_to(checkout)))
    return out


def main() -> None:
    ap = _MaskedParser(
        prog="inventory_items.py",
        description="Source-side cardsort inventory at a pinned commit "
                    "(ids, keys, counts, hashes only).")
    ap.add_argument("--checkout", type=Path, required=True,
                    help="local checkout of the book (a temp-dir clone)")
    ap.add_argument("--commit", required=True,
                    help="the sha the checkout must be at (verified; refused on mismatch)")
    ap.add_argument("--book", default="ula-proteus", help="registered book xml id")
    ap.add_argument("--map", type=Path, required=True,
                    help="pseudonym map CSV; every diagnostic and both "
                         "outputs pass its Mask (§2.3a)")
    ap.add_argument("--csv", type=Path, required=True)
    ap.add_argument("--md", type=Path, required=True)
    ap.add_argument("--work-dir", type=Path, required=True,
                    help="temp directory for the snippet cache (never under data/)")
    ap.add_argument("--scope", default="",
                    help="scope label copied to every row (e.g. S26, F25, both)")
    ap.add_argument("--extra-question-id", action="append", default=[],
                    help="logged question_id with no board at this pin; emitted "
                         "as a present-in-log/absent-from-source row")
    ap.add_argument("--db", type=Path, default=None,
                    help="optional stage database (SELECT-only): adds pooled "
                         "per-item attempt counts to the CSV")
    args = ap.parse_args()

    global _MASK
    _MASK = Mask(load_map(args.map))

    checkout = args.checkout.resolve()
    if not (checkout / ".git").exists():
        _die(f"--checkout is not a git checkout: {checkout}")
    head = _git(checkout, "rev-parse", "HEAD")
    if head != args.commit:
        _die(f"checkout HEAD {head} != expected commit {args.commit}; refusing")
    # The walk reads the working tree while blob_sha cites the commit; any
    # divergence would silently mislabel content. A temp clone is pristine.
    dirty = _git(checkout, "status", "--porcelain", "--ignored")
    if dirty:
        _die(f"checkout is not pristine ({len(dirty.splitlines())} "
             "modified/untracked/ignored entries per git status); refusing")
    if args.work_dir.resolve().is_relative_to(ROOT / "data"):
        _die("--work-dir must not be under data/ (task §2.1)")
    if args.work_dir.resolve().is_relative_to(checkout):
        _die("--work-dir must not be inside --checkout: the snippet cache "
             "would enter the source walk")

    # Collect the sync's own warnings (BUG-03 duplicates, empty <match>, …).
    warnings: list[str] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            warnings.append(_clean(record.getMessage()))

    handler = _Collect(level=logging.WARNING)
    watched = [logging.getLogger(name) for name in
               ("proteus.core.sources", "proteus.services.dragndrop.board")]
    for lg in watched:
        lg.addHandler(handler)
        # §2.3a: keep raw paths off stderr. propagate=False keeps records
        # from any root handler; the permanent NullHandler keeps
        # logging.lastResort from printing them once the collector is
        # detached (the second walk's warnings are by-design duplicates).
        lg.propagate = False
        lg.addHandler(logging.NullHandler())
    try:
        conn = db.connect(":memory:")
        db.bootstrap(conn)
        textbook_id = textbooks.add_textbook(
            conn,
            book_xml_id=args.book,
            git_repo_url=f"file://{checkout}",
            build_dir=str(checkout),
            git_ref=args.commit,
        )
        services = registry.available_services()
        result = sources.sync_question_sources(
            conn,
            source_dir=checkout,
            textbook_id=textbook_id,
            output_dir=args.work_dir / "question_sources",
            services=services,
        )
    finally:
        # _path_map re-walks the same files below; detaching here keeps each
        # sync warning in the report once, not twice.
        for lg in watched:
            lg.removeHandler(handler)

    claimed = frozenset().union(*(s.question_types for s in services))
    rel_by_xml_id = _path_map(checkout, claimed)

    board_rows = conn.execute(
        "SELECT q.question_id, q.xml_id, q.section_id, q.source_xml, "
        "b.source_hash, b.n_cards, b.n_zones, b.key_json "
        "FROM dnd_boards b JOIN questions q USING (question_id) "
        "ORDER BY q.question_id"
    ).fetchall()

    # Self-check: the sync's cardsort set must equal the path walk's.
    sync_ids = {r["xml_id"] for r in board_rows}
    walk_card_ids = {
        xml_id for xml_id in rel_by_xml_id
        if f"{args.book}__{xml_id}" in {r["question_id"] for r in board_rows}
    }
    if sync_ids != walk_card_ids:
        _die("path-walk cardsort ids disagree with the sync's dnd_boards set; "
             "script bug, fix the script, never the interpretation (task §3)")

    attempts: dict[str, int] = {}
    if args.db is not None:
        if not args.db.exists():
            _die(f"--db does not exist: {args.db} (SELECT-only; this script "
                 "never creates a database)")
        stage = db.connect(args.db)
        try:
            stage.execute("PRAGMA query_only = ON")
            attempts = {
                r["question_id"]: r["n"]
                for r in stage.execute(
                    "SELECT question_id, COUNT(*) AS n FROM dnd_attempts "
                    "GROUP BY question_id"
                )
            }
        finally:
            stage.close()

    rows_out: list[dict] = []
    for r in board_rows:
        spec = load_board(conn, r["question_id"])
        rel = rel_by_xml_id.get(r["xml_id"], "")
        blob = _git(checkout, "rev-parse", f"{args.commit}:{rel}") if rel else ""
        n_match, n_empty = _match_counts(r["source_xml"])
        cov = dnd_export._board_covariates(
            conn, {"n_cards": r["n_cards"], "n_zones": r["n_zones"],
                   "key_json": r["key_json"]},
        )
        rows_out.append({
            "question_id": r["question_id"],
            "xml_id": r["xml_id"],
            "section_id": r["section_id"],
            "source_path": rel,
            "source_commit": args.commit,
            "blob_sha": blob,
            "source_hash": r["source_hash"],
            "present_at_pin": "yes",
            "P": r["n_cards"],
            "R": r["n_zones"],
            "key_json": r["key_json"],
            "decoy_cards": json.dumps(list(spec.decoy_cards)),
            "decoy_zones": json.dumps(list(spec.decoy_zones)),
            "match_blocks": n_match,
            "empty_match_blocks": n_empty,
            **{k: cov[k] for k in (
                "num_slots", "state_space_size", "num_decoy_cards",
                "num_decoy_slots", "key_occupancy", "feedback_policy",
                "check_cost")},
            "scope": args.scope,
            "attempts": (attempts.get(r["question_id"], 0)
                         if args.db is not None else ""),
        })
    present = {r["question_id"] for r in board_rows}
    extra_ids = list(args.extra_question_id)
    for qid in KNOWN_LOGGED_ONLY.get(args.book, []):
        if qid not in extra_ids:
            extra_ids.append(qid)
    for qid in extra_ids:
        if qid in present:
            continue  # has a board at this pin, not a logged-only row here
        rows_out.append({
            **{c: "" for c in CSV_COLUMNS},
            "question_id": qid,
            "source_commit": args.commit,
            "present_at_pin": "no",
            "scope": args.scope,
            "attempts": (attempts.get(qid, 0)
                         if args.db is not None else ""),
        })
    rows_out.sort(key=lambda r: r["question_id"])

    csv_buf = io.StringIO()
    w = csv.DictWriter(csv_buf, fieldnames=CSV_COLUMNS)
    w.writeheader()
    w.writerows(rows_out)

    lines = [
        "# Item inventory, generated by inventory_items.py",
        f"# book: {args.book}; commit: {args.commit}; scope: {args.scope or '—'}",
        f"# cardsorts with a board: {sum(1 for r in rows_out if r['present_at_pin'] == 'yes')}; "
        f"logged-only rows: {sum(1 for r in rows_out if r['present_at_pin'] == 'no')}",
        f"# sync result: extracted={result.extracted} updated={result.updated} "
        f"unchanged={result.unchanged} skipped_files={len(result.skipped_files)} "
        f"handled={result.handled}",
        "",
        "| question_id | section | path | P | R | key | decoyC | decoyZ | match(empty) | hash[:12] |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows_out:
        if r["present_at_pin"] == "yes":
            lines.append(
                f"| {r['question_id']} | {r['section_id']} | {r['source_path']} "
                f"| {r['P']} | {r['R']} | {r['key_json']} | {r['decoy_cards']} "
                f"| {r['decoy_zones']} | {r['match_blocks']}({r['empty_match_blocks']}) "
                f"| {r['source_hash'][:12]} |"
            )
        else:
            lines.append(
                f"| {r['question_id']} | — | (absent from source at this pin; "
                f"present in the log) | — | — | — | — | — | — | — |"
            )
    if warnings:
        lines += ["", "## Walk warnings (masked)"] + [f"- {w}" for w in warnings]
    md_text = "\n".join(lines) + "\n"
    # §2.3a cleanliness gate: no mapped surname may reach either output.
    _MASK.assert_clean(csv_buf.getvalue())
    _MASK.assert_clean(md_text)
    args.csv.parent.mkdir(parents=True, exist_ok=True)
    args.csv.write_text(csv_buf.getvalue())
    args.md.parent.mkdir(parents=True, exist_ok=True)
    args.md.write_text(md_text)
    sys.stdout.write("\n".join(lines[:5]) + "\n")


def cli() -> int:
    """The launcher behind both ``proteus-inventory-items`` and ``python -m``:
    ``main()`` inside the §2.3a guard, path shapes always, mapped surnames
    once the map is loaded, so no traceback ever prints either (the PR #247
    review round)."""
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # never let a traceback print a real path (§2.3a)
        sys.stderr.write(
            f"inventory_items: {type(exc).__name__}: {_clean(str(exc))}\n"
            "(traceback suppressed, §2.3a masking)\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(cli())
