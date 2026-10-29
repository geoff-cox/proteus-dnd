"""
The paper-tables fixture (``cc-task-paper-tables.md``, Checkpoint 1 ruling
R12, 2026-10-18): the fourth hand-made DnD fixture, planted so that the
manuscript-table generator's tests have structure the three frozen fixtures
cannot give — golden_dnd.tab and golden_dnd_training_anomalies.tab together
yield 8 checks, 6 eligible anchors, all agreeing, one item, no cell with
five students and no alias.

``golden_dnd_paper_tables.tab`` holds seventeen single-sitting attempts
(``PaperStudent*``, 2026-05, one Session Id each, a page row five seconds
before the first move, rows five seconds apart). The hand replay, under the
reconstruction rules (board P = 4, R = 3, key (1, 1, 2, 0); a card not
named in the Input sits in the tray; the F2 sort key is timestamp, numeric
Problem View, then file order; a drop to a slot beyond R is skipped and
flags ``invalid_state``; the decode causes in the precedence of
``qc.ANCHOR_CAUSE_ORDER``):

* (a) ``PaperStudent01``–``05`` on the demo item: every move replays, the
  Input decodes to the replayed vector → ``(matches_input, anchor_cause) =
  (1, None)``, flags ``{replay_ok}``, ``ready``. 01 and 02 end on the key
  (Correct); 03 (2, 1, 0, 0), 04 (1, 0, 0, 3) and 05 (1, 2, 3, 0) do not.
* (b) ``PaperStudentDrop``: moves give (1, 1, 0, 0); the Input claims card 3
  in drop2, which no move placed → (0, None) at card distance 1; flags
  ``{check_mismatch}`` only — the diagnostics' *unexplained* residual.
* (c) ``PaperStudentTie``: a CHECK and the drop ``drag3 -> drop2`` share one
  timestamp and Problem View, the CHECK first in file order, so the replay
  compares it before the tied drop: (1, 1, 0, 0) against (1, 1, 2, 0) →
  (0, None), d = 1; flags ``{check_mismatch, row_order_ambiguous}`` —
  *ambiguous ordering*.
* (d) ``PaperStudentOffboard``: ``drag4 -> drop4`` is off the R = 3 board
  and skipped (``invalid_state``); card 3's move is missing, the Input is
  clean → (0, None), d = 1; flags ``{check_mismatch, invalid_state}``,
  ``exclude`` — *skipped event*, by elimination.
* (e) one non-eligible CHECK per decode cause, each after one clean move:
  a truncated JSON Input → ``malformed`` (``parse_error``, ``limited``);
  card 1 under drop1 and drop2 → ``duplicate_card``; ``drag5`` →
  ``unknown_card``; ``_dropA`` → ``unknown_zone``; a ``drop4`` key →
  ``offboard_slot`` (the last four ``invalid_state``, ``exclude``).
* (f) ``demo-book__demo-shapes-q2`` (the same four-shape board): one clean
  attempt (``PaperStudentQ2``) and one logged under the retired identifier
  ``demo-book__demo-shapes-q2-old`` (``PaperStudentOld``), which the test
  aliases to q2 before reconstructing — it binds to q2's board (P = 4, no
  ``missing_source_map``) and records ``canonical_question_id``.
* (g) ``PaperStudentMany`` on q2: six CHECKs after six moves, each Input the
  replayed state → six eligible agreeing anchors from one student.
* (h) ``demo-book__demo-shapes-q3``, no board: P is inferred as 3 (``drag3``
  appears only in the Input); replay (1, 1, 0) against (1, 1, 2) → an
  eligible, disagreeing anchor at d = 1 (no board, no R bound), the attempt
  ``{check_mismatch, missing_source_map}`` → ``exclude``.

Ingested beside the two fixtures above (three imports, one database): 30
checks, 23 eligible anchors, 19 agreeing, 4 disagreeing; the seven causes
read no_move_logging 0, resumed_attempt 0, malformed 2, duplicate_card 1,
unknown_card 2, unknown_zone 1, offboard_slot 1 (DemoStudent2 contributes
one malformed and one unknown_card). Eleven students carry an eligible
anchor on q1, eight of them from this file. Four logged identifiers; three
with a board (q1, q2 and the alias q2-old) resolving to two canonical items.
Frozen under the DnD golden rule of ``CLAUDE.md``: if this fails, the replay
changed, not the fixture.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from proteus.core import ingest as core_ingest
from proteus.services.dragndrop import aliases, decode, qc
from proteus.services.dragndrop.board import load_board
from proteus.services.dragndrop.derive import derive_events
from proteus.services.dragndrop.process import analyze_all
from proteus.services.dragndrop.reconstruct import reconstruct_all
from proteus.services.dragndrop.scoring import score_checks

FIXTURE = "golden_dnd_paper_tables.tab"
COMPANIONS = ("golden_dnd.tab", "golden_dnd_training_anomalies.tab")

Q1 = "demo-book__demo-shapes-q1"
Q2 = "demo-book__demo-shapes-q2"
Q2_OLD = "demo-book__demo-shapes-q2-old"
Q3 = "demo-book__demo-shapes-q3"

KEY = (1, 1, 2, 0)
READY, LIMITED, EXCLUDE = qc.READY, qc.LIMITED, qc.EXCLUDE
OK, DIS = (1, None), (0, None)  # an eligible anchor, agreeing / disagreeing

# Per planted student: the item, the flag set, the readiness, the
# (matches_input, anchor_cause) list of its checks in replay order, and the
# feedback the platform logged on each check.
PINNED = {
    "PaperStudent01": (Q1, {"replay_ok"}, READY, [OK], ["Correct"]),
    "PaperStudent02": (Q1, {"replay_ok"}, READY, [OK], ["Correct"]),
    "PaperStudent03": (Q1, {"replay_ok"}, READY, [OK], ["Incorrect"]),
    "PaperStudent04": (Q1, {"replay_ok"}, READY, [OK], ["Incorrect"]),
    "PaperStudent05": (Q1, {"replay_ok"}, READY, [OK], ["Incorrect"]),
    "PaperStudentDrop": (Q1, {"check_mismatch"}, LIMITED, [DIS], ["Correct"]),
    "PaperStudentTie": (
        Q1, {"check_mismatch", "row_order_ambiguous"}, LIMITED, [DIS], ["Correct"],
    ),
    "PaperStudentOffboard": (
        Q1, {"check_mismatch", "invalid_state"}, EXCLUDE, [DIS], ["Correct"],
    ),
    "PaperStudentMalformed": (
        Q1, {"parse_error"}, LIMITED, [(None, qc.CAUSE_MALFORMED)], ["Incorrect"],
    ),
    "PaperStudentDupCard": (
        Q1, {"invalid_state"}, EXCLUDE, [(None, qc.CAUSE_DUPLICATE_CARD)], ["Incorrect"],
    ),
    "PaperStudentUnknownCard": (
        Q1, {"invalid_state"}, EXCLUDE, [(None, qc.CAUSE_UNKNOWN_CARD)], ["Incorrect"],
    ),
    "PaperStudentUnknownZone": (
        Q1, {"invalid_state"}, EXCLUDE, [(None, qc.CAUSE_UNKNOWN_ZONE)], ["Incorrect"],
    ),
    "PaperStudentOffboardSlot": (
        Q1, {"invalid_state"}, EXCLUDE, [(None, qc.CAUSE_OFFBOARD_SLOT)], ["Incorrect"],
    ),
    "PaperStudentQ2": (Q2, {"replay_ok"}, READY, [OK], ["Correct"]),
    "PaperStudentOld": (Q2_OLD, {"replay_ok"}, READY, [OK], ["Correct"]),
    "PaperStudentMany": (
        Q2, {"replay_ok"}, READY, [OK] * 6,
        ["Incorrect", "Incorrect", "Incorrect", "Correct", "Incorrect", "Correct"],
    ),
    "PaperStudentNoBoard": (
        Q3, {"check_mismatch", "missing_source_map"}, EXCLUDE, [DIS], ["Incorrect"],
    ),
}
STUDENTS = tuple(PINNED)

# The replayed states at the five clean q1 checks (a): two on the key.
CLEAN_STATES = {
    "PaperStudent01": KEY,
    "PaperStudent02": KEY,
    "PaperStudent03": (2, 1, 0, 0),
    "PaperStudent04": (1, 0, 0, 3),
    "PaperStudent05": (1, 2, 3, 0),
}

# The four disagreeing eligible anchors: (replayed state, decoded Input),
# each at card distance exactly 1 — one card differs.
DISAGREEMENTS = {
    "PaperStudentDrop": ((1, 1, 0, 0), KEY),
    "PaperStudentTie": ((1, 1, 0, 0), KEY),
    "PaperStudentOffboard": ((1, 1, 0, 0), KEY),
    "PaperStudentNoBoard": ((1, 1, 0), (1, 1, 2)),
}

# PaperStudentMany's six checked states, in order.
MANY_STATES = [
    (1, 0, 0, 0), (1, 1, 0, 0), (1, 1, 3, 0), KEY, (1, 1, 2, 3), KEY,
]


@pytest.fixture
def paper_tables(db_conn, fixtures_dir, four_shapes_board):
    """The three DnD fixtures in one database, the two boards, the alias
    registered before reconstruction, then reconstruct → score → analyze."""
    ingested = [
        core_ingest.ingest_file(db_conn, fixtures_dir / name)
        for name in (*COMPANIONS, FIXTURE)
    ]
    derive_events(db_conn)
    four_shapes_board(db_conn)  # q1
    four_shapes_board(db_conn, question_id=Q2)
    alias_status = aliases.add_alias(
        db_conn, Q2_OLD, Q2, reason="renamed in source (fixture)"
    )
    reconstruct = reconstruct_all(db_conn)
    score = score_checks(db_conn)
    analyze = analyze_all(db_conn)
    return SimpleNamespace(
        conn=db_conn,
        ingested=ingested,
        alias_status=alias_status,
        reconstruct=reconstruct,
        score=score,
        analyze=analyze,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _attempt(conn, student):
    rows = conn.execute(
        "SELECT * FROM dnd_attempts WHERE student_id = ? ORDER BY attempt_index",
        (student,),
    ).fetchall()
    assert len(rows) == 1, (student, len(rows))
    return rows[0]


def _checks(conn, attempt_id):
    return conn.execute(
        "SELECT c.seq, c.matches_input, c.anchor_cause, c.s_json, "
        "d.configuration_json, d.feedback "
        "FROM dnd_configurations c JOIN dnd_events d USING (event_id) "
        "WHERE c.attempt_id = ? AND c.is_check = 1 ORDER BY c.seq",
        (attempt_id,),
    ).fetchall()


def _flags(attempt):
    return set(json.loads(attempt["qc_flags_json"]))


def _count(conn, sql, *params):
    return conn.execute(sql, params).fetchone()[0]


# The check rows of every attempt, optionally only this fixture's students.
_CHECKS = (
    "FROM dnd_configurations c JOIN dnd_attempts a USING (attempt_id) "
    "WHERE c.is_check = 1"
)
_PAPER = " AND a.student_id LIKE 'PaperStudent%'"


def _agreement(conn, only_paper=False):
    scope = _CHECKS + (_PAPER if only_paper else "")
    return (
        _count(conn, f"SELECT COUNT(*) {scope}"),
        _count(conn, f"SELECT COUNT(*) {scope} AND c.anchor_cause IS NULL"),
        _count(conn, f"SELECT COUNT(*) {scope} AND c.matches_input = 1"),
        _count(conn, f"SELECT COUNT(*) {scope} AND c.matches_input = 0"),
    )


def _cause_counts(conn, only_paper=False):
    scope = _CHECKS + (_PAPER if only_paper else "")
    rows = conn.execute(
        f"SELECT c.anchor_cause, COUNT(*) AS n {scope} "
        "AND c.anchor_cause IS NOT NULL GROUP BY c.anchor_cause"
    ).fetchall()
    found = {r["anchor_cause"]: r["n"] for r in rows}
    assert set(found) <= set(qc.ANCHOR_CAUSE_ORDER)
    return {cause: found.get(cause, 0) for cause in qc.ANCHOR_CAUSE_ORDER}


# ---------------------------------------------------------------------------
# The build: three imports, two boards, the alias, the stage summaries
# ---------------------------------------------------------------------------
class TestBuild:
    def test_three_imports_every_row_ingested(self, paper_tables):
        conn = paper_tables.conn
        assert _count(conn, "SELECT COUNT(*) FROM raw_imports") == 3
        # 14 + 16 + 79 rows; none collides (the planted tie is a drop and a
        # CHECK, whose event identities differ in Selection).
        assert [(r.inserted, r.skipped) for r in paper_tables.ingested] == [
            (14, 0), (16, 0), (79, 0),
        ]
        assert _count(conn, "SELECT COUNT(*) FROM events") == 109
        assert _count(
            conn, "SELECT COUNT(*) FROM events WHERE student_id LIKE 'PaperStudent%'"
        ) == 79

    def test_alias_registered_and_resolving(self, paper_tables):
        conn = paper_tables.conn
        assert paper_tables.alias_status == "added"
        assert aliases.resolve_question_id(conn, Q2_OLD) == Q2
        assert aliases.item_ids(conn, Q2) == (Q2, Q2_OLD)
        assert aliases.item_ids(conn, Q2_OLD) == (Q2_OLD,)
        # q2-old binds to q2's board through the alias; q3 has none.
        assert load_board(conn, Q2_OLD).question_id == Q2
        assert load_board(conn, Q3) is None

    def test_reconstruct_summary(self, paper_tables):
        r = paper_tables.reconstruct
        assert r.questions == 4
        assert r.attempts == 21  # 2 + 2 + 17
        assert r.checks == 30
        assert r.mismatches == 4
        assert r.non_eligible == 7
        # DemoStudent2's two unreplayable rows plus PaperStudentOffboard's
        # drop4; TrainingStudent4's tie plus PaperStudentTie's.
        assert r.skipped_actions == 3
        assert r.ambiguous_traces == 2
        assert r.noop_moves == 2  # TrainingStudent3 and TrainingStudent4
        assert (r.resumed_attempts, r.no_move_logging_checks, r.resumed_attempt_checks) == (0, 0, 0)

    def test_score_and_analyze_cover_the_boards(self, paper_tables):
        conn = paper_tables.conn
        # Every parseable check on an item with a board is scored: 21 - 2
        # malformed on q1, 7 on q2, 1 on q2-old (through the alias); q3 has
        # no board and no scores.
        assert (paper_tables.score.scored, paper_tables.score.skipped) == (27, 2)
        scored = {
            r["question_id"]: r["n"]
            for r in conn.execute(
                "SELECT question_id, COUNT(*) AS n FROM dnd_check_scores "
                "GROUP BY question_id"
            )
        }
        assert scored == {Q1: 19, Q2: 7, Q2_OLD: 1}
        assert paper_tables.analyze.attempts == 21
        assert _count(conn, "SELECT COUNT(*) FROM dnd_attempt_metrics") == 21


# ---------------------------------------------------------------------------
# The planted attempts, one student at a time
# ---------------------------------------------------------------------------
class TestPlantedAttempts:
    @pytest.mark.parametrize("student", STUDENTS)
    def test_one_single_sitting_attempt(self, paper_tables, student):
        attempt = _attempt(paper_tables.conn, student)
        assert attempt["question_id"] == PINNED[student][0]
        assert attempt["attempt_index"] == 1
        assert attempt["segmentation_rule"].startswith("initial;")
        assert attempt["prior_attempt_id"] is None

    @pytest.mark.parametrize("student", STUDENTS)
    def test_flag_set_and_readiness(self, paper_tables, student):
        attempt = _attempt(paper_tables.conn, student)
        _, flags, readiness, _, _ = PINNED[student]
        assert _flags(attempt) == flags
        assert attempt["readiness"] == readiness

    @pytest.mark.parametrize("student", STUDENTS)
    def test_checks_comparison_and_cause(self, paper_tables, student):
        conn = paper_tables.conn
        checks = _checks(conn, _attempt(conn, student)["attempt_id"])
        assert [(c["matches_input"], c["anchor_cause"]) for c in checks] == PINNED[student][3]

    @pytest.mark.parametrize("student", STUDENTS)
    def test_logged_feedback(self, paper_tables, student):
        conn = paper_tables.conn
        checks = _checks(conn, _attempt(conn, student)["attempt_id"])
        assert [c["feedback"] for c in checks] == PINNED[student][4]

    @pytest.mark.parametrize("student", STUDENTS)
    def test_canonical_identifier_only_on_the_aliased_attempt(self, paper_tables, student):
        attempt = _attempt(paper_tables.conn, student)
        expected = Q2 if student == "PaperStudentOld" else None
        assert attempt["canonical_question_id"] == expected

    @pytest.mark.parametrize("student", sorted(CLEAN_STATES))
    def test_clean_q1_checked_states(self, paper_tables, student):
        conn = paper_tables.conn
        [check] = _checks(conn, _attempt(conn, student)["attempt_id"])
        assert tuple(json.loads(check["s_json"])) == CLEAN_STATES[student]

    def test_two_of_the_five_clean_attempts_end_on_the_key(self, paper_tables):
        on_key = [s for s, state in CLEAN_STATES.items() if state == KEY]
        assert on_key == ["PaperStudent01", "PaperStudent02"]
        # ... and the platform graded exactly those Correct.
        assert [PINNED[s][4] for s in sorted(CLEAN_STATES)] == [
            ["Correct"], ["Correct"], ["Incorrect"], ["Incorrect"], ["Incorrect"],
        ]

    def test_many_checks_states(self, paper_tables):
        conn = paper_tables.conn
        checks = _checks(conn, _attempt(conn, "PaperStudentMany")["attempt_id"])
        assert len(checks) == 6
        assert [tuple(json.loads(c["s_json"])) for c in checks] == MANY_STATES


# ---------------------------------------------------------------------------
# The disagreeing eligible anchors: d = 1, and the signal behind each
# ---------------------------------------------------------------------------
class TestDisagreements:
    @pytest.mark.parametrize("student", sorted(DISAGREEMENTS))
    def test_card_distance_exactly_one(self, paper_tables, student):
        conn = paper_tables.conn
        attempt = _attempt(conn, student)
        [check] = _checks(conn, attempt["attempt_id"])
        assert (check["matches_input"], check["anchor_cause"]) == DIS
        replayed = tuple(json.loads(check["s_json"]))
        # The decoded Input is re-derived from the verbatim payload exactly as
        # reconstruct did it: P from the stored vector, R only with a board.
        board = load_board(conn, attempt["question_id"])
        decoded = decode.decode_configuration(
            json.loads(check["configuration_json"]),
            attempt["question_id"],
            len(replayed),
            board.n_zones if board is not None else None,
        )
        assert decoded.cause is None
        expected_replayed, expected_decoded = DISAGREEMENTS[student]
        assert replayed == expected_replayed
        assert decoded.s == expected_decoded
        assert sum(1 for a, b in zip(replayed, decoded.s) if a != b) == 1

    def test_unexplained_disagreement_carries_no_other_signal(self, paper_tables):
        # (b): check_mismatch alone — nothing in the attempt explains it.
        assert _flags(_attempt(paper_tables.conn, "PaperStudentDrop")) == {"check_mismatch"}

    def test_ambiguous_ordering_is_the_tied_check(self, paper_tables):
        # (c): the CHECK and the drop share (timestamp, problem_view); the
        # CHECK is first in file order and the replay compared it there.
        conn = paper_tables.conn
        attempt = _attempt(conn, "PaperStudentTie")
        assert "row_order_ambiguous" in _flags(attempt)
        rows = conn.execute(
            "SELECT d.kind, e.timestamp, e.problem_view FROM dnd_events d "
            "JOIN events e USING (event_id) WHERE e.student_id = ? "
            "AND d.kind != 'page_view' "
            "ORDER BY e.timestamp, CAST(e.problem_view AS INTEGER), e.rowid",
            ("PaperStudentTie",),
        ).fetchall()
        assert [r["kind"] for r in rows] == ["move", "move", "check", "move"]
        assert (rows[2]["timestamp"], rows[2]["problem_view"]) == (
            rows[3]["timestamp"], rows[3]["problem_view"],
        )
        # The drop after the check completes the key the Input had claimed.
        final = conn.execute(
            "SELECT s_json FROM dnd_configurations WHERE attempt_id = ? "
            "ORDER BY seq DESC LIMIT 1", (attempt["attempt_id"],),
        ).fetchone()
        assert tuple(json.loads(final["s_json"])) == KEY

    def test_skipped_event_leaves_no_configuration_row(self, paper_tables):
        # (d): three logged drops, two replayed — the drop4 row wrote no
        # configuration and raised invalid_state; the check is still compared.
        conn = paper_tables.conn
        attempt = _attempt(conn, "PaperStudentOffboard")
        assert "invalid_state" in _flags(attempt)
        n_drops = _count(
            conn,
            "SELECT COUNT(*) FROM dnd_events d JOIN events e USING (event_id) "
            "WHERE e.student_id = ? AND d.kind IN ('move', 'takeback')",
            "PaperStudentOffboard",
        )
        n_configs = _count(
            conn, "SELECT COUNT(*) FROM dnd_configurations WHERE attempt_id = ?",
            attempt["attempt_id"],
        )
        assert n_drops == 3
        assert n_configs == 4  # X0 + two applied drops + the check

    def test_board_less_disagreement_is_eligible(self, paper_tables):
        # (h): no board ⇒ P inferred (3), no R bound, the anchor eligible and
        # compared; missing_source_map excludes the attempt regardless.
        conn = paper_tables.conn
        attempt = _attempt(conn, "PaperStudentNoBoard")
        assert _flags(attempt) == {"check_mismatch", "missing_source_map"}
        assert attempt["readiness"] == EXCLUDE
        [check] = _checks(conn, attempt["attempt_id"])
        assert (check["matches_input"], check["anchor_cause"]) == DIS
        assert len(json.loads(check["s_json"])) == 3


# ---------------------------------------------------------------------------
# The counts the generator tests lean on
# ---------------------------------------------------------------------------
class TestCounts:
    def test_students_with_an_eligible_anchor_on_q1(self, paper_tables):
        conn = paper_tables.conn
        sql = (
            f"SELECT COUNT(DISTINCT a.student_id) {_CHECKS} "
            "AND c.anchor_cause IS NULL AND a.question_id = ?"
        )
        # 01–05, Drop, Tie and Offboard from this file; DemoStudent1,
        # TrainingStudent3 and TrainingStudent4 from the other two.
        assert _count(conn, sql + _PAPER, Q1) == 8
        assert _count(conn, sql, Q1) == 11

    def test_checks_eligible_agreeing_disagreeing(self, paper_tables):
        conn = paper_tables.conn
        assert _agreement(conn) == (30, 23, 19, 4)
        assert _agreement(conn, only_paper=True) == (22, 17, 13, 4)

    def test_seven_cause_counts(self, paper_tables):
        conn = paper_tables.conn
        assert _cause_counts(conn) == {
            qc.CAUSE_NO_MOVE_LOGGING: 0,
            qc.CAUSE_RESUMED_ATTEMPT: 0,
            qc.CAUSE_MALFORMED: 2,
            qc.CAUSE_DUPLICATE_CARD: 1,
            qc.CAUSE_UNKNOWN_CARD: 2,
            qc.CAUSE_UNKNOWN_ZONE: 1,
            qc.CAUSE_OFFBOARD_SLOT: 1,
        }
        assert _cause_counts(conn, only_paper=True) == {
            qc.CAUSE_NO_MOVE_LOGGING: 0,
            qc.CAUSE_RESUMED_ATTEMPT: 0,
            qc.CAUSE_MALFORMED: 1,
            qc.CAUSE_DUPLICATE_CARD: 1,
            qc.CAUSE_UNKNOWN_CARD: 1,
            qc.CAUSE_UNKNOWN_ZONE: 1,
            qc.CAUSE_OFFBOARD_SLOT: 1,
        }
        # The seven sum to the non-eligible checks.
        assert sum(_cause_counts(conn).values()) == paper_tables.reconstruct.non_eligible == 7

    def test_logged_identifiers_and_canonical_items(self, paper_tables):
        conn = paper_tables.conn
        logged = [
            r[0] for r in conn.execute(
                "SELECT DISTINCT question_id FROM dnd_attempts ORDER BY question_id"
            )
        ]
        assert logged == [Q1, Q2, Q2_OLD, Q3]
        # Convention A: three logged identifiers carry a board (q2-old
        # through the alias); convention B: they resolve to two canonical
        # items. q3 is the one board-less identifier under either.
        with_board = [q for q in logged if load_board(conn, q) is not None]
        assert with_board == [Q1, Q2, Q2_OLD]
        canonical = sorted({aliases.resolve_question_id(conn, q) for q in with_board})
        assert canonical == [Q1, Q2]
        assert [q for q in logged if load_board(conn, q) is None] == [Q3]
        stored = [
            r[0] for r in conn.execute(
                "SELECT DISTINCT COALESCE(canonical_question_id, question_id) "
                "FROM dnd_attempts ORDER BY 1"
            )
        ]
        assert stored == [Q1, Q2, Q3]

    def test_q2_cell_many_checks_from_few_students(self, paper_tables):
        conn = paper_tables.conn

        def cell(*question_ids):
            marks = ", ".join("?" for _ in question_ids)
            students = _count(
                conn,
                f"SELECT COUNT(DISTINCT student_id) FROM dnd_attempts "
                f"WHERE question_id IN ({marks})", *question_ids,
            )
            checks = _count(
                conn, f"SELECT COUNT(*) {_CHECKS} AND a.question_id IN ({marks})",
                *question_ids,
            )
            return students, checks

        # PaperStudentMany alone: six checks.
        many = _attempt(conn, "PaperStudentMany")
        assert len(_checks(conn, many["attempt_id"])) == 6
        # Convention A (per logged identifier) and B (per canonical item):
        # a cell of seven or eight checks from two or three students — under
        # five, so the by-offering guard must suppress it.
        assert cell(Q2) == (2, 7)
        assert cell(Q2_OLD) == (1, 1)
        assert cell(*aliases.item_ids(conn, Q2)) == (3, 8)
        assert cell(Q1) == (17, 21)
        assert cell(Q3) == (1, 1)
