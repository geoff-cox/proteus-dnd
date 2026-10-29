"""The Paper 1 scripts as a package (``cc-task-public-export`` PR 2, ruling R1 (a)).

The seven scripts that drive the frozen run's Stage 3 and Stage 6 and the
planning inventories — ``paper_tables`` (the manuscript-table generator),
``audit_sample`` (the manual-audit draw and scorer), ``audit_training_kit``
(the reviewers' training kit), ``inventory_exports``, ``inventory_items``,
``tabulate_corpus_facts`` and ``diagnose_mismatches`` — lived under
``docs/dnd/scripts/`` until 2026-10-27 and are modules here since, each with
a console entry point (``proteus-paper-tables``, ``proteus-audit-sample``,
``proteus-audit-kit``, ``proteus-inventory-exports``,
``proteus-inventory-items``, ``proteus-corpus-facts``,
``proteus-diagnose-mismatches``; ``pyproject.toml``). Module docstrings keep
their specifications; ``SCRIPT_VERSION`` values are unchanged by the move.

This package is a *client* of the drag-and-drop service, not core: it imports
``proteus.services.dragndrop`` directly (the isolation lint allows exactly
that), reads only the archive or a database the operator names, and writes
only where ``audit_sample.guard_output_dir`` permits. The repository root
every module calls ``ROOT`` comes from ``_root.find_root()``.
"""
