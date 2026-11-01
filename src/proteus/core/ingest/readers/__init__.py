"""
Pure export readers (core).

Each reader turns one export format into a stream of ``Event`` objects and does
nothing else, no filtering by question type, no database access. The shared
``core.ingest`` layer registers the import and writes the events. Adding a
format is adding a module here and registering it.
"""

from __future__ import annotations
