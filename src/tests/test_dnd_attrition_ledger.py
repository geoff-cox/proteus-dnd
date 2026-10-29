"""
The ordered exclusion ledger (``cc-task-attrition-ledger.md`` E-02, PR 2 of
2; the PR 2 spec of 2026-10-19, rulings R3, R4, R8 (a), R9 (b), R10 (a),
R11 (a), R14, J5, J7–J10, M6): the three export datasets ``flow``,
``readiness_split`` and ``exclusion_probe`` on the attrition fixture, every
cell hand-derived from the fixture table and the rules.

The build mirrors ``test_dnd_attrition_fixture.py`` (whose module docstring
derives each attempt's flags, readiness and per-check cause): roles import
→ ingest → derive → the four-shapes board (q1 only; q9 stays board-less) →
the regime row keyed by the file's sha256 (``move_logging_from``
2026-06-02) → reconstruct → score → analyze. The fixture holds 71 rows of
sixteen students: 44 drops, 23 checks, 1 reset, 1 page row (the staff
account's) and the ``shortanswer`` / ``mChoice`` pair of ``AttritionSA``.

**The pooled flow, by step** (rows_in → rows_out; the loss in brackets).
Each quantity's survival row is the DnD event at steps 0–2 (no attempt
exists before step 3, J10) and the attempt from step 3 on (R9 (b): steps 4
and 5 lose checks and events, never an attempt; J9: items at Ready are the
items with a ready attempt). Checks at step 0 are the ``dragNdrop`` rows.

* step 0 raw — events 71; checks 23; students 16 (every id has a DnD row,
  R11 (a)); items 2 / 2 (q1, q9); traces 16 (one item per student).
* step 1 governance — the staff account's 5 rows (page, 3 drops, check):
  events 66 [5]; checks 22 [1]; students 15 [1]; traces 15 [1]; items 2.
* step 2 question_type — the ``shortanswer`` and ``mChoice`` rows (the
  only page row left at step 1): events 64 [2]; nothing else moves.
* step 3 source_alignment — ``AttritionNoBoard`` on q9 carries
  ``missing_source_map``: attempts 16 → 15 [1]; its 3 rows (window rule;
  every one of the 64 DnD rows lies in exactly one attempt window, so the
  "inside no window" count is 0): events 61 [3]; checks 21 [1]; students
  14 [1]; items 1 / 1 [q9]; traces 14 [1].
* step 4 event_parsing — the two ``parse_ok = 0`` rows, both checks
  (``AttritionMalformed``'s truncated Input, ``AttritionRegime``'s
  ``not json``; the re-decode's malformed test finds the same two, the
  second stored under ``no_move_logging``): events 59 [2]; checks 19 [2];
  attempts 15 [0]; students / items / traces unchanged (14 / 1 / 14).
* step 5 state_validity — the two payloads that decode with a cause, both
  ``duplicate_card`` (``AttritionDupCard``'s, stored so; ``AttritionResumed``
  attempt 2's, stored ``resumed_attempt``); no parseable move is off-board
  (``drag4 → drop3`` is the Pentagon slot, R = 3): events 57 [2]; checks
  17 [2]; attempts 15 [0].
* step 6 readiness — exclude ``AttritionDupCard`` and ``AttritionResumed``
  #2, limited ``AttritionMalformed``, ``AttritionRegime``,
  ``AttritionLimited``, ``AttritionNoCheck``: attempts 9 [6]; their rows
  still standing after steps 4–5 — 2, 1, 4, 0, 4, 2 — events 44 [13];
  their checks still standing — Malformed's agreeing check, Limited's —
  checks 15 [2]; students 9 [5] (``AttritionResumed`` keeps attempt 1);
  items 1; traces 9 [5].
* step 7 anchor_coverage — attempts only, from Ready: on this fixture every
  ready attempt has an eligible anchor, 9 → 9 [0]. The loss is reachable —
  a resumed attempt whose only check agrees with the replay is ``replay_ok``
  and ready through the shadow comparison while that check is stored under
  ``resumed_attempt``, so it has no eligible anchor — and
  ``TestStepSevenLoss`` pins it on a one-cell variant of the fixture (below).
* step 8 rq3_subset — attempts only, from Ready: ``AttritionSA`` is
  eventually incorrect (outcomes 0, 0), 9 → 8 [1] — ``contrast.
  read_population`` under the default config returns the same 8.

Where this differs from the spec's §3 cross-check: events at step 6 are
44, not 45 — ``AttritionMalformed`` keeps four rows after step 4 (three
drops and the agreeing check), not three, so the step-6 loss is 13; and
step 7 loses nothing on this fixture (not structurally: see above), so the
"non-zero loss at every step 1–8" expectation holds at steps 1–6 and 8 on
the frozen fixture and at every step 1–8 on the resumed-agrees variant.

**The variants** (``_variant`` / ``_split_fixture``: the frozen fixture
copied to ``tmp_path`` with one cell changed, or split in two; the frozen
file is never written; rows are selected by Session Id, never by id):

* resumed-agrees — session 712's check payload becomes ``{drop1: [drag3]}``,
  which decodes to (0, 0, 1, 0), the replay of its one move from s_0: the
  shadow comparison agrees, so attempt 2 reads ``replay_ok`` / ready, its
  check stays stored ``resumed_attempt`` (no eligible anchor) and it is
  scored at distance from the key (outcomes 0, 0). Flow: step 5 loses one
  check (DupCard's only), step 6 loses five attempts (15 → 10; 9 students,
  ``AttritionResumed`` holding two) and their 2 + 4 + 0 + 4 + 2 = 12 rows
  (58 → 46; checks 18 → 16), step 7 loses the resumed attempt (10 → 9),
  step 8 loses it and ``AttritionSA`` (10 → 8). Probe cell (ready, 0, 0, 0)
  = 1; readiness_split pooled ready 10 attempts / 9 students.
* off-board — session 717's first drop goes to ``drop5`` on the 3-slot
  board: the replay skips it (``invalid_state``), the takeback that follows
  is a no-op (the card is still in the tray, ``noop_move``), the key is
  placed and checked (``replay_ok``) — exclude. Step 5 loses the move
  beside the two checks (events 59 → 56), step 6 loses seven attempts
  (15 → 8) and 19 rows (56 → 37; checks 17 → 14).
* alias — ``demo-book__demo-shapes-q9`` registered as an alias of q1 after
  derive, before reconstruct: the board-less student replays against the
  four-shapes board under its logged labels, agrees, and reads ready (its
  key distance makes the outcomes 0, 0), so step 3 loses nothing and
  ``items_logged`` stays 2 at every step while ``items_canonical`` reads 1
  (R4); ``by_item`` and ``by_source_version`` key by the canonical item.
* two imports — the rows of sessions 701–709 and 710–717 in two files
  (every window in one import; the regime row on the second): two
  ``by_offering`` keys whose events, checks, attempts, students and traces
  sum to the pooled cells at every step, items not (q1 is in both), and
  the governance loss emitted in the first offering only, unguarded (J5).

``n_students`` is the distinct students among the quantity's surviving
entities at ``rows_out``, so on the events row it drops to 13 at step 4
(``AttritionRegime``'s only row is gone) while the students quantity still
reads 14 (its attempt stands until readiness, R9 (b)); on the checks row:
15, 14, 14, 13, 12 (Regime's only check), 11 (DupCard's only check), 9.
On the items and traces rows it equals the surviving students (16, 15, 15,
14, 14, 14, 9).

**readiness_split** pooled: ready 9, limited 4, exclude 3; by_item q1
9 / 4 / 2 and q9 exclude 1; by_source_version ``hash`` (the fixture
board's ``source_hash``) 9 / 4 / 2 and ``none`` exclude 1; by_flag counts
an attempt once per flag — replay_ok ready 9, limited 1 (Limited), exclude
1 (NoBoard); parse_error limited 2; invalid_state exclude 2;
missing_source_map exclude 1; long_idle_gap limited 1; ``(no flag)``
limited 1 (NoCheck); by_flag_combination keys are the stored
``qc_flags_json`` strings.

**exclusion_probe** pooled cross-tab (readiness, has_eligible_anchor,
first_check_correct, eventual_correct → attempts / students):
(ready, 1, 1, 1) 6 / 6 — RuledKept, Clean1–3, Resumed #1, Takeback;
(ready, 1, 0, 1) 2 — Revised, Many; (ready, 1, 0, 0) 1 — SA;
(limited, 1, 1, 1) 2 — Malformed (its agreeing check is scored), Limited;
(limited, 0, none, none) 2 — Regime (unscored check), NoCheck (no check);
(exclude, 1, none, none) 1 — NoBoard (an eligible anchor, no key to
score); (exclude, 0, 0, 0) 2 — DupCard and Resumed #2 (scored at distance
1 on the first-zone-kept decode; no eligible anchor). The two marginal
families follow by summation.

Readings the spec leaves open, resolved here: a row exists for every
(step, quantity, breakdown, key) with the non-applicable cells empty
(attempts at steps 0–2, J10; every other quantity at steps 7–8); the
sort orders are the literal (step, quantity, breakdown, key) and
(breakdown, key, readiness, …) tuples; ``readiness_split`` may emit
zero-count rows (they are ignored), ``exclusion_probe`` may not.
Privacy (M6): no cell carries a student id, a pseudonym or a path, and no
assertion message names one — the leak checks count, then drop the text and
the needles before asserting; the needle list is opaque to reprs, counts
are bound to a name before they are asserted, and no fixture value that
pytest would print as a funcarg carries a path.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from proteus.core import ingest as core_ingest
from proteus.core.ingest.imports import file_hash
from proteus.services.dragndrop import (
    account_roles, aliases, contrast, decode, export, logging_regimes, qc,
)
from proteus.services.dragndrop import attrition as attrition_mod
from proteus.services.dragndrop import reconstruct as reconstruct_mod
from proteus.services.dragndrop.board import load_board
from proteus.services.dragndrop.derive import derive_events
from proteus.services.dragndrop.process import analyze_all
from proteus.services.dragndrop.reconstruct import reconstruct_all
from proteus.services.dragndrop.scoring import score_checks

FIXTURE = "golden_dnd_attrition.tab"
ROLES = "account_roles_fixture.csv"
REGIME_DATE = "2026-06-02"

Q1 = "demo-book__demo-shapes-q1"
Q9 = "demo-book__demo-shapes-q9"

FLOW = "flow"
SPLIT = "readiness_split"
PROBE = "exclusion_probe"
DATASETS = (FLOW, SPLIT, PROBE)

FLOW_COLUMNS = (
    "step", "step_name", "quantity", "breakdown", "key",
    "rows_in", "rows_lost", "rows_out", "reason", "n_students",
)
SPLIT_COLUMNS = ("breakdown", "key", "readiness", "n_attempts", "n_students")
PROBE_COLUMNS = (
    "breakdown", "key", "readiness", "has_eligible_anchor",
    "first_check_correct", "eventual_correct", "n_attempts", "n_students",
)

STEP_NAMES = (
    "raw", "governance", "question_type", "source_alignment",
    "event_parsing", "state_validity", "readiness", "anchor_coverage",
    "rq3_subset",
)
QUANTITIES = (
    "students", "items_logged", "items_canonical", "item_traces_logged",
    "item_traces_canonical", "attempts", "events", "checks",
)
# The fixed reason string of each step (spec §2.2; the number fill quotes them).
REASONS = (
    "raw: every row of the imported exports",
    "governance: rows of instructor, researcher and test accounts "
    "(dnd_account_roles, exclude = 1); offerings outside the manifest: none "
    "by construction",
    "question type: rows of components other than card-to-zone items, and "
    "page views",
    "source alignment: attempts on a logged identifier with no board after "
    "the identifier alias (missing_source_map)",
    "event parsing: rows whose payload did not parse (parse_ok = 0) and "
    "checks whose payload is malformed; attempts carrying parse_error are "
    "retained as limited (readiness_split, by_flag)",
    "state validity: check payloads that do not decode to a unique, "
    "board-valid configuration (re-decoded at read time; a check under a "
    "structural cause is counted by its payload) and moves naming an "
    "off-board card or slot; attempts carrying invalid_state are counted at "
    "readiness",
    "readiness: attempts in the exclude and limited classes (readiness NULL "
    "reported as not_computed)",
    "anchor coverage: ready attempts with no eligible anchor",
    "RQ3 subset: ready attempts with a scored check that are not eventually "
    "correct",
)

# An empty cell triple (J10 and the attempts-only steps 7–8).
EMPTY = None

# The pooled flow: per quantity, (rows_in, rows_lost, rows_out, n_students)
# for steps 0..8 — each derived in the module docstring.
FLOW_POOLED = {
    "events": [
        (71, 0, 71, 16),   # 0: every row of the import
        (71, 5, 66, 15),   # 1: the staff account's page, 3 drops, check
        (66, 2, 64, 15),   # 2: shortanswer + mChoice (the page row went at 1)
        (64, 3, 61, 14),   # 3: NoBoard's 3 rows; no row outside a window
        (61, 2, 59, 13),   # 4: the two parse_ok = 0 checks; Regime has no row left
        (59, 2, 57, 13),   # 5: DupCard's and Resumed #2's duplicate-card checks
        (57, 13, 44, 9),   # 6: 2 + 1 + 4 + 0 + 4 + 2 rows of the six non-ready attempts
        EMPTY, EMPTY,      # 7, 8: attempts only
    ],
    "checks": [
        (23, 0, 23, 15),   # 0: the dragNdrop rows; everyone but NoCheck has one
        (23, 1, 22, 14),   # 1: the staff check
        (22, 0, 22, 14),   # 2: no check is a non-DnD row
        (22, 1, 21, 13),   # 3: NoBoard's check
        (21, 2, 19, 12),   # 4: Malformed's truncated check, Regime's not-json check
        (19, 2, 17, 11),   # 5: DupCard's (stored duplicate_card), Resumed #2's (stored resumed_attempt)
        (17, 2, 15, 9),    # 6: Malformed's agreeing check, Limited's check
        EMPTY, EMPTY,
    ],
    "attempts": [
        EMPTY, EMPTY, EMPTY,  # 0–2: no attempt before step 3 (J10)
        (16, 1, 15, 14),   # 3: dnd_attempts in scope; NoBoard lost; Resumed holds two
        (15, 0, 15, 14),   # 4: no attempt is lost at parsing (R9 b)
        (15, 0, 15, 14),   # 5: nor at validity (R9 b)
        (15, 6, 9, 9),     # 6: exclude 2 + limited 4 lost; the nine ready
        (9, 0, 9, 9),      # 7: every ready attempt has an eligible anchor
        (9, 1, 8, 8),      # 8: SA (eventual_correct 0) lost; read_population = 8
    ],
    "students": [
        (16, 0, 16, 16), (16, 1, 15, 15), (15, 0, 15, 15),  # staff at 1
        (15, 1, 14, 14),   # 3: NoBoard
        (14, 0, 14, 14), (14, 0, 14, 14),  # 4, 5: attempts survive, so do their students
        (14, 5, 9, 9),     # 6: Malformed, DupCard, Regime, Limited, NoCheck
        EMPTY, EMPTY,
    ],
    "items_logged": [
        (2, 0, 2, 16), (2, 0, 2, 15), (2, 0, 2, 15),  # q1 and q9
        (2, 1, 1, 14),     # 3: q9 has no other surviving attempt
        (1, 0, 1, 14), (1, 0, 1, 14),
        (1, 0, 1, 9),      # 6: q1 has ready attempts (J9); its ready students are 9
        EMPTY, EMPTY,
    ],
    "item_traces_logged": [
        (16, 0, 16, 16), (16, 1, 15, 15), (15, 0, 15, 15),
        (15, 1, 14, 14), (14, 0, 14, 14), (14, 0, 14, 14),
        (14, 5, 9, 9),     # 6: one trace per student; Resumed's trace keeps attempt 1
        EMPTY, EMPTY,
    ],
}
# No alias is registered, so the canonical counts equal the logged ones (R4).
FLOW_POOLED["items_canonical"] = FLOW_POOLED["items_logged"]
FLOW_POOLED["item_traces_canonical"] = FLOW_POOLED["item_traces_logged"]

# The same flow under `--question demo-book__demo-shapes-q1` (J7): the raw
# rows are the q1 rows only, so the page row, the two non-DnD rows and
# NoBoard's three rows are out of scope from step 0; the staff account's q1
# rows (3 drops, a check) still go at step 1; nothing goes at steps 2 and 3.
FLOW_Q1 = {
    "events": [
        (65, 0, 65, 15), (65, 4, 61, 14), (61, 0, 61, 14), (61, 0, 61, 14),
        (61, 2, 59, 13), (59, 2, 57, 13), (57, 13, 44, 9), EMPTY, EMPTY,
    ],
    "checks": [
        (22, 0, 22, 14), (22, 1, 21, 13), (21, 0, 21, 13), (21, 0, 21, 13),
        (21, 2, 19, 12), (19, 2, 17, 11), (17, 2, 15, 9), EMPTY, EMPTY,
    ],
    "attempts": [
        EMPTY, EMPTY, EMPTY, (15, 0, 15, 14), (15, 0, 15, 14), (15, 0, 15, 14),
        (15, 6, 9, 9), (9, 0, 9, 9), (9, 1, 8, 8),
    ],
    "students": [
        (15, 0, 15, 15), (15, 1, 14, 14), (14, 0, 14, 14), (14, 0, 14, 14),
        (14, 0, 14, 14), (14, 0, 14, 14), (14, 5, 9, 9), EMPTY, EMPTY,
    ],
    "items_logged": [
        (1, 0, 1, 15), (1, 0, 1, 14), (1, 0, 1, 14), (1, 0, 1, 14),
        (1, 0, 1, 14), (1, 0, 1, 14), (1, 0, 1, 9), EMPTY, EMPTY,
    ],
    "item_traces_logged": [
        (15, 0, 15, 15), (15, 1, 14, 14), (14, 0, 14, 14), (14, 0, 14, 14),
        (14, 0, 14, 14), (14, 0, 14, 14), (14, 5, 9, 9), EMPTY, EMPTY,
    ],
}
FLOW_Q1["items_canonical"] = FLOW_Q1["items_logged"]
FLOW_Q1["item_traces_canonical"] = FLOW_Q1["item_traces_logged"]

READY, LIMITED, EXCLUDE = qc.READY, qc.LIMITED, qc.EXCLUDE
NO_FLAG = "(no flag)"

# readiness_split: (n_attempts, n_students) per cell.
SPLIT_POOLED = {READY: (9, 9), LIMITED: (4, 4), EXCLUDE: (3, 3)}
SPLIT_POOLED_Q1 = {READY: (9, 9), LIMITED: (4, 4), EXCLUDE: (2, 2)}  # NoBoard is q9
SPLIT_BY_ITEM = {
    (Q1, READY): (9, 9), (Q1, LIMITED): (4, 4), (Q1, EXCLUDE): (2, 2),  # DupCard, Resumed #2
    (Q9, EXCLUDE): (1, 1),  # NoBoard
}
SPLIT_BY_FLAG = {
    ("replay_ok", READY): (9, 9),      # the nine ready attempts
    ("replay_ok", LIMITED): (1, 1),    # Limited (replay_ok + long_idle_gap)
    ("replay_ok", EXCLUDE): (1, 1),    # NoBoard (replay_ok + missing_source_map)
    ("parse_error", LIMITED): (2, 2),  # Malformed, Regime
    ("missing_source_map", EXCLUDE): (1, 1),
    ("invalid_state", EXCLUDE): (2, 2),  # DupCard, Resumed #2
    ("long_idle_gap", LIMITED): (1, 1),
    (NO_FLAG, LIMITED): (1, 1),        # NoCheck: the empty set
}
SPLIT_BY_COMBINATION = {
    ('["replay_ok"]', READY): (9, 9),
    ('["replay_ok", "missing_source_map"]', EXCLUDE): (1, 1),
    ('["parse_error"]', LIMITED): (2, 2),
    ('["invalid_state"]', EXCLUDE): (2, 2),
    ('["replay_ok", "long_idle_gap"]', LIMITED): (1, 1),
    ("[]", LIMITED): (1, 1),
}

# exclusion_probe pooled: (readiness, has_eligible_anchor, first_check_correct,
# eventual_correct) -> (n_attempts, n_students), as derived in the docstring.
PROBE_CROSS = {
    (READY, "1", "1", "1"): (6, 6),
    (READY, "1", "0", "1"): (2, 2),
    (READY, "1", "0", "0"): (1, 1),
    (LIMITED, "1", "1", "1"): (2, 2),
    (LIMITED, "0", "none", "none"): (2, 2),
    (EXCLUDE, "1", "none", "none"): (1, 1),
    (EXCLUDE, "0", "0", "0"): (2, 2),
}
PROBE_READINESS_BY_EC = {  # has_eligible_anchor and first_check_correct = all
    (READY, "all", "all", "1"): (8, 8),
    (READY, "all", "all", "0"): (1, 1),
    (LIMITED, "all", "all", "1"): (2, 2),
    (LIMITED, "all", "all", "none"): (2, 2),
    (EXCLUDE, "all", "all", "none"): (1, 1),
    (EXCLUDE, "all", "all", "0"): (2, 2),
}
PROBE_ANCHOR_BY_EC = {  # readiness and first_check_correct = all
    ("all", "1", "all", "1"): (10, 10),   # 8 ready + Malformed + Limited
    ("all", "1", "all", "0"): (1, 1),     # SA
    ("all", "1", "all", "none"): (1, 1),  # NoBoard
    ("all", "0", "all", "none"): (2, 2),  # Regime, NoCheck
    ("all", "0", "all", "0"): (2, 2),     # DupCard, Resumed #2
}
PROBE_MARGINALS = {**PROBE_READINESS_BY_EC, **PROBE_ANCHOR_BY_EC}
PROBE_POOLED = {**PROBE_CROSS, **PROBE_MARGINALS}

HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _build(conn, four_shapes_board, roles, tabs, *, regime_on=None, before_reconstruct=None):
    """The frozen-run order on one database (as in
    test_dnd_attrition_fixture.py): roles import, ingest, derive, the board
    (q1 only; q9 stays board-less), the regime row (after the ingest, keyed
    by the file's sha256 — on every tab, or on `regime_on`), then
    reconstruct → score → analyze. `before_reconstruct(conn)` runs between
    the regime row and reconstruct (the alias variant). Returns a namespace
    of the connection and the import sha256 values — never a path, since
    pytest prints a failing test's funcargs (M6)."""
    account_roles.import_roles(conn, roles)
    for tab in tabs:
        core_ingest.ingest_file(conn, tab)
    derive_events(conn)
    four_shapes_board(conn)
    for tab in (regime_on or tabs):
        logging_regimes.add_regime(
            conn, file_hash(tab), REGIME_DATE,
            reason="fixture: one check logged before the component logged moves",
        )
    if before_reconstruct is not None:
        before_reconstruct(conn)
    reconstruct_all(conn)
    score_checks(conn)
    analyze_all(conn)
    offerings = tuple(file_hash(tab) for tab in tabs)
    return SimpleNamespace(conn=conn, sha256=offerings[0], offerings=offerings)


@pytest.fixture
def attrition(db_conn, fixtures_dir, four_shapes_board):
    """The frozen fixture, built in the frozen-run order."""
    return _build(db_conn, four_shapes_board, fixtures_dir / ROLES, [fixtures_dir / FIXTURE])


# The fixture's columns by position (the DataShop header). The variants
# select rows by Session Id and Selection, never by a student id.
_ACTION, _SESSION, _SELECTION, _INPUT = 2, 7, 9, 10


def _variant(tmp_path, fixtures_dir, name, edit, *, n_changed=1):
    """A copy of the frozen fixture in tmp_path with `edit(columns)` applied
    to every row (it returns the row's columns, changed or not); exactly
    `n_changed` rows may differ. The frozen file is never written."""
    lines = (fixtures_dir / FIXTURE).read_text(encoding="utf-8").splitlines()
    header, body = lines[0], lines[1:]
    edited = ["\t".join(edit(line.split("\t"))) for line in body]
    changed = sum(1 for before, after in zip(body, edited) if before != after)
    assert changed == n_changed
    path = tmp_path / name
    path.write_text(header + "\n" + "\n".join(edited) + "\n", encoding="utf-8")
    return path


def _split_fixture(tmp_path, fixtures_dir):
    """The frozen fixture's rows split by session into two files — sessions
    701–709 and 710–717 — so every attempt window lies in one import."""
    lines = (fixtures_dir / FIXTURE).read_text(encoding="utf-8").splitlines()
    header, body = lines[0], lines[1:]
    first = [line for line in body if int(line.split("\t")[_SESSION]) <= 709]
    second = [line for line in body if int(line.split("\t")[_SESSION]) >= 710]
    assert len(first) + len(second) == len(body) == 71
    paths = []
    for name, rows in (("first.tab", first), ("second.tab", second)):
        path = tmp_path / name
        path.write_text(header + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
        paths.append(path)
    return paths


def _resumed_agrees(columns):
    # Session 712's check: a payload that decodes to the replay of its one
    # move from s_0 (drag3 → drop1), so the shadow comparison agrees.
    if columns[_SESSION] == "712" and columns[_SELECTION] == "dragNdrop":
        payload = json.dumps({f"{Q1}_drop1": [f"{Q1}_drag3"]})
        columns[_ACTION] = payload
        columns[_INPUT] = payload
    return columns


def _off_board_drop(columns):
    # Session 717's first drop lands on drop5 — off the 3-slot board.
    if columns[_SESSION] == "717" and columns[_ACTION].endswith("_drop3"):
        columns[_ACTION] = columns[_ACTION].replace("_drop3", "_drop5")
    return columns


@pytest.fixture
def resumed_agrees(db_conn, tmp_path, fixtures_dir, four_shapes_board):
    tab = _variant(tmp_path, fixtures_dir, "resumed_agrees.tab", _resumed_agrees)
    return _build(db_conn, four_shapes_board, fixtures_dir / ROLES, [tab])


@pytest.fixture
def off_board(db_conn, tmp_path, fixtures_dir, four_shapes_board):
    tab = _variant(tmp_path, fixtures_dir, "off_board.tab", _off_board_drop)
    return _build(db_conn, four_shapes_board, fixtures_dir / ROLES, [tab])


@pytest.fixture
def aliased(db_conn, fixtures_dir, four_shapes_board):
    def register(conn):
        assert aliases.add_alias(conn, Q9, Q1, reason="test: q9 is q1") == "added"

    return _build(
        db_conn, four_shapes_board, fixtures_dir / ROLES, [fixtures_dir / FIXTURE],
        before_reconstruct=register,
    )


@pytest.fixture
def two_imports(db_conn, tmp_path, fixtures_dir, four_shapes_board):
    first, second = _split_fixture(tmp_path, fixtures_dir)
    # The regime student's row is in the second file.
    return _build(
        db_conn, four_shapes_board, fixtures_dir / ROLES, [first, second], regime_on=[second],
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _read_csv(text: str) -> tuple[list[str], list[dict]]:
    reader = csv.reader(io.StringIO(text))
    header = next(reader)
    rows = [dict(zip(header, r)) for r in reader]
    return header, rows


def _export_text(conn, dataset, **kw) -> str:
    buf = io.StringIO()
    export.export_dataset(conn, dataset, buf, **kw)
    return buf.getvalue()


def _export(conn, dataset, **kw) -> tuple[list[str], list[dict]]:
    return _read_csv(_export_text(conn, dataset, **kw))


def _n(cell: str) -> int | None:
    """A count cell: an int, or None for the empty cell (J10)."""
    return None if cell == "" else int(cell)


def _cells(row: dict) -> tuple | None:
    """(rows_in, rows_lost, rows_out, n_students) of a flow row, None when
    the three count cells are empty."""
    counts = tuple(_n(row[c]) for c in ("rows_in", "rows_lost", "rows_out"))
    if counts == (None, None, None):
        return None
    return (*counts, _n(row["n_students"]))


def _flow_index(rows: list[dict]) -> dict[tuple, dict]:
    """{(step, quantity, breakdown, key): row}; asserts the key is unique."""
    index: dict[tuple, dict] = {}
    for r in rows:
        key = (int(r["step"]), r["quantity"], r["breakdown"], r["key"])
        assert key not in index, f"duplicate flow row at step {key[0]} / {key[1]} / {key[2]}"
        index[key] = r
    return index


def _pooled_flow(rows: list[dict]) -> dict[tuple, dict]:
    return {
        (step, quantity): r
        for (step, quantity, breakdown, key), r in _flow_index(rows).items()
        if breakdown == "pooled"
    }


def _split_cells(rows: list[dict], breakdown: str) -> dict:
    """{(key, readiness): (n_attempts, n_students)} of one breakdown,
    zero-count rows left out."""
    return {
        (r["key"], r["readiness"]): (int(r["n_attempts"]), int(r["n_students"]))
        for r in rows
        if r["breakdown"] == breakdown and int(r["n_attempts"]) > 0
    }


def _probe_cells(rows: list[dict], breakdown: str) -> dict:
    return {
        (r["readiness"], r["has_eligible_anchor"], r["first_check_correct"], r["eventual_correct"]):
        (int(r["n_attempts"]), int(r["n_students"]))
        for r in rows
        if r["breakdown"] == breakdown
    }


class _Needles:
    """The private values no output may carry, opaque to reprs: pytest
    prints a failing test's funcargs and a failing frame's arguments, so the
    values live behind an iterator and a repr that shows only their number
    (M6)."""

    __slots__ = ("_values",)

    def __init__(self, values) -> None:
        self._values = tuple(v for v in values if v)

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        return f"<{len(self._values)} needles>"


def _assert_no_leak(text: str, needles) -> None:
    # The text and the needles are dropped before the assert so a failure's
    # traceback (which prints the frame's arguments) cannot echo a leaked
    # value or the needle list (M6).
    leaked = sum(1 for needle in needles if needle in text)
    del text, needles
    assert leaked == 0, f"{leaked} private value(s) leaked into an export"


# ---------------------------------------------------------------------------
# The registry side of the three datasets
# ---------------------------------------------------------------------------
class TestRegistration:
    def test_columns_as_specified(self):
        assert export.DATASETS[FLOW].columns == FLOW_COLUMNS
        assert export.DATASETS[SPLIT].columns == SPLIT_COLUMNS
        assert export.DATASETS[PROBE].columns == PROBE_COLUMNS

    def test_headers_match_the_columns(self, attrition):
        for name, columns in ((FLOW, FLOW_COLUMNS), (SPLIT, SPLIT_COLUMNS), (PROBE, PROBE_COLUMNS)):
            header, _ = _export(attrition.conn, name)
            assert header == list(columns), name

    def test_flow_description_says_the_check_counts_are_decode_outcomes(self):
        # J8: the ledger's step-4/5 check counts are decode outcomes, the
        # generator's cause tables stored causes; the description says so.
        assert "decode" in export.DATASETS[FLOW].description.lower()


# ---------------------------------------------------------------------------
# flow: the pooled ledger, cell by cell
# ---------------------------------------------------------------------------
class TestFlowPooled:
    def test_every_step_and_quantity_has_one_pooled_row(self, attrition):
        _, rows = _export(attrition.conn, FLOW)
        pooled = _pooled_flow(rows)
        assert set(pooled) == {(s, q) for s in range(9) for q in QUANTITIES}
        for (step, _), r in pooled.items():
            assert r["step_name"] == STEP_NAMES[step]
            assert r["key"] == "pooled"

    @pytest.mark.parametrize("quantity", QUANTITIES)
    def test_pooled_cells(self, attrition, quantity):
        _, rows = _export(attrition.conn, FLOW)
        pooled = _pooled_flow(rows)
        got = [_cells(pooled[(step, quantity)]) for step in range(9)]
        assert got == FLOW_POOLED[quantity]

    def test_students_n_students_equals_rows_out(self, attrition):
        _, rows = _export(attrition.conn, FLOW)
        for r in rows:
            if r["quantity"] == "students" and r["rows_out"] != "":
                assert r["n_students"] == r["rows_out"], r["step"]

    def test_reason_strings_are_fixed_per_step(self, attrition):
        _, rows = _export(attrition.conn, FLOW)
        assert rows
        for r in rows:
            assert r["reason"] == REASONS[int(r["step"])], (r["step"], r["quantity"])

    def test_sorted_by_step_quantity_breakdown_key(self, attrition):
        _, rows = _export(attrition.conn, FLOW)
        keys = [(int(r["step"]), r["quantity"], r["breakdown"], r["key"]) for r in rows]
        assert keys == sorted(keys)

    def test_by_offering_is_the_one_import_and_equals_pooled(self, attrition):
        # One import: the by_offering breakdown has one key, the fixture's
        # sha256 (M5: never a pseudonym), and every cell equals the pooled one
        # — governance losses included, unguarded (J5, R10 a).
        _, rows = _export(attrition.conn, FLOW)
        index = _flow_index(rows)
        assert {r["breakdown"] for r in rows} == {"pooled", "by_offering"}
        offering_keys = {key for (_, _, breakdown, key) in index if breakdown == "by_offering"}
        assert offering_keys == {attrition.sha256}
        assert HEX64.match(attrition.sha256)
        for step in range(9):
            for quantity in QUANTITIES:
                pooled = index[(step, quantity, "pooled", "pooled")]
                offering = index[(step, quantity, "by_offering", attrition.sha256)]
                assert _cells(offering) == _cells(pooled), (step, quantity)
                assert offering["step_name"] == pooled["step_name"]
                assert offering["reason"] == pooled["reason"]

    def test_a_loss_at_every_step_but_anchor_coverage(self, attrition):
        # Steps 1–6 and 8 each lose something in at least one quantity. Step
        # 7 loses nothing on this fixture — every ready attempt has an
        # eligible anchor — not structurally: a ready attempt without one
        # needs a check under a structural cause whose shadow comparison
        # agrees, which TestStepSevenLoss builds from a one-cell variant.
        _, rows = _export(attrition.conn, FLOW)
        pooled = _pooled_flow(rows)
        lost = {
            step: sum(_n(pooled[(step, q)]["rows_lost"]) or 0 for q in QUANTITIES)
            for step in range(9)
        }
        assert lost[0] == 0
        assert lost[7] == 0
        for step in (1, 2, 3, 4, 5, 6, 8):
            assert lost[step] > 0, step

    def test_step_8_is_read_population_under_the_default_config(self, attrition):
        # contrast.read_population's rule (readiness ready, no excluded flag,
        # first_check_correct not NULL, eventual_correct = 1) is step 8's.
        _, rows = _export(attrition.conn, FLOW)
        pooled = _pooled_flow(rows)
        population = contrast.read_population(attrition.conn)
        assert _n(pooled[(8, "attempts")]["rows_out"]) == len(population) == 8
        assert _n(pooled[(7, "attempts")]["rows_in"]) == _n(pooled[(8, "attempts")]["rows_in"]) == 9


# ---------------------------------------------------------------------------
# flow: the telescoping invariant, generic over every (quantity, breakdown, key)
# ---------------------------------------------------------------------------
def _telescopes(rows: list[dict]) -> int:
    """Check every (quantity, breakdown, key) series of a flow export and
    return the number of series checked."""
    series: dict[tuple, dict[int, dict]] = {}
    for r in rows:
        series.setdefault((r["quantity"], r["breakdown"], r["key"]), {})[int(r["step"])] = r
    assert series
    for (quantity, breakdown, key), by_step in series.items():
        where = (quantity, breakdown)
        assert sorted(by_step) == list(range(9)), where
        cells = {step: _cells(by_step[step]) for step in range(9)}
        # The empty-cell pattern: attempts have no value before step 3
        # (J10); steps 7 and 8 are attempts only.
        if quantity == "attempts":
            assert [cells[s] is None for s in range(9)] == [True] * 3 + [False] * 6, where
        else:
            assert [cells[s] is None for s in range(9)] == [False] * 7 + [True] * 2, where
        # rows_in − rows_lost == rows_out on every row; nothing negative.
        for step, c in cells.items():
            if c is None:
                continue
            rows_in, rows_lost, rows_out, n_students = c
            assert rows_in - rows_lost == rows_out, (where, step)
            assert rows_lost >= 0 and rows_out >= 0, (where, step)
            if n_students is not None:
                assert 0 <= n_students, (where, step)
        # rows_in(k+1) == rows_out(k) for k = 0…5 wherever both exist.
        for k in range(6):
            if cells[k] is not None and cells[k + 1] is not None:
                assert cells[k + 1][0] == cells[k][2], (where, k)
        # Steps 7 and 8 are siblings: both start from Ready (rows_out of 6).
        if quantity == "attempts":
            assert cells[7][0] == cells[8][0] == cells[6][2], where
        # Step 0 removes nothing.
        if cells[0] is not None:
            assert cells[0][1] == 0 and cells[0][0] == cells[0][2], where
    return len(series)


class TestFlowTelescoping:
    def test_telescoping_pooled_and_by_offering(self, attrition):
        _, rows = _export(attrition.conn, FLOW)
        # 8 quantities × (pooled + the one offering).
        assert _telescopes(rows) == 16

    def test_telescoping_under_the_question_scope(self, attrition):
        _, rows = _export(attrition.conn, FLOW, question_id=Q1)
        assert _telescopes(rows) == 16


# ---------------------------------------------------------------------------
# flow: the hidden step-4 / step-5 cases (R8 a) tied to the stored evidence
# ---------------------------------------------------------------------------
class TestHiddenCases:
    def _stored_causes(self, conn) -> dict[str, int]:
        found = {
            r["anchor_cause"]: r["n"] for r in conn.execute(
                "SELECT anchor_cause, count(*) AS n FROM dnd_configurations "
                "WHERE is_check = 1 AND anchor_cause IS NOT NULL GROUP BY anchor_cause"
            )
        }
        return {cause: found.get(cause, 0) for cause in qc.ANCHOR_CAUSE_ORDER}

    def _redecoded(self, conn) -> dict[str, int]:
        """The read-time re-decode of every reconstructed check's verbatim
        payload against its item's board (the ledger's evidence, R8 a):
        counts of malformed payloads and of payloads that decode with a
        cause, whatever cause reconstruct stored."""
        malformed = decoded_with_cause = 0
        for c in conn.execute(
            "SELECT a.question_id, d.parse_ok, d.configuration_json "
            "FROM dnd_configurations c JOIN dnd_events d USING (event_id) "
            "JOIN dnd_attempts a USING (attempt_id) WHERE c.is_check = 1"
        ):
            raw = c["configuration_json"]
            if not c["parse_ok"] or not raw:
                malformed += 1
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if not isinstance(obj, dict):
                malformed += 1
                continue
            board = load_board(conn, c["question_id"])
            if board is None:
                continue
            if decode.decode_configuration(obj, c["question_id"], board.n_cards, board.n_zones).cause:
                decoded_with_cause += 1
        return {"malformed": malformed, "decoded_with_cause": decoded_with_cause}

    def test_step_4_counts_the_regime_students_malformed_check(self, attrition):
        # Two checks are lost at step 4: the stored `malformed` one and the
        # one stored under the structural `no_move_logging` whose payload is
        # `not json` — the re-decode, not the stored cause, is the evidence.
        _, rows = _export(attrition.conn, FLOW)
        lost = _n(_pooled_flow(rows)[(4, "checks")]["rows_lost"])
        stored = self._stored_causes(attrition.conn)
        assert lost == 2
        assert lost == stored[qc.CAUSE_MALFORMED] + stored[qc.CAUSE_NO_MOVE_LOGGING]
        assert lost == self._redecoded(attrition.conn)["malformed"]
        # Both are parse_ok = 0 rows, so the events quantity loses the same two.
        assert _n(_pooled_flow(rows)[(4, "events")]["rows_lost"]) == 2
        assert attrition.conn.execute(
            "SELECT count(*) FROM dnd_events WHERE parse_ok = 0"
        ).fetchone()[0] == 2

    def test_step_5_counts_the_resumed_attempts_duplicate_card_check(self, attrition):
        # Two checks are lost at step 5: the stored `duplicate_card` one and
        # the resumed attempt's, stored `resumed_attempt`, whose payload
        # re-decodes to duplicate_card. No move is off-board, so the events
        # quantity loses exactly those two rows.
        _, rows = _export(attrition.conn, FLOW)
        pooled = _pooled_flow(rows)
        lost = _n(pooled[(5, "checks")]["rows_lost"])
        stored = self._stored_causes(attrition.conn)
        assert lost == 2
        assert lost == stored[qc.CAUSE_DUPLICATE_CARD] + stored[qc.CAUSE_RESUMED_ATTEMPT]
        assert lost == self._redecoded(attrition.conn)["decoded_with_cause"]
        assert _n(pooled[(5, "events")]["rows_lost"]) == 2
        # And neither step lost an attempt (R9 b): the carriers are visible
        # in readiness_split's by_flag breakdown instead.
        assert _n(pooled[(4, "attempts")]["rows_lost"]) == 0
        assert _n(pooled[(5, "attempts")]["rows_lost"]) == 0
        _, split = _export(attrition.conn, SPLIT)
        by_flag = _split_cells(split, "by_flag")
        assert by_flag[("parse_error", LIMITED)] == (2, 2)
        assert by_flag[("invalid_state", EXCLUDE)] == (2, 2)


# ---------------------------------------------------------------------------
# readiness_split
# ---------------------------------------------------------------------------
class TestReadinessSplit:
    def test_pooled(self, attrition):
        _, rows = _export(attrition.conn, SPLIT)
        pooled = _split_cells(rows, "pooled")
        assert pooled == {("pooled", readiness): n for readiness, n in SPLIT_POOLED.items()}
        assert sum(n for n, _ in pooled.values()) == 16
        # Every attempt has a readiness here, so not_computed never occurs.
        assert all(int(r["n_attempts"]) == 0 for r in rows if r["readiness"] == "not_computed")

    def test_vocabularies(self, attrition):
        _, rows = _export(attrition.conn, SPLIT)
        assert {r["breakdown"] for r in rows} == {
            "pooled", "by_offering", "by_item", "by_source_version", "by_flag",
            "by_flag_combination",
        }
        assert {r["readiness"] for r in rows} <= {READY, LIMITED, EXCLUDE, "not_computed"}
        keys = [(r["breakdown"], r["key"], r["readiness"]) for r in rows]
        assert keys == sorted(keys)

    def test_by_offering(self, attrition):
        _, rows = _export(attrition.conn, SPLIT)
        assert _split_cells(rows, "by_offering") == {
            (attrition.sha256, readiness): n for readiness, n in SPLIT_POOLED.items()
        }

    def test_by_item(self, attrition):
        _, rows = _export(attrition.conn, SPLIT)
        assert _split_cells(rows, "by_item") == SPLIT_BY_ITEM

    def test_by_source_version(self, attrition):
        [(source_hash,)] = attrition.conn.execute(
            "SELECT source_hash FROM dnd_boards WHERE question_id = ?", (Q1,)
        ).fetchall()
        assert source_hash == "hash"  # the four_shapes_board fixture's version
        _, rows = _export(attrition.conn, SPLIT)
        assert _split_cells(rows, "by_source_version") == {
            (source_hash, READY): (9, 9), (source_hash, LIMITED): (4, 4),
            (source_hash, EXCLUDE): (2, 2),
            ("none", EXCLUDE): (1, 1),  # q9: board-less
        }

    def test_by_flag(self, attrition):
        _, rows = _export(attrition.conn, SPLIT)
        by_flag = _split_cells(rows, "by_flag")
        assert by_flag == SPLIT_BY_FLAG
        # Every key is a name of qc.FLAG_ORDER or the empty-set marker.
        assert {key for key, _ in by_flag} <= set(qc.FLAG_ORDER) | {NO_FLAG}
        # An attempt counts once per flag: 11 replay_ok + 2 + 1 + 2 + 1 + 1.
        assert sum(n for n, _ in by_flag.values()) == 18

    def test_by_flag_combination_keys_are_the_stored_json(self, attrition):
        _, rows = _export(attrition.conn, SPLIT)
        combos = _split_cells(rows, "by_flag_combination")
        assert combos == SPLIT_BY_COMBINATION
        stored = {
            r[0] for r in attrition.conn.execute("SELECT DISTINCT qc_flags_json FROM dnd_attempts")
        }
        assert {key for key, _ in combos} == stored
        for key, _ in combos:
            assert json.loads(key) == [f for f in qc.FLAG_ORDER if f in json.loads(key)]

    @pytest.mark.parametrize("breakdown", ("by_offering", "by_item", "by_source_version", "by_flag_combination"))
    def test_every_breakdown_but_by_flag_sums_to_pooled(self, attrition, breakdown):
        _, rows = _export(attrition.conn, SPLIT)
        pooled = {readiness: n for (_, readiness), (n, _) in _split_cells(rows, "pooled").items()}
        sums: dict[str, int] = {}
        for (_, readiness), (n, _) in _split_cells(rows, breakdown).items():
            sums[readiness] = sums.get(readiness, 0) + n
        assert sums == pooled

    def test_n_students_never_exceeds_n_attempts(self, attrition):
        _, rows = _export(attrition.conn, SPLIT)
        for r in rows:
            assert 0 <= int(r["n_students"]) <= int(r["n_attempts"]), (r["breakdown"], r["readiness"])


# ---------------------------------------------------------------------------
# exclusion_probe
# ---------------------------------------------------------------------------
class TestExclusionProbe:
    def test_pooled_cross_tab_and_marginals(self, attrition):
        _, rows = _export(attrition.conn, PROBE)
        pooled = _probe_cells(rows, "pooled")
        assert pooled == PROBE_POOLED
        assert all(r["key"] == "pooled" for r in rows if r["breakdown"] == "pooled")
        # The full cross-tab sums to the sixteen attempts; so does each
        # marginal family.
        assert sum(n for n, _ in (pooled[c] for c in PROBE_CROSS)) == 16
        assert sum(n for n, _ in (pooled[c] for c in PROBE_READINESS_BY_EC)) == 16
        assert sum(n for n, _ in (pooled[c] for c in PROBE_ANCHOR_BY_EC)) == 16

    def test_the_six_student_cell(self, attrition):
        # The cell (ready, 1, 1, 1) holds six students — over the five-student
        # rule, which the generator applies at display time (R10 a).
        _, rows = _export(attrition.conn, PROBE)
        n_attempts, n_students = _probe_cells(rows, "pooled")[(READY, "1", "1", "1")]
        assert (n_attempts, n_students) == (6, 6)
        assert n_students >= 5

    def test_the_none_level(self, attrition):
        # `none` = NULL in dnd_attempt_metrics, i.e. no scored check: the
        # no-check student and the regime student (its only check is
        # unscored) share the (limited, 0, none, none) cell; the board-less
        # student has an eligible anchor but nothing scored.
        _, rows = _export(attrition.conn, PROBE)
        pooled = _probe_cells(rows, "pooled")
        assert pooled[(LIMITED, "0", "none", "none")] == (2, 2)
        assert pooled[(EXCLUDE, "1", "none", "none")] == (1, 1)
        assert attrition.conn.execute(
            "SELECT count(*) FROM dnd_attempt_metrics WHERE first_check_correct IS NULL"
        ).fetchone()[0] == 3

    def test_vocabularies_and_order(self, attrition):
        _, rows = _export(attrition.conn, PROBE)
        assert {r["breakdown"] for r in rows} == {"pooled", "by_offering"}
        assert {r["readiness"] for r in rows} <= {READY, LIMITED, EXCLUDE, "not_computed", "all"}
        assert {r["has_eligible_anchor"] for r in rows} <= {"0", "1", "all"}
        assert {r["first_check_correct"] for r in rows} <= {"1", "0", "none", "all"}
        assert {r["eventual_correct"] for r in rows} <= {"1", "0", "none", "all"}
        keys = [tuple(r[c] for c in PROBE_COLUMNS[:6]) for r in rows]
        assert keys == sorted(keys)
        # No empty cells: every row carries a positive count.
        assert all(int(r["n_attempts"]) > 0 for r in rows)

    def test_by_offering_is_the_two_marginal_families_unguarded(self, attrition):
        # R14: by offering, the two marginals only; R10 (a): with n_students,
        # never suppressed — the one-student cells are present.
        _, rows = _export(attrition.conn, PROBE)
        offering_rows = [r for r in rows if r["breakdown"] == "by_offering"]
        assert {r["key"] for r in offering_rows} == {attrition.sha256}
        assert _probe_cells(rows, "by_offering") == PROBE_MARGINALS
        for r in offering_rows:
            assert "all" in (r["readiness"], r["has_eligible_anchor"]), r["readiness"]
            assert r["first_check_correct"] == "all"
            assert int(r["n_students"]) >= 1
        assert any(int(r["n_students"]) == 1 for r in offering_rows)


# ---------------------------------------------------------------------------
# --question scope (J7)
# ---------------------------------------------------------------------------
class TestQuestionScope:
    @pytest.mark.parametrize("quantity", QUANTITIES)
    def test_flow_scoped_to_q1(self, attrition, quantity):
        _, rows = _export(attrition.conn, FLOW, question_id=Q1)
        pooled = _pooled_flow(rows)
        assert [_cells(pooled[(step, quantity)]) for step in range(9)] == FLOW_Q1[quantity]

    def test_raw_rows_exclude_the_board_less_items_student_and_rows(self, attrition):
        _, scoped = _export(attrition.conn, FLOW, question_id=Q1)
        _, whole = _export(attrition.conn, FLOW)
        raw_scoped = {q: _cells(_pooled_flow(scoped)[(0, q)]) for q in QUANTITIES}
        raw_whole = {q: _cells(_pooled_flow(whole)[(0, q)]) for q in QUANTITIES}
        # The page row, the two non-DnD rows and the board-less item's three
        # rows are outside q1: 71 → 65 raw rows, 16 → 15 students, 2 → 1
        # items, the board-less check gone (23 → 22).
        assert raw_whole["events"][2] - raw_scoped["events"][2] == 6
        assert raw_whole["students"][2] - raw_scoped["students"][2] == 1
        assert raw_whole["items_logged"][2] - raw_scoped["items_logged"][2] == 1
        assert raw_whole["checks"][2] - raw_scoped["checks"][2] == 1
        # Nothing is left for step 3 to lose under the scope.
        assert _cells(_pooled_flow(scoped)[(3, "attempts")]) == (15, 0, 15, 14)

    def test_readiness_split_scoped(self, attrition):
        _, rows = _export(attrition.conn, SPLIT, question_id=Q1)
        assert _split_cells(rows, "pooled") == {
            ("pooled", readiness): n for readiness, n in SPLIT_POOLED_Q1.items()
        }
        assert {key for key, _ in _split_cells(rows, "by_item")} == {Q1}
        assert {key for key, _ in _split_cells(rows, "by_source_version")} == {"hash"}
        by_flag = _split_cells(rows, "by_flag")
        assert ("missing_source_map", EXCLUDE) not in by_flag
        assert by_flag[("replay_ok", READY)] == (9, 9)

    def test_probe_scoped(self, attrition):
        _, rows = _export(attrition.conn, PROBE, question_id=Q1)
        pooled = _probe_cells(rows, "pooled")
        # The board-less student is the only q9 attempt: its cross-tab cell
        # and its two marginal cells (each held it alone) are absent, every
        # other cell is unchanged.
        gone = {
            (EXCLUDE, "1", "none", "none"), (EXCLUDE, "all", "all", "none"),
            ("all", "1", "all", "none"),
        }
        assert all(PROBE_POOLED[cell] == (1, 1) for cell in gone)
        assert pooled == {cell: n for cell, n in PROBE_POOLED.items() if cell not in gone}

    def test_an_unknown_identifier_scopes_to_nothing(self, attrition):
        # Not a spec'd case: whether the flow emits zero rows or no rows for
        # an empty scope is the implementation's choice; it may not emit a
        # count, and the other two datasets have no cell to emit.
        _, rows = _export(attrition.conn, FLOW, question_id="no-such-question")
        for r in rows:
            cells = _cells(r)
            assert cells is None or cells[:3] == (0, 0, 0), (r["step"], r["quantity"])
        _, split = _export(attrition.conn, SPLIT, question_id="no-such-question")
        assert _split_cells(split, "pooled") == {}
        _, probe = _export(attrition.conn, PROBE, question_id="no-such-question")
        assert probe == []


# ---------------------------------------------------------------------------
# Determinism and privacy
# ---------------------------------------------------------------------------
class TestDeterminism:
    @pytest.mark.parametrize("dataset", DATASETS)
    def test_two_runs_are_byte_identical(self, attrition, dataset):
        first = _export_text(attrition.conn, dataset)
        second = _export_text(attrition.conn, dataset)
        assert first == second
        assert first.endswith("\n") and "\r" not in first
        scoped_first = _export_text(attrition.conn, dataset, question_id=Q1)
        scoped_second = _export_text(attrition.conn, dataset, question_id=Q1)
        assert scoped_first == scoped_second

    @pytest.mark.parametrize("dataset", DATASETS)
    def test_row_count_is_returned(self, attrition, dataset):
        buf = io.StringIO()
        n = export.export_dataset(attrition.conn, dataset, buf)
        _, rows = _read_csv(buf.getvalue())
        assert n == len(rows) > 0


class TestPrivacy:
    """M6: the outputs carry import_sha256 values, canonical item
    identifiers, flag names and counts only — never a student id, a
    pseudonym or a path. The ids and pseudonyms are read from the database,
    so this module names none."""

    def _needles(self, attrition) -> _Needles:
        conn = attrition.conn
        ids = [r[0] for r in conn.execute("SELECT DISTINCT student_id FROM events")]
        n_ids = len(ids)  # bound first: a failing assert prints only the number
        assert n_ids == 16
        pseudonyms = [
            r[0] for r in conn.execute(
                f"SELECT DISTINCT pseudonym FROM {account_roles.TABLE} WHERE pseudonym IS NOT NULL"
            )
        ]
        assert bool(pseudonyms)
        # The fixtures directory is derived here rather than carried on the
        # `attrition` namespace, which pytest prints as a funcarg on failure.
        fixtures = Path(__file__).parent / "fixtures"
        return _Needles([*ids, *pseudonyms, "Y99", FIXTURE, str(fixtures), str(fixtures.resolve())])

    @pytest.mark.parametrize("dataset", DATASETS)
    @pytest.mark.parametrize("question_id", (None, Q1))
    def test_no_cell_carries_an_id_a_pseudonym_or_a_path(self, attrition, dataset, question_id):
        needles = self._needles(attrition)
        header, rows = _export(attrition.conn, dataset, question_id=question_id)
        assert rows
        for r in rows:
            for column in header:
                _assert_no_leak(r[column], needles)
        # The CSV text as a whole, too (a quoting artefact cannot hide one).
        _assert_no_leak(_export_text(attrition.conn, dataset, question_id=question_id), needles)

    @pytest.mark.parametrize("dataset", DATASETS)
    def test_offering_keys_are_the_import_sha256(self, attrition, dataset):
        _, rows = _export(attrition.conn, dataset)
        keys = {r["key"] for r in rows if r["breakdown"] == "by_offering"}
        assert keys == {attrition.sha256}
        for key in keys:
            assert HEX64.match(key)
        [(stored,)] = attrition.conn.execute("SELECT file_hash FROM raw_imports").fetchall()
        assert stored == attrition.sha256

    def test_item_keys_are_canonical_identifiers(self, attrition):
        _, rows = _export(attrition.conn, SPLIT)
        assert {r["key"] for r in rows if r["breakdown"] == "by_item"} == {Q1, Q9}


# ---------------------------------------------------------------------------
# The reproduced rules (R8 a): the off-board move and the malformed payload,
# each against reconstruct's own
# ---------------------------------------------------------------------------
FOUR_BY_THREE = (4, 3)    # the four-shapes board: P = 4, R = 3
TWO_NO_BOUND = (2, None)  # a board-less item: P inferred as 2, no R bound


def _label(kind: str, index: int, question_id: str = Q1) -> str:
    return f"{question_id}_{kind}{index}"


OFF_BOARD_CASES = [
    # (source_label, zone_label, shape, off-board?)
    (_label("drag", 4), _label("drop", 3), FOUR_BY_THREE, False),  # the last card, the last slot
    (_label("drag", 1), _label("drop", 1), FOUR_BY_THREE, False),
    (_label("drag", 1), "dragzone", FOUR_BY_THREE, False),         # a takeback always replays
    (_label("drag", 5), _label("drop", 1), FOUR_BY_THREE, True),   # card index > P
    (_label("drag", 0), _label("drop", 1), FOUR_BY_THREE, True),   # drag0 is malformed (1-based)
    (_label("drag", 1), _label("drop", 4), FOUR_BY_THREE, True),   # slot index > R
    (_label("drag", 1), _label("drop", 0), FOUR_BY_THREE, True),   # drop0 is malformed
    (_label("drag", 1), _label("drag", 2), FOUR_BY_THREE, True),   # a card label as the zone
    (_label("drop", 1), _label("drop", 1), FOUR_BY_THREE, True),   # a zone label as the card
    (_label("drag", 1), "garbage", FOUR_BY_THREE, True),           # the zone does not decode
    ("", _label("drop", 1), FOUR_BY_THREE, True),                  # no source label
    (None, _label("drop", 1), FOUR_BY_THREE, True),
    (_label("drag", 1), None, FOUR_BY_THREE, True),
    (_label("drag", 1, Q9), _label("drop", 1), FOUR_BY_THREE, True),  # another item's label
    (_label("drag", 1), _label("drop", 9), TWO_NO_BOUND, False),   # no R bound without a board
    (_label("drag", 3), _label("drop", 1), TWO_NO_BOUND, True),    # P still bounds the card
    (_label("drag", 1), _label("drop", 1), None, True),            # not even P is known
]

PAYLOAD_CASES = [
    # (configuration_json, the parsed payload; None = malformed)
    ("{}", {}),
    (json.dumps({_label("drop", 1): [_label("drag", 1)]}), {_label("drop", 1): [_label("drag", 1)]}),
    ("[]", None),       # a JSON array, not an object
    ('"x"', None),      # a JSON string
    ("0", None),        # a JSON number
    ("", None),         # empty
    ("   ", None),      # whitespace only
    (None, None),       # NULL
    ('{"a":', None),    # truncated
    ("not json", None),
]


class TestReproducedRules:
    @pytest.mark.parametrize("source,zone,shape,expected", OFF_BOARD_CASES)
    def test_off_board(self, source, zone, shape, expected):
        assert attrition_mod._off_board(source, zone, Q1, shape) is expected

    @pytest.mark.parametrize(
        "source,zone,shape,expected", [c for c in OFF_BOARD_CASES if c[2] is not None]
    )
    def test_off_board_is_the_replays_skip_rule(self, source, zone, shape, expected):
        # The reproduced rule must stay equivalent to reconstruct's: a
        # parseable move is off-board exactly when _apply_move skips it
        # (leaving the state untouched).
        n_cards, n_zones = shape
        state = [0] * n_cards
        event = {"parse_ok": 1, "source_label": source, "zone_label": zone, "event_id": "probe"}
        outcome = reconstruct_mod._apply_move(state, event, Q1, n_cards, n_zones)
        assert (outcome == reconstruct_mod._SKIPPED) is expected
        if expected:
            assert state == [0] * n_cards

    @pytest.mark.parametrize("raw,expected", PAYLOAD_CASES)
    def test_payload(self, raw, expected):
        assert attrition_mod._payload(raw) == expected

    @pytest.mark.parametrize("raw,expected", PAYLOAD_CASES)
    def test_payload_is_the_cross_checks_malformed_test(self, raw, expected):
        # A payload is malformed here exactly when reconstruct's cross-check
        # stores `malformed` for a parse_ok = 1 check that carries it.
        event = {"parse_ok": 1, "configuration_json": raw, "event_id": "probe"}
        _, cause, _ = reconstruct_mod._cross_check([0] * 4, event, Q1, 4, 3)
        assert (cause == qc.CAUSE_MALFORMED) is (expected is None)

    def test_parse_ok_zero_is_malformed_before_the_payload(self):
        # The caller tests parse_ok first, as _cross_check does.
        event = {"parse_ok": 0, "configuration_json": "{}", "event_id": "probe"}
        assert reconstruct_mod._cross_check([0] * 4, event, Q1, 4, 3)[1] == qc.CAUSE_MALFORMED


# ---------------------------------------------------------------------------
# Step 7 is reachable: the resumed-agrees variant
# ---------------------------------------------------------------------------
class TestStepSevenLoss:
    """A ready attempt with no eligible anchor (module docstring, the
    resumed-agrees variant): step 7 loses it, so the spec's "non-zero
    rows_lost at every step 1–8" holds on this run."""

    def test_the_resumed_attempt_is_ready_without_an_eligible_anchor(self, resumed_agrees):
        conn = resumed_agrees.conn
        # Exactly one ready attempt has no check stored with a NULL cause;
        # it is the resumed one, and its one check is stored under the
        # structural cause.
        [(n_ready_without_anchor,)] = conn.execute(
            "SELECT count(*) FROM dnd_attempts a WHERE a.readiness = ? AND NOT EXISTS ("
            "SELECT 1 FROM dnd_configurations c WHERE c.attempt_id = a.attempt_id "
            "AND c.is_check = 1 AND c.anchor_cause IS NULL)",
            (READY,),
        ).fetchall()
        assert n_ready_without_anchor == 1
        [(cause, n_checks)] = conn.execute(
            "SELECT c.anchor_cause, count(*) FROM dnd_attempts a "
            "JOIN dnd_configurations c ON c.attempt_id = a.attempt_id AND c.is_check = 1 "
            "WHERE a.readiness = ? AND a.prior_attempt_id IS NOT NULL GROUP BY c.anchor_cause",
            (READY,),
        ).fetchall()
        assert (cause, n_checks) == (qc.CAUSE_RESUMED_ATTEMPT, 1)

    def test_flow_loses_one_attempt_at_anchor_coverage(self, resumed_agrees):
        _, rows = _export(resumed_agrees.conn, FLOW)
        pooled = _pooled_flow(rows)
        assert [_cells(pooled[(step, "attempts")]) for step in range(3, 9)] == [
            (16, 1, 15, 14), (15, 0, 15, 14), (15, 0, 15, 14),
            (15, 5, 10, 9),   # 6: the resumed attempt now stands; nine students
            (10, 1, 9, 9),    # 7: it has no eligible anchor; its student keeps attempt 1
            (10, 2, 8, 8),    # 8: it and SA are not eventually correct
        ]
        # On the rows: one check lost at validity (DupCard's only), then the
        # five non-ready attempts' 2 + 4 + 0 + 4 + 2 rows at readiness.
        assert [_cells(pooled[(step, "events")]) for step in (5, 6)] == [
            (59, 1, 58, 13), (58, 12, 46, 9),
        ]
        assert [_cells(pooled[(step, "checks")]) for step in (5, 6)] == [
            (19, 1, 18, 11), (18, 2, 16, 9),
        ]
        assert _cells(pooled[(6, "students")]) == (14, 5, 9, 9)
        assert _telescopes(rows) == 16

    def test_a_loss_at_every_step(self, resumed_agrees):
        _, rows = _export(resumed_agrees.conn, FLOW)
        pooled = _pooled_flow(rows)
        lost = {
            step: sum(_n(pooled[(step, q)]["rows_lost"]) or 0 for q in QUANTITIES)
            for step in range(9)
        }
        assert lost[0] == 0
        assert all(lost[step] > 0 for step in range(1, 9)), lost

    def test_readiness_split_and_probe(self, resumed_agrees):
        _, split = _export(resumed_agrees.conn, SPLIT)
        assert _split_cells(split, "pooled") == {
            ("pooled", READY): (10, 9), ("pooled", LIMITED): (4, 4), ("pooled", EXCLUDE): (2, 2),
        }
        by_flag = _split_cells(split, "by_flag")
        assert by_flag[("replay_ok", READY)] == (10, 9)
        assert by_flag[("invalid_state", EXCLUDE)] == (1, 1)
        _, probe = _export(resumed_agrees.conn, PROBE)
        pooled = _probe_cells(probe, "pooled")
        assert pooled[(READY, "0", "0", "0")] == (1, 1)
        assert pooled[(READY, "all", "all", "0")] == (2, 2)
        assert pooled[("all", "0", "all", "0")] == (2, 2)
        assert pooled[(EXCLUDE, "0", "0", "0")] == (1, 1)
        assert sum(n for cell, (n, _) in pooled.items() if "all" not in cell) == 16


# ---------------------------------------------------------------------------
# An off-board move: the step-5 move rule end to end
# ---------------------------------------------------------------------------
class TestOffBoardMove:
    def test_the_move_is_skipped_and_the_attempt_excluded(self, off_board):
        [(n,)] = off_board.conn.execute(
            "SELECT count(*) FROM dnd_attempts WHERE qc_flags_json LIKE '%invalid_state%' "
            "AND qc_flags_json LIKE '%replay_ok%'"
        ).fetchall()
        assert n == 1

    def test_flow_counts_the_move_at_state_validity(self, off_board):
        _, rows = _export(off_board.conn, FLOW)
        pooled = _pooled_flow(rows)
        # Step 5: the two checks as before plus the off-board move; step 6:
        # seven attempts (the takeback student's joins the six) and their
        # 2 + 1 + 4 + 0 + 4 + 2 + 6 rows.
        assert [_cells(pooled[(step, "events")]) for step in (4, 5, 6)] == [
            (61, 2, 59, 13), (59, 3, 56, 13), (56, 19, 37, 8),
        ]
        assert [_cells(pooled[(step, "checks")]) for step in (5, 6)] == [
            (19, 2, 17, 11), (17, 3, 14, 8),
        ]
        assert [_cells(pooled[(step, "attempts")]) for step in (5, 6, 7, 8)] == [
            (15, 0, 15, 14), (15, 7, 8, 8), (8, 0, 8, 8), (8, 1, 7, 7),
        ]
        assert _cells(pooled[(6, "students")]) == (14, 6, 8, 8)
        assert _telescopes(rows) == 16

    def test_readiness_split_carries_the_flags(self, off_board):
        _, split = _export(off_board.conn, SPLIT)
        assert _split_cells(split, "pooled") == {
            ("pooled", READY): (8, 8), ("pooled", LIMITED): (4, 4), ("pooled", EXCLUDE): (4, 4),
        }
        by_flag = _split_cells(split, "by_flag")
        assert by_flag[("invalid_state", EXCLUDE)] == (3, 3)
        assert by_flag[("noop_move", EXCLUDE)] == (1, 1)
        assert by_flag[("replay_ok", EXCLUDE)] == (2, 2)
        _, probe = _export(off_board.conn, PROBE)
        assert _probe_cells(probe, "pooled")[(EXCLUDE, "1", "1", "1")] == (1, 1)


# ---------------------------------------------------------------------------
# The alias: logged versus canonical counts (R4) and canonical keys
# ---------------------------------------------------------------------------
class TestAliasVariant:
    def test_items_logged_and_canonical_differ(self, aliased):
        _, rows = _export(aliased.conn, FLOW)
        pooled = _pooled_flow(rows)
        logged = [_cells(pooled[(step, "items_logged")]) for step in range(9)]
        canonical = [_cells(pooled[(step, "items_canonical")]) for step in range(9)]
        n_students = [16, 15, 15, 15, 15, 15, 10]
        assert logged == [(2, 0, 2, n) for n in n_students] + [EMPTY, EMPTY]
        assert canonical == [(1, 0, 1, n) for n in n_students] + [EMPTY, EMPTY]
        # One item per student, so the traces agree under either keying.
        for step in range(7):
            assert _cells(pooled[(step, "item_traces_logged")]) == _cells(
                pooled[(step, "item_traces_canonical")]
            ), step
        # The aliased item's attempt has a board now: nothing is lost at
        # step 3; it is ready and eventually incorrect.
        assert [_cells(pooled[(step, "attempts")]) for step in (3, 6, 7, 8)] == [
            (16, 0, 16, 15), (16, 6, 10, 10), (10, 0, 10, 10), (10, 2, 8, 8),
        ]
        assert _telescopes(rows) == 16

    def test_by_item_and_by_source_version_key_the_canonical_item(self, aliased):
        _, rows = _export(aliased.conn, SPLIT)
        assert {key for key, _ in _split_cells(rows, "by_item")} == {Q1}
        assert {key for key, _ in _split_cells(rows, "by_source_version")} == {"hash"}
        pooled = {
            ("pooled", READY): (10, 10), ("pooled", LIMITED): (4, 4), ("pooled", EXCLUDE): (2, 2),
        }
        assert _split_cells(rows, "pooled") == pooled
        assert _split_cells(rows, "by_item") == {(Q1, r): n for (_, r), n in pooled.items()}
        assert _split_cells(rows, "by_source_version") == {
            ("hash", r): n for (_, r), n in pooled.items()
        }

    def test_the_question_scope_is_the_logged_identifier(self, aliased):
        # J7: `--question <logged id>` — the alias scopes to the aliased
        # student's rows and attempt alone, keyed under the canonical item.
        _, rows = _export(aliased.conn, SPLIT, question_id=Q9)
        assert _split_cells(rows, "pooled") == {("pooled", READY): (1, 1)}
        assert {key for key, _ in _split_cells(rows, "by_item")} == {Q1}
        _, flow = _export(aliased.conn, FLOW, question_id=Q9)
        pooled = _pooled_flow(flow)
        assert _cells(pooled[(0, "events")]) == (3, 0, 3, 1)
        assert _cells(pooled[(0, "items_logged")]) == (1, 0, 1, 1)
        assert _cells(pooled[(0, "items_canonical")]) == (1, 0, 1, 1)


# ---------------------------------------------------------------------------
# Two imports: the by_offering breakdown with two keys
# ---------------------------------------------------------------------------
# The quantities the two files partition (every window in one import, every
# student in one file), so their cells sum to the pooled ones; items do not
# (q1 is in both files).
ADDITIVE = ("events", "checks", "attempts", "students", "item_traces_logged", "item_traces_canonical")


class TestTwoImports:
    def test_two_offering_keys(self, two_imports):
        first, second = two_imports.offerings
        assert first != second and HEX64.match(first) and HEX64.match(second)
        for dataset in DATASETS:
            _, rows = _export(two_imports.conn, dataset)
            assert {r["key"] for r in rows if r["breakdown"] == "by_offering"} == {first, second}

    def test_every_offering_series_telescopes(self, two_imports):
        _, rows = _export(two_imports.conn, FLOW)
        assert _telescopes(rows) == 24  # 8 quantities × (pooled + two offerings)

    def test_offerings_sum_to_pooled_where_the_entities_partition(self, two_imports):
        _, rows = _export(two_imports.conn, FLOW)
        index = _flow_index(rows)
        first, second = two_imports.offerings
        for step in range(9):
            for quantity in ADDITIVE:
                pooled = _cells(index[(step, quantity, "pooled", "pooled")])
                parts = [_cells(index[(step, quantity, "by_offering", key)]) for key in (first, second)]
                if pooled is None:
                    assert parts == [None, None], (step, quantity)
                    continue
                assert tuple(sum(p[i] for p in parts) for i in range(4)) == pooled, (step, quantity)
        items = sorted(_n(index[(0, "items_logged", "by_offering", key)]["rows_out"]) for key in (first, second))
        assert items == [1, 2]
        assert _n(index[(0, "items_logged", "pooled", "pooled")]["rows_out"]) == 2

    def test_governance_loss_is_emitted_per_offering_unguarded(self, two_imports):
        # J5, R10 (a): the staff account's rows are in the first file alone,
        # and the one-student cells stand.
        _, rows = _export(two_imports.conn, FLOW)
        index = _flow_index(rows)
        first, second = two_imports.offerings
        assert _cells(index[(1, "events", "by_offering", first)]) == (39, 5, 34, 8)
        assert _cells(index[(1, "events", "by_offering", second)]) == (32, 0, 32, 7)
        assert _cells(index[(3, "attempts", "by_offering", first)]) == (8, 1, 7, 7)
        assert _cells(index[(6, "attempts", "by_offering", second)]) == (8, 5, 3, 3)

    def test_readiness_split_and_probe_by_offering_sum_to_pooled(self, two_imports):
        _, split = _export(two_imports.conn, SPLIT)
        pooled = {r: n for (_, r), (n, _) in _split_cells(split, "pooled").items()}
        sums: dict[str, int] = {}
        for (_, r), (n, _) in _split_cells(split, "by_offering").items():
            sums[r] = sums.get(r, 0) + n
        assert sums == pooled == {READY: 9, LIMITED: 4, EXCLUDE: 3}
        _, probe = _export(two_imports.conn, PROBE)
        marginals = {cell: n for cell, (n, _) in _probe_cells(probe, "pooled").items() if "all" in cell}
        by_offering: dict[tuple, int] = {}
        for r in probe:
            if r["breakdown"] == "by_offering":
                cell = tuple(r[c] for c in PROBE_COLUMNS[2:6])
                by_offering[cell] = by_offering.get(cell, 0) + int(r["n_attempts"])
        assert by_offering == marginals


# ---------------------------------------------------------------------------
# Rows inside no attempt window: counted at step 3, surfaced as a count
# ---------------------------------------------------------------------------
class TestNoWindowRows:
    def test_stray_rows_are_step_3_losses_logged_as_a_count(self, attrition, monkeypatch, caplog):
        # No attempt holds any row (no membership, no window): every
        # card-to-zone row of a retained account (64) is a stray — counted
        # under step 3's fixed reason and logged as a count, never as ids (M6).
        monkeypatch.setattr(
            attrition_mod, "_membership",
            lambda conn, filters, attempts: attrition_mod._Membership({}, {}, {}, {}),
        )
        needles = TestPrivacy()._needles(attrition)
        with caplog.at_level(logging.WARNING, logger=attrition_mod.__name__):
            _, rows = _export(attrition.conn, FLOW)
        pooled = _pooled_flow(rows)
        assert _cells(pooled[(3, "events")]) == (64, 64, 0, 0)
        assert _cells(pooled[(3, "checks")]) == (22, 22, 0, 0)
        assert pooled[(3, "events")]["reason"] == REASONS[3]
        messages = [r.getMessage() for r in caplog.records if r.name == attrition_mod.__name__]
        assert len(messages) == 1
        assert "64" in messages[0] and "step 3" in messages[0]
        _assert_no_leak(caplog.text, needles)
        _telescopes(rows)

    def test_no_stray_row_on_the_fixture(self, attrition, caplog):
        with caplog.at_level(logging.WARNING, logger=attrition_mod.__name__):
            _export(attrition.conn, FLOW)
        assert not [r for r in caplog.records if r.name == attrition_mod.__name__]


# ---------------------------------------------------------------------------
# A session change inside a tied second (the Copilot round on PR #239)
# ---------------------------------------------------------------------------
TIED_STUDENT = "AttritionTied"
TIED_T = "2026-06-03 23:00:10.000000"
TIED_SESSIONS = ("901", "902")  # A → B → A, every DnD row at TIED_T


def _tied_rows():
    """One more student: a page view, then six DnD rows at one timestamp in
    problem_view order — session 901 (a move, a check that agrees: ready),
    session 902 (one move, no check: limited), session 901 again (an
    off-board move the replay skips, a move, a check that disagrees:
    exclude). Reconstruct splits at each session change, so the two 901
    attempts' windows both hold TIED_T and the window rule alone gave the
    third attempt's rows to the first."""
    agree = json.dumps({f"{Q1}_drop1": [f"{Q1}_drag1"]})

    def drop(card, slot):
        return f"{Q1}_drag{card} -> {Q1}_drop{slot}"

    def row(ts, session, view, selection, action, input_="", grade=""):
        problem = "sec-demo-shapes" if selection == "page" else Q1
        return "\t".join([
            ts, TIED_STUDENT, action, problem, "1", "", "", session, str(view),
            selection, input_, grade, grade, "", "8", "university",
        ])

    return [
        row("2026-06-03 23:00:00.000000", "901", 1, "page", "viewassignment"),
        row(TIED_T, "901", 2, "dragNdrop-drop", drop(1, 1)),
        row(TIED_T, "901", 3, "dragNdrop", agree, agree, "Correct"),
        row(TIED_T, "902", 4, "dragNdrop-drop", drop(2, 1)),
        row(TIED_T, "901", 5, "dragNdrop-drop", drop(1, 5)),  # off the 3-slot board
        row(TIED_T, "901", 6, "dragNdrop-drop", drop(1, 2)),
        row(TIED_T, "901", 7, "dragNdrop", agree, agree, "Incorrect"),  # the replay has drag1 in drop2
    ]


def _tied_fixture(tmp_path, fixtures_dir):
    lines = (fixtures_dir / FIXTURE).read_text(encoding="utf-8").splitlines()
    path = tmp_path / "tied.tab"
    path.write_text("\n".join(lines + _tied_rows()) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def tied(db_conn, tmp_path, fixtures_dir, four_shapes_board):
    tab = _tied_fixture(tmp_path, fixtures_dir)
    return _build(db_conn, four_shapes_board, fixtures_dir / ROLES, [tab])


def _plus(cell, delta):
    return tuple(a + b for a, b in zip(cell, delta))


class TestTiedTimestampRecurringSession:
    def _attempts(self, conn):
        # The appended student's attempts, selected by session (never by id):
        # (session, attempt_index, started_at, ended_at, readiness).
        return [
            tuple(r) for r in conn.execute(
                "SELECT session_id, attempt_index, started_at, ended_at, readiness "
                "FROM dnd_attempts WHERE session_id IN (?, ?) AND question_id = ? "
                "ORDER BY attempt_id",
                (*TIED_SESSIONS, Q1),
            )
        ]

    def test_the_two_same_session_windows_overlap_with_different_readiness(self, tied):
        rows = self._attempts(tied.conn)
        assert [(s, i, r) for s, i, _, _, r in rows] == [
            ("901", 1, READY), ("902", 2, LIMITED), ("901", 3, EXCLUDE),
        ]
        assert all((st, en) == (TIED_T, TIED_T) for _, _, st, en, _ in rows)

    def test_flow_gives_the_third_attempts_rows_to_it(self, tied, caplog):
        # Seven rows more than the frozen fixture: the page row (a step-2
        # loss), the off-board move (step 5), then at step 6 the 902 move
        # and the third attempt's move and check (3 rows, 1 check) — the
        # window rule alone lost 1 row and no check there, the third
        # attempt's rows riding on the first attempt's readiness.
        with caplog.at_level(logging.WARNING, logger=attrition_mod.__name__):
            _, rows = _export(tied.conn, FLOW)
        assert not [r for r in caplog.records if r.name == attrition_mod.__name__]
        pooled = _pooled_flow(rows)
        f = FLOW_POOLED
        assert _cells(pooled[(0, "events")]) == _plus(f["events"][0], (7, 0, 7, 1))
        assert _cells(pooled[(2, "events")]) == _plus(f["events"][2], (7, 1, 6, 1))
        assert _cells(pooled[(5, "events")]) == _plus(f["events"][5], (6, 1, 5, 1))
        assert _cells(pooled[(6, "events")]) == _plus(f["events"][6], (5, 3, 2, 1))
        assert _cells(pooled[(5, "checks")]) == _plus(f["checks"][5], (2, 0, 2, 1))
        assert _cells(pooled[(6, "checks")]) == _plus(f["checks"][6], (2, 1, 1, 1))
        assert _cells(pooled[(3, "attempts")]) == _plus(f["attempts"][3], (3, 0, 3, 1))
        assert _cells(pooled[(6, "attempts")]) == _plus(f["attempts"][6], (3, 2, 1, 1))
        assert _cells(pooled[(7, "attempts")]) == _plus(f["attempts"][7], (1, 0, 1, 1))
        assert _cells(pooled[(6, "students")]) == _plus(f["students"][6], (1, 0, 1, 1))
        assert _cells(pooled[(6, "items_logged")]) == _plus(f["items_logged"][6], (0, 0, 0, 1))
        assert _cells(pooled[(6, "item_traces_logged")]) == _plus(f["item_traces_logged"][6], (1, 0, 1, 1))
        _telescopes(rows)

    def test_readiness_split_has_the_three_classes(self, tied):
        _, split = _export(tied.conn, SPLIT)
        cells = _split_cells(split, "pooled")
        for readiness in (READY, LIMITED, EXCLUDE):
            assert cells[("pooled", readiness)] == _plus(SPLIT_POOLED[readiness], (1, 1))

    def test_the_skipped_row_follows_its_replayed_neighbours(self, tied):
        # The off-board move has no configuration (the replay skipped it),
        # so it is the fallback's case: both 901 windows hold it; the
        # neighbour before it is the 902 move (not a candidate), the one
        # after the third attempt's move — so the third attempt, where the
        # lowest id (the old rule) was the first.
        conn = tied.conn
        attempts = attrition_mod._load_attempts(conn, {}, frozenset(), attrition_mod._Items(conn))
        membership = attrition_mod._membership(conn, {}, attempts)
        [(event_id,)] = conn.execute(
            "SELECT e.event_id FROM events e JOIN dnd_events d USING (event_id) "
            "WHERE e.session_id = ? AND d.zone_label = ?",
            ("901", f"{Q1}_drop5"),
        )
        assert event_id not in membership.members
        ids = {
            index: attempt_id for attempt_id, index in conn.execute(
                "SELECT attempt_id, attempt_index FROM dnd_attempts WHERE session_id = ?", ("901",)
            )
        }
        row = conn.execute(attrition_mod._ROWS_SQL + " AND e.event_id = ?", (event_id,)).fetchone()
        candidates = attrition_mod._candidates(
            membership.windows, row["student_id"], row["problem_name"], row["session_id"], row["timestamp"],
        )
        assert [a.attempt_id for a in candidates] == [ids[1], ids[3]]
        assert membership.attempt_of(row).attempt_id == ids[3]
        # Every replayed row of the student is a member of the attempt its
        # session and order say.
        held = [
            membership.attempt_of(r).attempt_id for r in conn.execute(
                attrition_mod._ROWS_SQL + " AND e.session_id IN (?, ?) AND e.selection IN (?, ?, ?) "
                "ORDER BY CAST(e.problem_view AS INTEGER)",
                (*TIED_SESSIONS, *attrition_mod.DND_SELECTIONS),
            )
        ]
        [(second,)] = conn.execute("SELECT attempt_id FROM dnd_attempts WHERE session_id = ?", ("902",))
        assert held == [ids[1], ids[1], second, ids[3], ids[3], ids[3]]
