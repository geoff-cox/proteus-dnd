"""Paper 1, export-inventory task A (§3): per-file inventory of DataShop exports.

Companion to tabulate_corpus_facts.py. Reads `.tab` exports directly, no
database *file*, through the pipeline's own reader
(proteus.core.ingest.readers.datashop.read), so column handling matches
ingest exactly, and cross-checks every file against the UI's
preview_upload() summary via an in-memory connection (task §3, B5
amendment). Denominators only (§2.2): counts, spans, cardinalities, and
identity facts; no outcome, nothing conditioned on Feedback
Classification, is computed, printed, or stored.

No real path, instructor surname, or identifier value is ever printed:
files are keyed by pseudonym and sha256 from the map
(data/private/offering-pseudonyms.csv, gitignored), every emitted line
passes through a masking filter, and the report is refused if a masked
token survives (§2.3a).

Parse rules cited (task §3, unusable-row categories):
  - the core reader skips nothing: datashop.py read() yields every data row
    ("Does not skip rows: faithful capture ... each service decides
    downstream"), so "rows the reader would skip" is 0 for every file;
  - required columns: datashop.py _REQUIRED_COLUMNS = {Time, Anon Student
    Id, Problem Name, Selection, Problem View}; a file missing one raises
    ValueError and will not ingest;
  - timestamp parse: proteus.core.ingest.normalize.parse_timestamp (ISO
    8601; "%Y-%m-%d %H:%M:%S[.%f]" as UTC; long-form calendar dates); the
    DnD derive is defensive about failures (process.py _parse_ts -> None);
  - known Selection vocabulary: the services' claimed values
    (dragndrop/service.py: dragNdrop, dragNdrop-drop, dragNdrop-reset;
    shortanswer/service.py: shortanswer) plus the shared "page"
    (dragndrop/derive.py _PAGE_SELECTION).

Usage:
    proteus-inventory-exports \
        --out /tmp/inventory.md [--dir data/private] \
        [--map data/private/offering-pseudonyms.csv] \
        [--reconcile PATH] [FILE ...]
"""

import argparse
import csv
import hashlib
import re
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from proteus.core.ingest.normalize import parse_timestamp
from proteus.core.ingest.readers.datashop import (
    _REQUIRED_COLUMNS, _row_to_event, read)
from proteus.paper1._root import find_root

ROOT = find_root()  # the checkout (proteus.paper1._root)

# The five identity columns plus the ten §3 optional columns and CF (Code)
# (B4 amendment). Anything else a header carries is reported as "extra".
OPTIONAL_COLUMNS = [
    "Session Id", "Action", "Input", "Feedback Text", "Feedback Classification",
    "CF (Institution)", "Class", "CF (Week No)", "Level (Chapter)",
    "Level (SubChapter)", "CF (Code)",
]
# Claimed selections of both registered services plus the shared "page".
KNOWN_SELECTIONS = {"dragNdrop", "dragNdrop-drop", "dragNdrop-reset", "page", "shortanswer"}
# DnD classification uses exactly the three selections derive consumes
# (dragndrop/derive.py); tabulate_corpus_facts' LIKE 'dragNdrop%' is equivalent
# on any corpus with no other dragNdrop-* values, a file that carries one is
# flagged loudly below rather than silently folded in.
_DND_SELECTIONS = {"dragNdrop", "dragNdrop-drop", "dragNdrop-reset"}
# Metadata values are printed only on an EXACT match to a known template
# literal; everything else is withheld with a classification word only, so a
# real institution name containing "University"/"PROTEUS" can never print
# (D5 / §2.3, institution identity).
_TEMPLATE_LITERALS = frozenset({"university", "PROTEUS_Year2_ULA_Template"})
_TEMPLATED = re.compile(r"proteus|template|universit", re.I)


def _die(msg: str) -> None:
    sys.stderr.write(msg + "\n")
    raise SystemExit(2)


def _mask_paths(text: str) -> str:
    """Best-effort §2.3a redaction for any message that may echo a real path
    (argparse errors, I/O errors) before the pseudonym map is available."""
    text = re.sub(r"\S*data/private/\S*", "<masked-private-path>", text)
    text = re.sub(r"[A-Za-z0-9]{1,4}\d\d-[a-z]+-[a-z]+\.tab", "<masked-export>", text)
    # …and every remaining path-shaped token: §2.3a says every printed path,
    # so even a benign --out path is reduced rather than argued about.
    text = re.sub(r"(?:~|\.{1,2})?/[\w.@+~\-/]+", "<masked-path>", text)
    return text


class _MaskedParser(argparse.ArgumentParser):
    def error(self, message):  # argparse echoes argv values, mask them (§2.3a)
        self.exit(2, f"{self.prog}: error: {_mask_paths(message)}\n")


class Mask:
    """Reduce every real path/surname to its pseudonym before printing (§2.3a)."""

    def __init__(self, rows: list[dict]):
        self.by_relpath = {r["path"]: r["pseudonym"] for r in rows}
        self.by_sha = {r["sha256"]: r["pseudonym"] for r in rows}
        self.subs: list[tuple[re.Pattern, str]] = []
        self.checks: list[tuple[str, re.Pattern]] = []
        for r in rows:
            stem = Path(r["path"]).name
            self.subs.append((re.compile(re.escape(r["path"])), r["pseudonym"]))
            self.subs.append((re.compile(re.escape(stem)), r["pseudonym"]))
            if r["surname"]:
                pcode = re.search(r"P\d\d", r["pseudonym"]).group(0)
                if r["surname"] == "may":
                    # An English word: mask only in token-ish contexts, or the
                    # self-check would fire on ordinary prose.
                    pat = re.compile(r"(?<=[_\-/])may(?=[_\-.])")
                else:
                    # Substring, case-insensitive: surnames surface inside
                    # course-fork tokens in Problem Name (e.g. a book id built
                    # from an instructor's login), not just in filenames.
                    pat = re.compile(re.escape(r["surname"]), re.I)
                self.subs.append((pat, f"[{pcode}]"))
                self.checks.append((pcode, pat))

    def __call__(self, text: str) -> str:
        for pat, repl in self.subs:
            text = pat.sub(repl, text)
        return text

    def assert_clean(self, text: str) -> None:
        for pseud, pat in self.checks:
            if pat.search(text):
                _die(f"masking self-check failed near {pseud}; report not written")

    def name(self, path: Path) -> str:
        rel = str(path.resolve().relative_to(ROOT)) if path.resolve().is_relative_to(ROOT) else str(path)
        return self.by_relpath.get(rel, f"sha256:{sha256_file(path)[:12]}")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_map(map_path: Path) -> list[dict]:
    with open(map_path, newline="") as f:
        return list(csv.DictReader(line for line in f if not line.startswith("#")))


def _vhash(value: str) -> str:
    return hashlib.sha1(value.encode()).hexdigest()[:16]


def _fmt_dt(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else "—"


def _weeks_summary(weeks: set[str]) -> str:
    nums = sorted(int(w) for w in weeks if w.isdigit())
    other = sorted(w for w in weeks if not w.isdigit())
    if not nums:
        return "none" + (f" (+{len(other)} non-numeric)" if other else "")
    gaps = [n for n in range(nums[0], nums[-1] + 1) if n not in set(nums)]
    s = f"{nums[0]}-{nums[-1]}" if len(nums) > 1 else str(nums[0])
    if gaps:
        s += f" gaps:{','.join(map(str, gaps))}"
    if other:
        s += f" +{len(other)} non-numeric"
    return s


def _read_replace(path: Path):
    """Fallback for a file that fails the pipeline reader's strict UTF-8 open:
    identical parse (csv + _row_to_event), undecodable bytes replaced. Used
    ONLY to inventory such a file; the strict failure itself is the finding,
    the file will not ingest as-is (datashop.py opens encoding="utf-8")."""
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            yield _row_to_event(row)


def inspect_file(path: Path) -> dict:
    """One streaming pass over the pipeline reader's Events; denominators only."""
    with open(path, newline="", encoding="utf-8") as f:
        header = next(csv.reader(f, delimiter="\t"))
    # Raw field names, exactly as csv.DictReader/datashop.read() will see them:
    # a padded name like " Time " is *missing* to ingest, so it must be missing
    # to this precheck too, or the lazy reader raises after the check passed.
    padded = [h for h in header if h != h.strip()]
    missing = sorted(_REQUIRED_COLUMNS - set(header))
    if missing:
        # read(path) raises ValueError lazily, on iteration, for such a file;
        # record the will-not-ingest flag and inventory nothing beyond the
        # header (§3) instead of crashing mid-report.
        utf8 = "n/a, MISSING REQUIRED COLUMNS (will not ingest)"
        events = iter(())
    else:
        try:
            path.read_text(encoding="utf-8")
            utf8 = "ok"
            events = read(path)
        except UnicodeDecodeError:
            n_bad = path.read_bytes().decode("utf-8", errors="replace").count("\ufffd")
            utf8 = f"FAIL({n_bad} bad seqs, WILL NOT INGEST as-is; stats below via errors=replace)"
            events = _read_replace(path)
    m = dict(
        utf8=utf8,
        bytes=path.stat().st_size, header=header,
        missing_required=missing,
        optional_present=[c for c in OPTIONAL_COLUMNS if c in header],
        extra=[c for c in header if c not in _REQUIRED_COLUMNS and c not in OPTIONAL_COLUMNS],
        padded_header=padded,
        rows=0, sel=Counter(), students=set(), students_dnd=set(),
        problems_dnd=set(), course_vals=set(), class_vals=set(),
        code_vals=set(), weeks=set(), n_input_check=0, n_check_no_input=0,
        n_pn_empty=0, n_pn_empty_nonpage=0, n_sid_empty=0, n_time_blank=0,
        n_time_bad=0, n_sel_unknown=0, sel_unknown_vals=Counter(),
        dndlike_unknown=Counter(),
        tmin=None, tmax=None, dmin=None, dmax=None, dnd_epochs=[],
    )
    for e in events:
        m["rows"] += 1
        m["sel"][e.selection] += 1
        if e.student_id:
            m["students"].add(e.student_id)
        else:
            m["n_sid_empty"] += 1
        if not e.problem_name:
            m["n_pn_empty"] += 1
            if e.selection != "page":
                m["n_pn_empty_nonpage"] += 1
        if e.course_id:
            m["course_vals"].add(e.course_id)
        if e.class_id:
            m["class_vals"].add(e.class_id)
        if e.week_no:
            m["weeks"].add(e.week_no)
        if e.selection not in KNOWN_SELECTIONS:
            m["n_sel_unknown"] += 1
            m["sel_unknown_vals"][e.selection or "(blank)"] += 1
        is_dnd = e.selection in _DND_SELECTIONS
        if e.selection.startswith("dragNdrop") and not is_dnd:
            m["dndlike_unknown"][e.selection] += 1
        if is_dnd and e.student_id:
            m["students_dnd"].add(e.student_id)
        if is_dnd and e.problem_name:
            m["problems_dnd"].add(e.problem_name)
        if e.selection == "dragNdrop":
            if e.input:
                m["n_input_check"] += 1
            else:
                m["n_check_no_input"] += 1
        if not e.timestamp:
            m["n_time_blank"] += 1
            m["n_time_bad"] += 1
            continue
        try:
            ts = parse_timestamp(e.timestamp)
        except ValueError:
            m["n_time_bad"] += 1
            continue
        if m["tmin"] is None or ts < m["tmin"]:
            m["tmin"] = ts
        if m["tmax"] is None or ts > m["tmax"]:
            m["tmax"] = ts
        if is_dnd:
            m["dnd_epochs"].append(ts.timestamp())
            if m["dmin"] is None or ts < m["dmin"]:
                m["dmin"] = ts
            if m["dmax"] is None or ts > m["dmax"]:
                m["dmax"] = ts
    # CF (Code) is not carried on Event (the pipeline ignores it, B4); a
    # second cheap pass reads just that column so its cardinality is real.
    if "CF (Code)" in header:
        # errors="replace" so the one strict-UTF-8-failing file is still
        # characterized; its value hashes may differ where bytes were replaced.
        with open(path, newline="", encoding="utf-8", errors="replace") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                v = (row.get("CF (Code)") or "").strip()
                if v:
                    m["code_vals"].add((_vhash(v),
                                        "template-like" if _TEMPLATED.search(v) else "opaque"))
    eps = sorted(m.pop("dnd_epochs"))
    if eps:
        med = datetime.fromtimestamp(statistics.median(eps), tz=timezone.utc)
        m["dnd_median"] = med
        if len(eps) >= 4:
            q1, _, q3 = statistics.quantiles(eps, n=4)
            m["dnd_iqr_days"] = (q3 - q1) / 86400.0
        else:
            m["dnd_iqr_days"] = 0.0
        if datetime(2025, 8, 1, tzinfo=timezone.utc) <= med <= datetime(2025, 12, 31, 23, 59, 59, tzinfo=timezone.utc):
            m["term_content"] = "F25"
        elif datetime(2026, 1, 1, tzinfo=timezone.utc) <= med <= datetime(2026, 5, 31, 23, 59, 59, tzinfo=timezone.utc):
            m["term_content"] = "S26"
        else:
            m["term_content"] = "OTHER"
    else:
        m["dnd_median"] = None
        m["dnd_iqr_days"] = None
        has_dnd = any(m["sel"].get(k) for k in _DND_SELECTIONS)
        m["term_content"] = "undatable" if has_dnd else "no-DnD"
    m["prefixes"] = Counter(
        bp for bp in (book_prefix(p) for p in m["problems_dnd"]) if bp)
    m["n_prob_noprefix"] = sum(1 for p in m["problems_dnd"] if not book_prefix(p))
    return m


def book_prefix(name: str) -> str | None:
    """Book token of a problem name. ULA uses the documented ``book__qid``;
    AC/APEX/FCLA use ``book_<n>_qid`` (single underscores), a corpus fact
    §3 did not anticipate (it assumed ``bookID__questionID`` corpus-wide)."""
    if "__" in name:
        return name.split("__", 1)[0]
    m2 = re.match(r"^(.+?)_\d+_", name)
    return m2.group(1) if m2 else None


def _import_preview_upload():
    """The UI's ``preview_upload()`` for the B5 cross-check, or None.

    ``proteus_ui`` is a top-level folder of the private checkout, not an
    installed package: a console-script run needs the checkout on ``sys.path``
    (the script inserted it itself until 2026-10-27). The public pipeline
    ships without the Streamlit UI (``cc-task-public-export``), so a package
    that cannot be imported is reported by the caller, never a failure.
    """
    try:
        from proteus_ui.services.imports_service import preview_upload
    except ImportError:
        if not (ROOT / "proteus_ui").is_dir():
            return None
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        try:
            from proteus_ui.services.imports_service import preview_upload
        except ImportError:
            return None
    return preview_upload


PREVIEW_CHECK_NOT_RUN = (
    "_Not run: the UI cross-check could not be imported (`proteus_ui` is not in "
    "this tree (the public pipeline ships without the Streamlit UI) or its `ui` "
    "extra is not installed); the per-file counts above stand on the core reader "
    "alone._"
)


def preview_check(paths: list[Path], mask: Mask, emit) -> None:
    """Cross-check against the UI's preview_upload() on one in-memory conn;
    a note instead when the UI is not importable (``_import_preview_upload``)."""
    from proteus.core import db

    preview_upload = _import_preview_upload()
    if preview_upload is None:
        emit(PREVIEW_CHECK_NOT_RUN)
        return

    conn = db.connect(":memory:")
    db.bootstrap(conn)
    emit("| file | n_events agrees | per-Selection agrees | detected books | warnings |")
    emit("|---|---|---|---|---|")
    for path, m in ((p, INSPECTED[p]) for p in paths):
        try:
            pv = preview_upload(conn, path)
        except (UnicodeDecodeError, ValueError) as exc:
            emit(f"| {mask.name(path)} | CRASH | CRASH | — | {type(exc).__name__}, the UI preview "
                 "crashes on this file exactly as ingest would |")
            continue
        ok_n = "yes" if pv.n_events == m["rows"] else f"NO ({pv.n_events} vs {m['rows']})"
        ok_sel = "yes" if dict(pv.per_selection) == dict(m["sel"]) else "NO"
        warn = "; ".join(pv.parse_warnings) or "none"
        emit(f"| {mask.name(path)} | {ok_n} | {ok_sel} | {', '.join(pv.detected_books)} | {warn} |")
    conn.close()


_ID5 = ["Anon Student Id", "Problem Name", "Time", "Selection", "Problem View"]


def _id5_set(path: Path) -> set:
    out = set()
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            out.add(hashlib.sha1(
                "|".join((row.get(c) or "").strip() for c in _ID5).encode()).digest())
    return out


def reconcile(target: Path, others: list[Path], mask: Mask, emit) -> None:
    """Containment of `target` in every other file (§3, final row): raw bytes
    (export-serialization identity) and the pipeline's 5-field event identity
    (student|problem|timestamp|selection|problem_view, events.py)."""
    with open(target, "rb") as f:
        f.readline()
        tlines = {hashlib.sha1(l).hexdigest() for l in f if l.strip()}
    t5 = _id5_set(target)
    emit(f"\n## Reconciliation, {mask.name(target)} "
         f"({len(tlines):,} distinct data rows; {len(t5):,} distinct identity 5-tuples)\n")
    emit("| candidate | shared raw rows | of target | shared id-5-tuples | of target | of candidate |")
    emit("|---|---|---|---|---|---|")
    hits = []
    for p in others:
        if p.resolve() == target.resolve():
            continue
        with open(p, "rb") as f:
            f.readline()
            clines = {hashlib.sha1(l).hexdigest() for l in f if l.strip()}
        c5 = _id5_set(p)
        sh_raw, sh_id = len(tlines & clines), len(t5 & c5)
        if sh_raw or sh_id:
            hits.append((p, sh_raw, len(clines)))
            emit(f"| {mask.name(p)} | {sh_raw:,} | {sh_raw/len(tlines):.4f} "
                 f"| {sh_id:,} | {sh_id/len(t5):.4f} | {sh_id/len(c5):.4f} |")
    if not hits:
        emit("| (none) | 0 | — | 0 | — | — |")
    emit("")
    tm = INSPECTED[target]
    for p, shared, _ in hits:
        cm = INSPECTED[p]
        emit(f"- vs {mask.name(p)}: students target⊆candidate: "
             f"{tm['students'] <= cm['students']} ({len(tm['students'])} vs {len(cm['students'])}); "
             f"DnD problems target⊆candidate: {tm['problems_dnd'] <= cm['problems_dnd']} "
             f"({len(tm['problems_dnd'])} vs {len(cm['problems_dnd'])}); "
             f"span target within candidate: {cm['tmin'] <= tm['tmin'] and tm['tmax'] <= cm['tmax']} "
             f"(target {_fmt_dt(tm['tmin'])} → {_fmt_dt(tm['tmax'])}, "
             f"candidate {_fmt_dt(cm['tmin'])} → {_fmt_dt(cm['tmax'])})")


INSPECTED: dict[Path, dict] = {}


def main() -> None:
    ap = _MaskedParser(
        prog="inventory_exports.py",
        description="Per-file DataShop export inventory (denominators only; pseudonymized output).")
    ap.add_argument("files", nargs="*", type=Path,
                    help="explicit export files; when given (and --dir is not), ONLY these are inventoried")
    ap.add_argument("--dir", type=Path, default=None,
                    help="directory walked recursively for .tab files "
                         "(default data/private/ only when no explicit files are given)")
    ap.add_argument("--map", type=Path, default=ROOT / "data/private/offering-pseudonyms.csv")
    ap.add_argument("--out", type=Path, required=True, help="write the report here (required; nothing is hardcoded)")
    ap.add_argument("--reconcile", type=Path, default=None, help="file to test for raw-row containment in every other file")
    args = ap.parse_args()

    rows = load_map(args.map)
    mask = Mask(rows)
    walked = set()
    if args.dir is not None:
        walked = set(args.dir.rglob("*.tab"))
    elif not args.files:
        walked = set((ROOT / "data/private").rglob("*.tab"))
    paths = sorted(walked | {p.resolve() for p in args.files}
                   | ({args.reconcile.resolve()} if args.reconcile else set()),
                   key=lambda p: mask.name(p))
    shas = {p: sha256_file(p) for p in paths}

    lines: list[str] = []
    emit = lambda s="": lines.append(mask(s))

    emit("# Export inventory (Task A), generated by inventory_exports.py")
    emit(f"# files: {len(paths)}; generated deterministically; all labels are pseudonyms\n")

    for p in paths:
        INSPECTED[p] = inspect_file(p)

    # ---- the one master table, files as rows (§3) ----
    emit("## Master table\n")
    hdr = ("| pseudonym | sha256[:12] | bytes | rows | req.cols | utf8-strict | check | move | reset | page | shortans "
           "| other | input✓check | check-no-input | pn-empty | sid-empty | time-bad | sel-unknown "
           "| students | students-DnD | DnD-problems | prefixes | course-vals | class-vals | CF(Code)-vals "
           "| weeks | span | DnD-span | DnD-median | IQR-days | claimed | term-content | book-agree |")
    emit(hdr)
    emit("|" + "---|" * (hdr.count("|") - 1))
    claimed = {r["path"]: r for r in rows}
    # Empirical per-book prefix consensus (the prefix carried by the most files
    # claiming that book). Majority-empirical: a book whose majority were
    # mislabeled would fool it, the cross-book disjointness check below is the
    # backstop; a genuine wrong-book file surfaces as MISMATCH here (§3's
    # MANUSCRIPT-RELEVANT trigger) or as a cross-book prefix collision there.
    votes: dict[str, Counter] = {}
    for p in paths:
        rel = str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)
        b = claimed.get(rel, {}).get("book")
        if b:
            for pf in INSPECTED[p]["prefixes"]:
                votes.setdefault(b, Counter())[pf] += 1
    consensus = {b: c.most_common(1)[0][0] for b, c in votes.items() if c}
    for p in paths:
        m = INSPECTED[p]
        rel = str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)
        cl = claimed.get(rel, {})
        claim = f"{cl.get('book','?')}/{cl.get('term','?')}" if cl.get("book") else "(unclaimed)"
        book_agree = "n/a"
        if cl.get("book"):
            pf = set(m["prefixes"])
            exp = consensus.get(cl["book"])
            if not pf:
                book_agree = "no-prefixed-DnD"
            elif len(pf) > 1:
                book_agree = f"MULTI({len(pf)})"
            elif pf == {exp}:
                book_agree = "agree"
            else:
                book_agree = f"MISMATCH(expected {exp})"
        term_flag = m["term_content"]
        if cl.get("term") and m["term_content"] in ("F25", "S26"):
            term_flag += "" if m["term_content"] == cl["term"] else " ≠CLAIM"
        emit("| " + " | ".join(str(x) for x in [
            mask.name(p), shas[p][:12], m["bytes"], m["rows"],
            "ok" if not m["missing_required"] else "MISSING:" + ",".join(m["missing_required"]),
            m["utf8"],
            m["sel"].get("dragNdrop", 0), m["sel"].get("dragNdrop-drop", 0),
            m["sel"].get("dragNdrop-reset", 0), m["sel"].get("page", 0),
            m["sel"].get("shortanswer", 0),
            sum(v for k, v in m["sel"].items() if k not in KNOWN_SELECTIONS),
            m["n_input_check"], m["n_check_no_input"], m["n_pn_empty"], m["n_sid_empty"],
            m["n_time_bad"], m["n_sel_unknown"],
            len(m["students"]), len(m["students_dnd"]), len(m["problems_dnd"]),
            len(m["prefixes"]), len(m["course_vals"]), len(m["class_vals"]), len(m["code_vals"]),
            _weeks_summary(m["weeks"]),
            f"{_fmt_dt(m['tmin'])} → {_fmt_dt(m['tmax'])}",
            f"{_fmt_dt(m['dmin'])} → {_fmt_dt(m['dmax'])}",
            _fmt_dt(m["dnd_median"]),
            f"{m['dnd_iqr_days']:.1f}" if m["dnd_iqr_days"] is not None else "—",
            claim, term_flag, book_agree,
        ]) + " |")

    # ---- headers ----
    emit("\n## Headers\n")
    sigs = {}
    for p in paths:
        sigs.setdefault(tuple(INSPECTED[p]["header"]), []).append(mask.name(p))
    for sig, names in sigs.items():
        who = f"all {len(names)} files" if len(sigs) == 1 else f"{len(names)} file(s): {', '.join(sorted(names))}"
        emit(f"- {who} share the header ({len(sig)} columns): all required present"
             if not (_REQUIRED_COLUMNS - set(sig)) else
             f"- {who} MISSING required columns: {sorted(_REQUIRED_COLUMNS - set(sig))}, will not ingest")
        extras = [c for c in sig if c not in _REQUIRED_COLUMNS and c not in OPTIONAL_COLUMNS]
        opt_absent = [c for c in OPTIONAL_COLUMNS if c not in sig]
        emit(f"  optional absent: {opt_absent or 'none'}; extra beyond §3's list: {extras or 'none'}")
    pad = {mask.name(p): INSPECTED[p]["padded_header"] for p in paths if INSPECTED[p]["padded_header"]}
    if pad:
        emit(f"- field names with surrounding whitespace (invisible to ingest's column lookup): {pad}")

    # ---- other-selection vocabulary ----
    emit("\n## Selection values outside the known vocabulary\n")
    any_unknown = False
    for p in paths:
        m = INSPECTED[p]
        if m["sel_unknown_vals"]:
            any_unknown = True
            vals = ", ".join(f"`{k}`×{v}" for k, v in sorted(m["sel_unknown_vals"].items()))
            emit(f"- {mask.name(p)}: {vals}")
    if not any_unknown:
        emit("- none in any file")
    dndlike = {mask.name(p): dict(INSPECTED[p]["dndlike_unknown"])
               for p in paths if INSPECTED[p]["dndlike_unknown"]}
    emit(f"- dragNdrop-like values outside the service's three (a LIKE "
         f"'dragNdrop%' query would silently fold these in): {dndlike or 'none'}")

    # ---- book prefixes and item-name structure by claimed book ----
    emit("\n## DnD problem names by claimed book (unions, not sums)\n")
    by_book_names: dict[str, dict[Path, set]] = {}
    for p in paths:
        rel = str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)
        b = claimed.get(rel, {}).get("book") or "(unclaimed)"
        by_book_names.setdefault(b, {})[p] = INSPECTED[p]["problems_dnd"]
    uni_by_book: dict[str, set] = {}
    for b in sorted(by_book_names):
        fsets = by_book_names[b]
        uni = set().union(*fsets.values()) if fsets else set()
        uni_by_book[b] = uni
        pfx = Counter(bp for bp in (book_prefix(n) for n in uni) if bp)
        nopfx = sum(1 for n in uni if not book_prefix(n))
        inter = [len(a & c) for i, (pa, a) in enumerate(sorted(fsets.items()))
                 for pc, c in sorted(fsets.items())[i + 1:]]
        emit(f"- {b}: union {len(uni)} distinct DnD names across {len(fsets)} file(s) "
             f"(per-file sum {sum(len(v) for v in fsets.values())}); prefixes {dict(sorted(pfx.items()))}; "
             f"no-prefix names {nopfx}; pairwise shared-name range "
             f"{min(inter) if inter else '—'}-{max(inter) if inter else '—'}")
    named = {b: u for b, u in uni_by_book.items() if b != "(unclaimed)"}
    coll = [(a, b2, len(named[a] & named[b2])) for a in sorted(named) for b2 in sorted(named)
            if a < b2 and named[a] & named[b2]]
    emit(f"- cross-book FULL-NAME collisions: {coll or 'none, item namespaces disjoint across books'}")
    pfx_sets = {b: {bp for bp in (book_prefix(n) for n in u) if bp} for b, u in named.items()}
    pcoll = [(a, b2, sorted(pfx_sets[a] & pfx_sets[b2])) for a in sorted(pfx_sets)
             for b2 in sorted(pfx_sets) if a < b2 and pfx_sets[a] & pfx_sets[b2]]
    emit(f"- cross-book PREFIX collisions: {pcoll or 'none'}")

    # ---- metadata columns: values never printed unless templated ----
    emit("\n## Metadata columns (values withheld unless unmistakably templated)\n")
    for label, key in [("CF (Institution)", "course_vals"), ("Class", "class_vals")]:
        univ: dict[str, set] = {}
        for p in paths:
            for v in INSPECTED[p][key]:
                univ.setdefault(v, set()).add(mask.name(p))
        emit(f"### {label}: {len(univ)} distinct value(s) corpus-wide")
        for v, names in sorted(univ.items(), key=lambda kv: (sorted(kv[1]), _vhash(kv[0]))):
            shown = (f"`{v}`" if v in _TEMPLATE_LITERALS else
                     f"(withheld; {len(v)} chars, "
                     f"{'template-like' if _TEMPLATED.search(v) else 'opaque'})")
            emit(f"- {shown}, in {len(names)} file(s): {', '.join(sorted(names))}")
    emit("### CF (Code), cardinalities and classification only (values never printed)")
    code_univ: dict[tuple, set] = {}
    for p in paths:
        for hv in INSPECTED[p]["code_vals"]:
            code_univ.setdefault(hv, set()).add(mask.name(p))
    shared_codes = {h: n for h, n in code_univ.items() if len(n) > 1}
    cls_corpus = Counter(c for _, c in code_univ)
    emit(f"- distinct values corpus-wide: {len(code_univ)} "
         f"(classification: {dict(cls_corpus) or 'n/a'}); values appearing in >1 file: {len(shared_codes)}")
    for p in paths:
        vals = INSPECTED[p]["code_vals"]
        cls = Counter(c for _, c in vals)
        emit(f"- {mask.name(p)}: {len(vals)} distinct "
             f"({'constant within file' if len(vals) == 1 else 'varies' if vals else 'empty'}"
             f"{'; ' + str(dict(cls)) if vals else ''})")

    # ---- preview_upload cross-check ----
    emit("\n## preview_upload() cross-check (in-memory connection; B5 amendment)\n")
    preview_check(paths, mask, emit)

    # ---- reconciliation ----
    if args.reconcile:
        reconcile(args.reconcile.resolve(), [p for p in paths], mask, emit)

    text = "\n".join(lines) + "\n"
    mask.assert_clean(text)
    args.out.write_text(text)
    sys.stdout.write(text)


def cli() -> int:
    """The launcher behind both ``proteus-inventory-exports`` and
    ``python -m``: ``main()`` inside the §2.3a guard, so no traceback ever
    prints a real path (the PR #247 review round)."""
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # never let a traceback print a real path (§2.3a)
        sys.stderr.write(
            f"inventory_exports: {type(exc).__name__}: {_mask_paths(str(exc))}\n"
            "(traceback suppressed, §2.3a path masking)\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(cli())
