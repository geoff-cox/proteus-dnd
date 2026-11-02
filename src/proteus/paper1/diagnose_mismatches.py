"""Paper 1, replay-mismatch diagnosis Task B: mechanism-signature counts.

Reads one stage database (``--db``) built by the task's preflight sequence
(init → ingest --derive → textbook add → sync-sources → dnd validate →
dnd reconstruct; never score/analyze) and prints, per the task table, the
counts behind the seven candidate mechanisms S1-S7 over the disagreeing
checks D (``dnd_configurations.is_check = 1 AND matches_input = 0``) and the
matching checks M, then an exclusive classification of D by the task's
precedence (S5 → S7 → S2 → S1(c) → S4 → S3 → S6 → unexplained) beside the raw
per-signature counts and the multi-signature overlap.

Discipline (task §1): counts of checks, attempts, rows and events by
*structural* class only. Nothing here reads ``dnd_events.feedback``,
``feedback_classification``, ``dnd_check_scores`` or any outcome column;
no per-item row is printed; offerings are named by pseudonym through
``raw_imports.file_hash`` (``file_path`` is never selected); no path, id or
timestamp value is printed. The one proportion printed is the S1 contrast
share the task table asks for (a QC agreement share, not an outcome).

Decoding reuses the pipeline's own code (``decode.decode_configuration``,
``reconstruct.determine_n_cards``, ``board.load_board``) so the decoded state
is exactly what ``_cross_check`` compared. The card distance d(s, t) is the
number of card positions on which two state vectors differ.

Usage:
    proteus-diagnose-mismatches \
        --db /tmp/<session>/stages/<stage>/stage.db --label <stage> \
        [--map data/private/offering-pseudonyms.csv] \
        [--raw-files Y00-B03-P11 Y00-B03-P13 Y00-B03-P21]   # S5 on the .tab files

``--db`` refuses the pipeline's default database and any path under the
repository's ``data/`` directory (task §1). The repository root is
``proteus.paper1._root.find_root()``: ``PROTEUS_ROOT`` if set, else the
nearest ancestor of the package (or of the working directory) holding
``pyproject.toml``.
"""

import argparse
import csv
import json
import re
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path



from proteus.paper1._root import find_root
from proteus.paper1.inventory_exports import _MaskedParser, _mask_paths, load_map
from proteus.services.dragndrop import decode
from proteus.services.dragndrop.board import load_board
from proteus.services.dragndrop.reconstruct import determine_n_cards

ROOT = find_root()  # the checkout (proteus.paper1._root)

DND_SELECTIONS = ("dragNdrop", "dragNdrop-drop", "dragNdrop-reset")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def card_distance(a, b):
    return sum(1 for x, y in zip(a, b) if x != y)


def placements(s):
    """The set of (card, zone) placements with zone != 0."""
    return {(j, z) for j, z in enumerate(s, start=1) if z != 0}


def seeded_state(start, moves_before, n_cards):
    """Replay `moves_before` (list of (kind, card, zone) in order; kind in
    move/takeback/reset) on top of `start` instead of s0."""
    s = list(start)
    for kind, card, zone in moves_before:
        if kind == "reset":
            s = [0] * n_cards
        elif card is not None and zone is not None and 1 <= card <= n_cards:
            s[card - 1] = zone
    return tuple(s)


def ts_second(ts):
    return (ts or "")[:19]


def seconds_between(a, b):
    from proteus.core.ingest.normalize import parse_timestamp
    try:
        return (parse_timestamp(b) - parse_timestamp(a)).total_seconds()
    except Exception:
        return None


def pct(n, d):
    return f"{(100.0 * n / d):.1f}%" if d else "—"


def fmt_hist(counter, lo=1):
    keys = sorted(counter)
    if not keys:
        return "{}"
    return "{" + ", ".join(f"{k}: {counter[k]}" for k in keys) + "}"


# --------------------------------------------------------------------------
# load
# --------------------------------------------------------------------------
def load_stage(conn, by_sha):
    conn.row_factory = sqlite3.Row
    offering_of_import = {}
    for r in conn.execute("SELECT import_id, file_hash FROM raw_imports"):
        offering_of_import[r["import_id"]] = by_sha.get(r["file_hash"], f"import{r['import_id']}")

    questions = [r[0] for r in conn.execute("SELECT DISTINCT question_id FROM dnd_attempts")]
    qinfo = {}
    for q in questions:
        board = load_board(conn, q)
        qinfo[q] = {
            "P": determine_n_cards(conn, q),
            "board": board,
            "R": board.n_zones if board else None,
            "decoy_cards": bool(board.decoy_cards) if board else None,
            "decoy_zones": bool(board.decoy_zones) if board else None,
        }

    attempts = {}
    for r in conn.execute(
        "SELECT attempt_id, student_id, question_id, attempt_index, segmentation_rule "
        "FROM dnd_attempts"
    ):
        attempts[r["attempt_id"]] = {
            "student": r["student_id"], "question": r["question_id"],
            "index": r["attempt_index"], "rule": (r["segmentation_rule"] or "").split(";")[0],
            "rows": [], "offering": None,
        }

    # configuration rows joined to their event (no feedback / outcome columns)
    sql = (
        "SELECT c.attempt_id, c.seq, c.event_id, c.s_json, c.is_check, c.matches_input, "
        "c.is_noop, d.kind, d.source_label, d.zone_label, d.configuration_json, d.parse_ok, "
        "d.parse_note, e.timestamp, e.session_id, e.import_id, e.week_no "
        "FROM dnd_configurations c "
        "LEFT JOIN dnd_events d ON d.event_id = c.event_id "
        "LEFT JOIN events e ON e.event_id = c.event_id "
        "ORDER BY c.attempt_id, c.seq"
    )
    for r in conn.execute(sql):
        a = attempts[r["attempt_id"]]
        q = a["question"]
        row = dict(r)
        row["s"] = tuple(json.loads(r["s_json"]))
        row["card"] = row["zone"] = None
        if r["kind"] in ("move", "takeback") and r["parse_ok"]:
            c = decode.parse_label(r["source_label"] or "", q)
            z = decode.parse_label(r["zone_label"] or "", q)
            if c and c.kind == "drag":
                row["card"] = c.index
            if z and z.kind in ("drop", "dragzone"):
                row["zone"] = z.index
        if r["is_check"]:
            row["decoded"] = None
            row["unknown"] = row["dups"] = ()
            row["keys"] = None
            if r["parse_ok"] and r["configuration_json"]:
                try:
                    cfg = json.loads(r["configuration_json"])
                except json.JSONDecodeError:
                    cfg = None
                if isinstance(cfg, dict):
                    dec = decode.decode_configuration(cfg, q, qinfo[q]["P"])
                    row["decoded"] = dec.s
                    row["unknown"] = dec.unknown_labels
                    row["dups"] = dec.duplicate_cards
                    row["keys"] = list(cfg.keys())
                    row["empty_lists"] = sum(1 for v in cfg.values() if isinstance(v, list) and not v)
        a["rows"].append(row)
        if a["offering"] is None and r["import_id"] is not None:
            a["offering"] = offering_of_import[r["import_id"]]

    # page-view times per student (any page), for the phantom-check test
    page_times = defaultdict(list)
    for r in conn.execute(
        "SELECT e.student_id, e.timestamp FROM dnd_events d JOIN events e USING (event_id) "
        "WHERE d.kind = 'page_view' AND e.timestamp IS NOT NULL ORDER BY e.timestamp"
    ):
        page_times[r["student_id"]].append(r["timestamp"])

    # move / check / reset rows per offering and per (offering, week)
    per_off = defaultdict(Counter)
    per_off_week = defaultdict(Counter)
    for r in conn.execute(
        "SELECT e.import_id, e.week_no, d.kind, d.parse_ok, d.parse_note FROM dnd_events d "
        "JOIN events e USING (event_id) WHERE d.kind != 'page_view'"
    ):
        off = offering_of_import[r["import_id"]]
        per_off[off][r["kind"]] += 1
        if not r["parse_ok"]:
            per_off[off]["parse_ok=0"] += 1
        if r["parse_note"]:
            per_off[off]["parse_note set"] += 1
        per_off_week[(off, r["week_no"])][r["kind"]] += 1

    first_move_day = {}
    for r in conn.execute(
        "SELECT e.import_id, MIN(substr(e.timestamp,1,10)) AS d0, MAX(substr(e.timestamp,1,10)) AS d1 "
        "FROM dnd_events d JOIN events e USING (event_id) WHERE d.kind IN ('move','takeback') "
        "GROUP BY e.import_id"
    ):
        first_move_day[offering_of_import[r["import_id"]]] = (r["d0"], r["d1"])
    span_day = {}
    for r in conn.execute(
        "SELECT e.import_id, MIN(substr(e.timestamp,1,10)) AS d0, MAX(substr(e.timestamp,1,10)) AS d1 "
        "FROM dnd_events d JOIN events e USING (event_id) WHERE d.kind != 'page_view' "
        "GROUP BY e.import_id"
    ):
        span_day[offering_of_import[r["import_id"]]] = (r["d0"], r["d1"])

    return qinfo, attempts, page_times, per_off, per_off_week, first_move_day, span_day


# --------------------------------------------------------------------------
# per-check feature extraction
# --------------------------------------------------------------------------
def analyze(qinfo, attempts, page_times, raw_dup_keys):
    """Return a list of check dicts with all signature memberships."""
    # index attempts by (student, question) for prior-attempt lookup
    by_sq = defaultdict(list)
    for aid, a in attempts.items():
        by_sq[(a["student"], a["question"])].append(aid)
    for k in by_sq:
        by_sq[k].sort(key=lambda aid: attempts[aid]["index"])

    checks = []
    for aid, a in attempts.items():
        q = a["question"]
        P = qinfo[q]["P"]
        rows = a["rows"]
        idx = by_sq[(a["student"], q)].index(aid)
        prior = attempts[by_sq[(a["student"], q)][idx - 1]] if idx > 0 else None
        prior_last_checked = prior_last_replayed = None
        prior_placed_replayed = prior_placed_checked = None
        if prior is not None:
            pr = prior["rows"]
            prior_last_replayed = pr[-1]["s"]
            prior_placed_replayed = sum(1 for z in prior_last_replayed if z != 0)
            pchecks = [r for r in pr if r["is_check"] and r.get("decoded") is not None]
            if pchecks:
                prior_last_checked = pchecks[-1]["decoded"]
                prior_placed_checked = sum(1 for z in prior_last_checked if z != 0)

        # session interleaving within the attempt
        sessions = [r["session_id"] for r in rows if r["event_id"] is not None]
        distinct_sessions = len(set(sessions))
        interleaved = False
        seen, last = set(), None
        for s in sessions:
            if s != last and s in seen:
                interleaved = True
            seen.add(s); last = s
        has_reset = any(r["kind"] == "reset" for r in rows)

        # attempt-level raw-collision exposure (S5): any event key of the attempt in the dup set
        attempt_collides = any(
            (a["student"], q, r["timestamp"]) in raw_dup_keys for r in rows if r["event_id"]
        )

        first_check_seen = False
        moves_before = []          # (kind, card, zone) incl. noops and resets, in order
        moved_since_reset = []     # card indices moved since last reset (ordered)
        reset_before = False
        for i, r in enumerate(rows):
            if r["kind"] in ("move", "takeback"):
                moves_before.append((r["kind"], r["card"], r["zone"]))
                if r["card"] is not None:
                    moved_since_reset.append(r["card"])
            elif r["kind"] == "reset":
                moves_before.append(("reset", None, None))
                moved_since_reset = []
                reset_before = True
            if not r["is_check"]:
                continue
            rep = r["s"]
            dec = r.get("decoded")
            c = {
                "attempt": aid, "offering": a["offering"], "question": q, "P": P,
                "rule": a["rule"], "has_prior": prior is not None,
                "first_check": not first_check_seen, "in_D": r["matches_input"] == 0,
                "in_M": r["matches_input"] == 1, "comparable": r["matches_input"] is not None,
                "rep_is_s0": all(z == 0 for z in rep), "dist": None,
                "n_moves_before": sum(1 for m in moves_before if m[0] != "reset"),
                "reset_before": reset_before, "has_reset": has_reset,
                "distinct_sessions": distinct_sessions, "interleaved": interleaved,
                "attempt_collides": attempt_collides,
                "unknown": bool(r.get("unknown")), "dups": bool(r.get("dups")),
                "keys": r.get("keys"), "empty_lists": r.get("empty_lists"),
                "decoy_cards": qinfo[q]["decoy_cards"], "decoy_zones": qinfo[q]["decoy_zones"],
                "R": qinfo[q]["R"],
                "prev_is_reset": i > 0 and rows[i - 1]["kind"] == "reset",
                "ts_same_adjacent_exact": False, "ts_same_adjacent_second": False,
                "s1c_checked": False, "s1c_replayed": False, "s1d_replayed": False, "s1d_checked": False,
                "s4_next1": False, "s4_next2": False,
                "s3_kind": None, "s3_card_class": None, "s3_swap2": False,
                "superset": None, "secs_since_page": None, "ts": r["timestamp"],
                "seed_chk": None, "seed_rep": None, "diff_comp": None,
            }
            first_check_seen = True
            if dec is not None:
                c["dist"] = card_distance(rep, dec)
                types = Counter()
                for k in range(P):
                    if rep[k] != dec[k]:
                        types["slot→slot" if (rep[k] and dec[k]) else ("replay placed / board tray" if rep[k] else "replay tray / board placed")] += 1
                if types:
                    c["diff_comp"] = ("pure " + next(iter(types))) if len(types) == 1 else "mixed"
                if prior is not None:
                    # Task C option (i) price: read-time simulation of a resumed attempt
                    # initialized at the prior attempt's last checked configuration
                    if prior_last_checked is not None:
                        c["seed_chk"] = seeded_state(prior_last_checked, moves_before, P) == dec
                    c["seed_rep"] = seeded_state(prior_last_replayed, moves_before, P) == dec
                pr_, pd_ = placements(rep), placements(dec)
                if pd_ > pr_:
                    c["superset"] = "decoded ⊇ replayed (placements missing from replay)"
                elif pr_ > pd_:
                    c["superset"] = "replayed ⊇ decoded (replay has extra placements)"
                elif pr_ == pd_:
                    c["superset"] = "equal placement sets"
                else:
                    c["superset"] = "neither"
                # S1(c)/(d)
                if prior is not None and c["first_check"]:
                    if prior_last_checked is not None:
                        c["s1c_checked"] = seeded_state(prior_last_checked, moves_before, P) == dec
                        c["s1d_checked"] = c["dist"] == prior_placed_checked
                    c["s1c_replayed"] = seeded_state(prior_last_replayed, moves_before, P) == dec
                    c["s1d_replayed"] = c["dist"] == prior_placed_replayed
                # S3
                if c["dist"] == 1:
                    j = next(k for k in range(P) if rep[k] != dec[k])
                    card = j + 1
                    if rep[j] != 0 and dec[j] != 0:
                        c["s3_kind"] = "slot→slot (both placed, different slots)"
                    elif rep[j] != 0:
                        c["s3_kind"] = "replay placed / board tray"
                    else:
                        c["s3_kind"] = "replay tray / board placed"
                    if moved_since_reset and moved_since_reset[-1] == card:
                        c["s3_card_class"] = "last moved card"
                    elif card in moved_since_reset:
                        c["s3_card_class"] = "moved earlier (not last)"
                    else:
                        c["s3_card_class"] = "never moved in attempt"
                elif c["dist"] == 2:
                    js = [k for k in range(P) if rep[k] != dec[k]]
                    c["s3_swap2"] = rep[js[0]] == dec[js[1]] and rep[js[1]] == dec[js[0]] and rep[js[0]] != 0 and rep[js[1]] != 0
                # S4: next move(s) after the check
                nxt = [x for x in rows[i + 1:] if x["kind"] in ("move", "takeback")][:2]
                if nxt:
                    s1 = seeded_state(rep, [(nxt[0]["kind"], nxt[0]["card"], nxt[0]["zone"])], P)
                    c["s4_next1"] = s1 == dec
                    if len(nxt) > 1:
                        s2 = seeded_state(s1, [(nxt[1]["kind"], nxt[1]["card"], nxt[1]["zone"])], P)
                        c["s4_next2"] = s2 == dec and not c["s4_next1"]
            # timestamps shared with an adjacent move row
            for nb in (rows[i - 1] if i > 0 else None, rows[i + 1] if i + 1 < len(rows) else None):
                if nb is not None and nb["kind"] in ("move", "takeback") and nb["timestamp"] and r["timestamp"]:
                    if nb["timestamp"] == r["timestamp"]:
                        c["ts_same_adjacent_exact"] = True
                    if ts_second(nb["timestamp"]) == ts_second(r["timestamp"]):
                        c["ts_same_adjacent_second"] = True
            # phantom test: seconds since the student's latest page view before this check
            if r["timestamp"]:
                pts = [t for t in page_times.get(a["student"], ()) if t <= r["timestamp"]]
                if pts:
                    c["secs_since_page"] = seconds_between(pts[-1], r["timestamp"])
            checks.append(c)
    return checks


# --------------------------------------------------------------------------
# S5 on the raw files
# --------------------------------------------------------------------------
def raw_multiplicities(paths, map_root):
    """Per file: rows per 5-field identity key with multiplicity > 1 (task
    definition) and per 4-field key without Problem View (the addition).
    Returns (per_file_counts, dup4_keys) where dup4_keys is the set of
    (Anon Student Id, Problem Name, Time) for 4-field duplicates, used to mark
    attempts exposed to a potential collision."""
    out = {}
    dup_keys = set()
    for pseud, path in paths:
        c5, c4 = Counter(), Counter()
        path = Path(path)
        if not path.is_absolute():
            path = map_root / path  # the map's paths are relative to the checkout that holds the map
        if not path.is_file():
            sys.exit(f"raw file for {pseud} not found at the map's path (resolved against the map's repository root)")
        with open(path, newline="", encoding="utf-8", errors="replace") as fh:
            reader = csv.reader(fh, delimiter="\t")
            header = next(reader)
            ix = {name: header.index(name) for name in ("Anon Student Id", "Problem Name", "Time", "Selection", "Problem View")}
            for row in reader:
                k5 = tuple(row[ix[n]] for n in ("Anon Student Id", "Problem Name", "Time", "Selection", "Problem View"))
                c5[k5] += 1
                c4[k5[:4]] += 1
        d5 = {k: v for k, v in c5.items() if v > 1}
        d4 = {k: v for k, v in c4.items() if v > 1}
        d4_dnd = {k: v for k, v in d4.items() if k[3] in DND_SELECTIONS}
        out[pseud] = {
            "rows": sum(c5.values()),
            "5-field keys with multiplicity>1": len(d5), "excess rows (5-field)": sum(v - 1 for v in d5.values()),
            "4-field keys (no Problem View) with multiplicity>1": len(d4), "excess rows (4-field)": sum(v - 1 for v in d4.values()),
            "4-field DnD keys with multiplicity>1": len(d4_dnd),
        }
        for k in d4:
            dup_keys.add((k[0], k[1], k[2]))
    return out, dup_keys


ORDER = ["S5", "S7", "S2", "S1", "S4", "S3", "S6"]
ALT_ORDER = ["S5", "S7", "S1", "S2", "S4", "S3", "S6"]


def memberships_of(c):
    m = []
    if c["attempt_collides"]:
        m.append("S5")
    if c["unknown"] or c["dups"] or (c["keys"] is not None and any((lambda p: p is None or p.kind != "drop")(decode.parse_label(k, c["question"])) for k in c["keys"])):
        m.append("S7")
    if c["rep_is_s0"]:
        m.append("S2")
    if c["s1c_checked"] or c["s1c_replayed"]:
        m.append("S1")
    if c["s4_next1"]:
        m.append("S4")
    if c["dist"] == 1:
        m.append("S3")
    if c["interleaved"] or c["prev_is_reset"]:
        m.append("S6")
    return m


def classify(D, order, broaden_s1=False):
    excl = Counter()
    for c in D:
        m = memberships_of(c)
        if broaden_s1 and c["seed_chk"] and "S1" not in m:
            m.append("S1")
        excl[next((s for s in order if s in m), "unexplained")] += 1
    return ", ".join(f"{s}: {excl[s]}" for s in order + ["unexplained"])


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------
def report(label, qinfo, attempts, checks, per_off, per_off_week, first_move_day, span_day, raw_counts, fix01, split_date=None):
    D = [c for c in checks if c["in_D"]]
    M = [c for c in checks if c["in_M"]]
    NC = [c for c in checks if not c["comparable"]]
    offs = sorted({c["offering"] for c in checks})
    print(f"\n# Task B: stage {label}")
    print(f"checks={len(checks)}  D (matches_input=0)={len(D)}  M (=1)={len(M)}  not comparable={len(NC)}  attempts={len(attempts)}  items={len(qinfo)}")
    print("D per offering: " + ", ".join(f"{o}: {sum(1 for c in D if c['offering']==o)}" for o in offs))
    print("M per offering: " + ", ".join(f"{o}: {sum(1 for c in M if c['offering']==o)}" for o in offs))

    # ---- S1
    print("\n## S1 restored state")
    fa = [c for c in checks if c["first_check"] and c["has_prior"]]
    fb = [c for c in checks if c["first_check"] and not c["has_prior"]]
    nfa = sum(1 for c in fa if c["in_D"]); nfb = sum(1 for c in fb if c["in_D"])
    print(f"(a) first checks of attempts WITH a prior attempt:    n={len(fa)}  n in D={nfa}  share={pct(nfa, len(fa))}")
    print(f"(b) first checks of attempts WITHOUT a prior attempt: n={len(fb)}  n in D={nfb}  share={pct(nfb, len(fb))}")
    nonfirst = [c for c in checks if not c["first_check"]]
    print(f"    (context) non-first checks: n={len(nonfirst)}  n in D={sum(1 for c in nonfirst if c['in_D'])}  share={pct(sum(1 for c in nonfirst if c['in_D']), len(nonfirst))}")
    for o in offs:
        fa_o = [c for c in fa if c["offering"] == o]; fb_o = [c for c in fb if c["offering"] == o]
        print(f"    {o}: (a) n={len(fa_o)} in D={sum(1 for c in fa_o if c['in_D'])}   (b) n={len(fb_o)} in D={sum(1 for c in fb_o if c['in_D'])}")
    faD = [c for c in fa if c["in_D"]]
    both = sum(1 for c in faD if c["s1c_checked"] and c["s1c_replayed"])
    only_chk = sum(1 for c in faD if c["s1c_checked"] and not c["s1c_replayed"])
    only_rep = sum(1 for c in faD if c["s1c_replayed"] and not c["s1c_checked"])
    neither = sum(1 for c in faD if not c["s1c_checked"] and not c["s1c_replayed"])
    print(f"(c) among (a)∩D (n={len(faD)}): decoded = prior last CHECKED config + this attempt's moves: {only_chk + both}"
          f" (of which also = prior last REPLAYED + moves: {both}); = prior last REPLAYED + moves only: {only_rep}; neither: {neither}")
    print(f"    (a)∩D first checks with zero moves before the check: {sum(1 for c in faD if c['n_moves_before']==0)}; with ≥1 move: {sum(1 for c in faD if c['n_moves_before']>0)}")
    print(f"(d) among (a)∩D: d(replayed, decoded) = cards placed at end of prior attempt (last replayed state): {sum(1 for c in faD if c['s1d_replayed'])}; = cards placed in prior last checked config: {sum(1 for c in faD if c['s1d_checked'])}")
    rules = Counter(c["rule"] for c in faD)
    print(f"    (a)∩D by boundary rule that opened the attempt: {dict(rules)}")
    # phantom-check addition
    print("    [addition] phantom-check signature, first checks in D with replayed = s0 (no move before the check), by seconds since the student's latest page-view row:")
    buckets = [("≤2s", 2), ("≤5s", 5), ("≤30s", 30), ("≤300s", 300), (">300s", float("inf"))]
    for grp_name, grp in (("with prior attempt", faD), ("without prior attempt", [c for c in fb if c["in_D"]])):
        sub = [c for c in grp if c["rep_is_s0"]]
        bc = Counter()
        for c in sub:
            s = c["secs_since_page"]
            if s is None:
                bc["no page view before"] += 1
            else:
                for name, lim in buckets:
                    if s <= lim:
                        bc[name] += 1; break
        print(f"      {grp_name}: n={len(sub)}  " + ", ".join(f"{k}: {bc[k]}" for k in [b[0] for b in buckets] + ["no page view before"] if bc[k]))
    subM = [c for c in checks if c["first_check"] and c["in_M"] and c["rep_is_s0"]]
    print(f"      (context) first checks in M with replayed = s0 (empty board checked, payload empty): {len(subM)}")

    # ---- S2
    print("\n## S2 missing moves / term logging")
    for o in offs:
        k = per_off[o]
        print(f"    {o}: move rows={k['move']} takeback rows={k['takeback']} check rows={k['check']} reset rows={k['reset']}  parse_ok=0: {k['parse_ok=0']}  parse_note set: {k['parse_note set']}  DnD span (days) {span_day.get(o)}  move rows span (days) {first_move_day.get(o)}")
    print(f"    n in D with replayed = s0: {sum(1 for c in D if c['rep_is_s0'])}   (n in D with ≥1 move before the check: {sum(1 for c in D if c['n_moves_before']>0)})")
    dists = [c["dist"] for c in D if c["dist"] is not None]
    if dists:
        srt = sorted(dists)
        p90 = srt[min(len(srt) - 1, int(round(0.9 * len(srt))) - 1)] if len(srt) > 1 else srt[0]
        print(f"    d(replayed, decoded) over D: median={statistics.median(dists)}  p90={p90}  histogram={fmt_hist(Counter(dists))}")
    for o in offs:
        do = [c["dist"] for c in D if c["offering"] == o and c["dist"] is not None]
        if do:
            print(f"      {o}: median={statistics.median(do)}  histogram={fmt_hist(Counter(do))}")
    sup = Counter(c["superset"] for c in D)
    print(f"    placement-set relation over D: {dict(sup)}")
    print("    [addition] per (offering, CF Week No): move+takeback rows / check rows / checks in D / checks in M / checks in D with replayed = s0")
    wk = defaultdict(Counter)
    for aid, a in attempts.items():
        for r in a["rows"]:
            if r["is_check"]:
                key = (a["offering"], r["week_no"])
                if r["matches_input"] == 0:
                    wk[key]["D"] += 1
                    if all(z == 0 for z in r["s"]):
                        wk[key]["D_s0"] += 1
                elif r["matches_input"] == 1:
                    wk[key]["M"] += 1
    def wsort(w):
        try:
            return (0, int(w[1]))
        except (TypeError, ValueError):
            return (1, str(w[1]))
    for o in offs:
        keys = sorted({k for k in set(per_off_week) | set(wk) if k[0] == o}, key=wsort)
        cells = []
        for k in keys:
            m = per_off_week[k]["move"] + per_off_week[k]["takeback"]
            cells.append(f"w{k[1]}: {m}/{per_off_week[k]['check']}/{wk[k]['D']}/{wk[k]['M']}/{wk[k]['D_s0']}")
        print(f"      {o}: " + "  ".join(cells))

    # ---- S3
    print("\n## S3 single-move error")
    d1 = [c for c in D if c["dist"] == 1]
    print(f"    n in D with d = 1: {len(d1)}")
    print(f"      differing card: {dict(Counter(c['s3_card_class'] for c in d1))}")
    print(f"      kind: {dict(Counter(c['s3_kind'] for c in d1))}")
    print(f"    n in D with d = 2 and the two cards' slots exchanged (swap): {sum(1 for c in D if c['s3_swap2'])}")

    # ---- S4
    print("\n## S4 late or reordered row")
    print(f"    n in D where applying the NEXT move row after the check to the replayed state reproduces the decoded state: {sum(1 for c in D if c['s4_next1'])}")
    print(f"    [addition] … the next TWO move rows (and not the first alone): {sum(1 for c in D if c['s4_next2'])}")
    print(f"    n in D whose check row shares a timestamp with an adjacent move row, exact (microsecond): {sum(1 for c in D if c['ts_same_adjacent_exact'])}; same second: {sum(1 for c in D if c['ts_same_adjacent_second'])}")
    print(f"    (context) same-second adjacency among M: {sum(1 for c in M if c['ts_same_adjacent_second'])}")

    # ---- S5
    print("\n## S5 identity collision (raw .tab files)")
    if raw_counts:
        for pseud, k in raw_counts.items():
            print(f"    {pseud}: " + "; ".join(f"{a}={b}" for a, b in k.items()))
    else:
        print("    (no --raw-files given)")
    print(f"    FIX-01 intra-file collision warnings in the ingest logs: {fix01}")
    print(f"    checks in D whose attempt contains a 4-field duplicate key: {sum(1 for c in D if c['attempt_collides'])}")

    # ---- S6
    print("\n## S6 reset / multi-tab")
    print(f"    n in D whose attempt contains ≥2 Session Id values interleaved in time: {sum(1 for c in D if c['interleaved'])}  (attempts with ≥2 distinct Session Id at all: {sum(1 for a in attempts.values() if len({r['session_id'] for r in a['rows'] if r['event_id']})>1)})")
    print(f"    n in D immediately following a reset row: {sum(1 for c in D if c['prev_is_reset'])}")
    print(f"    n of resets per stage: {sum(per_off[o]['reset'] for o in offs)}  ({', '.join(f'{o}: {per_off[o][chr(114)+chr(101)+chr(115)+chr(101)+chr(116)]}' for o in offs)})")
    print(f"    [addition] n in D within an attempt containing a reset: {sum(1 for c in D if c['has_reset'])}; n in D with a reset earlier in the attempt: {sum(1 for c in D if c['reset_before'])}")

    # ---- S7
    print("\n## S7 decoding convention")
    for o in offs:
        shapes = Counter()
        for c in checks:
            if c["offering"] != o or c["keys"] is None:
                continue
            kinds = Counter()
            for k in c["keys"]:
                p = decode.parse_label(k, c["question"])
                kinds["drop" if (p and p.kind == "drop") else ("dragzone" if (p and p.kind == "dragzone") else "other")] += 1
            R = c["R"]
            rel = "n/a" if R is None else ("=R" if kinds["drop"] == R else ("<R" if kinds["drop"] < R else ">R"))
            shapes[f"drop keys {rel}; empty-list zones {'present' if c['empty_lists'] else 'none'}; dragzone key {'yes' if kinds['dragzone'] else 'no'}; other keys {kinds['other']}"] += 1
        print(f"    {o} top-level key shapes (checks): {dict(shapes)}")
    for o in offs:
        print(f"    {o}: checks with unknown labels (decode) = {sum(1 for c in checks if c['offering']==o and c['unknown'])}; duplicate cards = {sum(1 for c in checks if c['offering']==o and c['dups'])}")
    def iclass(c):
        if c["decoy_cards"] is None:
            return "no board"
        if c["decoy_cards"] and c["decoy_zones"]:
            return "decoy cards and decoy slots"
        if c["decoy_cards"]:
            return "decoy cards only"
        if c["decoy_zones"]:
            return "decoy slots only"
        return "no decoys"
    print(f"    D by item class: {dict(Counter(iclass(c) for c in D))}")
    print(f"    M by item class: {dict(Counter(iclass(c) for c in M))}")
    print(f"    items by class: {dict(Counter(('no board' if q['decoy_cards'] is None else ('decoy cards and decoy slots' if q['decoy_cards'] and q['decoy_zones'] else ('decoy cards only' if q['decoy_cards'] else ('decoy slots only' if q['decoy_zones'] else 'no decoys')))) for q in qinfo.values()))}")

    # ---- difference composition (addition) and Task C prices
    print("\n## [addition] composition of the replayed-vs-decoded difference over D")
    print(f"    D: {dict(Counter(c['diff_comp'] for c in D))}")
    print(f"    D with ≥1 move before the check (not S2): {dict(Counter(c['diff_comp'] for c in D if not c['rep_is_s0']))}")

    print("\n## Task C prices (counts; read-time simulation, no code change)")
    resumed = [c for c in checks if c["has_prior"]]
    res_first = [c for c in resumed if c["first_check"]]
    print(f"    resumed attempts (attempt with a prior attempt by the same student on the same item): {len({c['attempt'] for c in resumed})}; their first checks: n={len(res_first)}, in D={sum(1 for c in res_first if c['in_D'])}")
    print(f"    option (ii) cause class `resumed_first_check`: checks leaving the denominator = {len(res_first)} (of which in D {sum(1 for c in res_first if c['in_D'])}, in M {sum(1 for c in res_first if c['in_M'])}); all checks of resumed attempts = {len(resumed)} (in D {sum(1 for c in resumed if c['in_D'])})")
    for seed, label_ in (("seed_chk", "prior attempt's last CHECKED configuration"), ("seed_rep", "prior attempt's last REPLAYED state")):
        sim = [c for c in resumed if c[seed] is not None]
        d2m = sum(1 for c in sim if c["in_D"] and c[seed])
        m2d = sum(1 for c in sim if c["in_M"] and not c[seed])
        print(f"    option (i) replay rule, initialize resumed attempts at the {label_}: simulated checks={len(sim)}; D→M={d2m}; M→D={m2d}; net D after={len(D) - d2m + m2d}; attempts with any check flipping={len({c['attempt'] for c in sim if (c['in_D'] and c[seed]) or (c['in_M'] and not c[seed])})}")
        print(f"        first checks only: D→M={sum(1 for c in sim if c['first_check'] and c['in_D'] and c[seed])}, M→D={sum(1 for c in sim if c['first_check'] and c['in_M'] and not c[seed])}; later checks: D→M={sum(1 for c in sim if not c['first_check'] and c['in_D'] and c[seed])}, M→D={sum(1 for c in sim if not c['first_check'] and c['in_M'] and not c[seed])}")

    # ---- exclusive classification
    print("\n## Exclusive classification of D (precedence S5 → S7 → S2 → S1(c) → S4 → S3 → S6 → unexplained)")
    def memberships(c):
        m = []
        if c["attempt_collides"]:
            m.append("S5")
        if c["unknown"] or c["dups"] or (c["keys"] is not None and any((lambda p: p is None or p.kind != "drop")(decode.parse_label(k, c["question"])) for k in c["keys"])):
            m.append("S7")
        if c["rep_is_s0"]:
            m.append("S2")
        if c["s1c_checked"] or c["s1c_replayed"]:
            m.append("S1")
        if c["s4_next1"]:
            m.append("S4")
        if c["dist"] == 1:
            m.append("S3")
        if c["interleaved"] or c["prev_is_reset"]:
            m.append("S6")
        return m
    order = ["S5", "S7", "S2", "S1", "S4", "S3", "S6"]
    excl = Counter(); raw = Counter(); multi = 0; pair = Counter(); excl_off = defaultdict(Counter)
    alt_order = ["S5", "S7", "S1", "S2", "S4", "S3", "S6"]
    alt = Counter()
    for c in D:
        m = memberships(c)
        for s in m:
            raw[s] += 1
        if len(m) > 1:
            multi += 1
            pair[tuple(sorted(m, key=order.index))] += 1
        cls = next((s for s in order if s in m), "unexplained")
        excl[cls] += 1
        excl_off[c["offering"]][cls] += 1
        alt[next((s for s in alt_order if s in m), "unexplained")] += 1
    print("    raw per-signature membership over D: " + ", ".join(f"{s}: {raw[s]}" for s in order))
    print("    exclusive class (task precedence):   " + ", ".join(f"{s}: {excl[s]}" for s in order + ["unexplained"]))
    print("    [addition] exclusive class with S1 before S2: " + ", ".join(f"{s}: {alt[s]}" for s in alt_order + ["unexplained"]))
    print(f"    checks in D matching more than one signature: {multi}; combinations: {dict(pair)}")
    print(f"    [addition] S1 broadened to any check of a resumed attempt whose replay seeded at the prior last checked configuration equals the decoded state, S1 before S2: {classify(D, ALT_ORDER, broaden_s1=True)}")
    print(f"    [addition] D checks whose seeded replay equals decoded, by task-precedence class: {dict(Counter(next((s_ for s_ in order if s_ in memberships_of(c)), 'unexplained') for c in D if c['seed_chk']))}")
    for o in offs:
        print(f"    {o}: " + ", ".join(f"{s}: {excl_off[o][s]}" for s in order + ["unexplained"]))
    unexpl = [c for c in D if not memberships(c)]
    if unexpl:
        print(f"    unexplained: n={len(unexpl)}  d histogram={fmt_hist(Counter(c['dist'] for c in unexpl if c['dist'] is not None))}  first-check={sum(1 for c in unexpl if c['first_check'])}  with prior attempt={sum(1 for c in unexpl if c['has_prior'])}  placement relation={dict(Counter(c['superset'] for c in unexpl))}")
        print(f"      moves before the check (histogram, capped at 10): {fmt_hist(Counter(min(c['n_moves_before'],10) for c in unexpl))}")
        print(f"      composition: {dict(Counter(c['diff_comp'] for c in unexpl))}")

    if split_date:
        print(f"\n## [addition] split at {split_date} (check timestamp < date = before; the first move row in every F25 offering falls on/after it)")
        before = [c for c in checks if (c["ts"] or "")[:10] < split_date]
        after = [c for c in checks if (c["ts"] or "")[:10] >= split_date]
        for name, grp in (("before", before), ("on/after", after)):
            gD = [c for c in grp if c["in_D"]]; gM = [c for c in grp if c["in_M"]]
            print(f"    {name}: checks={len(grp)} D={len(gD)} M={len(gM)}  per offering D/M: " + ", ".join(f"{o}: {sum(1 for c in gD if c['offering']==o)}/{sum(1 for c in gM if c['offering']==o)}" for o in offs))
            fa_ = [c for c in grp if c["first_check"] and c["has_prior"]]; fb_ = [c for c in grp if c["first_check"] and not c["has_prior"]]; nf_ = [c for c in grp if not c["first_check"]]
            print(f"      S1 contrast: (a) n={len(fa_)} in D={sum(1 for c in fa_ if c['in_D'])} share={pct(sum(1 for c in fa_ if c['in_D']), len(fa_))}; (b) n={len(fb_)} in D={sum(1 for c in fb_ if c['in_D'])} share={pct(sum(1 for c in fb_ if c['in_D']), len(fb_))}; non-first n={len(nf_)} in D={sum(1 for c in nf_ if c['in_D'])} share={pct(sum(1 for c in nf_ if c['in_D']), len(nf_))}")
            print(f"      exclusive (task precedence): {classify(gD, ORDER)}")
            print(f"      exclusive (S1 before S2):     {classify(gD, ALT_ORDER)}")
            print(f"      exclusive (S1 broadened, before S2): {classify(gD, ALT_ORDER, broaden_s1=True)}")
            print(f"      n in D with replayed = s0: {sum(1 for c in gD if c['rep_is_s0'])}; d=1: {sum(1 for c in gD if c['dist']==1)}; composition: {dict(Counter(c['diff_comp'] for c in gD))}")
            sim = [c for c in grp if c["has_prior"] and c["seed_chk"] is not None]
            print(f"      option (i) price in this window: D→M={sum(1 for c in sim if c['in_D'] and c['seed_chk'])}, M→D={sum(1 for c in sim if c['in_M'] and not c['seed_chk'])}; option (ii): resumed first checks={sum(1 for c in grp if c['has_prior'] and c['first_check'])} (in D {sum(1 for c in grp if c['has_prior'] and c['first_check'] and c['in_D'])})")
        def a_start(a):
            ts = [r["timestamp"] for r in a["rows"] if r["event_id"] and r["timestamp"]]
            return min(ts)[:10] if ts else ""
        ab = [a for a in attempts.values() if a_start(a) < split_date]; aa = [a for a in attempts.values() if a_start(a) >= split_date]
        for name, grp in (("before", ab), ("on/after", aa)):
            print(f"    attempts starting {name}: {len(grp)}; students: {len({a['student'] for a in grp})}; items: {len({a['question'] for a in grp})}; attempts with ≥1 check: {sum(1 for a in grp if any(r['is_check'] for r in a['rows']))}")
            for o in offs:
                g = [a for a in grp if a["offering"] == o]
                stu = len({a["student"] for a in g})
                print(f"      {o}: attempts={len(g)} students={stu if stu >= 5 else '<5'} items={len({a['question'] for a in g})}")


def main():
    ap = _MaskedParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--label", default="stage")
    ap.add_argument("--map", type=Path, default=ROOT / "data/private/offering-pseudonyms.csv")
    ap.add_argument("--raw-files", nargs="*", default=[], help="pseudonyms of the stage's .tab files (paths resolved from the map; never printed)")
    ap.add_argument("--fix01", type=int, default=0, help="FIX-01 collision warning count from the captured ingest logs")
    ap.add_argument("--split-date", default=None, help="YYYY-MM-DD; adds a before/after re-cut of every count (timestamp relation)")
    a = ap.parse_args()
    db = a.db.resolve()
    refused = (ROOT / "data").resolve() in db.parents or db == (ROOT / "data").resolve()
    for parent in db.parents:
        # any checkout's data/ directory, not just this one's
        if parent.name == "data" and (parent.parent / "src" / "proteus").is_dir():
            refused = True
    try:
        from proteus.core.db import default_db_path
        refused = refused or db == Path(default_db_path()).resolve()
    except Exception:  # the helper is a convenience; the structural rule above stands alone
        pass
    if refused:
        sys.exit("refusing the default database / a database under data/ (task §1)")
    if not db.exists():
        sys.exit("database not found")
    rows = load_map(a.map)
    by_sha = {r["sha256"]: r["pseudonym"] for r in rows}
    by_pseud = {r["pseudonym"]: r for r in rows}
    raw_counts, dup_keys = {}, set()
    if a.raw_files:
        map_root = Path(a.map).resolve().parents[2]  # the map sits two levels below its repository root
        raw_counts, dup_keys = raw_multiplicities([(p, by_pseud[p]["path"]) for p in a.raw_files], map_root)
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    qinfo, attempts, page_times, per_off, per_off_week, first_move_day, span_day = load_stage(conn, by_sha)
    conn.close()
    checks = analyze(qinfo, attempts, page_times, dup_keys)
    report(a.label, qinfo, attempts, checks, per_off, per_off_week, first_move_day, span_day, raw_counts, a.fix01, a.split_date)


def cli() -> int:
    """The launcher behind both ``proteus-diagnose-mismatches`` and
    ``python -m``: ``main()`` inside the task §1 guard, so no traceback ever
    echoes a path or an identifier (the PR #247 review round)."""
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # never echo a path or an identifier in a traceback (task §1)
        sys.stderr.write(f"{type(exc).__name__}: {_mask_paths(str(exc))}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(cli())
