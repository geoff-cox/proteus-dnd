"""
The attrition fixture (``cc-task-attrition-ledger.md`` E-02; PR 1 spec §2.6,
owner rulings 2026-10-19): the fifth hand-made DnD fixture, planted so that
every exclusion step of the manuscript's ``subsec-analysis-unit`` loses at
least one row, including the two hidden step-5 cases — a check whose payload
fails to decode inside an attempt that carries a structural cause.

``golden_dnd_attrition.tab`` holds sixteen students and seventeen sessions
(``Attrition*``, 2026-06, one import of 71 rows) — sixteen attempts by
fifteen students once the staff account is withheld at derive (one Session Id per attempt,
rows five seconds apart, every CHECK's payload in both ``Action`` and
``Input``). Built in the order of the frozen run — ``init`` → ``dnd roles
import`` → ``ingest --derive`` — with the four-shapes board registered
(P = 4, R = 3, key (1, 1, 2, 0)) and a logging-regime row dated 2026-06-02
recorded against the file's sha256 after the ingest, then reconstruct →
score → analyze. The hand replay, under the rules of ``qc.py`` /
``reconstruct.py`` (``readiness``: an excluding flag ⇒ ``exclude``;
``replay_ok`` with nothing but ``noop_move`` beside it ⇒ ``ready``; anything
else ⇒ ``limited``):

* (a) ``AttritionStaff1`` — a page row and the key placed and checked. Ruled
  ``instructor, exclude yes``: all 5 rows stay in ``events`` and none
  reaches ``dnd_events`` (R1 a, J1), so no attempt exists.
* (b) ``AttritionRuledKept`` (ruled ``student, exclude no``) and
  ``AttritionClean1``–``3``: drag1→drop1, drag2→drop1, drag3→drop2 replay
  to (1, 1, 2, 0); the CHECK decodes to the same → ``(matches_input,
  anchor_cause) = (1, None)``, ``checks_matched = n_checks`` → ``replay_ok``
  alone → ``ready``.
* (c) ``AttritionRevised`` — (1, 2, 0, 0) at the first CHECK (decodes to
  (1, 2, 0, 0), agrees), then drag2→drop1, drag3→drop2 → (1, 1, 2, 0) at
  the second (agrees): two agreeing anchors → ``replay_ok``, ``ready``;
  first scored check at distance 2, the second at 0 → ``first_check_correct
  0``, ``eventual_correct 1``.
* (d) ``AttritionSA`` — a ``shortanswer`` and an ``mChoice`` row (never
  derived), then drag1→drop1 → (1, 0, 0, 0) and a CHECK decoding to the
  same: ``replay_ok``, ``ready``; the one scored check is at distance 2 →
  outcomes (0, 0).
* (e) ``AttritionNoBoard`` on ``demo-book__demo-shapes-q9``, no board and
  no alias: P is inferred as 2 (the highest ``drag`` index seen), no R
  bound; q9_drag1→q9_drop1, q9_drag2→q9_drop2 → (1, 2); the CHECK decodes
  to (1, 2) and agrees → ``replay_ok`` **and** ``missing_source_map`` →
  ``exclude`` (the excluding flag wins); no key, so no score and NULL
  outcomes.
* (f) ``AttritionMalformed`` — drag1→drop1, drag2→drop1 → (1, 1, 0, 0); the
  first CHECK's Input is cut mid-string, so derive writes it ``parse_ok =
  0`` and the replay stores ``(None, malformed)``; drag3→drop2 →
  (1, 1, 2, 0); the second CHECK agrees ``(1, None)``. ``parse_error``; one
  of two checks matched, so no ``replay_ok``; no mismatch → ``limited``.
* (g) ``AttritionDupCard`` — (1, 1, 0, 0) replayed; the CHECK lists drag1
  under drop1 and drop2 → decode cause ``duplicate_card`` → stored
  ``(None, duplicate_card)``, ``invalid_state`` → ``exclude``.
* (h) ``AttritionResumed`` — session 711: the key placed and checked
  (agrees; ``replay_ok``, ``ready``, no prior). Session 712 two hours
  later (a session change, also over the 1800 s gap): attempt 2 with
  ``prior_attempt_id`` = attempt 1; drag3→drop1 from s_0 → (0, 0, 1, 0);
  its CHECK payload has drag1 in two zones. The structural cause comes
  first in precedence, so the stored cause is ``resumed_attempt`` with a
  NULL comparison, while the decode outcome still feeds the flags:
  ``invalid_state`` → ``exclude``. The hidden step-5 case: the stored
  cause says nothing about the decode failure; re-decoding the verbatim
  payload (R8 a) does.
* (i) ``AttritionRegime`` — one CHECK dated 2026-06-01, payload ``not
  json`` (``parse_ok = 0``). Under the regime row (move_logging_from
  2026-06-02; the tripwire is silent because no move row precedes the
  date) the stored cause is ``no_move_logging``, the first name in
  precedence; the decode outcome still raises ``parse_error`` →
  ``limited``. The second hidden case. Its only check is unscored, so its
  outcomes are NULL too.
* (j) ``AttritionLimited`` — drag1→drop1, then drag2→drop1 320 s later
  (over the 300 s analytic idle threshold, under the 1800 s attempt
  boundary), drag3→drop2, the key checked and agreeing: ``replay_ok`` and
  ``long_idle_gap`` → ``limited``; ``max_action_gap_s`` 320.
* (k) ``AttritionMany`` — six CHECKs after three moves: (1, 0, 0, 0),
  (1, 1, 0, 0), then the key four times, each payload the replayed state
  → six agreeing anchors from one student, ``replay_ok``, ``ready``;
  outcomes (0, 1). A cell the PR 2 guard must suppress.
* (l) ``AttritionNoCheck`` — two drops and no CHECK: no anchor, so neither
  ``replay_ok`` nor a mismatch; the empty flag set reads ``limited``;
  NULL outcomes (step 7).
* (m) ``AttritionTakeback`` — drag4→drop3 (0, 0, 0, 3), drag4→dragzone
  (0, 0, 0, 0), reset (0, 0, 0, 0), then the key placed and checked:
  ``replay_ok``, ``ready``; eight configurations.

Totals on the sixteen attempts: 22 checks, 18 eligible anchors, all
agreeing (0 mismatches); four non-eligible checks, one per cause
``no_move_logging``, ``resumed_attempt``, ``malformed``, ``duplicate_card``;
readiness ready 9, limited 4, exclude 3; 80 configurations; 19 scored
checks (the two malformed skipped), 13 of them on the key. Frozen under the
DnD golden rule of ``CLAUDE.md``: if this fails, the replay (or the filter)
changed, not the fixture.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from proteus.core import ingest as core_ingest
from proteus.core.ingest.imports import file_hash
from proteus.services.dragndrop import account_roles, decode, logging_regimes, qc
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
STAFF = "AttritionStaff1"

KEY = (1, 1, 2, 0)
READY, LIMITED, EXCLUDE = qc.READY, qc.LIMITED, qc.EXCLUDE
OK = (1, None)  # an eligible, agreeing anchor

# Per planted student, in attempt order: the item, the flag set, the
# readiness and the (matches_input, anchor_cause) list of its checks in
# replay order — each derived by hand in the module docstring.
PINNED = {
    "AttritionRuledKept": [(Q1, {"replay_ok"}, READY, [OK])],  # (b)
    "AttritionClean1": [(Q1, {"replay_ok"}, READY, [OK])],  # (b)
    "AttritionClean2": [(Q1, {"replay_ok"}, READY, [OK])],  # (b)
    "AttritionClean3": [(Q1, {"replay_ok"}, READY, [OK])],  # (b)
    "AttritionRevised": [(Q1, {"replay_ok"}, READY, [OK, OK])],  # (c)
    "AttritionSA": [(Q1, {"replay_ok"}, READY, [OK])],  # (d)
    # (e): the anchor agrees, but no board ⇒ the excluding flag.
    "AttritionNoBoard": [(Q9, {"replay_ok", "missing_source_map"}, EXCLUDE, [OK])],
    # (f): the truncated payload never reaches the decoder.
    "AttritionMalformed": [(Q1, {"parse_error"}, LIMITED, [(None, qc.CAUSE_MALFORMED), OK])],
    # (g): one card in two zones.
    "AttritionDupCard": [(Q1, {"invalid_state"}, EXCLUDE, [(None, qc.CAUSE_DUPLICATE_CARD)])],
    # (h): attempt 1 clean; attempt 2 resumed, its duplicate-card payload
    # hidden under the structural cause, the decode flag still raised.
    "AttritionResumed": [
        (Q1, {"replay_ok"}, READY, [OK]),
        (Q1, {"invalid_state"}, EXCLUDE, [(None, qc.CAUSE_RESUMED_ATTEMPT)]),
    ],
    # (i): dated before the regime date; the malformed payload hidden under
    # no_move_logging, parse_error still raised.
    "AttritionRegime": [(Q1, {"parse_error"}, LIMITED, [(None, qc.CAUSE_NO_MOVE_LOGGING)])],
    # (j): a 320 s gap over the 300 s analytic threshold.
    "AttritionLimited": [(Q1, {"replay_ok", "long_idle_gap"}, LIMITED, [OK])],
    "AttritionMany": [(Q1, {"replay_ok"}, READY, [OK] * 6)],  # (k)
    "AttritionNoCheck": [(Q1, set(), LIMITED, [])],  # (l): no anchor at all
    "AttritionTakeback": [(Q1, {"replay_ok"}, READY, [OK])],  # (m)
}
STUDENTS = tuple(PINNED)

# (first_check_correct, eventual_correct) from the scored checks only.
OUTCOMES = {
    "AttritionClean1": (1, 1),  # (b): the key checked once
    "AttritionRevised": (0, 1),  # (c)
    "AttritionSA": (0, 0),  # (d)
    "AttritionMany": (0, 1),  # (k)
    "AttritionNoCheck": (None, None),  # (l)
    "AttritionNoBoard": (None, None),  # (e): no key, no score
    "AttritionRegime": (None, None),  # (i): its only check is unscored
}

# The replayed configurations, (seq, state, is_check), of the attempts whose
# replay is not the plain key placement.
STATES = {
    ("AttritionResumed", 2): [  # (h): from s_0, drag3→drop1, the CHECK
        (0, (0, 0, 0, 0), 0), (1, (0, 0, 1, 0), 0), (2, (0, 0, 1, 0), 1),
    ],
    ("AttritionRegime", 1): [(0, (0, 0, 0, 0), 0), (1, (0, 0, 0, 0), 1)],  # (i)
    ("AttritionNoBoard", 1): [  # (e): P inferred as 2
        (0, (0, 0), 0), (1, (1, 0), 0), (2, (1, 2), 0), (3, (1, 2), 1),
    ],
    ("AttritionTakeback", 1): [  # (m)
        (0, (0, 0, 0, 0), 0), (1, (0, 0, 0, 3), 0), (2, (0, 0, 0, 0), 0),
        (3, (0, 0, 0, 0), 0), (4, (1, 0, 0, 0), 0), (5, (1, 1, 0, 0), 0),
        (6, (1, 1, 2, 0), 0), (7, KEY, 1),
    ],
}


@pytest.fixture
def attrition(db_conn, fixtures_dir, four_shapes_board):
    """The frozen-run order on one database: roles import, ingest, derive,
    the board, the regime row (after the ingest, keyed by the file's
    sha256), then reconstruct → score → analyze."""
    tab = fixtures_dir / FIXTURE
    roles = account_roles.import_roles(db_conn, fixtures_dir / ROLES)
    ingested = core_ingest.ingest_file(db_conn, tab)
    derived = derive_events(db_conn)
    four_shapes_board(db_conn)  # q1 only; q9 stays board-less
    regime = logging_regimes.add_regime(
        db_conn, file_hash(tab), REGIME_DATE,
        reason="fixture: one check logged before the component logged moves",
    )
    reconstruct = reconstruct_all(db_conn)
    score = score_checks(db_conn)
    analyze = analyze_all(db_conn)
    return SimpleNamespace(
        conn=db_conn,
        roles=roles,
        ingested=ingested,
        derived=derived,
        regime=regime,
        reconstruct=reconstruct,
        score=score,
        analyze=analyze,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _attempts(conn, student):
    return conn.execute(
        "SELECT * FROM dnd_attempts WHERE student_id = ? ORDER BY attempt_index",
        (student,),
    ).fetchall()


def _one_attempt(conn, student):
    rows = _attempts(conn, student)
    assert len(rows) == 1, f"expected one attempt, found {len(rows)}"
    return rows[0]


def _checks(conn, attempt_id):
    return conn.execute(
        "SELECT c.seq, c.matches_input, c.anchor_cause, c.s_json, "
        "d.configuration_json, d.parse_ok, d.feedback "
        "FROM dnd_configurations c JOIN dnd_events d USING (event_id) "
        "WHERE c.attempt_id = ? AND c.is_check = 1 ORDER BY c.seq",
        (attempt_id,),
    ).fetchall()


def _states(conn, attempt_id):
    return [
        (c["seq"], tuple(json.loads(c["s_json"])), c["is_check"])
        for c in conn.execute(
            "SELECT seq, s_json, is_check FROM dnd_configurations "
            "WHERE attempt_id = ? ORDER BY seq", (attempt_id,),
        )
    ]


def _flags(attempt):
    return set(json.loads(attempt["qc_flags_json"]))


def _count(conn, sql, *params):
    return conn.execute(sql, params).fetchone()[0]


def _metrics(conn, attempt_id):
    return conn.execute(
        "SELECT * FROM dnd_attempt_metrics WHERE attempt_id = ?", (attempt_id,)
    ).fetchone()


# ---------------------------------------------------------------------------
# The build: one import under the roles, the board, the regime row
# ---------------------------------------------------------------------------
class TestBuild:
    def test_roles_then_ingest_then_derive(self, attrition):
        assert (attrition.roles.added, attrition.roles.unchanged) == (2, 0)
        # 71 rows: 44 drops, 23 checks, 1 reset, 1 page, and the two
        # non-DnD rows of AttritionSA; none collides.
        assert (attrition.ingested.inserted, attrition.ingested.skipped) == (71, 0)
        assert attrition.ingested.per_selection == {
            "dragNdrop-drop": 44, "dragNdrop": 23, "dragNdrop-reset": 1,
            "page": 1, "shortanswer": 1, "mChoice": 1,
        }
        # 69 DnD-relevant rows, the staff id's 5 withheld; two malformed
        # checks ((f) and (i)).
        d = attrition.derived
        assert (d.inserted, d.excluded, d.skipped, d.malformed) == (64, 5, 0, 2)
        assert attrition.regime == "added"
        assert load_board(attrition.conn, Q1) is not None
        assert load_board(attrition.conn, Q9) is None

    def test_staff_id_is_absent_downstream_and_present_in_events(self, attrition):
        conn = attrition.conn
        assert _count(conn, "SELECT count(*) FROM events WHERE student_id = ?", STAFF) == 5
        assert _count(
            conn, "SELECT count(*) FROM dnd_events d JOIN events e USING (event_id) "
            "WHERE e.student_id = ?", STAFF,
        ) == 0
        assert _attempts(conn, STAFF) == []
        assert _count(conn, "SELECT count(*) FROM dnd_events") == 64

    def test_reconstruct_summary(self, attrition):
        r = attrition.reconstruct
        assert r.questions == 2  # q1 and the board-less q9
        assert r.attempts == 16  # fifteen students, AttritionResumed twice
        # X0 + applied moves + resets + checks per attempt: 5 (RuledKept)
        # + 15 (Clean×3) + 7 (Revised) + 3 (SA) + 4 (NoBoard) + 6
        # (Malformed) + 4 (DupCard) + 5 + 3 (Resumed) + 2 (Regime) + 5
        # (Limited) + 10 (Many) + 3 (NoCheck) + 8 (Takeback).
        assert r.configurations == 80
        assert r.checks == 22
        assert r.mismatches == 0  # every eligible anchor agrees
        assert r.non_eligible == 4  # (f), (g), (h) attempt 2, (i)
        assert (r.noop_moves, r.skipped_actions, r.ambiguous_traces) == (0, 0, 0)
        assert (r.resumed_attempts, r.seeded_attempts) == (1, 0)
        assert (r.no_move_logging_checks, r.resumed_attempt_checks) == (1, 1)

    def test_score_and_analyze(self, attrition):
        conn = attrition.conn
        # 21 checks on q1, the two malformed ones skipped; 13 on the key:
        # RuledKept, Clean×3, Revised's second, Malformed's second,
        # Resumed's first, Limited, Many's last four, Takeback. q9 has no
        # board and no score.
        assert (attrition.score.scored, attrition.score.skipped) == (19, 2)
        assert attrition.score.correct == 13
        assert _count(conn, "SELECT count(*) FROM dnd_check_scores WHERE question_id = ?", Q9) == 0
        assert attrition.analyze.attempts == 16
        assert attrition.analyze.with_convergence == 15  # all but (e)
        assert _count(conn, "SELECT count(*) FROM dnd_attempt_metrics") == 16

    def test_regime_row_binds_to_the_import(self, attrition):
        conn = attrition.conn
        assert logging_regimes.regime_by_import(conn) == {
            attrition.ingested.import_id: REGIME_DATE,
        }
        # The tripwire is silent: no move, takeback or reset row precedes
        # the date (every row but AttritionRegime's check is 2026-06-03 or
        # later).
        assert logging_regimes.assert_data_consistent(conn) == {
            attrition.ingested.import_id: REGIME_DATE,
        }


# ---------------------------------------------------------------------------
# The planted attempts, one student at a time
# ---------------------------------------------------------------------------
class TestPlantedAttempts:
    @pytest.mark.parametrize("student", STUDENTS)
    def test_attempt_count_item_and_provenance(self, attrition, student):
        attempts = _attempts(attrition.conn, student)
        assert len(attempts) == len(PINNED[student])
        for index, (attempt, (question_id, _, _, _)) in enumerate(zip(attempts, PINNED[student]), start=1):
            assert attempt["question_id"] == question_id
            assert attempt["attempt_index"] == index
            assert attempt["canonical_question_id"] is None  # no alias in play
        first = attempts[0]
        assert first["segmentation_rule"].startswith("initial;")
        assert first["prior_attempt_id"] is None

    @pytest.mark.parametrize("student", STUDENTS)
    def test_flag_set_and_readiness(self, attrition, student):
        attempts = _attempts(attrition.conn, student)
        for attempt, (_, flags, readiness, _) in zip(attempts, PINNED[student]):
            assert _flags(attempt) == flags
            assert attempt["readiness"] == readiness

    @pytest.mark.parametrize("student", STUDENTS)
    def test_checks_comparison_and_cause(self, attrition, student):
        conn = attrition.conn
        for attempt, (_, _, _, checks) in zip(_attempts(conn, student), PINNED[student]):
            got = [(c["matches_input"], c["anchor_cause"]) for c in _checks(conn, attempt["attempt_id"])]
            assert got == checks

    @pytest.mark.parametrize("student", sorted(OUTCOMES))
    def test_outcomes(self, attrition, student):
        conn = attrition.conn
        metrics = _metrics(conn, _attempts(conn, student)[0]["attempt_id"])
        assert (metrics["first_check_correct"], metrics["eventual_correct"]) == OUTCOMES[student]

    @pytest.mark.parametrize("student, index", sorted(STATES))
    def test_replayed_states(self, attrition, student, index):
        conn = attrition.conn
        attempt = _attempts(conn, student)[index - 1]
        assert _states(conn, attempt["attempt_id"]) == STATES[(student, index)]

    def test_clean_attempts_check_the_key(self, attrition):
        conn = attrition.conn
        for student in ("AttritionRuledKept", "AttritionClean1", "AttritionClean2",
                        "AttritionClean3", "AttritionLimited", "AttritionTakeback"):
            [check] = _checks(conn, _one_attempt(conn, student)["attempt_id"])
            assert tuple(json.loads(check["s_json"])) == KEY
            assert check["feedback"] == "Correct"

    def test_many_checks_states(self, attrition):
        conn = attrition.conn
        checks = _checks(conn, _one_attempt(conn, "AttritionMany")["attempt_id"])
        assert [tuple(json.loads(c["s_json"])) for c in checks] == [
            (1, 0, 0, 0), (1, 1, 0, 0), KEY, KEY, KEY, KEY,
        ]
        assert [c["feedback"] for c in checks] == [
            "Incorrect", "Incorrect", "Correct", "Correct", "Correct", "Correct",
        ]

    def test_long_idle_gap_is_the_320_second_pause(self, attrition):
        conn = attrition.conn
        attempt = _one_attempt(conn, "AttritionLimited")
        assert _metrics(conn, attempt["attempt_id"])["max_action_gap_s"] == 320.0


# ---------------------------------------------------------------------------
# The two hidden step-5 cases: the stored cause is structural, the decode
# flag is raised, and only a re-decode of the verbatim payload (R8 a) says
# the payload failed
# ---------------------------------------------------------------------------
class TestHiddenCases:
    def test_resumed_attempt_hides_a_duplicate_card_payload(self, attrition):
        conn = attrition.conn
        first, second = _attempts(conn, "AttritionResumed")
        assert first["prior_attempt_id"] is None
        assert second["prior_attempt_id"] == first["attempt_id"]
        assert second["segmentation_rule"].startswith("session_change;")
        assert (first["session_id"], second["session_id"]) == ("711", "712")
        assert (_flags(first), first["readiness"]) == ({"replay_ok"}, READY)
        assert (_flags(second), second["readiness"]) == ({"invalid_state"}, EXCLUDE)
        [check] = _checks(conn, second["attempt_id"])
        assert (check["matches_input"], check["anchor_cause"]) == (None, qc.CAUSE_RESUMED_ATTEMPT)
        assert check["parse_ok"] == 1  # it parsed; it does not decode
        board = load_board(conn, Q1)
        decoded = decode.decode_configuration(
            json.loads(check["configuration_json"]), Q1, board.n_cards, board.n_zones,
        )
        assert decoded.cause == qc.CAUSE_DUPLICATE_CARD
        assert decoded.duplicate_cards == (1,)

    def test_regime_hides_a_malformed_payload(self, attrition):
        conn = attrition.conn
        attempt = _one_attempt(conn, "AttritionRegime")
        assert attempt["prior_attempt_id"] is None
        assert (_flags(attempt), attempt["readiness"]) == ({"parse_error"}, LIMITED)
        [check] = _checks(conn, attempt["attempt_id"])
        assert (check["matches_input"], check["anchor_cause"]) == (None, qc.CAUSE_NO_MOVE_LOGGING)
        assert check["parse_ok"] == 0
        assert check["configuration_json"] == "not json"
        assert attempt["started_at"][:10] == "2026-06-01" < REGIME_DATE

    def test_the_visible_decode_causes_are_stored(self, attrition):
        conn = attrition.conn
        [malformed, agreeing] = _checks(conn, _one_attempt(conn, "AttritionMalformed")["attempt_id"])
        assert (malformed["parse_ok"], malformed["anchor_cause"]) == (0, qc.CAUSE_MALFORMED)
        assert (agreeing["matches_input"], agreeing["anchor_cause"]) == OK
        [dup] = _checks(conn, _one_attempt(conn, "AttritionDupCard")["attempt_id"])
        assert (dup["parse_ok"], dup["anchor_cause"]) == (1, qc.CAUSE_DUPLICATE_CARD)


# ---------------------------------------------------------------------------
# The counts the ledger (PR 2) will read: a non-zero loss at every step
# ---------------------------------------------------------------------------
class TestStepCoverage:
    def test_step_1_governance(self, attrition):
        conn = attrition.conn
        assert account_roles.excluded_ids(conn) == frozenset({STAFF})
        assert _count(conn, "SELECT count(DISTINCT student_id) FROM events") == 16
        assert _count(
            conn, "SELECT count(DISTINCT e.student_id) FROM dnd_events d JOIN events e USING (event_id)"
        ) == 15

    def test_step_2_question_type(self, attrition):
        # The two non-DnD rows of AttritionSA never reach dnd_events.
        conn = attrition.conn
        assert _count(
            conn, "SELECT count(*) FROM events WHERE selection IN ('shortanswer', 'mChoice')"
        ) == 2
        assert _count(
            conn, "SELECT count(*) FROM dnd_events d JOIN events e USING (event_id) "
            "WHERE e.selection NOT IN ('dragNdrop', 'dragNdrop-drop', 'dragNdrop-reset', 'page')"
        ) == 0

    def test_step_3_source_alignment(self, attrition):
        conn = attrition.conn
        no_board = [
            r["question_id"] for r in conn.execute(
                "SELECT DISTINCT question_id FROM dnd_attempts ORDER BY question_id"
            ) if load_board(conn, r["question_id"]) is None
        ]
        assert no_board == [Q9]
        assert _count(
            conn, "SELECT count(*) FROM dnd_attempts WHERE qc_flags_json LIKE '%missing_source_map%'"
        ) == 1

    def test_step_4_event_parsing(self, attrition):
        conn = attrition.conn
        assert _count(conn, "SELECT count(*) FROM dnd_events WHERE parse_ok = 0") == 2
        assert _count(
            conn, "SELECT count(*) FROM dnd_attempts WHERE qc_flags_json LIKE '%parse_error%'"
        ) == 2

    def test_step_5_state_validity_by_cause(self, attrition):
        conn = attrition.conn
        found = {
            r["anchor_cause"]: r["n"] for r in conn.execute(
                "SELECT anchor_cause, count(*) AS n FROM dnd_configurations "
                "WHERE is_check = 1 AND anchor_cause IS NOT NULL GROUP BY anchor_cause"
            )
        }
        assert {cause: found.get(cause, 0) for cause in qc.ANCHOR_CAUSE_ORDER} == {
            qc.CAUSE_NO_MOVE_LOGGING: 1,
            qc.CAUSE_RESUMED_ATTEMPT: 1,
            qc.CAUSE_MALFORMED: 1,
            qc.CAUSE_DUPLICATE_CARD: 1,
            qc.CAUSE_UNKNOWN_CARD: 0,
            qc.CAUSE_UNKNOWN_ZONE: 0,
            qc.CAUSE_OFFBOARD_SLOT: 0,
        }
        assert sum(found.values()) == attrition.reconstruct.non_eligible == 4
        # Two attempts carry invalid_state: the stored duplicate_card and
        # the one hidden under resumed_attempt.
        assert _count(
            conn, "SELECT count(*) FROM dnd_attempts WHERE qc_flags_json LIKE '%invalid_state%'"
        ) == 2

    def test_step_6_readiness_split(self, attrition):
        conn = attrition.conn
        split = {
            r["readiness"]: r["n"] for r in conn.execute(
                "SELECT readiness, count(*) AS n FROM dnd_attempts GROUP BY readiness"
            )
        }
        assert split == {READY: 9, LIMITED: 4, EXCLUDE: 3}
        # J9: items at Ready are items with at least one ready attempt — q1 only.
        assert [
            r[0] for r in conn.execute(
                "SELECT DISTINCT question_id FROM dnd_attempts WHERE readiness = ? ORDER BY 1",
                (READY,),
            )
        ] == [Q1]

    def test_step_7_null_outcomes(self, attrition):
        # The outcomes come from the scored checks only: AttritionNoCheck
        # (no check), AttritionNoBoard (no key, nothing scored) and
        # AttritionRegime (its only check is malformed, so unscored) carry
        # NULL outcomes; AttritionMalformed does not — its second check is
        # scored.
        assert _count(
            attrition.conn,
            "SELECT count(*) FROM dnd_attempt_metrics WHERE first_check_correct IS NULL",
        ) == 3

    def test_eligible_anchors_and_students_per_item(self, attrition):
        conn = attrition.conn
        checks = "FROM dnd_configurations c JOIN dnd_attempts a USING (attempt_id) WHERE c.is_check = 1"
        assert _count(conn, f"SELECT count(*) {checks}") == 22
        assert _count(conn, f"SELECT count(*) {checks} AND c.anchor_cause IS NULL") == 18
        assert _count(conn, f"SELECT count(*) {checks} AND c.matches_input = 1") == 18
        assert _count(conn, f"SELECT count(*) {checks} AND c.matches_input = 0") == 0
        # Fourteen students on q1 (every non-staff student but NoBoard), one on q9.
        assert _count(conn, "SELECT count(DISTINCT student_id) FROM dnd_attempts WHERE question_id = ?", Q1) == 14
        assert _count(conn, "SELECT count(DISTINCT student_id) FROM dnd_attempts WHERE question_id = ?", Q9) == 1
        # AttritionMany alone contributes six of q1's 21 checks.
        assert _count(conn, f"SELECT count(*) {checks} AND a.student_id = 'AttritionMany'") == 6
        assert _count(conn, f"SELECT count(*) {checks} AND a.question_id = ?", Q1) == 21
