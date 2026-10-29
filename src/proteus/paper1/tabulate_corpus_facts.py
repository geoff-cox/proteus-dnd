"""Paper 1, task N2: corpus facts and RQ3 candidate-group sizes.

Reads the F20 modeling_table export plus the events table and prints the
tables for `paper1-corpus-facts-<date>.md`. Group *sizes* only, per D3.1's
freeze discipline: no process-feature outcomes are computed by group here.

Usage (after init/ingest --derive/sync-sources/reconstruct/score/analyze):
    python -m proteus.cli dnd export modeling_table -o /tmp/modeling_table.csv
    proteus-corpus-facts /tmp/modeling_table.csv data/proteus.db
"""

import argparse
import csv
import json
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict


def dist(values):
    values = sorted(values)
    if not values:
        return "n/a"
    return (
        f"min {values[0]} / median {statistics.median(values):g} / "
        f"max {values[-1]} (n={len(values)})"
    )


def main(csv_path, db_path):
    rows = list(csv.DictReader(open(csv_path)))
    conn = sqlite3.connect(db_path)

    print("## A. Export-level facts (events table)\n")
    for label, sql in [
        ("all events: rows, date range",
         "SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM events"),
        ("DnD events (dragNdrop*): rows, date range",
         "SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM events "
         "WHERE selection LIKE 'dragNdrop%'"),
        ("distinct students, all events",
         "SELECT COUNT(DISTINCT student_id) FROM events"),
        ("distinct students with DnD events",
         "SELECT COUNT(DISTINCT student_id) FROM events "
         "WHERE selection LIKE 'dragNdrop%'"),
        ("distinct DnD problems in the log",
         "SELECT COUNT(DISTINCT problem_name) FROM events "
         "WHERE selection LIKE 'dragNdrop%'"),
        ("course_id / class_id values",
         "SELECT GROUP_CONCAT(DISTINCT course_id), "
         "GROUP_CONCAT(DISTINCT class_id) FROM events"),
    ]:
        print(f"- {label}: {conn.execute(sql).fetchone()}")

    print("\nDnD activity by calendar month (check, move, and reset events):")
    for month, n in conn.execute(
        "SELECT substr(timestamp,1,7) m, COUNT(*) FROM events "
        "WHERE selection LIKE 'dragNdrop%' GROUP BY m ORDER BY m"
    ):
        print(f"  - {month}: {n}")

    print("\n## B. Attempt-level facts (F20 modeling_table)\n")
    print(f"- attempts: {len(rows)}")
    print(f"- students: {len({r['student_id'] for r in rows})}")
    print(f"- items with >=1 attempt: {len({r['question_id'] for r in rows})}")
    print(f"- feedback_policy values: {Counter(r['feedback_policy'] for r in rows)}")
    print(f"- readiness: {Counter(r['readiness'] for r in rows)}")
    print(f"- week_no coverage: {sorted({r['week_no'] for r in rows})}")
    print(f"- class_id values: {sorted({r['class_id'] for r in rows})}")

    per_student = Counter(r["student_id"] for r in rows)
    per_item = Counter(r["question_id"] for r in rows)
    print(f"- attempts per student: {dist(list(per_student.values()))}")
    print(f"- attempts per item:    {dist(list(per_item.values()))}")

    qc = Counter()
    for r in rows:
        for flag in json.loads(r["qc_flags_json"] or "[]"):
            qc[flag] += 1
    print(f"- QC flag frequencies (attempts carrying each): {dict(qc)}")

    def group_of(r):
        if int(r["n_checks"] or 0) == 0:
            return "no-checks"
        if r["first_check_correct"] == "1":
            return "direct"
        if r["eventual_correct"] == "1":
            return "revised"
        return "never-correct"

    print("\n## C. RQ3 candidate-group sizes (D3.1 leading contrast)\n")
    print("Group sizes only; no process features by group (freeze discipline).\n")
    for scope, keep in [
        ("all attempts", lambda r: True),
        ("ready only (primary readiness policy)",
         lambda r: r["readiness"] == "ready"),
        ("ready + limited (RQ4 knob 7 alternative)",
         lambda r: r["readiness"] in ("ready", "limited")),
    ]:
        sub = [r for r in rows if keep(r)]
        groups = defaultdict(list)
        for r in sub:
            groups[group_of(r)].append(r)
        print(f"### {scope} (n={len(sub)})")
        for g in ("direct", "revised", "never-correct", "no-checks"):
            rs = groups.get(g, [])
            print(
                f"- {g}: {len(rs)} attempts, "
                f"{len({r['student_id'] for r in rs})} students, "
                f"{len({r['question_id'] for r in rs})} items"
            )
        print()

    print("### Per-item arm sizes, ready-only, direct vs revised\n")
    arms = defaultdict(lambda: [0, 0])
    for r in rows:
        if r["readiness"] != "ready":
            continue
        g = group_of(r)
        if g == "direct":
            arms[r["question_id"]][0] += 1
        elif g == "revised":
            arms[r["question_id"]][1] += 1
    both5 = sum(1 for d, v in arms.values() if d >= 5 and v >= 5)
    both1 = sum(1 for d, v in arms.values() if d >= 1 and v >= 1)
    print(f"- items with >=1 attempt in BOTH arms: {both1}")
    print(f"- items with >=5 attempts in BOTH arms: {both5}")
    print("- per-item (direct, revised), descending by total:")
    for q, (d, v) in sorted(arms.items(), key=lambda kv: -sum(kv[1])):
        if d + v:
            print(f"  - {q}: ({d}, {v})")


def cli(argv=None) -> int:
    """The console entry point (``proteus-corpus-facts``): the modeling-table
    CSV and the database, in that order."""
    parser = argparse.ArgumentParser(
        prog="proteus-corpus-facts", description=__doc__.split("\n\n")[0],
    )
    parser.add_argument("modeling_table_csv", help="the `dnd export modeling_table` CSV")
    parser.add_argument("db", help="the database the export came from")
    args = parser.parse_args(argv)
    main(args.modeling_table_csv, args.db)
    return 0


if __name__ == "__main__":
    sys.exit(cli())
