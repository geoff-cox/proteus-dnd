# `data/`: where a checkout keeps its inputs and its database

Nothing under this folder is tracked except this file and `.gitignore`. The
pipeline creates what it needs on first use.

| Path | What goes there | Tracked? |
|---|---|---|
| `data/proteus.db` | the default SQLite database (`proteus init`; `--db PATH` on any command overrides it) | Never. The root `.gitignore` ignores `*.db` wherever it is |
| `data/private/` | the documented drop location for real platform exports (DataShop `.tab`, Runestone `useinfo` CSV) and for every file that names a person or an institution: a pseudonym map, a staff-account rulings file, an archive of a run | Never. Ignored wholesale; never quote a path from it in a commit, an issue or an output |
| `data/question_sources/` | the question-source cache written by `proteus sync-sources` (one `.ptx` snippet per question) | Never. Regenerable |
| `data/question_previews/` | the built question previews (`proteus sync-questions`, the previewer) | Never. Regenerable |

Every `.tab` and `.csv` anywhere under `data/` is ignored by default, so a
real export dropped here by mistake stays uncommittable; `git status` should
never list a file under `data/private/`. Real response exports do not leave
the machine they were downloaded to. A file whose name contains `LOCAL-ONLY`
declares itself uncommittable anywhere in the tree (the root `.gitignore`).

The test suite reads none of this: every fixture is hand-made and lives under
`src/tests/fixtures/`.
