# PROTEUS — Drag‑and‑Drop CLI Testing Cheatsheet

Copy‑paste commands for exercising the **DnD service** end‑to‑end, starting from an
empty database. Verified against the current `proteus-rs-pipeline` source (the
`dnd` namespace and the `test_cli_dnd.py` end‑to‑end test).

**Assumptions**
- Run from the **repo root**, with the package installed (`pip install -e .`).
- Invocation is **`python -m proteus.cli`**; the installed console script `proteus`
  (since 2026-10-27, `cc-task-public-export` PR 1) is the same entry point.
- Database defaults to **`data/proteus.db`** (the repo's `data/` directory, git-ignored;
  `data/` is created on first use); override on any command with `--db PATH`.
- Adjust the two paths you'll see throughout:
  - `data/S26_ULA_datashop.tab` — your DataShop export.
  - `/path/to/ula-proteus/source` — a **local** PreTeXt source tree for the book.

**The two tracks** (important):
- **Source‑free** — works from the `.tab` alone: `init → ingest --derive → reconstruct → analyze → export checks`.
- **Board‑dependent** — `validate`, `score`, and board‑aware metrics need `dnd_boards`,
  which come from `sync-sources` over PreTeXt source. First register the book
  (`textbook add`), then either `sync-sources --book <book>` to clone its public
  repo automatically, or `sync-sources --source-dir <path> --textbook-id <id>` to
  walk a local source checkout.

---

## 0. One‑time setup

```bash
python -m pip install -e ".[dev]"      # add ",ui" for the Streamlit UI extras
python -m proteus.cli --help           # root (service‑agnostic) commands
python -m proteus.cli dnd --help       # DnD subcommands
```

---

## 1. Fresh database

`init` runs every migration to head. The textbook registry starts **empty** —
register a book with `proteus textbook add` (there is no seeded row). No separate
`migrate` is needed on a fresh DB.

```bash
# Guaranteed-empty start (handles leftover WAL/SHM sidecars too).
# init creates data/ automatically; the default DB lands at data/proteus.db.
rm -f data/proteus.db data/proteus.db-wal data/proteus.db-shm
python -m proteus.cli init
```

```bash
# …or wipe an existing DB and recreate without a prompt
python -m proteus.cli reset --yes
```

Optional sanity checks:

```bash
python -m proteus.cli migrate --dry-run   # expect: no migrations pending
python -m proteus.cli status              # core counts; DnD tables present, 0 events
```

---

## 2. Ingest the DataShop export

`ingest` parses **every** row into the shared `events` table. `--derive` then runs each
service's derive step, materializing `dnd_events` in the same command (and SA `responses`).
It also refreshes the question catalog. Ingest is **idempotent** — re‑running the same
file is a no‑op (file‑hash guard).

```bash
python -m proteus.cli ingest --derive data/S26_ULA_datashop.tab
```

```bash
# Equivalent split, if you want the two steps separately:
python -m proteus.cli ingest data/S26_ULA_datashop.tab   # → events only
python -m proteus.cli dnd derive                          # → dnd_events

# Derive can be scoped:
python -m proteus.cli dnd derive --question ula-proteus__ula-proteus-2-2-equivalence-representations-v1
python -m proteus.cli dnd derive --import-id 1
```

```bash
# Format is auto-detected from .tab; force it if needed:
python -m proteus.cli ingest --derive --format datashop_tab data/S26_ULA_datashop.tab
```

Check what landed:

```bash
python -m proteus.cli status   # DnD line shows counts by kind: drop / check / reset / page; boards=0
```

---

## 3. Reconstruct attempts  *(source‑free — no boards needed)*

Replays moves from the initial board into per‑action configurations, segments them into
attempts, and cross‑checks each `check` row against its logged `Input`. `mismatches`
should be **0** (replay == logged Input); non‑zero means dropped/missing move rows.

```bash
python -m proteus.cli dnd reconstruct
```

Attempt‑segmentation knobs (defaults are conservative):

```bash
python -m proteus.cli dnd reconstruct \
  --gap-seconds 1800 \      # idle gap that starts a new attempt
  --no-split-session \      # don't split on session change
  --no-split-page \         # don't split when a page view intervenes
  --split-on-reset          # treat every reset as an attempt boundary (off by default)
```

A *resumed* attempt (the same student's earlier attempt on the same logged question in the
database) records `prior_attempt_id`; its checks are stored not comparable (`resumed_attempt`)
because the platform restored the last checked board without logging a move. The D3.2 knob-10
alternative seeds the replay instead (dnd/0015):

```bash
python -m proteus.cli dnd reconstruct --seed-resumed   # a resumed attempt starts at the prior
                                                       # attempt's last decodable checked
                                                       # configuration (s_0 if none); seeded = 1
```

```bash
# Scope to one question:
python -m proteus.cli dnd reconstruct --question ula-proteus__ula-proteus-2-2-equivalence-representations-v1
```

Source‑free export already works at this point:

```bash
python -m proteus.cli dnd export checks               # → stdout
python -m proteus.cli dnd export checks -o checks.csv # → file
```

---

## 4. Build boards from PreTeXt source  *(required for validate / score)*

`sync-sources` caches one `.ptx` per question, sets `questions.source_xml` and
`questions.title` (the question's authored `<title>`, shown in the UI selectors), and runs
the DnD board handler (builds `dnd_boards` + cards/zones/key). Register the book first, then
either clone its public repo with `--book`, or point `--source-dir` at a local checkout
(`--output-dir` defaults to `data/question_sources`). Rows synced before core migration
`0014` have no title until a re-run backfills them (reported as `titles backfilled`).

```bash
# Register the book once: book id, public repo URL, built-HTML dir.
python -m proteus.cli textbook add ula-proteus https://github.com/<org>/ula-proteus.git /path/to/built/html

# Option A — clone the registered repo and extract in one step:
python -m proteus.cli sync-sources --book ula-proteus

# Option B — walk a local source checkout (the id is printed by `textbook add`):
python -m proteus.cli sync-sources \
  --source-dir /path/to/ula-proteus/source \
  --textbook-id <id> \
  --output-dir data/question_sources
```

```bash
# Rebuild boards from already-synced source (e.g. after changing board logic),
# without re-walking the source tree:
python -m proteus.cli dnd sync-boards
python -m proteus.cli dnd sync-boards --question ula-proteus__ula-proteus-2-2-equivalence-representations-v1
```

A question identifier retired in source while Runestone kept serving it (ULA's RREF item,
renamed 2025-07-05) binds to its successor's board through the **identifier alias**
(dnd/0014). Register the alias set once per database, after the source sync and before
`validate`. The ULA alias file is committed; the import is idempotent and prints the alias
set's sha256, which every `reconstruct` / `score` / `analyze` run records and the manifest
carries. Raw rows are never rewritten: attempts keep the logged `question_id` and gain
`canonical_question_id`; item-level tables (`boards`, `item_summary`, the per-item arms of
`dnd contrast`) are by canonical identifier.

```bash
python -m proteus.cli dnd alias import src/proteus/services/dragndrop/aliases/ula-proteus.csv
python -m proteus.cli dnd alias list
# one-off (refuses self-aliases, chains and an alias id that has its own board):
python -m proteus.cli dnd alias add <retired-id> <successor-id> --reason "renamed …" --source-commit <sha>
```

The three Fall 2025 offerings logged no drop or reset row before a calendar date (the
**logging regime**, dnd/0015): a check logged before its export's date is stored not
comparable (`no_move_logging`). The committed regime file keys on the exports' sha256; the
import refuses a sha256 the database has not imported (so it applies to a database holding
those exports), prints the regime set's sha256, which `reconstruct` records and the manifest
carries, and `reconstruct` refuses an import whose move rows contradict its row.

```bash
python -m proteus.cli dnd regime import src/proteus/services/dragndrop/logging_regimes/ula-proteus.csv
python -m proteus.cli dnd regime list
python -m proteus.cli dnd regime remove <sha256>
```

The owner's staff-account rulings (**account roles**, dnd/0016): an id ruled instructor,
researcher or test is withheld at `derive` — its rows stay in `events`, never reach
`dnd_events`, and so never reach an attempt, a score or an export. The rulings file is
gitignored (`data/private/ULA/account-roles.csv`); its ids embed names, so no command
prints one. Import it **before the first `ingest --derive`**: `derive` never removes a
derived row, and both the importer and `derive` refuse a database where a ruled account
is already derived. The import prints the roles set's sha256, which `derive` records and
the manifest carries with the counts by role.

```bash
python -m proteus.cli dnd roles import data/private/ULA/account-roles.csv   # before the first derive
python -m proteus.cli dnd roles list                                        # pseudonym, role, exclude — never an id
python -m proteus.cli dnd roles remove <id>
```

Cross‑validate boards against the data — the key inferred from `Correct` checks must match
the source‑derived key, and every `drag#`/`drop#` label seen must exist on its board.
An aliased identifier validates against its successor's board and reports
`key=match (alias of <successor>)`; a wrong alias fails as `mismatch`.
**Exits 1** on any failure:

```bash
python -m proteus.cli dnd validate
python -m proteus.cli dnd validate --question ula-proteus__ula-proteus-2-2-equivalence-representations-v1
```

---

## 5. Score and analyze  *(need boards from step 4)*

```bash
# Score every checked configuration against its board's key.
# Writes dnd_check_scores: σ, distance, baseline-corrected g, label-free σ̃,
# per-card, all-or-nothing, and the error profile — omissions, wrong_slot, and
# the two leaks. decoy_source_leak / decoy_target_leak are the journal's
# decoy-card / decoy-slot leak counts (L_P / L_R).
python -m proteus.cli dnd score

# Per-attempt process metrics → dnd_attempt_metrics: move/takeback/reset/check
# tallies, the move mix and excess moves, check cadence and post-check gains,
# timing (total/active, gaps) under the recorded idle threshold, first/final
# check outcomes, distinct/repeated state counts, and the convergence curve.
python -m proteus.cli dnd analyze
```

```bash
# Scope either to one question:
python -m proteus.cli dnd score   --question ula-proteus__ula-proteus-2-2-equivalence-representations-v1
python -m proteus.cli dnd analyze --question ula-proteus__ula-proteus-2-2-equivalence-representations-v1
```

---

## 6. Export

Eleven datasets. Omit `-o` to print to stdout; `--question` filters any of them (the ledger's three scope to one logged identifier).
`python -m proteus.cli dnd export --help` prints the same list from the registry.

| Dataset | One row per | Rows come from | Columns filled by |
| :-- | :-- | :-- | :-- |
| `checks` | check — logged configuration + reconstruction context | `derive` | `reconstruct` |
| `check_scores` | scored check — every scoring metric side by side | `score` | — |
| `attempt_metrics` | attempt — process metrics, segmentation and QC context | `reconstruct` | `analyze` |
| `segments` | materialized check / reset / session / active‑work span | `reconstruct` | — |
| `configurations` | configuration in replay order — the walk itself | `reconstruct` | — |
| `boards` | cardsort question — board spec + item‑design covariates | `sync-boards` | — |
| `modeling_table` | attempt — identifiers, outcomes, covariates, features | `reconstruct` | `sync-boards`, `score`, `analyze` |
| `item_summary` | cardsort question — diagnostic summary | `sync-boards` | `score`, `analyze` |
| `flow` | (step, quantity, breakdown, key) of the participant flow — rows in, lost, out and the reason, steps 0–8 (E-02; `import_sha256` is the offering key; `n_students` for the display's guard) | every stage | `reconstruct`, `score`, `analyze` |
| `readiness_split` | (breakdown, key, readiness) — attempts and students per readiness class, pooled and by item / offering / source version / flag / flag combination | `reconstruct` | `sync-boards` |
| `exclusion_probe` | (breakdown, key, readiness, eligibility, first-check, eventual) — counts only; the no-scored-check level as `none` | `reconstruct` | `score`, `analyze` |

Run a dataset before its "columns filled by" stages and you still get every row —
those columns just come out empty. (`analyze` fills the first/final‑check outcome
columns only once `score` has run.)

```bash
python -m proteus.cli dnd export check_scores    -o check_scores.csv
python -m proteus.cli dnd export attempt_metrics -o attempt_metrics.csv
python -m proteus.cli dnd export item_summary    -o item_summary.csv
python -m proteus.cli dnd export flow            -o flow.csv   # the ledger's three datasets (E-02) stay archive-internal: their by-offering rows are unsuppressed
python -m proteus.cli dnd export checks          -o checks.csv
python -m proteus.cli dnd export modeling_table  -o modeling_table.csv
python -m proteus.cli dnd export item_summary    -o item_summary.csv
python -m proteus.cli dnd export check_scores --question ula-proteus__ula-proteus-2-2-equivalence-representations-v1
```

---

## 7. Full end‑to‑end, one block  *(mirrors the green test)*

Edit `SRC` and `TAB`, then paste the whole block.

```bash
set -e
DB=data/proteus.db
BOOK=ula-proteus                         # <-- edit me (book xml id)
REPO=https://github.com/<org>/ula-proteus.git   # <-- edit me (public repo URL)
BUILD=/path/to/built/html                # <-- edit me (built HTML dir)
TAB=data/S26_ULA_datashop.tab            # <-- edit me (your DataShop export)

rm -f "$DB" "$DB-wal" "$DB-shm"
python -m proteus.cli --db "$DB" init
# python -m proteus.cli --db "$DB" dnd roles import data/private/ULA/account-roles.csv   # a corpus with rulings: before the first derive
python -m proteus.cli --db "$DB" ingest --derive "$TAB"
python -m proteus.cli --db "$DB" textbook add "$BOOK" "$REPO" "$BUILD"
python -m proteus.cli --db "$DB" sync-sources --book "$BOOK"
python -m proteus.cli --db "$DB" dnd reconstruct
python -m proteus.cli --db "$DB" dnd validate
python -m proteus.cli --db "$DB" dnd score
python -m proteus.cli --db "$DB" dnd analyze
python -m proteus.cli --db "$DB" dnd export check_scores -o check_scores.csv
python -m proteus.cli --db "$DB" status
```

**Source‑free variant** (no PreTeXt source on hand — stops before scoring):

```bash
set -e
DB=data/proteus.db
TAB=data/S26_ULA_datashop.tab            # <-- edit me

rm -f "$DB" "$DB-wal" "$DB-shm"
python -m proteus.cli --db "$DB" init
python -m proteus.cli --db "$DB" ingest --derive "$TAB"
python -m proteus.cli --db "$DB" dnd reconstruct
python -m proteus.cli --db "$DB" dnd analyze
python -m proteus.cli --db "$DB" dnd export checks -o checks.csv
python -m proteus.cli --db "$DB" status
```

---

## 8. Spot‑check the DB directly (optional)

Read‑only sanity queries with the `sqlite3` CLI:

```bash
sqlite3 data/proteus.db "SELECT kind, count(*) FROM dnd_events GROUP BY kind;"
sqlite3 data/proteus.db "SELECT count(*) AS boards FROM dnd_boards;"
sqlite3 data/proteus.db "SELECT count(*) AS scored FROM dnd_check_scores;"
sqlite3 data/proteus.db "SELECT question_id, count(*) FROM dnd_attempts GROUP BY question_id ORDER BY 2 DESC LIMIT 10;"
```

---

## 9. Frozen-run tooling  *(Paper 1: sensitivity, contrast, manifest)*

Three commands added for the frozen run (`paper1-frozen-run-procedure.md`
Stages 1.3, 4 and 5). All three read a finished primary run (`reconstruct →
score → analyze` with the frozen defaults); the two DnD commands refuse the
canonical `data/proteus.db` unless `--allow-canonical` is passed.

```bash
# RQ4 (D3.2): the primary + ten one-knob alternatives, each on a scratch copy
# under OUT/<knob>/ (scratch.sqlite, config.json with the F22 run configs,
# checks / attempt_metrics / modeling_table exports), then OUT/deltas.csv in the
# column order of tab-sensitivity-results. The RQ3 column stays empty unless
# --estimate is given; --knob k2-split-on-reset runs one alternative; the
# knob-10 run (--knob k10-seeded-replay) also writes seeded_strata.csv.
python -m proteus.cli dnd sensitivity run --primary-db /scratch/primary.sqlite --out /scratch/sens

# The bracket evidence (gap quantiles, session changes, page views, resets): counts only.
python -m proteus.cli --db /scratch/primary.sqlite dnd sensitivity evidence -o /scratch/evidence.md

# RQ3 (P5): counts-only by default — group sizes, items with >= 5 per arm, the
# reporting-level verdict. --estimate adds blocks 1–3, the structural
# descriptors, per-item rows and the representatives (seeded bootstrap; the
# representative rule frozen 2026-10-07, Stage 0.9 — standardized block-1
# vector, within group, within the item with most attempts; no flag).
python -m proteus.cli --db /scratch/primary.sqlite dnd contrast --out /scratch/rq3
python -m proteus.cli --db /scratch/primary.sqlite dnd contrast --out /scratch/rq3 --estimate --seed 20260907 --B 2000

# The run manifest (root command; never records a path).
python -m proteus.cli --db /scratch/primary.sqlite manifest -o /scratch/manifest.yaml --pseudonyms /private/offering-pseudonyms.csv
python -m proteus.cli manifest diff /scratch/manifest.yaml /archive/manifest.yaml
```

The knob table, the P6 materiality thresholds and the P5 group names are
constants in `sensitivity.py` and `contrast.py` with their freeze dates; a
configuration outside the table is labelled `post_hoc` in its `config.json`.
Group names are structural everywhere: `first_check_correct` and
`first_check_incorrect_eventually_correct`.

---

## Command reference

**Root (service‑agnostic)**

| Command | Purpose | Key flags |
| :-- | :-- | :-- |
| `init` | Create fresh DB (runs migrations; empty registry) | `--db` |
| `reset` | Delete & recreate DB | `--yes` |
| `migrate` | Apply pending migrations | `--dry-run` |
| `ingest <file>` | Parse all rows → `events` | `--derive`, `--question`, `--format {datashop_tab,runestone_useinfo_csv}` |
| `reingest-events <file>` | Backfill `events` from an imported file | `--format datashop_tab` |
| `sync-catalog` | Rebuild question catalog from events | *(also auto‑runs after ingest)* |
| `textbook add <book> <url> <build>` | Register a public book in the registry | `--ref`, `--notes` |
| `sync-sources` | Clone a registered book (`--book`) or walk a local tree; cache `.ptx` per question + run service source handlers (builds DnD boards) | `--book` **or** `--source-dir`+`--textbook-id`, `--clone-root`, `--output-dir` |
| `sync-questions` | Cache HTML previews from a **built** PreTeXt tree (SA) | `--build-dir`*, `--output-dir`*, `--textbook-id`* |
| `status` | DB summary incl. per‑service contributions | — |
| `manifest` | Run manifest: imports by sha256 + pseudonym, textbook refs, source pin, migrations, every run's config, table counts, one fingerprint; `manifest diff A B` | `-o/--output`, `--pseudonyms` |

**DnD namespace** — `python -m proteus.cli dnd <stage>`

| Stage | Purpose | Needs boards? | Key flags |
| :-- | :-- | :-- | :-- |
| `derive` | `events` → typed `dnd_events`; withholds the excluded accounts (dnd/0016) and prints the count | no | `--question`, `--import-id` |
| `sync-boards` | Rebuild boards from synced source | (builds them) | `--question` |
| `validate` | Key & label cross‑validation (exit 1 on fail); an aliased id reports `match (alias of …)` | yes | `--question` |
| `alias add` / `remove` / `list` / `import` | Question-identifier aliases at board binding (dnd/0014): a retired id resolves to its successor's board; `import` loads a committed alias file (idempotent; prints the alias set's sha256) | the successor's | `--reason`, `--source-commit`, `--recorded-on`, `--replace-board` |
| `regime import` / `list` / `remove` | Logging regimes (dnd/0015): per export (by sha256) the date from which moves were logged; a check before it is stored `no_move_logging`; `import` loads the committed file (idempotent; refuses an unknown sha256; prints the regime set's sha256) | no | — |
| `roles import` / `list` / `remove` | Account roles (dnd/0016): the owner's staff-account rulings; an id ruled `exclude = yes` is withheld at `derive` (its rows stay in `events`); `import` loads the gitignored rulings file before the first derive (idempotent; prints the roles set's sha256; refuses an excluded id that is already derived; prints no id) | no | — |
| `reconstruct` | Replay moves → attempts/configurations; check vs `Input`; `prior_attempt_id` and the structural causes (dnd/0015) | no | `--gap-seconds`, `--no-split-session`, `--no-split-page`, `--split-on-reset`, `--seed-resumed`, `--question` |
| `score` | Configurations → `dnd_check_scores` (all metrics) | yes | `--question` |
| `analyze` | Attempts → `dnd_attempt_metrics` | yes | `--question` |
| `export <dataset>` | Dump a view to CSV | depends on dataset | `-o/--output`, `--question`; datasets: `checks`, `check_scores`, `attempt_metrics`, `segments`, `configurations`, `boards`, `modeling_table`, `item_summary` (§6) |
| `sensitivity run` | RQ4 harness: primary + ten D3.2 alternatives on scratch copies → `deltas.csv` (§9; knob 10 adds `seeded_strata.csv`) | yes (a finished primary run) | `--primary-db`*, `--out`*, `--knob`, `--estimate`, `--allow-canonical` |
| `sensitivity evidence` | Bracket evidence: gap quantiles, session changes, page views, resets (counts only) | no | `-o/--output`, `--allow-canonical` |
| `contrast` | RQ3 runner: counts-only default; `--estimate` adds the three blocks, bootstrap intervals, representatives (§9) | yes | `--out`*, `--estimate`, `--seed`, `--B`, `--include-limited`, `--exclude-flag`, `--allow-canonical` |

\* = required flag.

**Global flags** (any command): `--db PATH` (default `data/proteus.db`), `-v` / `--verbose`.
