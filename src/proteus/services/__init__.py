"""
Question-type services.

One package per question type (``shortanswer``, ``dragndrop``, …), each
exposing a ``ServiceDescriptor`` from its ``service`` module. Services are
discovered only through ``proteus.core.registry`` — core never imports a
service directly, and **no service may import another service's package**
(isolation invariants, plan §2.2/§2.4).
"""

from __future__ import annotations
