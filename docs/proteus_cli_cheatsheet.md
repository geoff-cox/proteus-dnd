# PROTEUS CLI Cheatsheet

A reference for every command-line service the `proteus` tool currently exposes,
drawn from `src/proteus/cli.py` and the two registered service packages
(`services/shortanswer`, `services/dragndrop`).

---

## How to invoke it

Run the package as a module, or — since the console script landed on 2026-10-27
(`[project.scripts]` in `pyproject.toml`, `cc-task-public-export` PR 1) — as the
installed command `proteus`, which is the same entry point:

```bash
python -m proteus.cli <command> [options]
proteus <command> [options]           # the same, as the installed console script
python -m proteus.cli --help          # list root commands + service namespaces
python -m proteus.cli dnd --help       # list one service's stages
```

**Global flags** (accepted on every command):

| Flag | Default | Description |
| :-- | :-- | :-- |
| `--db PATH` | `<repo>/data/proteus.db` | SQLite database file. The default lands in the repo's git-ignored `data/` directory and is created on first use; pass `--db` (or `--db :memory:`) to override. |
| `-v`, `--verbose` | off | Switch logging from INFO to DEBUG. |

**The shape of the pipeline.** One generic `ingest` fills a shared `events`
table; each service then *derives* its own typed rows from those events and runs
its own stages on top. Commands fall into three groups:

```
events  ──derive──▶  shortanswer.responses ──▶ screen ▶ extract ▶ match ▶ dispose ▶ export
        └─derive──▶  dnd_events ──▶ reconstruct ▶ score ▶ analyze ▶ export
                                     (score/validate also need boards from source)
```

The two **services** are discovered through `proteus.core.registry`; a service
whose import fails simply loses its namespace (with a stderr warning) while the
rest of the CLI keeps working.

---

## Service 1 — Core (root, service-agnostic)

**Description.** The lifecycle and ingest commands that aren't tied to a
question type: create/reset/migrate the database, pull export files into the
shared `events` table, reconcile the question catalog, register textbooks, and
sync question source (which is also what *builds DnD boards*).

**Pattern.**

```bash
python -m proteus.cli <command> [options]
```

**Commands.**

| Command | Purpose |
| :-- | :-- |
| `init` | Create a fresh database with the schema applied. Fails if the DB already exists. |
| `reset` | Delete and re-create the database. |
| `migrate` | Apply pending forward-only migrations (core first, then each service). |
| `ingest <file>` | Import a DataShop `.tab` or Runestone `.csv` into the shared `events` table. Idempotent (guarded by file hash); also refreshes the catalog. |
| `reingest-events <file>` | Backfill the `events` table from an already-imported file (does not touch derived rows). |
| `status` | Print a summary of DB contents: core counts plus each service's contribution. |
| `manifest` | Write the run manifest (E-03): imports by SHA-256 and pseudonym (never a path), textbook refs with `file://` URLs redacted, the source-hash pin, migration state, every pipeline run's configuration (a run without one is marked `provenance: incomplete`), per-table counts, and one fingerprint. `manifest diff A B` lists every leaf that differs. |
| `delete-import <import_id>` | Delete an import and **every** derived row across services, after printing the blast radius. |
| `sync-catalog` | Populate the question catalog from events (one row per problem, page views excluded, with an inferred type). Idempotent; runs automatically after `ingest`. |
| `textbook add <book> <url> <dir>` | Register a public PreTeXt textbook in the registry for source sync / preview. |
| `sync-questions` | Walk a **built** PreTeXt HTML tree and cache one standalone HTML preview per short-answer question. |
| `sync-sources` | Clone a registered book (`--book`) or walk a local source tree (`--source-dir`); cache one `.ptx` per question, set `questions.source_xml` and `questions.title`, and run each service's source handler — **this is what builds `dnd_boards`**. |

**Arguments.**

| Command | Argument | Req. | Description |
| :-- | :-- | :-: | :-- |
| `reset` | `--yes` |  | Skip the "type yes to continue" confirmation. |
| `migrate` | `--dry-run` |  | Preview pending migrations without applying them. |
| `ingest` | `file` | ✓ | Path to the export file (positional). |
| `ingest` | `--derive` |  | After ingest, run every service's derive step over the new import (the old one-command UX). |
| `ingest` | `--question` |  | With `--derive`, restrict the derive step to one `question_id`; events always capture the whole file. |
| `ingest` | `--format` |  | Override auto-detection: `datashop_tab` or `runestone_useinfo_csv`. |
| `manifest` | `-o`, `--output` |  | Write the YAML manifest here (default: stdout). |
| `manifest` | `--pseudonyms` |  | CSV with `sha256` and `pseudonym` columns naming the imports; unmapped imports are named by their hash. |
| `manifest diff` | `a b` | ✓ | Two manifest files to compare (exit 1 when they differ). |
| `reingest-events` | `file` | ✓ | Path to the original export file (positional). |
| `reingest-events` | `--format` |  | Override auto-detection: `datashop_tab`. |
| `delete-import` | `import_id` | ✓ | The `raw_imports` id to delete (positional int). |
| `delete-import` | `--yes` |  | Skip the confirmation prompt. |
| `textbook add` | `book_xml_id` | ✓ | The book's xml id, e.g. `ula-proteus` (positional). |
| `textbook add` | `git_url` | ✓ | Public git URL (`https://` or `file://`; no credentials) (positional). |
| `textbook add` | `build_dir` | ✓ | Local path to the book's built HTML (positional). |
| `textbook add` | `--ref` |  | Git ref (branch/tag/SHA); default branch if omitted. |
| `textbook add` | `--notes` |  | Optional free-text note. |
| `sync-questions` | `--build-dir` | ✓ | Built PreTeXt HTML directory to walk. |
| `sync-questions` | `--output-dir` | ✓ | Where per-question HTML files are written (`<dir>/<textbook-id>/<xml_id>.html`). |
| `sync-questions` | `--textbook-id` | ✓ | `textbook_id` (from the `textbooks` table) the build belongs to. |
| `sync-sources` | `--book` | †  | Registered `book_xml_id` to clone and sync. Mutually exclusive with `--source-dir`. |
| `sync-sources` | `--source-dir` | †  | Local PreTeXt source tree to walk directly (no clone); requires `--textbook-id`. |
| `sync-sources` | `--textbook-id` | †  | `textbook_id` the source belongs to (required with `--source-dir`). |
| `sync-sources` | `--clone-root` |  | Where to clone `--book` repos (default `data/textbook_clones`). |
| `sync-sources` | `--output-dir` |  | Per-question `.ptx` snippet cache (`<dir>/<book>/<question_id>.ptx`; default `data/question_sources`). |

† `sync-sources` takes **either** `--book` **or** `--source-dir` + `--textbook-id`.

> **Title backfill (core migration `0014`).** `questions.title` — the naming unit's
> `<title>` as verbatim inner XML — is filled during sync. The migration adds the column
> only: rows synced before it stay NULL until the next `sync-sources` run repopulates
> them, reported as `titles backfilled` in the sync summary.

**Sample usages.**

```bash
# Fresh database (handles leftover WAL/SHM sidecars), then ingest + derive in one shot
rm -f data/proteus.db data/proteus.db-wal data/proteus.db-shm
python -m proteus.cli init
python -m proteus.cli ingest --derive data/S26_ULA_datashop.tab

# Inspect, and preview a migration check
python -m proteus.cli status
python -m proteus.cli migrate --dry-run

# Register a book, then build boards from a local source checkout
python -m proteus.cli textbook add ula-proteus https://github.com/org/ula-proteus build/html
python -m proteus.cli sync-sources --source-dir /path/to/ula-proteus/source --textbook-id 1

# Undo an import (with its full derived cascade)
python -m proteus.cli delete-import 1 --yes
```

---

## Service 2 — Short Answer (`sa`)

**Description.** The five-stage short-answer pipeline — derive responses, screen
for eligibility, extract reasoning chains, match against a codebook, and flag /
dispose — plus the controlled-vocabulary and codebook management commands and a
CSV export.

**Pattern.**

```bash
python -m proteus.cli sa <stage> [options]
```

> The old un-namespaced forms (`proteus screen`, `proteus extract`, …) were
> **retired** (FU-1): they now fail with argparse's normal "invalid choice"
> error, which lists `sa` among the valid commands. Every stage command exists
> only under `sa`.

**Commands.**

| Command | Purpose |
| :-- | :-- |
| `sa derive` | Stage 1 — materialize `responses` from ingested short-answer events. |
| `sa screen` | Stage 2 — eligibility screening over ingested responses. |
| `sa extract` | Stage 3 — chain extraction over `INCLUDE`-screened responses. |
| `sa match` | Stage 4 — codebook matching over chain steps. |
| `sa dispose` | Stage 5 — flagging and disposition over screened responses. |
| `sa vocab <import\|add\|list>` | Import, inspect, or seed the controlled vocabulary. |
| `sa codebook <import\|add-pattern\|list>` | Import the group's codebook CSV; manage codes and their match patterns. |
| `sa export <dataset>` | Dump a pipeline-stage view to CSV, or draw the blinded `gold_sample` coding sheet + answer key. |
| `sa compare <gold_csv>` | Compare human gold coding (`response_id`, `code_id` rows) against one pipeline run: include/exclude agreement and per-code precision/recall, as a markdown report. |

**Arguments.**

| Command | Argument | Req. | Description |
| :-- | :-- | :-: | :-- |
| `sa derive` | `--question` |  | Only derive responses for this `question_id`. |
| `sa derive` | `--import-id` |  | Only derive events from this import (int). |
| `sa screen` / `extract` / `match` / `dispose` | `--question` |  | Restrict the stage to one `question_id`. |
| `sa screen` / `extract` / `match` / `dispose` | `--run-id` |  | Reuse an existing `pipeline_runs` row instead of starting a new one (int). Typically the same `run_id` across stages. |
| `sa screen` / `extract` / `match` / `dispose` | `--prompt-version` |  | `prompt_version` recorded on a new run. Defaults: `stage2-rules-v1` / `stage3-keywords-v1` / `stage4-keywords-v1` / `stage5-rules-v1`. |
| `sa screen` / `extract` / `match` / `dispose` | `--model-id` |  | `model_id` recorded on a new run. Defaults: `rule-baseline` (screen/dispose) or `keyword-baseline` (extract/match). |
| `sa screen` / `extract` / `match` / `dispose` | `--notes` |  | Free-text notes on the `pipeline_runs` row. |
| `sa vocab add` | `canonical_form` | ✓ | The canonical form to register (positional). |
| `sa vocab add` | `--description` |  | Human-readable description of the form. |
| `sa vocab add` | `--label` |  | Vocabulary version label (defaults to the standard label). |
| `sa vocab list` | `--label` |  | Vocabulary version to list. |
| `sa vocab import` | `csv_path` | ✓ | Two-column CSV: `canonical_form`, `description` (positional). |
| `sa vocab import` | `--label` |  | Vocabulary version label (defaults to the standard label; created on demand). |
| `sa vocab import` | `--dry-run` |  | Validate and report exact would-be counts, then roll back. |
| `sa codebook import` | `csv_path` | ✓* | The codebook CSV (positional; *omit with `--report-only`). |
| `sa codebook import` | `--label` |  | Codebook version label (defaults to the standard label; created on demand). |
| `sa codebook import` | `--dry-run` |  | Validate and report exact would-be counts, then roll back. |
| `sa codebook import` | `--report-only` |  | Write nothing; emit the DB state for `--label` in the same CSV schema. |
| `sa codebook add-pattern` | `--notes` |  | Optional notes for the pattern. |
| `sa codebook add-pattern` | `--label` |  | Codebook version label (defaults to the standard label). |
| `sa codebook list` | `--label` |  | Codebook version to list. |
| `sa codebook list` | `--patterns` |  | Also print each code's match patterns. |
| `sa export` | `dataset` | ✓ | One of `responses`, `screenings`, `chains`, `matches`, `dispositions`, `gold_sample` (positional). |
| `sa export` | `-o`, `--output` |  | File to write (defaults to stdout). |
| `sa export` | `--question` |  | Filter to one `question_id`. |
| `sa export` | `--run-id` |  | Filter to one `pipeline_runs` row (int); required by `gold_sample --strata screening`. |
| `sa export` | `--label` |  | Filter screenings to one verdict label. |
| `sa export` | `--code-id` |  | Filter matches to one codebook code (e.g. `CS7`). |
| `sa export` | `--disposition` |  | Filter dispositions to one bucket (e.g. `auto_accept`). |
| `sa export` | `--seed` |  | `gold_sample` only: RNG seed for reproducible stratified sampling (default `0`; same DB + seed + options ⇒ same sample). |
| `sa export` | `--per-stratum` |  | `gold_sample` only: responses drawn per stratum (default `10`; smaller strata are taken whole). |
| `sa export` | `--strata` |  | `gold_sample` only: `question` (default) or `screening` (question × Stage 2 label; needs `--run-id`). The coder sheet shows no pipeline outputs either way. |
| `sa export` | `--key` |  | `gold_sample` only: path for the companion answer key (IDs + strata, no response text). Default: the `--output` path with a `.key.csv` suffix. |
| `sa compare` | `gold_csv` | ✓ | Gold CSV with `response_id` and `code_id` columns (extra columns ignored; blank `code_id` = included, no code) (positional). |
| `sa compare` | `--run-id` | ✓ | `pipeline_runs` row to compare against (int). |
| `sa compare` | `--question` |  | Restrict the comparison universe to one `question_id`. |
| `sa compare` | `--universe` |  | CSV with a `response_id` column bounding the comparison universe (the `gold_sample` answer key works as-is), so responses the coders never saw don't count as gold-excluded. |
| `sa compare` | `-o`, `--out` |  | File for the markdown report (defaults to stdout). |

> `sa vocab` and `sa codebook` are sub-command groups; run `sa vocab --help` /
> `sa codebook --help` for the seldom-used per-pattern arguments.

**Sample usages.**

```bash
# Run the five stages on one run_id so they share provenance
python -m proteus.cli sa derive
RUN=$(python -m proteus.cli sa screen --notes "pilot S26" | grep -oP 'run_id:\s*\K\d+')
python -m proteus.cli sa extract --run-id "$RUN"
python -m proteus.cli sa match   --run-id "$RUN"
python -m proteus.cli sa dispose --run-id "$RUN"

# Scope a stage to one question
python -m proteus.cli sa screen --question some-book__some-question-v1

# Export stage views to CSV
python -m proteus.cli sa export responses -o responses.csv
python -m proteus.cli sa export matches --code-id CS7 -o cs7_matches.csv
python -m proteus.cli sa export dispositions --disposition escalate -o escalations.csv

# Draw the blinded gold sample (writes gold.csv + gold.key.csv)
python -m proteus.cli sa export gold_sample --seed 0 --per-stratum 10 -o gold.csv

# Score a run against adjudicated human coding, bounded to the sampled universe
python -m proteus.cli sa compare adjudicated_gold.csv \
  --run-id 17 --universe gold.key.csv -o run17_vs_gold.md

# Codebook / vocabulary
python -m proteus.cli sa codebook list --patterns
python -m proteus.cli sa vocab add "control structure" --description "loop/branch construct"

# Import the group's codebook CSV (validate first, then commit)
python -m proteus.cli sa codebook import cbk2.csv --label codebook2-june1 --dry-run
python -m proteus.cli sa codebook import cbk2.csv --label codebook2-june1

# Round-trip: emit the stored codebook back in the same CSV schema
python -m proteus.cli sa codebook import --label codebook2-june1 --report-only > codebook_state.csv

# Seed the controlled vocabulary from the committed draft (the ~17 terms of
# the journal's first-pass draft live at src/tests/fixtures/vocab_seed_draft.csv)
python -m proteus.cli sa vocab import src/tests/fixtures/vocab_seed_draft.csv --dry-run
python -m proteus.cli sa vocab import src/tests/fixtures/vocab_seed_draft.csv
```

**Real-data ingest procedure (CP1.6 — owner decision 7d).** Real SA response
exports are **never committed**: free-text answers can self-identify, so they
stay local-only and untracked. The guard in `.gitignore` makes this structural:
`data/private/` is ignored wholesale (it is the documented drop location), and
every `.tab`/`.csv` anywhere under `data/` is ignored by default. The one
already-tracked file (`data/rs_exports/real-data.tab`, the DnD reference
export) predates the guard; its short-answer `Action` cells were blanked in
PR #115. Caution for any future export: in DataShop exports short-answer text
can appear in the `Action` column even when `Input` is empty, so no committed
export should ever be presumed answer-free without checking both columns. The
canonical q3 source is the owner's DataShop `.tab`;
`ulashortanswerresponses.csv` is a human extract and is never ingested.

```bash
# 1. Drop the real export in the protected location (git-ignored):
mkdir -p data/private
cp /path/to/<real-export>.tab data/private/

# 2. Fresh database, then ingest + derive in one step:
python -m proteus.cli init
python -m proteus.cli ingest --derive data/private/<real-export>.tab

# 3. Verify the pivot-question responses landed (count is export-dependent:
#    39 non-blank for the single canonical fall-2025 export; 243 across the
#    full six-file fa2025+sp2026 corpus):
python -m proteus.cli status
sqlite3 data/proteus.db "SELECT COUNT(*) FROM responses \
  WHERE question_id = 'ula-proteus__ula-proteus-1-4-pivots-reading-q3';"

# 4. Confirm nothing is committable (should print nothing):
git status --porcelain data/private/
```

> **The codebook freeze (owner decision 5; executed 2026-07-20).** Importing the
> group's CSV under the frozen version label **is** the codebook freeze: it
> registers `CS1`–`CS10` (with surface markers and one `structural` pattern row
> per `|`-delimited `patterns` entry), closing the codebook half of CP0's gate.
> The freeze was executed under **`codebook2-june1`** (decision 5 originally named
> `codebook2-april24`; the coding for 1.4.8 was done in Codebook 2's June 1
> revision, so June 1 is the frozen version — same ten codes as April 24, differing
> only in `CS1`'s name; see the 2026-07-20 journal decision-log entry). The import
> is atomic and idempotent — re-importing the same file under the same label
> inserts nothing. Pattern text is stored, not evaluated (evaluation is CP3.11).

---

## Service 3 — Drag-and-Drop (`dnd`)

**Description.** The cardsort pipeline — derive typed `dnd_events`, build and
validate boards, replay moves into per-attempt configurations, score each check
against the board key, compute per-attempt process metrics, and export. It runs
on **two tracks**: a *source-free* track that works from the `.tab` alone, and a
*board-dependent* track for anything that needs the answer key.

**Pattern.**

```bash
python -m proteus.cli dnd <stage> [options]
```

**Commands.**

| Stage | Purpose | Boards? |
| :-- | :-- | :-- |
| `dnd derive` | `events` → typed `dnd_events` (move / takeback / check / reset / page). | no |
| `dnd sync-boards` | Rebuild board specs (cards, zones, key, decoys) from already-synced source. | builds them |
| `dnd validate` | Cross-validate boards against data: key from `Correct` checks must match the source key, and every `drag#`/`drop#` label must exist. Exits 1 on failure. | **yes** |
| `dnd alias add` / `remove` / `list` / `import` | Question-identifier aliases at board binding (dnd/0014): a retired identifier resolves to its successor's board at lookup time; raw rows are never rewritten. `import` loads a committed alias file (idempotent; prints the alias set's sha256, which every reconstruct / score / analyze run records and the manifest carries). | the successor's |
| `dnd reconstruct` | Replay moves from the empty board into per-action configurations, segment into attempts, and cross-check each check against its logged `Input`. | no |
| `dnd score` | Score every checked configuration vs the key: σ, distance, per-card, baseline-corrected `g`, label-free σ̃, decoy leaks — all side by side → `dnd_check_scores`. | **yes** |
| `dnd analyze` | Per-attempt process metrics (move/takeback/reset/check tallies, time-to-first-check, convergence curve) → `dnd_attempt_metrics`. | for the convergence curve |
| `dnd export <dataset>` | Dump a DnD stage view to CSV. | depends on dataset |
| `dnd sensitivity run` | Paper 1 RQ4 harness (E-01): clone the primary database once per frozen D3.2 alternative, re-run the stages the knob changes, export, and write `deltas.csv` in the order of `tab-sensitivity-results`. Refuses the canonical database. | **yes** (a finished primary run) |
| `dnd sensitivity evidence` | The gap / session / page-view / reset distributions that justify the D3.2 brackets — counts only. | no |
| `dnd contrast` | Paper 1 RQ3 runner (E-15): counts-only by default (group sizes, items with ≥ 5 per arm, reporting level); `--estimate` adds the three blocks with student-clustered bootstrap intervals and the representative attempts. | **yes** (after `score` and `analyze`) |

**Arguments.**

| Command | Argument | Req. | Description |
| :-- | :-- | :-: | :-- |
| `dnd derive` | `--question` |  | Only derive events for this `question_id` (page rows are excluded by the filter). |
| `dnd derive` | `--import-id` |  | Only derive events from this import (int). |
| `dnd sync-boards` | `--question` |  | Only rebuild this question's board. |
| `dnd validate` | `--question` |  | Only validate this question. |
| `dnd alias add` | `alias_question_id`, `canonical_question_id` | ✓ | The retired identifier as logged (`<book_xml_id>__<xml_id>`) and its successor (positional). Refused: a self-alias, a chain, an alias id that has its own board, an id with no events and no catalog row. |
| `dnd alias add` | `--reason`, `--source-commit`, `--recorded-on` |  | Provenance of the alias: free text; the rename commit; ISO date (default today, UTC). |
| `dnd alias add` / `import` | `--replace-board` |  | Retire the alias identifier's own board (and clear its results) instead of refusing the ambiguity; recorded on the row. |
| `dnd alias import` | `file` | ✓ | Alias file: CSV with the header `alias_question_id, canonical_question_id, reason, source_commit, recorded_on`; `#` comment lines allowed. ULA: `src/proteus/services/dragndrop/aliases/ula-proteus.csv`. |
| `dnd alias remove` | `alias_question_id` | ✓ | Remove one alias; results computed under it are left — re-run the stages. |
| `dnd reconstruct` | `--question` |  | Only reconstruct this question. |
| `dnd reconstruct` | `--gap-seconds` |  | Idle gap (seconds) that starts a new attempt (float; default `1800`). |
| `dnd reconstruct` | `--no-split-session` |  | Do **not** start a new attempt on a session change. |
| `dnd reconstruct` | `--no-split-page` |  | Do **not** start a new attempt when a page view intervenes. |
| `dnd reconstruct` | `--split-on-reset` |  | Treat every reset as an attempt boundary (off by default). |
| `dnd score` | `--question` |  | Only score this question. |
| `dnd analyze` | `--question` |  | Only analyze this question's attempts. |
| `dnd export` | `dataset` | ✓ | One of `checks`, `check_scores`, `attempt_metrics`, `segments`, `configurations`, `boards`, `modeling_table`, `item_summary` (positional). |
| `dnd export` | `-o`, `--output` |  | File to write (defaults to stdout). |
| `dnd export` | `--question` |  | Filter to one `question_id`. |
| `dnd sensitivity run` | `--primary-db` | ✓ | The primary-configuration database (frozen defaults already run). |
| `dnd sensitivity run` | `--out` | ✓ | Output directory: `<out>/<knob>/` per configuration plus `deltas.csv` / `deltas.json`. |
| `dnd sensitivity run` | `--knob` |  | Run only this frozen alternative (repeatable; the nine keys are `k1-gap-600`, `k1-gap-3600`, `k2-split-on-reset`, `k3-truncate-at-first-correct`, `k4-count-noop-moves`, `k5-exclude-order-ambiguous`, `k6-idle-120`, `k6-idle-600`, `k7-ready-and-limited`). |
| `dnd sensitivity run` | `--estimate` |  | Fill the primary-RQ3 column (empty by default — the counts-only posture). |
| `dnd sensitivity run` | `--allow-canonical` |  | Permit `data/proteus.db` as the primary (refused otherwise). |
| `dnd sensitivity evidence` | `-o`, `--output` |  | Write the Markdown report here (default: stdout); reads the global `--db`. |
| `dnd contrast` | `--out` | ✓ | Output directory (`counts.json`, `arms.csv`, `provenance.json`; with `--estimate` also `tab-process-results.csv`, `block1_per_item.csv`, `representatives.csv`). |
| `dnd contrast` | `--estimate` |  | Compute the estimates (needs `--seed`). |
| `dnd contrast` | `--seed`, `--B` |  | Bootstrap seed and replicates (default B = 2000). |
| `dnd contrast` | `--include-limited` |  | D3.2 knob 7: admit limited attempts; each contributes a feature only when its flags permit it (the QC rulebook's matrix). |
| `dnd contrast` | `--exclude-flag` |  | Drop attempts carrying this flag (repeatable; knob 5 is `row_order_ambiguous`). |

**Export datasets.**

| Dataset | Contents |
| :-- | :-- |
| `checks` | Every check with its logged configuration, reconstruction context and anchor eligibility (one row per check). |
| `check_scores` | Per-check scores, every metric side by side (one row per scored check). |
| `attempt_metrics` | Per-attempt process metrics with segmentation and QC context, the phase-window block last (one row per attempt). |
| `segments` | Materialized check / reset / session / active-work spans (one row per segment). |
| `configurations` | Every reconstructed configuration in replay order (one row per configuration). |
| `boards` | Registered board specs with the item-design covariates (one row per cardsort question). |
| `modeling_table` | The attempt-level modeling table: identifiers, outcomes, covariates, features, QC (one row per attempt). |
| `item_summary` | Per-question diagnostic summary (one row per cardsort question). |

Every attempt-, check- and configuration-level dataset carries `canonical_question_id` — the item identifier after the question-identifier alias (dnd/0014), equal to `question_id` except for rows logged under a retired identifier — followed by `resumed` / `prior_attempt_id` on the attempt-shaped datasets and `seeded` on the check-shaped ones (dnd/0015); `checks`, `attempt_metrics`, `configurations` and `modeling_table` close with `import_sha256`, the import file's SHA-256 — the offering key the run manifest lists beside the pseudonym (PR #236, 2026-10-18; the pseudonym itself is never exported). `boards` / `item_summary` end with `aliases`, the retired identifiers that resolve to the item (`;`-joined, empty for most). Per-item counts are by canonical identifier.

**The two tracks.**

- **Source-free** (from the `.tab` alone): `init → ingest --derive → dnd reconstruct → dnd analyze → dnd export checks`.
- **Board-dependent** (needs `dnd_boards`): register the book (`textbook add`), build boards (`sync-sources`, or rebuild with `dnd sync-boards`), then `dnd validate` / `dnd score`.

**Sample usages.**

```bash
# Source-free track
python -m proteus.cli ingest --derive data/S26_ULA_datashop.tab
python -m proteus.cli dnd reconstruct
python -m proteus.cli dnd analyze
python -m proteus.cli dnd export checks -o checks.csv

# Tune attempt segmentation
python -m proteus.cli dnd reconstruct \
  --gap-seconds 1800 \
  --no-split-session \
  --no-split-page \
  --split-on-reset

# Board-dependent track (after sync-sources has built dnd_boards)
python -m proteus.cli dnd sync-boards          # rebuild boards from synced source
python -m proteus.cli dnd validate             # exits 1 if a key/label check fails
python -m proteus.cli dnd score
python -m proteus.cli dnd export check_scores -o scores.csv

# Scope any stage to one question
python -m proteus.cli dnd score \
  --question ula-proteus__ula-proteus-2-2-equivalence-representations-v1
```

---

## End-to-end recipe (DnD, from empty database to scores)

```bash
# 1. fresh DB
python -m proteus.cli init

# 2. ingest the export and derive typed rows for every service
python -m proteus.cli ingest --derive data/S26_ULA_datashop.tab

# 3. reconstruct attempts (source-free) and check the replay
python -m proteus.cli dnd reconstruct       # 'mismatches' should be 0

# 4. build boards from PreTeXt source (needed for validate/score)
python -m proteus.cli textbook add ula-proteus <git_url> <build_dir>
python -m proteus.cli sync-sources --source-dir /path/to/ula-proteus/source --textbook-id 1
python -m proteus.cli dnd alias import src/proteus/services/dragndrop/aliases/ula-proteus.csv   # retired ids → their successors' boards

# 5. validate, score, analyze
python -m proteus.cli dnd validate
python -m proteus.cli dnd score
python -m proteus.cli dnd analyze

# 6. export
python -m proteus.cli dnd export check_scores -o scores.csv
```

> **Tip for the "board but no checks / no scores" case:** a question can have a
> board from `sync-sources` while its responses live under a different
> `problem_name`, or simply not have been reconstructed/scored yet. Confirm what
> a given question actually has with `python -m proteus.cli status`, and reach for
> `dnd derive` → `dnd reconstruct` → `dnd score` (boards required for the last) on
> that `question_id`.
