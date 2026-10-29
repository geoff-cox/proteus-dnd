# EXPORT.md — where this tree comes from

This repository is a **filtered export** of the private repository
`proteus-rs-pipeline` at the commit below, written by its `release/export.py`
from the path list `release/export-manifest.txt` there, with the public-only
files (`pyproject.toml`, `README.md`, `CITATION.cff`, `CLAUDE.md`,
`OPEN-TODOS.md`, the CI workflow, `data/README.md`, the two `.gitignore`
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
private_commit: cffd1479cdd416cf8b2dc2b3dd5e585e4ab345b9
package_version: 0.2.0
manifest_sha256: 00bae024b5d09da3ca83d64dbe79fd3f7dfe9bce36143b852c5f9ac762a713e5
subtree src/proteus/core: 8c82237e5cb7de8d850776bab2347d59fcb108d3
subtree src/proteus/services/dragndrop: 7457244162efc96282b30899647c3630c7d0b897
subtree src/proteus/paper1: 263238f190cf24fb2de1fc46e1572ef3391436c7
blob src/proteus/cli.py: bcf2603d39df8d5836577a7317971bb9e3f65126
blob src/proteus/__init__.py: 1709a4e32f5b74de2cb365f7c0cf14b6e8c8d71e
public_tree src/proteus: 5c91ea916245c9935a91112d3e6a36059e771a7e
```
