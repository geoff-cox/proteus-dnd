# proteus-dnd

The PROTEUS drag-and-drop pipeline. The Python package `proteus` reads the
event exports of a [Runestone](https://runestone.academy) textbook (DataShop
`.tab` files, or Runestone `useinfo` CSV) into one `events` table, binds
each drag-and-drop item to its board (cards, zones, key) from the book's
PreTeXt source, rebuilds every student's board after every logged move,
validates the rebuilt boards against the boards the platform logged at each
check, scores the attempts, computes per-attempt process features and
exports the datasets reported in the paper *Reconstructing and Validating
Drag-and-Drop Response Trajectories*. The paper cites one tagged release of
this repository.

This repository is a **filtered export** of a private repository
(`proteus-rs-pipeline`), which also holds a second question-type service, a
Streamlit UI, the documentation sources and the research records. Code is
edited there and exported here; nothing in this tree is edited in place.
`EXPORT.md` names the private commit each export was cut from and the tree
identifiers that prove the pipeline here is the one at that commit.

## What is here

| Path | What it is |
|---|---|
| `src/proteus/core/` | the service-agnostic core: the SQLite schema and its forward-only migrations, ingest of DataShop and Runestone exports into `events`, textbook registration and question-source sync, the run manifest, the question previewer |
| `src/proteus/services/dragndrop/` | the drag-and-drop service: board binding, event decoding, replay (reconstruct), validation against the logged checks, scoring, process features, the exports, the sensitivity harness and the process contrast; its migrations, the committed identifier-alias and logging-regime files |
| `src/proteus/paper1/` | the Paper 1 scripts as console commands (`proteus-*`): the manuscript tables, the manual-audit draw, the training-kit builder, the export and item inventories, the corpus facts, the replay-mismatch diagnosis |
| `src/proteus/cli.py` | the command line, `proteus` |
| `src/tests/` | the test suite and its hand-made fixtures (`src/tests/fixtures/README.md`) |
| `paper1/` | the manual-audit training kit, built from the fixtures (`paper1/audit-training-kit/README.md`), and the board-identity witness the frozen-run manifest cites |
| `docs/proteus_cli_cheatsheet.md` | the core operator document: every root command, flag and expected output |
| `docs/dnd-cli-cheatsheet.md` | the drag-and-drop operator document, stage by stage, with the end-to-end block the tests mirror |
| `data/` | where a checkout keeps its inputs and its database; nothing tracked but `README.md` and `.gitignore` |
| `OPEN-TODOS.md` | the parked items that code comments cite by number |
| `LICENSE` | the MIT license the pipeline is released under |
| `EXPORT.md` | the private commit this tree comes from, and the subtree ids that attest it |

## Install

Python 3.11 or 3.12 (CI runs both).

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
pytest
proteus --help
```

`pytest` runs the whole suite from the fixtures alone; nothing needs real
data. The `preview` extra (`pip install -e ".[dev,preview]"`, the PreTeXt
CLI pinned at 2.55.0) is only for building question previews: install it in
a virtual environment, never on a system Python with a Debian-patched
setuptools (its transitive pdfCropMargins wheel fails to build there). An
HTML preview build also needs Node/npm and, on the first build, outbound
access to the Runestone Services CDN. The preview tests mock the toolchain
and run without it.

## Running the pipeline

The command line is `proteus` (also `python -m proteus.cli`). The root
commands are service-agnostic (`init`, `migrate`, `ingest`, `status`,
`delete-import`, `sync-catalog`, `textbook`, `sync-sources`,
`sync-questions`, `manifest`) and the drag-and-drop service adds its own
namespace, `proteus dnd …`: `derive`, `sync-boards`, `validate`,
`reconstruct`, `score`, `analyze`, `export`, `alias`, `regime`, `roles`,
`sensitivity`, `contrast`. Every command, its flags, what to expect and the
order a full run uses them in is in the two cheatsheets:

- [`docs/proteus_cli_cheatsheet.md`](docs/proteus_cli_cheatsheet.md)
  (core) the database, ingest, the question catalog, textbooks and sources,
  previews, the manifest.
- [`docs/dnd-cli-cheatsheet.md`](docs/dnd-cli-cheatsheet.md)
  (drag-and-drop service) from an empty database to the exports, including the
  frozen-run tooling (the sensitivity harness, the contrast runner).

The shortest useful sequence, on one DataShop export of a book whose PreTeXt
source you have checked out:

```bash
proteus init
proteus ingest --derive data/private/<export>.tab
proteus textbook add <book_xml_id> <repository-url> <path-to-built-html>   # prints the numeric textbook_id
proteus sync-sources --source-dir <source-checkout>/src --textbook-id <textbook_id>
proteus dnd validate
proteus dnd reconstruct
proteus dnd score
proteus dnd analyze
proteus dnd export checks -o data/private/checks.csv   # exports go under data/private/, ignored wholesale
```

_Note: The database defaults to `data/proteus.db`; `--db PATH` on any command
overrides it. `--textbook-id` takes the integer `textbook_id` that
`textbook add` prints, not the book's `xml:id`. Without a book source,
`reconstruct`, `analyze` and `export checks` still run (the board-dependent
steps are `validate` and `score`)._

| Paper 1 commands       |  |
|------------------------|-------------------------------|
| `proteus-paper-tables` | `proteus-diagnose-mismatches` |
| `proteus-audit-sample` | `proteus-inventory-exports`   |
| `proteus-corpus-facts` | `proteus-inventory-items`     |
| `proteus-audit-kit`    |                               |

_Run `--help` for more information._

For example, the following command regenerates the committed training kit
byte for byte from the fixtures (a test does the same on every run).
```bash
proteus-audit-kit build --out paper1/audit-training-kit
```

## Data

No student data is in this repository and none may enter it. `data/README.md`
says where a checkout keeps its real exports (`data/private/`, ignored
wholesale) and the `.gitignore` rules that keep every `.tab` under `data/`,
and every `.csv` wherever a command writes it (the tracked fixtures, alias
and regime tables, the board witness and the kit are the negated
exceptions), out of git. The fixtures under `src/tests/fixtures/` are
hand-made; the training kit is built from them. Offerings are referred to by
pseudonym (`Y##-B##-P##`), never by a name.

## A note on the schema

`src/proteus/core/schema/core.sql` is the frozen version-3 baseline of the
private repository, split from a single schema when the services were
separated. It still declares tables of the short-answer service this export
does not include (`responses`, `vocabulary_versions`, `codebook_versions`,
…); they are created empty and nothing here reads or writes them. Every
change since version 3 lives in a migration (`src/proteus/migrations/` for
the core, `src/proteus/services/dragndrop/migrations/` for the service)
forward-only, and a fresh `init` equals a migrated database.

## Provenance

This tree has no inherited history: one commit per export, each generating
a fresh tree. The private repository's pull requests are the change
history. `EXPORT.md` records the private commit, the ids of the three
subtrees copied whole (`src/proteus/core`, `src/proteus/services/dragndrop`,
`src/proteus/paper1`) and of `cli.py` and `__init__.py`, so

```bash
git rev-parse HEAD:src/proteus/core
```

here equals `git rev-parse <private commit>:src/proteus/core` there. A later
tag whose `src/proteus` tree id equals the one recorded for a run's commit is
provably the same pipeline.

## License

MIT: see [`LICENSE`](LICENSE). `pyproject.toml` and `CITATION.cff` carry the
same SPDX identifier.

## Citation

`CITATION.cff`. The archived release's DOI is added at tag time.
