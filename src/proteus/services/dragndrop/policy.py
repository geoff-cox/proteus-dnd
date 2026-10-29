"""
Feedback-policy classification (alignment item FP-3).

Runestone cardsorts have shipped two feedback policies (the platform version
at collection time decides which; it is not derivable from the question
source). This module is the single home of the vocabulary and of the gate /
disclosure-layer logic the journal describes in
``subsec-platform-feedback-policies``; :mod:`reconstruct` calls it per check
while replaying an attempt and persists the results. Pure functions, no
database access.

The two eras:

* ``legacy`` (through Spring 2026) — a single layer equivalent to the new
  policy's most disclosing one: every check is graded on any state, and the
  student is shown the grader counts *and* the highlighted wrong cards from
  the first press. (Only the ``Correct``/``Incorrect`` verdict is logged, so
  the disclosure is reconstructed, not read from the data.)
* ``layered`` (from mid-2026) — disclosure is gated by the placement count.

The disclosure layer names what the student saw at a check, unifying both
eras (legacy always discloses at the ``highlight`` level):

* ``refused`` (φ1) — the check was not graded: fewer than ``ℓ₀`` cards are
  placed, so the platform shows only the arithmetic ``ℓ₀ − placed`` count of
  cards left to place and does not advance the graded counter.
* ``count`` (φ2) — graded, count-level: the σ / w / u partition, no
  highlighting (a layered attempt's first two graded checks).
* ``highlight`` (φ3) — graded, card-level: the σ / w / u partition **and**
  the incorrectly placed cards highlighted (a layered attempt's third graded
  check onward, and every legacy check).
"""

from __future__ import annotations

__all__ = [
    "LEGACY",
    "LAYERED",
    "VALID_POLICIES",
    "LAYERS",
    "HIGHLIGHT_AFTER",
    "DEFAULT_EXPORT_POLICY",
    "DEFAULT_CHECK_COST",
    "is_graded",
    "disclosure_layer",
]

LEGACY = "legacy"
LAYERED = "layered"
VALID_POLICIES: tuple[str, ...] = (LEGACY, LAYERED)

# The F18 item-covariate era defaults (rule 11). The feedback policy is a
# property of the platform version at collection time — not of the question
# source — so the `boards` export cannot *know* it per question; until a
# per-era record exists (per import, or a dated cutover:
# dev/open-todos.md item 21), exports fill the
# `feedback_policy` covariate from this conservative default (every collected
# export predates the mid-2026 change). The honest granular record is FP-3's
# per-attempt `dnd_attempts.feedback_policy` — modeling joins should prefer
# it. `check_cost` follows the same era logic (tab-item-design-covariates:
# free / limited / penalized / delayed / refused-under-conditions): legacy
# checks are free; the layered era refuses below the placement gate.
DEFAULT_EXPORT_POLICY = LEGACY
DEFAULT_CHECK_COST = "free"

# Disclosure layers, least to most revealing (tab-layered-policy: φ1/φ2/φ3).
LAYERS: tuple[str, ...] = ("refused", "count", "highlight")

# The layered policy highlights the wrong cards from this graded check on.
HIGHLIGHT_AFTER = 3


def is_graded(policy: str, n_placed: int, n_nondecoy: int | None) -> bool | None:
    """
    Whether a check on a state with ``n_placed`` placed cards is graded.

    ``legacy`` grades every check regardless of placement. ``layered`` grades
    only when ``n_placed ≥ ℓ₀`` (``n_nondecoy``, every placed card counting —
    a placed decoy included). Returns ``None`` when the answer can't be
    decided: a layered attempt with no synced board, where ``ℓ₀`` is unknown.
    """
    if policy == LEGACY:
        return True
    if n_nondecoy is None:
        return None
    return n_placed >= n_nondecoy


def disclosure_layer(
    policy: str, graded: bool | None, graded_ordinal: int | None
) -> str | None:
    """
    The layer the student saw at a check, from the policy, whether the check
    was graded, and its 1-based ordinal **among graded checks** (``None`` for
    a refused check). ``None`` when ``graded`` is undecidable.

    ``legacy`` always discloses at ``highlight``. ``layered`` shows
    ``refused`` until the placement gate is met, then ``count`` for the first
    ``HIGHLIGHT_AFTER − 1`` graded checks and ``highlight`` from the
    ``HIGHLIGHT_AFTER``-th on.
    """
    if graded is None:
        return None
    if policy == LEGACY:
        return "highlight"
    if not graded:
        return "refused"
    if graded_ordinal is not None and graded_ordinal >= HIGHLIGHT_AFTER:
        return "highlight"
    return "count"
