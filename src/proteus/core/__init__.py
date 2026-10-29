"""
Shared, service-agnostic infrastructure for the PROTEUS pipeline.

This package is the home for everything that is *not* specific to a single
question-type service (short-answer, drag-and-drop, …). Per the multi-service
expansion plan (§2.1) it will grow to hold the DB layer, the generic event
ingest, the service registry, and the migration runner.

The first resident is the migration runner (`proteus.core.migrate`, plan item
A0.2). Modules are added here incrementally; nothing in `core` may import from
`proteus.services.*` except through the registry.
"""

from __future__ import annotations
