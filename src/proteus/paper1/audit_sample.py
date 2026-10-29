"""Paper 1, task N5.3: manual-audit sampling, blinded packets, and scoring.

Companion to ``docs/dnd/planning/paper1-manual-audit-protocol.md``,
which is the authority for every rule implemented here (strata, floors,
blinding, scoring). The script exists so the draw and the scoring are
reproducible from the archived database rather than done by hand.

Two subcommands, both read-only against the database:

``draw``
    Assign every reconstructed attempt to a stratum, draw the seeded sample
    with the protocol's coverage floors, and write three things under the
    output directory: the *sealed* key (``sealed/``: which attempt each
    audit id is, its stratum, flags, and per-check pipeline states — never
    shown to reviewers before scoring), the *blinded* reviewer packets
    (``packets/``: positional rows only; check payloads, grades, key,
    student ids, and absolute times withheld), and the empty reviewer
    sheets. The output directory must lie under ``data/private/`` or outside
    the repository: packets are derived from student logs.

``score``
    Score completed reviewer sheets against the sealed key and the
    pipeline's stored states, and write the agreement tables (Wilson
    intervals), the anomaly-detection table, inter-reviewer agreement, and
    the disagreement log that adjudication fills in.

Usage::

    proteus-audit-sample draw \
        --db data/proteus.db --out data/private/audit-v1 --seed 20260907
    proteus-audit-sample score \
        --db data/proteus.db --out data/private/audit-v1 \
        --sheet A=data/private/audit-v1/sheet_A.csv \
        --sheet B=data/private/audit-v1/sheet_B.csv
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from proteus.core.ingest.normalize import parse_timestamp
from proteus.paper1._root import find_root
from proteus.services.dragndrop import decode, reconstruct
from proteus.services.dragndrop.board import load_board

ROOT = find_root()  # the checkout; guard_output_dir reads it (proteus.paper1._root)

SCRIPT_VERSION = "1.0"

# Strata, in assignment priority (protocol §3). An attempt belongs to the
# first stratum whose rule it satisfies.
STRATA = ("mismatch", "excluded", "limited", "agree")

# Reviewer anomaly codes (protocol §6) and the pipeline flag each one is the
# human counterpart of. ``unknown_event_type`` has no counterpart: it is never
# emitted (Appendix C). ``session_break`` is unreachable under the primary
# configuration, so it has none either.
ANOMALY_FOR_FLAG = {
    "parse_error": "MALFORMED",
    "invalid_state": "OFFBOARD",
    "noop_move": "NOOP",
    "duplicate_row": "DUP",
    "row_order_ambiguous": "TIE",
    "long_idle_gap": "GAP",
}
ANOMALY_CODES = tuple(ANOMALY_FOR_FLAG.values()) + ("OTHER",)

# The primary configuration the draw must be made under (D3.2 primary column).
PRIMARY_CONFIG = {
    "gap_seconds": 1800.0,
    "split_on_session_change": True,
    "split_on_page_view": True,
    "split_on_reset": False,
    "feedback_policy": "legacy",
    "idle_threshold_s": 300.0,
}

SHEET_COLUMNS = (
    "audit_id", "row_no", "target", "state", "anomalies", "judgment",
    "notes", "minutes",
)


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(2)


def open_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    """95% Wilson score interval for k successes in n trials."""
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (centre - half, centre + half)


def fmt_rate(k: int, n: int) -> str:
    if n == 0:
        return "n/a"
    lo, hi = wilson(k, n)
    return f"{k}/{n} = {k / n:.3f} [{lo:.3f}, {hi:.3f}]"


def cohen_kappa(pairs: list[tuple[bool, bool]]) -> float | None:
    """Cohen's kappa for two raters' binary judgments; None if undefined."""
    n = len(pairs)
    if n == 0:
        return None
    po = sum(1 for a, b in pairs if a == b) / n
    pa = sum(1 for a, _ in pairs if a) / n
    pb = sum(1 for _, b in pairs if b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    if pe == 1.0:
        return None
    return (po - pe) / (1 - pe)


# ---------------------------------------------------------------------------
# Reading the population
# ---------------------------------------------------------------------------


def latest_reconstruct_run(conn: sqlite3.Connection) -> tuple[dict, str]:
    """The newest reconstruct run of any status: a failed or partial run
    leaves per-question rewrites behind, so an older completed run must not
    vouch for the database's current state."""
    row = conn.execute(
        "SELECT config_json, status FROM pipeline_runs WHERE service = 'dnd' "
        "AND stage = 'reconstruct' ORDER BY run_id DESC LIMIT 1"
    ).fetchone()
    if row is None or not row["config_json"]:
        die("no dnd reconstruct run recorded in pipeline_runs")
    return json.loads(row["config_json"]), row["status"]


def assert_primary_config(config: dict, status: str, allow_nonprimary: bool) -> None:
    if status != "completed":
        die(f"the latest reconstruct run has status {status!r}; the database "
            "may be partially rewritten — re-run `dnd reconstruct` to completion")
    if config.get("question_id") is not None:
        msg = ("the latest reconstruct run was scoped to one question "
               f"({config['question_id']!r}); the audit needs a full-corpus run")
        if not allow_nonprimary:
            die(msg + " (pass --allow-nonprimary to override for a rehearsal)")
        print("warning: " + msg)
    drift = {
        k: (config.get(k), v) for k, v in PRIMARY_CONFIG.items()
        if config.get(k) != v
    }
    if drift and not allow_nonprimary:
        die(
            "the latest reconstruct run is not the primary configuration "
            f"(observed vs primary): {drift}; the audit sample is drawn under "
            "the primary configuration only (pass --allow-nonprimary to "
            "override for a rehearsal)"
        )
    if drift:
        print(f"warning: drawing under a non-primary configuration: {drift}")


def segmentation_config(config: dict) -> reconstruct.SegmentationConfig:
    return reconstruct.SegmentationConfig(
        gap_seconds=config["gap_seconds"],
        split_on_session_change=config["split_on_session_change"],
        split_on_page_view=config["split_on_page_view"],
        split_on_reset=config["split_on_reset"],
    )


def stratum_of(flags: set[str], readiness: str, n_mismatch: int = 0) -> str:
    """The protocol's §3 strata, first rule that applies. The `mismatch`
    stratum is entered through a stored disagreement on an eligible anchor
    (protocol §12, 2026-10-06): since migration dnd/0015 a check of a
    resumed attempt or one logged before its offering's first-move date
    stores a null comparison with its cause, and its disagreement feeds the
    attempt's `check_mismatch` flag only (the shadow comparison) — such an
    attempt is `limited` with no eligible anchor, §3's rule-3 case."""
    if n_mismatch > 0:
        return "mismatch"
    if readiness == "exclude":
        return "excluded"
    if readiness == "limited":
        return "limited"
    return "agree"


def load_offering_map(path: Path | None) -> tuple[dict[str, str], dict[str, str]]:
    """Optional CSV with ``pseudonym`` and ``sha256`` and/or ``path``
    columns (the export-inventory session's private map has this shape)."""
    by_hash: dict[str, str] = {}
    by_path: dict[str, str] = {}
    if path is None:
        return by_hash, by_path
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("sha256"):
                by_hash[row["sha256"]] = row["pseudonym"]
            if row.get("path"):
                by_path[Path(row["path"]).name] = row["pseudonym"]
    return by_hash, by_path


def offering_labels(conn: sqlite3.Connection, map_path: Path | None) -> dict[int, str]:
    by_hash, by_path = load_offering_map(map_path)
    labels: dict[int, str] = {}
    for row in conn.execute("SELECT import_id, file_path, file_hash FROM raw_imports"):
        label = by_hash.get(row["file_hash"]) or by_path.get(Path(row["file_path"]).name)
        labels[row["import_id"]] = label or f"import-{row['import_id']}"
    return labels


def read_population(conn: sqlite3.Connection, offerings: dict[int, str]) -> list[dict]:
    first_import = {
        r["attempt_id"]: r["import_id"]
        for r in conn.execute(
            "SELECT c.attempt_id, e.import_id FROM dnd_configurations c "
            "JOIN events e ON e.event_id = c.event_id WHERE c.seq = 1"
        )
    }
    checks_by_attempt: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for r in conn.execute(
        "SELECT attempt_id, seq, event_id, s_json, matches_input "
        "FROM dnd_configurations WHERE is_check = 1 ORDER BY attempt_id, seq"
    ):
        checks_by_attempt[r["attempt_id"]].append(r)

    population = []
    for a in conn.execute(
        "SELECT attempt_id, student_id, question_id, attempt_index, "
        "qc_flags_json, readiness FROM dnd_attempts ORDER BY attempt_id"
    ):
        flags = set(json.loads(a["qc_flags_json"] or "[]"))
        import_id = first_import.get(a["attempt_id"])
        if import_id is None:
            # Every row of the attempt failed to replay: fall back to the
            # learner's first logged row on the item.
            row = conn.execute(
                "SELECT import_id FROM events WHERE student_id = ? AND "
                "problem_name = ? ORDER BY timestamp LIMIT 1",
                (a["student_id"], a["question_id"]),
            ).fetchone()
            import_id = row["import_id"] if row else None
        checks = checks_by_attempt.get(a["attempt_id"], [])
        # Eligible anchors (protocol §7; manuscript Appendix B): a stored
        # comparison. Since migration dnd/0012 a non-eligible check stores
        # NULL with its cause (dnd_configurations.anchor_cause), and since
        # dnd/0015 so does a check excluded by a structural cause
        # (resumed_attempt, no_move_logging), so "compared" is exactly
        # "eligible" under the two-tier definition.
        n_eligible = sum(1 for c in checks if c["matches_input"] is not None)
        n_mismatch = sum(1 for c in checks if c["matches_input"] == 0)
        population.append({
            "attempt_id": a["attempt_id"],
            "student_id": a["student_id"],
            "question_id": a["question_id"],
            "attempt_index": a["attempt_index"],
            "import_id": import_id,
            "offering": offerings.get(import_id, f"import-{import_id}"),
            "readiness": a["readiness"],
            "flags": sorted(flags),
            "stratum": stratum_of(flags, a["readiness"], n_mismatch),
            "n_checks": len(checks),
            "n_eligible": n_eligible,
            "n_mismatch": n_mismatch,
        })
    return population


# ---------------------------------------------------------------------------
# Drawing the sample (protocol §4)
# ---------------------------------------------------------------------------


def draw_stratum(
    pool: list[dict], target: int, rng: random.Random, coverage: list
) -> list[dict]:
    """Seeded draw: coverage floors first (one attempt per distinct value of
    each coverage key present in the pool), then simple random fill to
    ``target``. Floors can exceed the target; a pool at or below the target
    is a census."""
    pool = list(pool)
    if len(pool) <= target:
        return pool
    rng.shuffle(pool)
    chosen: list[dict] = []
    chosen_ids: set[int] = set()
    for key in coverage:
        covered: set = set()
        for attempt in chosen:
            covered.update(key(attempt))
        for attempt in pool:
            values = set(key(attempt))
            if attempt["attempt_id"] in chosen_ids or not values - covered:
                continue
            chosen.append(attempt)
            chosen_ids.add(attempt["attempt_id"])
            covered.update(values)
    for attempt in pool:
        if len(chosen) >= target:
            break
        if attempt["attempt_id"] not in chosen_ids:
            chosen.append(attempt)
            chosen_ids.add(attempt["attempt_id"])
    return chosen


def draw_sample(population: list[dict], args, rng: random.Random) -> list[dict]:
    by_item = lambda a: [a["question_id"]]  # noqa: E731
    by_offering = lambda a: [a["offering"]]  # noqa: E731
    by_flag = lambda a: [f for f in a["flags"] if f in ANOMALY_FOR_FLAG]  # noqa: E731
    targets = {
        "mismatch": args.cap_mismatch,
        "excluded": args.cap_excluded,
        "limited": args.cap_limited,
        "agree": args.n_agree,
    }
    # Protocol §4: item, then offering, then (limited only) warning flag.
    coverage = {
        "mismatch": [by_item, by_offering],
        "excluded": [by_item, by_offering],
        "limited": [by_item, by_offering, by_flag],
        "agree": [by_item, by_offering],
    }
    sample: list[dict] = []
    for stratum in STRATA:
        pool = [a for a in population if a["stratum"] == stratum]
        chosen = draw_stratum(pool, targets[stratum], rng, coverage[stratum])
        for a in chosen:
            a = dict(a)
            a["double_review"] = stratum != "agree"
            sample.append(a)
    # Reviewer B also reads a seeded fraction of the agree stratum.
    agree = [a for a in sample if a["stratum"] == "agree"]
    n_double = math.ceil(args.double_agree_frac * len(agree))
    for a in rng.sample(agree, n_double) if agree else []:
        a["double_review"] = True
    # Audit ids must not reveal the stratum: shuffle before numbering.
    rng.shuffle(sample)
    width = max(3, len(str(len(sample))))
    for i, a in enumerate(sample, start=1):
        a["audit_id"] = f"A{i:0{width}d}"
    return sample


# ---------------------------------------------------------------------------
# Packets (protocol §5)
# ---------------------------------------------------------------------------


def attempt_rows(conn: sqlite3.Connection, attempt: dict, seg_config) -> list[sqlite3.Row]:
    """The attempt's logged rows, in replay order, by re-running the
    pipeline's own timeline and segmentation code (so skipped rows are
    included). Verified against the stored check events."""
    events = reconstruct._timeline(conn, attempt["question_id"], attempt["student_id"])
    pages = reconstruct._page_view_times(conn, attempt["student_id"])
    segments = reconstruct._segment(events, pages, seg_config)
    if attempt["attempt_index"] > len(segments):
        die(f"attempt {attempt['attempt_id']}: segmentation no longer yields "
            f"attempt_index {attempt['attempt_index']}; re-run reconstruct first")
    _, rows = segments[attempt["attempt_index"] - 1]
    stored = [
        r["event_id"] for r in conn.execute(
            "SELECT event_id FROM dnd_configurations WHERE attempt_id = ? "
            "AND is_check = 1 ORDER BY seq", (attempt["attempt_id"],)
        )
    ]
    listed = [r["event_id"] for r in rows if r["kind"] == "check"]
    if stored != listed:
        die(f"attempt {attempt['attempt_id']}: the stored checks do not match "
            "the re-derived segment; the database was reconstructed under a "
            "different configuration")
    return rows


def observable_flags(
    attempt: dict, rows: list[sqlite3.Row], n_cards: int, n_zones: int | None
) -> set[str]:
    """The attempt's flags whose evidence is visible in the blinded packet
    (protocol §7). A flag raised only by a withheld check payload — a
    malformed check, a payload placing one card in two zones or naming
    unknown labels, a repeated check — cannot be audited from the packet."""
    flags = set(attempt["flags"])
    seen: set[str] = set()
    q = attempt["question_id"]
    for r in rows:
        if r["kind"] in ("move", "takeback"):
            if not r["parse_ok"]:
                seen.add("parse_error")
                continue
            card = decode.parse_label(r["source_label"] or "", q)
            zone = decode.parse_label(r["zone_label"] or "", q)
            if (card is None or card.kind != "drag" or not 1 <= card.index <= n_cards
                    or zone is None or zone.kind == "drag"
                    or (zone.kind == "drop" and n_zones is not None
                        and not 1 <= zone.index <= n_zones)):
                seen.add("invalid_state")
    for prev, cur in zip(rows, rows[1:]):
        if (prev["kind"] in ("move", "takeback", "reset") and prev["kind"] == cur["kind"]
                and prev["timestamp"] == cur["timestamp"]
                and prev["source_label"] == cur["source_label"]
                and prev["zone_label"] == cur["zone_label"]):
            seen.add("duplicate_row")
    # These three are visible whenever raised: the replay, the offsets, and
    # the Problem View values are all in the packet.
    seen |= {"noop_move", "row_order_ambiguous", "long_idle_gap"}
    return {f for f in flags if f in ANOMALY_FOR_FLAG and f in seen}


def strip_prefix(label: str | None, question_id: str) -> str:
    if label is None:
        return ""
    prefix = question_id + "_"
    return label[len(prefix):] if label.startswith(prefix) else label


def render_packet(
    conn: sqlite3.Connection, attempt: dict, rows: list[sqlite3.Row],
    item_code: str, n_cards: int, n_zones: int | None,
) -> tuple[str, list[dict]]:
    """The blinded packet text and the sealed per-check rows."""
    t0 = None
    for r in rows:
        if r["timestamp"]:
            try:
                t0 = parse_timestamp(r["timestamp"])
                break
            except ValueError:
                continue
    lines = [
        f"# Audit packet {attempt['audit_id']}",
        "",
        f"Item {item_code}: P = {n_cards} cards, "
        f"R = {n_zones if n_zones is not None else 'unknown (no item specification synced)'} slots; "
        "the tray is zone 0 (`dragzone`).",
        "",
        "Rows are listed in the pipeline's replay order. `t` is seconds after "
        "the first row; `PV` is the export's Problem View value. Check payloads "
        "and grades are withheld: reconstruct the state at every CHECK row and "
        "at the end, then fill the reviewer sheet.",
        "",
        "| row | t (s) | PV | kind | action |",
        "|---|---|---|---|---|",
    ]
    sealed_checks: list[dict] = []
    check_seq = [
        r for r in conn.execute(
            "SELECT seq, event_id, s_json, matches_input FROM dnd_configurations "
            "WHERE attempt_id = ? AND is_check = 1 ORDER BY seq", (attempt["attempt_id"],)
        )
    ]
    check_ord = 0
    for no, r in enumerate(rows, start=1):
        offset = ""
        if r["timestamp"] and t0 is not None:
            try:
                offset = f"{(parse_timestamp(r['timestamp']) - t0).total_seconds():g}"
            except ValueError:
                offset = "?"
        kind = r["kind"]
        if kind in ("move", "takeback"):
            if r["parse_ok"]:
                action = (f"`{strip_prefix(r['source_label'], attempt['question_id'])} -> "
                          f"{strip_prefix(r['zone_label'], attempt['question_id'])}`")
            else:
                raw = conn.execute(
                    "SELECT action FROM events WHERE event_id = ?", (r["event_id"],)
                ).fetchone()["action"] or ""
                action = "`" + strip_prefix(raw, attempt["question_id"])[:120].replace("`", "'") + "` (verbatim)"
            label = "drop"
        elif kind == "reset":
            label, action = "RESET", ""
        elif kind == "check":
            label, action = "CHECK", "(payload withheld)"
            stored = check_seq[check_ord]
            check_ord += 1
            sealed_checks.append({
                "audit_id": attempt["audit_id"],
                "row_no": no,
                "event_id": r["event_id"],
                "seq": stored["seq"],
                "replayed_state": stored["s_json"],
                "matches_input": "" if stored["matches_input"] is None else stored["matches_input"],
                "parse_ok": r["parse_ok"],
            })
        else:
            label, action = kind, ""
        lines.append(f"| {no} | {offset} | {r['problem_view'] or ''} | {label} | {action} |")
    final = conn.execute(
        "SELECT s_json FROM dnd_configurations WHERE attempt_id = ? ORDER BY seq DESC LIMIT 1",
        (attempt["attempt_id"],),
    ).fetchone()["s_json"]
    sealed_checks.append({
        "audit_id": attempt["audit_id"], "row_no": "final", "event_id": "",
        "seq": "", "replayed_state": final, "matches_input": "", "parse_ok": "",
    })
    lines.append("")
    return "\n".join(lines) + "\n", sealed_checks


def write_csv(path: Path, rows: list[dict], columns: tuple[str, ...] | list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(columns), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def guard_output_dir(out: Path, allow_anywhere: bool) -> None:
    out = out.resolve()
    inside_repo = out.is_relative_to(ROOT)
    under_private = out.is_relative_to(ROOT / "data" / "private")
    if inside_repo and not under_private and not allow_anywhere:
        die(f"{out} is inside the repository but not under data/private/; "
            "packets derive from student logs and must not be committable")


def cmd_draw(args) -> None:
    guard_output_dir(args.out, args.allow_anywhere)
    conn = open_ro(args.db)
    config, status = latest_reconstruct_run(conn)
    assert_primary_config(config, status, args.allow_nonprimary)
    seg_config = segmentation_config(config)
    offerings = offering_labels(conn, args.offering_map)
    population = read_population(conn, offerings)
    if not population:
        die("no attempts in dnd_attempts")
    rng = random.Random(args.seed)
    sample = draw_sample(population, args, rng)

    # Item codes: one per question in the sample, in seeded random order so
    # the code order reveals nothing about the book's structure.
    questions = sorted({a["question_id"] for a in sample})
    rng.shuffle(questions)
    item_codes = {q: f"I{i:02d}" for i, q in enumerate(questions, start=1)}

    out = args.out
    (out / "sealed").mkdir(parents=True, exist_ok=True)
    (out / "packets").mkdir(exist_ok=True)
    items_rows, sealed_checks, sheet_rows = [], [], []
    combined = []
    for q in questions:
        board = load_board(conn, q)
        n_cards = board.n_cards if board else reconstruct.determine_n_cards(conn, q)
        items_rows.append({
            "item_code": item_codes[q], "question_id": q,
            "n_cards": n_cards, "n_zones": board.n_zones if board else "",
            "key_json": json.dumps(list(board.key)) if board else "",
        })
    for attempt in sorted(sample, key=lambda a: a["audit_id"]):
        rows = attempt_rows(conn, attempt, seg_config)
        item = next(i for i in items_rows if i["question_id"] == attempt["question_id"])
        text, checks = render_packet(
            conn, attempt, rows, item["item_code"], item["n_cards"],
            item["n_zones"] if item["n_zones"] != "" else None,
        )
        (out / "packets" / f"{attempt['audit_id']}.md").write_text(text, encoding="utf-8")
        combined.append(text)
        sealed_checks.extend(checks)
        for c in checks:
            sheet_rows.append({
                "audit_id": attempt["audit_id"], "row_no": c["row_no"],
                "target": "check" if c["row_no"] != "final" else "final",
                "state": "", "anomalies": "", "judgment": "", "notes": "", "minutes": "",
            })
        attempt["item_code"] = item["item_code"]
        attempt["n_rows"] = len(rows)
        visible = observable_flags(
            attempt, rows, item["n_cards"],
            item["n_zones"] if item["n_zones"] != "" else None,
        )
        attempt["observable_flags"] = sorted(visible)
        attempt["hidden_flags"] = sorted(
            f for f in attempt["flags"] if f in ANOMALY_FOR_FLAG and f not in visible
        )
    (out / "packets-all.md").write_text("\n".join(combined), encoding="utf-8")

    key_columns = (
        "audit_id", "stratum", "double_review", "item_code", "question_id",
        "attempt_id", "student_id", "attempt_index", "import_id", "offering",
        "readiness", "flags", "observable_flags", "hidden_flags",
        "n_checks", "n_eligible", "n_mismatch", "n_rows",
    )
    key_rows = [
        {**a, "flags": " ".join(a["flags"]),
         "observable_flags": " ".join(a["observable_flags"]),
         "hidden_flags": " ".join(a["hidden_flags"]),
         "double_review": int(a["double_review"])}
        for a in sorted(sample, key=lambda a: a["audit_id"])
    ]
    write_csv(out / "sealed" / "sample_key.csv", key_rows, key_columns)
    write_csv(out / "sealed" / "checks.csv", sealed_checks,
              ("audit_id", "row_no", "event_id", "seq", "replayed_state", "matches_input", "parse_ok"))
    write_csv(out / "sealed" / "items.csv", items_rows,
              ("item_code", "question_id", "n_cards", "n_zones", "key_json"))
    write_csv(out / "sheet_A_template.csv", sheet_rows, SHEET_COLUMNS)
    double = {a["audit_id"] for a in sample if a["double_review"]}
    write_csv(out / "sheet_B_template.csv",
              [r for r in sheet_rows if r["audit_id"] in double], SHEET_COLUMNS)

    pop_counts = Counter(a["stratum"] for a in population)
    sample_counts = Counter(a["stratum"] for a in sample)
    manifest = {
        "script_version": SCRIPT_VERSION,
        "drawn_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "db": str(args.db), "db_sha256": sha256_file(args.db),
        "reconstruct_config": config,
        "seed": args.seed,
        "params": {
            "n_agree": args.n_agree, "cap_mismatch": args.cap_mismatch,
            "cap_excluded": args.cap_excluded, "cap_limited": args.cap_limited,
            "double_agree_frac": args.double_agree_frac,
        },
        "population": {s: pop_counts.get(s, 0) for s in STRATA},
        "sample": {s: sample_counts.get(s, 0) for s in STRATA},
        "double_review": len(double),
        "items_in_sample": len(questions),
        "offerings_in_sample": len({a["offering"] for a in sample}),
    }
    (out / "sealed" / "draw_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print("population by stratum:", dict(manifest["population"]))
    print("sample by stratum:    ", dict(manifest["sample"]))
    print(f"double-reviewed: {len(double)} · items: {len(questions)} · "
          f"offerings: {manifest['offerings_in_sample']}")
    print(f"written under {out}")


# ---------------------------------------------------------------------------
# Scoring (protocol §7)
# ---------------------------------------------------------------------------


def parse_state(text: str) -> tuple[int, ...] | None:
    text = (text or "").strip().strip("()[]")
    if not text:
        return None
    try:
        return tuple(int(x) for x in text.replace(";", ",").split(","))
    except ValueError:
        return None


def parse_codes(text: str) -> set[str]:
    return {c.strip().upper() for c in (text or "").replace(",", ";").split(";") if c.strip()}


def read_sheet(path: Path) -> dict[tuple[str, str], dict]:
    """A reviewer sheet keyed by (audit id, row number).

    A sheet holds exactly the template's rows, one per target (protocol §6;
    the handbook's §3), so a repeated key is refused — one line per duplicate,
    then exit status 2 — rather than letting the later row silently replace
    the earlier one before the comparison (owner entry O-146, option (a),
    2026-10-15; the protocol's 2026-10-15 "Sheet reader" amendment line).
    ``check`` and ``score`` both read sheets through here.
    """
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    missing = set(SHEET_COLUMNS) - set(rows[0].keys() if rows else SHEET_COLUMNS)
    if missing:
        die(f"{path}: sheet is missing columns {sorted(missing)}")
    counts = Counter((r["audit_id"], str(r["row_no"])) for r in rows)
    duplicated = [(key, n) for key, n in counts.items() if n > 1]
    if duplicated:
        for (aid, row_no), n in duplicated:
            print(f"error: {path}: target {aid or '(blank)'} row {row_no or '(blank)'} appears "
                  f"{n} times; a sheet holds exactly the template's rows, one per target "
                  "(handbook §3)", file=sys.stderr)
        raise SystemExit(2)
    return {(r["audit_id"], str(r["row_no"])): r for r in rows}


def decoded_input(conn: sqlite3.Connection, event_id: str, question_id: str, n_cards: int):
    row = conn.execute(
        "SELECT configuration_json, parse_ok FROM dnd_events WHERE event_id = ?", (event_id,)
    ).fetchone()
    if row is None or not row["parse_ok"] or not row["configuration_json"]:
        return None
    try:
        configuration = json.loads(row["configuration_json"])
    except json.JSONDecodeError:
        return None
    return decode.decode_configuration(configuration, question_id, n_cards).s


def cmd_score(args) -> None:
    # The results and the disagreement log name audit ids, strata, and
    # states drawn from student logs: the same guard as ``draw``.
    guard_output_dir(args.out, args.allow_anywhere)
    sealed = args.out / "sealed"
    manifest = json.loads((sealed / "draw_manifest.json").read_text(encoding="utf-8"))
    db_hash = sha256_file(args.db)
    if db_hash != manifest["db_sha256"]:
        die("the database differs from the one this sample was drawn from "
            f"(manifest {manifest['db_sha256'][:12]}…, given {db_hash[:12]}…); "
            "human-anchor states are decoded from the database, so score "
            "against the archived draw database only")
    conn = open_ro(args.db)
    with open(sealed / "sample_key.csv", newline="", encoding="utf-8") as fh:
        key = {r["audit_id"]: r for r in csv.DictReader(fh)}
    with open(sealed / "checks.csv", newline="", encoding="utf-8") as fh:
        checks = list(csv.DictReader(fh))
    with open(sealed / "items.csv", newline="", encoding="utf-8") as fh:
        items = {r["question_id"]: r for r in csv.DictReader(fh)}
    sheets = {}
    for spec in args.sheet:
        name, _, path = spec.partition("=")
        if not path:
            die("--sheet takes NAME=PATH")
        sheets[name] = read_sheet(Path(path))
    if not sheets:
        die("at least one --sheet is required")

    # Per (audit, row) truth: pipeline replayed state and the decoded Input.
    truth = {}
    for c in checks:
        a = key[c["audit_id"]]
        n_cards = int(items[a["question_id"]]["n_cards"])
        decoded = None
        if c["row_no"] != "final":
            decoded = decoded_input(conn, c["event_id"], a["question_id"], n_cards)
        truth[(c["audit_id"], c["row_no"])] = {
            "replayed": tuple(json.loads(c["replayed_state"])),
            "decoded": decoded,
            # The sealed key's blank comparison marks a non-eligible check
            # (dnd/0012: stored NULL with its cause); see draw_population.
            "eligible": c["matches_input"] != "" and c["row_no"] != "final",
            "matches_input": c["matches_input"],
        }

    lines = ["# Manual-audit scoring", "",
             f"Scored {datetime.now(timezone.utc).isoformat(timespec='seconds')} "
             f"with audit_sample.py v{SCRIPT_VERSION}; sheets: {', '.join(sheets)}.", ""]
    disagreements = []

    def tally(rows_filter):
        """Agreement counts over the (audit, row) pairs passing the filter,
        per reviewer: replay agreement (all rows), anchor agreement
        (eligible checks), final-state agreement (final rows)."""
        out = {}
        for name, sheet in sheets.items():
            replay = [0, 0]; anchor = [0, 0]; final = [0, 0]; done = 0; blank = 0
            for (aid, row_no), t in truth.items():
                if not rows_filter(key[aid]):
                    continue
                entry = sheet.get((aid, row_no))
                if entry is None:
                    continue
                state = parse_state(entry["state"])
                if state is None:
                    blank += 1
                    continue
                done += 1
                target = final if row_no == "final" else replay
                target[1] += 1
                target[0] += state == t["replayed"]
                if t["eligible"]:
                    anchor[1] += 1
                    anchor[0] += state == t["decoded"]
            out[name] = {"rows": done, "blank": blank, "replay": replay,
                         "anchor": anchor, "final": final}
        return out

    def table(title, groups):
        lines.append(f"## {title}")
        lines.append("")
        lines.append("| Group | Reviewer | Targets scored | Not audited | Replay agreement (checks) | Anchor agreement (eligible checks) | Final-state agreement |")
        lines.append("|---|---|---|---|---|---|---|")
        for gname, flt in groups:
            for name, t in tally(flt).items():
                lines.append(
                    f"| {gname} | {name} | {t['rows']} | {t['blank']} | {fmt_rate(*t['replay'])} | "
                    f"{fmt_rate(*t['anchor'])} | {fmt_rate(*t['final'])} |"
                )
        lines.append("")

    table("Agreement by stratum",
          [("overall", lambda a: True)] + [(s, (lambda s: lambda a: a["stratum"] == s)(s)) for s in STRATA])
    item_codes = sorted({a["item_code"] for a in key.values()})
    table("Agreement by item",
          [(code, (lambda c: lambda a: a["item_code"] == c)(code)) for code in item_codes])
    offerings = sorted({a["offering"] for a in key.values()})
    if len(offerings) > 1:
        table("Agreement by offering",
              [(o, (lambda o: lambda a: a["offering"] == o)(o)) for o in offerings])

    # Mismatch attempts: does the human replay side with the pipeline (the
    # log lacks the move) or with the payload (the pipeline mis-replayed)?
    mismatch_ids = sorted(aid for aid, a in key.items() if a["stratum"] == "mismatch")
    if mismatch_ids:
        lines.append("## Mismatch attempts: which side the reviewer's replay supports")
        lines.append("")
        lines.append("| Audit id | Reviewer | Disagreeing checks scored | Agrees with pipeline | Agrees with payload | Agrees with neither |")
        lines.append("|---|---|---|---|---|---|")
        for aid in mismatch_ids:
            for name, sheet in sheets.items():
                scored = with_pipeline = with_payload = neither = 0
                for (a2, row_no), t in truth.items():
                    if a2 != aid or row_no == "final" or t["matches_input"] != "0":
                        continue
                    entry = sheet.get((aid, row_no))
                    state = parse_state(entry["state"]) if entry else None
                    if state is None:
                        continue
                    scored += 1
                    p_ok = state == t["replayed"]
                    d_ok = t["decoded"] is not None and state == t["decoded"]
                    with_pipeline += p_ok
                    with_payload += d_ok
                    neither += not (p_ok or d_ok)
                if scored:
                    lines.append(f"| {aid} | {name} | {scored} | {with_pipeline} | {with_payload} | {neither} |")
        lines.append("")

    # Anomaly detection: pipeline flag vs reviewer code, per attempt.
    lines.append("## Anomaly detection (reviewer codes vs pipeline flags)")
    lines.append("")
    lines.append("Sensitivity counts only flag instances whose evidence is in the "
                 "blinded packet (`observable_flags` in the sealed key); a flag raised "
                 "only by a withheld check payload is listed as not auditable, never "
                 "as a miss.")
    lines.append("")
    lines.append("| Flag | Code | Reviewer | Observable flagged attempts detected (sensitivity) | Flagged but not auditable from the packet | Unflagged attempts reported (false-alarm rate) |")
    lines.append("|---|---|---|---|---|---|")
    codes_by_reviewer: dict[str, dict[str, set[str]]] = {}
    audited_by_reviewer: dict[str, set[str]] = {}
    for name, sheet in sheets.items():
        codes_by_attempt: dict[str, set[str]] = defaultdict(set)
        seen_attempts: set[str] = set()
        for (aid, _), entry in sheet.items():
            if parse_state(entry["state"]) is not None or entry["anomalies"].strip():
                seen_attempts.add(aid)
            codes_by_attempt[aid] |= parse_codes(entry["anomalies"])
        codes_by_reviewer[name] = codes_by_attempt
        audited_by_reviewer[name] = seen_attempts
        for flag, code in ANOMALY_FOR_FLAG.items():
            hit = [0, 0]; false = [0, 0]; hidden = 0
            for aid in sorted(seen_attempts):
                flagged = flag in key[aid]["flags"].split()
                visible = flag in key[aid]["observable_flags"].split()
                reported = code in codes_by_attempt[aid]
                if flagged and not visible:
                    hidden += 1
                    continue
                target = hit if flagged else false
                target[1] += 1
                target[0] += reported
                if flagged != reported:
                    disagreements.append({
                        "audit_id": aid, "row_no": "final", "stratum": key[aid]["stratum"],
                        "kind": "reviewer_vs_flag", "reviewer": name,
                        "reviewer_codes": " ".join(sorted(codes_by_attempt[aid])),
                        "pipeline_flags": key[aid]["flags"],
                        "detail": f"{'missed' if flagged else 'false alarm'}: {flag}/{code}",
                        "resolution": "", "category": "",
                    })
            lines.append(f"| `{flag}` | {code} | {name} | {fmt_rate(*hit)} | {hidden} | {fmt_rate(*false)} |")
    lines.append("")

    # Inter-reviewer agreement on double-reviewed attempts.
    if len(sheets) >= 2:
        names = list(sheets)
        a_name, b_name = names[0], names[1]
        lines.append(f"## Inter-reviewer agreement ({a_name} vs {b_name}, double-reviewed attempts)")
        lines.append("")
        state_pairs = [0, 0]
        code_pairs: dict[str, list[tuple[bool, bool]]] = defaultdict(list)
        double_ids = sorted(
            aid for aid, a in key.items() if a["double_review"] == "1"
            and aid in audited_by_reviewer[a_name] and aid in audited_by_reviewer[b_name]
        )
        for aid in double_ids:
            ca = codes_by_reviewer[a_name][aid]
            cb = codes_by_reviewer[b_name][aid]
            for code in ANOMALY_CODES:
                code_pairs[code].append((code in ca, code in cb))
            if ca != cb:
                disagreements.append({
                    "audit_id": aid, "row_no": "final", "stratum": key[aid]["stratum"],
                    "kind": "reviewer_vs_reviewer_codes", "reviewer": f"{a_name}|{b_name}",
                    "reviewer_codes": f"{' '.join(sorted(ca))}|{' '.join(sorted(cb))}",
                    "pipeline_flags": key[aid]["flags"], "detail": "",
                    "resolution": "", "category": "",
                })
        for (aid, row_no), t in truth.items():
            if key[aid]["double_review"] != "1":
                continue
            ea, eb = sheets[a_name].get((aid, row_no)), sheets[b_name].get((aid, row_no))
            if ea is None or eb is None:
                continue
            sa, sb = parse_state(ea["state"]), parse_state(eb["state"])
            if sa is not None and sb is not None:
                state_pairs[1] += 1
                state_pairs[0] += sa == sb
                if sa != sb:
                    disagreements.append({
                        "audit_id": aid, "row_no": row_no, "stratum": key[aid]["stratum"],
                        "kind": "reviewer_vs_reviewer",
                        "reviewer": f"{a_name}|{b_name}",
                        "reviewer_state": f"{sa}|{sb}",
                        "pipeline_state": t["replayed"], "decoded_input": t["decoded"],
                        "matches_input": t["matches_input"], "resolution": "", "category": "",
                    })
        lines.append(f"- State agreement per scored target: {fmt_rate(*state_pairs)}")
        for code in ANOMALY_CODES:
            k = cohen_kappa(code_pairs[code])
            lines.append(f"- Code {code}: Cohen's kappa = {'undefined' if k is None else f'{k:.2f}'} "
                         f"over {len(code_pairs[code])} double-reviewed attempts")
        lines.append("")

    # Reviewer-vs-pipeline disagreements, every reviewer.
    for name, sheet in sheets.items():
        for (aid, row_no), t in truth.items():
            entry = sheet.get((aid, row_no))
            if entry is None:
                continue
            state = parse_state(entry["state"])
            if state is None or state == t["replayed"]:
                continue
            disagreements.append({
                "audit_id": aid, "row_no": row_no, "stratum": key[aid]["stratum"],
                "kind": "reviewer_vs_pipeline", "reviewer": name,
                "reviewer_state": state, "pipeline_state": t["replayed"],
                "decoded_input": t["decoded"], "matches_input": t["matches_input"],
                "resolution": "", "category": "",
            })
    write_csv(args.out / "disagreements.csv", disagreements,
              ("audit_id", "row_no", "stratum", "kind", "reviewer", "reviewer_state",
               "pipeline_state", "decoded_input", "matches_input", "reviewer_codes",
               "pipeline_flags", "detail", "resolution", "category"))
    lines.append(f"Disagreement log: {len(disagreements)} rows in `disagreements.csv` "
                 "(fill `resolution` and `category` at adjudication).")
    lines.append("")
    (args.out / "audit-results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


# ---------------------------------------------------------------------------


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    d = sub.add_parser("draw", help="draw the stratified sample and write packets")
    d.add_argument("--db", type=Path, required=True)
    d.add_argument("--out", type=Path, required=True)
    d.add_argument("--seed", type=int, default=20260907)
    d.add_argument("--n-agree", type=int, default=80)
    d.add_argument("--cap-mismatch", type=int, default=60)
    d.add_argument("--cap-excluded", type=int, default=40)
    d.add_argument("--cap-limited", type=int, default=40)
    d.add_argument("--double-agree-frac", type=float, default=0.25)
    d.add_argument("--offering-map", type=Path, default=None,
                   help="private CSV mapping export sha256/path to an offering pseudonym")
    d.add_argument("--allow-nonprimary", action="store_true")
    d.add_argument("--allow-anywhere", action="store_true",
                   help="lift the data/private guard (rehearsals on fixtures only)")
    d.set_defaults(func=cmd_draw)

    s = sub.add_parser("score", help="score completed reviewer sheets")
    s.add_argument("--db", type=Path, required=True)
    s.add_argument("--out", type=Path, required=True, help="the draw's output directory")
    s.add_argument("--sheet", action="append", default=[], metavar="NAME=PATH")
    s.add_argument("--allow-anywhere", action="store_true",
                   help="lift the data/private guard (rehearsals on fixtures only)")
    s.set_defaults(func=cmd_score)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
