"""
Cohort trajectory aggregation: transition counts, the transition network,
and the distance-coarsened Markov chain (build item F21).

The service-layer home of the aggregates the dev harness
(``scripts/dnd/poc_dnd_metrics.py``) prototyped — promoted here so
the ``needs_trajectories`` charts (F24) and the journal-figure regeneration
(EX-8) read one implementation. Like :mod:`~proteus.services.dragndrop.cohort`
it is a pure, read-only aggregator: no ``altair``, no ``streamlit``, no
``pandas``, and the row filter is a view bound narrowing which *attempts* enter
the pool (the INDIRECT ``EXISTS`` linkage of ``filters.filtered_attempts``) —
never a compute input.

Four aggregates, with their journal homes:

* **Full-state transition counts** C_XY = #{observed ordinary moves X -> Y}
  (ch-baseline-features ``subsec-transition-counts``,
  ``g-baseline-transition-count``) and the **transition network** they weight —
  vertices are the observed states with their visit counts, directed edges
  carry C_XY (ch-visualization-diagnostics
  ``subsec-full-state-transition-network``). Ordinary moves are ``move`` /
  ``takeback`` rows; a reset's jump back to X0 is *not* an edge of the
  configuration graph.
* **The distance-coarsened Markov chain** (ch-statistical-modeling
  ``subsec-markov``): one-step transition counts pooled over every retained
  board-changing action — resets included as jumps — with the per-distance
  fix/lateral/break/jump move mix (``fig-cohort-markov``), the estimated
  transition matrix (``fig-cohort-markov-matrix``), and expected moves to
  solve **t** = (I-Q)^-1 **1** (``g-expected-moves-to-solve``) with d = 0
  absorbing.
* **Per-attempt distance curves** (F24; ch-visualization-diagnostics
  ``subsec-trajectory-distance-curves``): each attempt's d over its retained
  walk with the check positions marked — the data behind the convergence
  overlay (``fig-cohort-convergence``) and the per-attempt small multiples
  (``fig-cohort-small-multiples``). Curves are read from the same replayed
  walk as everything else here (the dev-harness encoding), not from the
  serialized ``dnd_attempt_metrics.convergence_json`` copy of it, so the
  curve, its check marks, and the Markov pool can never disagree — and the
  charts need only ``dnd reconstruct``, not the analyze pass.

The pooled walk is exactly the retained state-changing walk ``process.py``
analyzes: per attempt, the ``dnd_configurations`` rows minus checks (they never
change the board) and noop drops (F1), then collapsed to *board-changing* steps
by dropping any row whose state equals its predecessor's (so a reset pressed on
an already-reset board is no transition, matching the journal's "retained
board-changing actions" and the dev harness's dedup).

Estimation choices preserved verbatim from the dev harness (EX-8 requires the
regenerated figures to match):

* the move-mix ``jump`` share is the residual after fix/lateral/break — the
  reset-jump share under distance coarsening, which cannot see *why* a step
  moved |delta d| >= 2 (a reset landing within one step of the prior distance is
  counted in that band instead);
* the transient set is every distance d >= 1 with observed outflow; transition
  mass into d = 0 is absorption;
* ``solve_expected_moves`` is plain Gaussian elimination; a singular system
  (no observed path to the solved state) yields ``expected = None``, the
  "not finite" read-out.

One deliberate correction to the dev harness (PR #151 review): mass into a
*positive* distance with no observed outflow — a destination every walk ended
at — is a censored unsolved ending, not a solve. The dev estimator folded it
into the absorption deficit, which reads falsely finite (``{1: {2: 1}}`` would
report one expected move to solve from an attempt that ended unsolved). Here
such mass makes ``expected`` unavailable instead, matching the journal's own
conditioning ("if absorption is reachable from each retained transient
distance"); the observed move-mix rates and transition matrix — the two
journal figures — are what they always were.

Availability is the F24 gate: :func:`trajectory_data` returns ``None`` when the
question has no synced board or no reconstructed configurations at all, and an
all-empty bundle when only the row filter selects nothing (mirroring
``cohort_data``'s degrade-cleanly posture). An attempt whose stored walk no
longer fits the board (stale rows left by a re-synced source, the F17a gap) is
excluded whole and counted in ``n_excluded_attempts`` — dropping single states
would fabricate transitions across the gap.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import Counter, OrderedDict, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from proteus.core.filters import FilterSpec, events_conditions, has_event_filters
from proteus.services.dragndrop import aliases
from proteus.services.dragndrop.board import load_board
from proteus.services.dragndrop.scoring import distance

log = logging.getLogger(__name__)

__all__ = [
    "NetworkNode",
    "NetworkEdge",
    "MoveMixRate",
    "MatrixCell",
    "ExpectedMoves",
    "DistanceMarkov",
    "AttemptCurve",
    "TrajectoryData",
    "pooled_transitions",
    "distance_markov",
    "solve_expected_moves",
    "trajectory_data",
]

# The dnd_events kinds that are ordinary moves — the C_XY edge set. X0 (kind
# None) and resets contribute states/visits but never an edge.
_ORDINARY_MOVE_KINDS = frozenset({"move", "takeback"})


# ---------------------------------------------------------------------------
# The chart-ready bundle
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class NetworkNode:
    """One observed state and how often the retained walks visit it."""

    s: tuple[int, ...]
    visits: int


@dataclass(frozen=True)
class NetworkEdge:
    """C_XY: how many ordinary moves went s_from -> s_to."""

    s_from: tuple[int, ...]
    s_to: tuple[int, ...]
    count: int


@dataclass(frozen=True)
class MoveMixRate:
    """One fig-cohort-markov bar: the move mix out of one starting distance."""

    distance: int
    n_from: int
    fixing: float  # share to d-1
    lateral: float  # share staying at d
    breaking: float  # share to d+1
    jump: float  # residual share (|delta d| >= 2; the reset-jump band)


@dataclass(frozen=True)
class MatrixCell:
    """One fig-cohort-markov-matrix cell: p(from_d -> to_d) and its count."""

    from_d: int
    to_d: int
    count: int
    prob: float


@dataclass(frozen=True)
class ExpectedMoves:
    """One entry of t = (I-Q)^-1 1: expected moves to solve from a distance."""

    distance: int
    moves: float


@dataclass(frozen=True)
class DistanceMarkov:
    """The estimated distance chain, in the dev-harness display shapes."""

    n_transitions: int  # every pooled board-changing transition, d=0 rows too
    d_max: int
    rates: tuple[MoveMixRate, ...]  # one per distance 1..d_max with outflow
    matrix: tuple[MatrixCell, ...]  # those distances x to_d in 0..d_max
    transient: tuple[int, ...]  # the distances behind Q, ascending
    # None = not estimable: the system is singular (no observed path to the
    # solved state) or censored terminal mass exists (see module docstring).
    expected: tuple[ExpectedMoves, ...] | None


@dataclass(frozen=True)
class AttemptCurve:
    """One attempt's distance curve over its retained walk (F24).

    ``check_tidx`` holds each check's position as an index into ``d_curve``
    (the replayed walk position at which the check occurred), in check
    order; ``solved`` is whether the last check sat at d = 0. ``label`` is
    display-ready and unique per attempt (student id, suffixed with the
    attempt index past the first).

    Basis (A10/BUG-10, owner decision 2026-08-25 — documented, not
    changed): ``d_curve`` and ``solved`` are computed on the *replayed*
    walk, not from each check's logged Input (which the F10 outcome layer
    scores). On a ``check_mismatch`` attempt the two disagree — ``solved``
    can be False while F10's ``eventual_correct`` is 1 for the same
    attempt. Deliberately kept on the replayed basis, matching
    ``process.post_check_gains`` (see its docstring for the rationale).
    """

    attempt_id: int
    label: str
    d_curve: tuple[int, ...]
    check_tidx: tuple[int, ...]
    solved: bool


@dataclass(frozen=True)
class TrajectoryData:
    """One question's pooled trajectory aggregates (the F24 chart bundle)."""

    question_id: str
    n_attempts: int  # attempts whose walks entered the pool
    n_excluded_attempts: int  # board-invalid walks skipped (stale rows)
    start_distance: int  # d*(X0), the theoretical floor / headline row
    nodes: tuple[NetworkNode, ...]  # sorted by state
    edges: tuple[NetworkEdge, ...]  # sorted by (s_from, s_to)
    markov: DistanceMarkov | None  # None when no transitions were observed
    curves: tuple[AttemptCurve, ...]  # one per pooled attempt, walk order


# ---------------------------------------------------------------------------
# Pure estimation (unit-tested against the journal's worked example)
# ---------------------------------------------------------------------------
def pooled_transitions(
    state_walks: Iterable[Sequence[tuple[int, ...]]],
    key: tuple[int, ...],
) -> dict[int, Counter]:
    """
    Distance-coarsened one-step transition counts pooled over walks.

    Each walk is one attempt's retained board-changing state sequence (X0
    first); every consecutive pair contributes one transition, resets
    included. Returns ``{from_d: Counter({to_d: n})}``.
    """
    trans: defaultdict[int, Counter] = defaultdict(Counter)
    for walk in state_walks:
        ds = [distance(s, key) for s in walk]
        for a, b in zip(ds, ds[1:]):
            trans[a][b] += 1
    return dict(trans)


def solve_expected_moves(
    states: Sequence[int], q: Sequence[Sequence[float]]
) -> list[float] | None:
    """
    Solve (I - Q) t = 1 by Gaussian elimination; ``None`` if singular (some
    retained distance has no observed path to the solved state).
    """
    n = len(states)
    a = [[(1.0 if i == j else 0.0) - q[i][j] for j in range(n)] for i in range(n)]
    b = [1.0] * n
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(a[r][c]))
        if abs(a[piv][c]) < 1e-12:
            return None
        a[c], a[piv] = a[piv], a[c]
        b[c], b[piv] = b[piv], b[c]
        pv = a[c][c]
        a[c] = [x / pv for x in a[c]]
        b[c] /= pv
        for r in range(n):
            if r != c and a[r][c] != 0.0:
                f = a[r][c]
                a[r] = [a[r][k] - f * a[c][k] for k in range(n)]
                b[r] -= f * b[c]
    return b


def distance_markov(trans: Mapping[int, Mapping[int, int]]) -> DistanceMarkov | None:
    """
    The estimated distance chain from pooled transition counts, or ``None``
    when no transition was observed.

    Row-normalizes each starting distance's counts into the move-mix rates and
    the transition-matrix cells, then solves the transient block for expected
    moves to solve. All shapes and conventions match the dev harness (see the
    module docstring); rows out of d = 0 are pooled data but never displayed —
    the chain treats solved as absorbing.
    """
    counts = {
        d: {d2: n for d2, n in row.items() if n} for d, row in trans.items()
    }
    counts = {d: row for d, row in counts.items() if row}
    if not counts:
        return None

    n_transitions = sum(n for row in counts.values() for n in row.values())
    d_max = max(
        max(counts),
        max(max(row) for row in counts.values()),
    )

    rates: list[MoveMixRate] = []
    matrix: list[MatrixCell] = []
    for d in range(1, d_max + 1):
        row = counts.get(d, {})
        tot = sum(row.values())
        if tot == 0:
            continue
        fixing = row.get(d - 1, 0) / tot
        lateral = row.get(d, 0) / tot
        breaking = row.get(d + 1, 0) / tot
        rates.append(
            MoveMixRate(
                distance=d,
                n_from=tot,
                fixing=fixing,
                lateral=lateral,
                breaking=breaking,
                jump=1 - fixing - lateral - breaking,
            )
        )
        for to_d in range(0, d_max + 1):
            c = row.get(to_d, 0)
            matrix.append(MatrixCell(from_d=d, to_d=to_d, count=c, prob=c / tot))

    transient = tuple(
        d for d in range(1, d_max + 1) if sum(counts.get(d, {}).values()) > 0
    )
    idx = {d: i for i, d in enumerate(transient)}
    q = [[0.0] * len(transient) for _ in transient]
    for d in transient:
        row = counts[d]
        tot = sum(row.values())
        for d2, c in row.items():
            if d2 in idx:  # mass to d=0 (absorbing) is the deficit
                q[idx[d]][idx[d2]] = c / tot
    # Mass into a positive distance with no outflow is a censored unsolved
    # ending; folding it into the absorption deficit would read as solved
    # and make t falsely finite (PR #151 review). The expectation is
    # unavailable instead; the rates/matrix above stand as observed.
    censored = any(
        d2 > 0 and d2 not in idx for d in transient for d2 in counts[d]
    )
    t = None if censored else solve_expected_moves(transient, q)
    expected = (
        None
        if t is None
        else tuple(ExpectedMoves(distance=d, moves=t[idx[d]]) for d in transient)
    )

    return DistanceMarkov(
        n_transitions=n_transitions,
        d_max=d_max,
        rates=tuple(rates),
        matrix=tuple(matrix),
        transient=transient,
        expected=expected,
    )


# ---------------------------------------------------------------------------
# Read (the view bound) and assembly
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _Walk:
    """One attempt's retained walk with its display context (internal)."""

    attempt_id: int
    label: str
    steps: tuple[tuple[str | None, tuple[int, ...]], ...]  # (kind, state)
    check_tidx: tuple[int, ...]  # walk index each check graded


def _read_walks(
    conn: sqlite3.Connection,
    question_id: str,
    spec: FilterSpec,
    n_cards: int,
    n_zones: int,
) -> tuple[list[_Walk], int]:
    """
    Per-attempt retained board-changing walks (X0 first, kind ``None``) with
    each check's walk position, narrowed by ``spec`` — plus the count of
    attempts excluded because a stored state no longer fits the board.

    An attempt matches the events-only dimensions when one of its action
    events matches (the ``filters.filtered_attempts`` EXISTS precedent — its
    events share the same student/course/class, so any one is representative).
    """
    # dnd/0014: the item's logged identifiers (see cohort._read_checks).
    ids = aliases.item_ids(conn, question_id)
    conditions = [f"a.question_id IN ({', '.join('?' * len(ids))})"]
    params: list[str] = list(ids)
    if has_event_filters(spec):
        ev_conditions, ev_params = events_conditions(spec, "e")
        conditions.append(
            "EXISTS (SELECT 1 FROM dnd_configurations cf "
            "JOIN events e ON e.event_id = cf.event_id "
            "WHERE cf.attempt_id = a.attempt_id AND "
            + " AND ".join(ev_conditions)
            + ")"
        )
        params.extend(ev_params)
    rows = conn.execute(
        "SELECT c.attempt_id AS attempt_id, c.s_json AS s_json, "
        "c.is_check AS is_check, c.is_noop AS is_noop, d.kind AS kind, "
        "a.student_id AS student_id, a.attempt_index AS attempt_index "
        "FROM dnd_attempts a "
        "JOIN dnd_configurations c ON c.attempt_id = a.attempt_id "
        "LEFT JOIN dnd_events d ON d.event_id = c.event_id "
        "WHERE " + " AND ".join(conditions) + " "
        "ORDER BY c.attempt_id, c.seq",
        params,
    ).fetchall()

    grouped: "OrderedDict[int, list[sqlite3.Row]]" = OrderedDict()
    for row in rows:
        grouped.setdefault(row["attempt_id"], []).append(row)

    walks: list[_Walk] = []
    n_excluded = 0
    for attempt_id, configs in grouped.items():
        steps: list[tuple[str | None, tuple[int, ...]]] = []
        check_tidx: list[int] = []
        valid = True
        for c in configs:
            if c["is_check"]:
                # A check grades the current board; mark its walk position.
                if steps:
                    check_tidx.append(len(steps) - 1)
                continue
            if c["is_noop"]:
                continue  # never board-changing
            s = tuple(json.loads(c["s_json"]))
            if len(s) != n_cards or any(not (0 <= z <= n_zones) for z in s):
                valid = False
                break
            if steps and s == steps[-1][1]:
                continue  # e.g. a reset pressed on an already-reset board
            steps.append((c["kind"], s))
        if not valid:
            n_excluded += 1
            continue
        student = configs[0]["student_id"]
        index = configs[0]["attempt_index"]
        label = student if student else f"#{attempt_id}"
        if index and index > 1:
            label = f"{label} · #{index}"
        walks.append(
            _Walk(
                attempt_id=attempt_id,
                label=label,
                steps=tuple(steps),
                check_tidx=tuple(check_tidx),
            )
        )
    if n_excluded:
        log.warning(
            "%s: excluded %d attempt(s) whose reconstructed walk does not fit "
            "the current board (P=%d, R=%d) — likely a re-synced source",
            question_id, n_excluded, n_cards, n_zones,
        )
    return walks, n_excluded


def trajectory_data(
    conn: sqlite3.Connection,
    question_id: str,
    *,
    filter_spec: FilterSpec | None = None,
) -> TrajectoryData | None:
    """
    Aggregate one question's reconstructed walks into the trajectory bundle.

    Returns ``None`` when the question has no synced board or no replayed
    walk at all — never reconstructed, or a broken set holding only check
    rows — the F24 availability gate (``needs_trajectories`` charts
    disabled). Returns a valid all-empty bundle when a walk exists but
    ``filter_spec`` selects none of it, so the page degrades cleanly
    per-filter instead of gating the charts away. ``filter_spec`` is the
    read-time row filter applied as a view bound; its ``question_id`` field
    is ignored in favour of the explicit argument.
    """
    board = load_board(conn, question_id)
    if board is None:
        return None
    spec = filter_spec or FilterSpec()

    walks, n_excluded = _read_walks(
        conn, question_id, spec, board.n_cards, board.n_zones
    )
    # An attempt with no retained state at all (not even X0) has no walk —
    # a check-only configuration set is a broken reconstruction, not data.
    walks = [w for w in walks if w.steps]
    if not walks and not n_excluded:
        # The same identifiers `_read_walks` scoped to (dnd/0014): an item
        # whose walks live only under an alias is available, not gated.
        ids = aliases.item_ids(conn, question_id)
        any_walk_row = conn.execute(
            "SELECT 1 FROM dnd_attempts a "
            "JOIN dnd_configurations c ON c.attempt_id = a.attempt_id "
            f"WHERE a.question_id IN ({', '.join('?' * len(ids))}) "
            "AND c.is_check = 0 LIMIT 1",
            ids,
        ).fetchone()
        if any_walk_row is None:
            return None  # no walk anywhere: the charts gate closed

    visits: Counter = Counter()
    edges: Counter = Counter()
    curves: list[AttemptCurve] = []
    for walk in walks:
        for kind, s in walk.steps:
            visits[s] += 1
        for (_, s_prev), (kind, s) in zip(walk.steps, walk.steps[1:]):
            if kind in _ORDINARY_MOVE_KINDS:
                edges[(s_prev, s)] += 1
        d_curve = tuple(distance(s, board.key) for _, s in walk.steps)
        curves.append(
            AttemptCurve(
                attempt_id=walk.attempt_id,
                label=walk.label,
                d_curve=d_curve,
                check_tidx=walk.check_tidx,
                solved=bool(walk.check_tidx)
                and d_curve[walk.check_tidx[-1]] == 0,
            )
        )

    markov = distance_markov(
        pooled_transitions(
            [[s for _, s in walk.steps] for walk in walks], board.key
        )
    )

    return TrajectoryData(
        question_id=question_id,
        n_attempts=len(walks),
        n_excluded_attempts=n_excluded,
        start_distance=distance((0,) * board.n_cards, board.key),
        nodes=tuple(
            NetworkNode(s=s, visits=n) for s, n in sorted(visits.items())
        ),
        edges=tuple(
            NetworkEdge(s_from=a, s_to=b, count=n)
            for (a, b), n in sorted(edges.items())
        ),
        markov=markov,
        curves=tuple(curves),
    )
