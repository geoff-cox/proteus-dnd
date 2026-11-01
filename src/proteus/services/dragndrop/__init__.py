"""
The Drag-and-Drop (DnD) question-type service (plan §2.5, Phase B).

Fully deterministic: no screening, no chains, no codebook, no LLM. Stages are
declared by this service's ServiceDescriptor (see service.py), discovered only
through proteus.core.registry, nothing outside proteus.services may import
this package directly.
"""
