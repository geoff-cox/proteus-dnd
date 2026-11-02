# EXPORT.md — where this tree comes from

This repository is a **filtered export** of the private repository
`proteus-rs-pipeline` at the commit below, written by its `release/export.py`
from the path list `release/export-manifest.txt` there, with the public-only
files (`pyproject.toml`, `README.md`, `CITATION.cff`, `LICENSE`, the CI 
workflow, `data/README.md`, the two `.gitignore` files) taken from templates 
(all of it read from that one commit). It has no inherited history: one commit
per export, and each export replaces what git tracks here. Nothing here is
edited in place. A change is made in the private repository and exported again.

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
private_commit: 771265855a9a7b897a9cd641bc2572b475f9cc1b
package_version: 0.2.0
manifest_sha256: ef9e2c9146be07331c015831a63f5ef32e0cbad32a0f96990ce407f27bf19a70
subtree src/proteus/core: d336417187a82f3b2b744678f8804cabc5f6d652
subtree src/proteus/services/dragndrop: 0439187a26c10b273c36f7ca44be776831e3a813
subtree src/proteus/paper1: 227337217b117f1679fed99cb417c3bc735c3216
blob src/proteus/cli.py: b64595a4005253629af50d4d5bd08db1a8429ef4
blob src/proteus/__init__.py: 1709a4e32f5b74de2cb365f7c0cf14b6e8c8d71e
public_tree src/proteus: d76bdf13cfa7a98190e4e2a3c3526661ef78c11e
```
