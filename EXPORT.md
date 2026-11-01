# EXPORT.md — where this tree comes from

This repository is a **filtered export** of the private repository
`proteus-rs-pipeline` at the commit below, written by its `release/export.py`
from the path list `release/export-manifest.txt` there, with the public-only
files (`pyproject.toml`, `README.md`, `CITATION.cff`, `CLAUDE.md`,
`OPEN-TODOS.md`, `LICENSE`, the CI workflow, `data/README.md`, the two `.gitignore`
files) taken from templates — all of it read from that one commit. It has
no inherited history: one commit per export, and each export replaces what
git tracks here. Nothing here is edited in place — a change is made in the
private repository and exported again.

The identifiers below let a reader prove that the pipeline in this tree is
the one at that private commit: `git rev-parse HEAD:src/proteus/core` here
equals `git rev-parse <private_commit>:src/proteus/core` there, and so on
for the drag-and-drop service, the Paper 1 package, `cli.py` and
`__init__.py`. The last line is this tree's own `src/proteus` id
(`git rev-parse HEAD:src/proteus`); it differs from the private one, which
also holds the short-answer service. The frozen run's manifest records it as
`proteus_src_tree` beside `proteus_private_commit` (ruling R5 of
`cc-task-public-export`), so a later tag whose `src/proteus` id equals it is
provably the run's pipeline.

```
private_repo: proteus-rs-pipeline
private_commit: 86240b50211abc5dc37a8f9d035c8a5d5a77aa26
package_version: 0.2.0
manifest_sha256: fa66af5ecea1f0ce0378c2f8d589176a289e5f0055c197aed8ef44235afe0ada
subtree src/proteus/core: 58eefbc84347ead25073d1584739876cce2cd112
subtree src/proteus/services/dragndrop: af6aa1059c23e9b35e9203ad7eefe6859db832e3
subtree src/proteus/paper1: 330f6fddb245392f7c654a2ac14374d85422ff47
blob src/proteus/cli.py: 9a0d4e2ddd55c56c723155166dccda46f8758e92
blob src/proteus/__init__.py: 1709a4e32f5b74de2cb365f7c0cf14b6e8c8d71e
public_tree src/proteus: e5d97ad872675737201914db3a95358423fa86e0
```
