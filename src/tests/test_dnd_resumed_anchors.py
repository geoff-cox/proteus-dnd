"""
Tests for the two structural anchor causes, the resumed-attempt provenance,
the seeded replay (D3.2 knob 10) and the logging-regime file (dnd/0015;
``cc-task-resumed-anchors``; ``paper1-decisions.md`` 2026-10-05, owner
rulings 2026-10-06 at the brief's Checkpoint 1).

Every fixture is hand-built on the four-shapes board (P = 4, R = 3, key
(1, 1, 2, 0)): one student, one item, attempts separated by a session
change. The hand computation sits in each test's docstring. Nothing here
counts anything by outcome; the structural tests read student, question,
time and the configuration sequence only.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from proteus import cli, db
from proteus.core import ingest as core_ingest
from proteus.core import manifest
from proteus.services.dragndrop import (
    aliases,
    export,
    logging_regimes,
    process,
    qc,
    sensitivity,
    validate,
)
from proteus.services.dragndrop import reconstruct as reconstruct_mod
from proteus.services.dragndrop.derive import derive_events
from proteus.services.dragndrop.reconstruct import reconstruct_all
from proteus.services.dragndrop.scoring import score_checks

Q = "demo-book__demo-shapes-q1"
Q_OLD = "demo-book__demo-shapes-q1-old"  # a retired identifier (alias interplay)
HASH = "f" * 64  # the synthetic import's sha256
REGIME_DATE = "2026-02-07"


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------
def _ts(day: int, hh: int, mm: int, ss: int) -> str:
    return f"2026-02-{day:02d} {hh:02d}:{mm:02d}:{ss:02d}.000000"


def _move(eid, ts, card, zone, *, session="100", q=Q, student="S"):
    target = f"{q}_drop{zone}" if zone else "dragzone"
    return {
        "eid": eid, "selection": "dragNdrop-drop",
        "action": f"{q}_drag{card} -> {target}", "input": "",
        "feedback": "Incorrect", "ts": ts, "session": session,
        "problem": q, "student": student,
    }


def _check(eid, ts, zones, *, feedback="Incorrect", session="100", q=Q, student="S"):
    payload = json.dumps({
        f"{q}_drop{z}": [f"{q}_drag{c}" for c in cards] for z, cards in zones.items()
    })
    return {
        "eid": eid, "selection": "dragNdrop", "action": payload, "input": payload,
        "feedback": feedback, "ts": ts, "session": session, "problem": q,
        "student": student,
    }


def _raw_check(eid, ts, raw_input, *, session="100", q=Q, student="S"):
    """A check whose Input is given verbatim (a malformed payload)."""
    return {
        "eid": eid, "selection": "dragNdrop", "action": raw_input,
        "input": raw_input, "feedback": "Incorrect", "ts": ts,
        "session": session, "problem": q, "student": student,
    }


def _seed(conn, rows, *, file_hash=HASH):
    """Raw `events` rows under one import (sha256 ``file_hash``), then derive."""
    import_id = conn.execute(
        "INSERT INTO raw_imports (file_path, file_hash, file_format, row_count) "
        "VALUES ('synthetic.tab', ?, 'datashop_tab', ?)",
        (file_hash, len(rows)),
    ).lastrowid
    for i, r in enumerate(rows):
        conn.execute(
            "INSERT INTO events (event_id, import_id, student_id, session_id, "
            "problem_name, selection, action, input, feedback_text, "
            "feedback_classification, problem_view, timestamp, course_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, 'course')",
            (
                r["eid"], import_id, r["student"], r["session"], r["problem"],
                r["selection"], r["action"], r["input"], r["feedback"],
                str(i + 1), r["ts"],
            ),
        )
    conn.commit()
    derive_events(conn)
    return import_id


def _attempts(conn, q=Q):
    return conn.execute(
        "SELECT attempt_id, attempt_index, prior_attempt_id, qc_flags_json, "
        "readiness FROM dnd_attempts WHERE question_id = ? ORDER BY attempt_index",
        (q,),
    ).fetchall()


def _configs(conn, attempt_id):
    return [
        (c["seq"], tuple(json.loads(c["s_json"])), c["is_check"],
         c["matches_input"], c["anchor_cause"], c["seeded"])
        for c in conn.execute(
            "SELECT seq, s_json, is_check, matches_input, anchor_cause, seeded "
            "FROM dnd_configurations WHERE attempt_id = ? ORDER BY seq",
            (attempt_id,),
        )
    ]


def _checks(conn, attempt_id):
    return [c for c in _configs(conn, attempt_id) if c[2] == 1]


def _flags(row):
    return set(json.loads(row["qc_flags_json"]))


def _latest_config(conn, stage):
    row = conn.execute(
        "SELECT config_json FROM pipeline_runs WHERE service = 'dnd' AND stage = ? "
        "ORDER BY run_id DESC LIMIT 1",
        (stage,),
    ).fetchone()
    return json.loads(row["config_json"])


def _export(conn, name):
    buf = io.StringIO()
    export.export_dataset(conn, name, buf)
    rows = list(csv.DictReader(io.StringIO(buf.getvalue())))
    header = buf.getvalue().splitlines()[0].split(",")
    return header, rows


# The reference trace: the key's first two cards placed and checked in one
# sitting; a new session twenty minutes later (the platform restores the
# board (1, 1, 0, 0) without logging a move), the third card placed, and the
# key checked.
#   attempt 1 (session 100): m1 drag1→drop1 (1,0,0,0); m2 drag2→drop1
#     (1,1,0,0); c1 {drop1: [1, 2]} — eligible, agrees.
#   attempt 2 (session 200): m3 drag3→drop2 — from s_0 (0,0,2,0), seeded
#     (1,1,2,0); c2 {drop1: [1, 2], drop2: [3]} = (1,1,2,0).
TWO_ATTEMPTS = [
    _move("m1", _ts(6, 10, 0, 0), 1, 1),
    _move("m2", _ts(6, 10, 0, 5), 2, 1),
    _check("c1", _ts(6, 10, 0, 10), {"1": [1, 2]}),
    _move("m3", _ts(6, 10, 20, 0), 3, 2, session="200"),
    _check("c2", _ts(6, 10, 20, 5), {"1": [1, 2], "2": [3]}, feedback="Correct",
           session="200"),
]


@pytest.fixture
def two_attempts(db_conn, four_shapes_board):
    four_shapes_board(db_conn)
    _seed(db_conn, TWO_ATTEMPTS)
    return db_conn


@pytest.fixture
def file_db(tmp_path, four_shapes_board):
    """A file database holding the reference trace (for the CLI and the
    harness, which clone a path)."""
    path = tmp_path / "resumed.sqlite"
    conn = db.connect(path)
    db.bootstrap(conn)
    four_shapes_board(conn)
    _seed(conn, TWO_ATTEMPTS)
    conn.close()
    return path


# ---------------------------------------------------------------------------
# 1. Seeding off (the primary): every check of a resumed attempt is stored
#    resumed_attempt; readiness is what it was
# ---------------------------------------------------------------------------
class TestResumedAttemptCause:
    def test_second_attempt_is_resumed_and_its_check_never_compared(self, two_attempts):
        """Attempt 2's prior is attempt 1; c2 is stored not comparable with
        cause resumed_attempt and a NULL comparison; attempt 1 is untouched
        (no prior; c1 compared, agreeing; replay_ok; ready). The shadow
        comparison at c2 — (0,0,2,0) replayed from s_0 against the logged
        (1,1,2,0) — disagrees, so attempt 2 carries check_mismatch and reads
        limited, exactly as it did before the cause existed."""
        result = reconstruct_all(two_attempts)
        a1, a2 = _attempts(two_attempts)
        assert a1["prior_attempt_id"] is None
        assert a2["prior_attempt_id"] == a1["attempt_id"]
        assert _checks(two_attempts, a1["attempt_id"]) == [(3, (1, 1, 0, 0), 1, 1, None, None)]
        assert _checks(two_attempts, a2["attempt_id"]) == [
            (2, (0, 0, 2, 0), 1, None, "resumed_attempt", None)
        ]
        assert (_flags(a1), a1["readiness"]) == ({"replay_ok"}, "ready")
        assert (_flags(a2), a2["readiness"]) == ({"check_mismatch"}, "limited")
        # Every configuration's seeded marker is NULL: the pass did not seed.
        assert all(c[5] is None for aid in (a1["attempt_id"], a2["attempt_id"])
                   for c in _configs(two_attempts, aid))
        assert (result.attempts, result.resumed_attempts, result.seeded_attempts) == (2, 1, 0)
        # The stored mismatch count is over eligible anchors: none here.
        assert (result.mismatches, result.non_eligible) == (0, 1)
        assert (result.resumed_attempt_checks, result.no_move_logging_checks) == (1, 0)
        config = _latest_config(two_attempts, "reconstruct")
        assert config["stage_version"] == 5 == reconstruct_mod.STAGE_VERSION
        assert config["seed_resumed_attempts"] is False
        assert config["regime_set_sha256"] is None

    def test_shadow_agreement_keeps_a_resumed_attempt_ready(self, db_conn, four_shapes_board):
        """Ruling R2a: no attempt is demoted for resumption alone. Attempt 2
        takes both cards back (from s_0 each takeback is a no-op: the card
        is already in the tray) and checks an empty board, whose payload
        decodes to (0,0,0,0) and agrees with the s_0 replay. Its only check
        is stored resumed_attempt, yet the shadow comparison agrees, so the
        attempt carries replay_ok (with the informational noop_move) and
        reads ready — as before."""
        four_shapes_board(db_conn)
        _seed(db_conn, TWO_ATTEMPTS[:3] + [
            _move("t1", _ts(6, 10, 20, 0), 1, 0, session="200"),
            _move("t2", _ts(6, 10, 20, 5), 2, 0, session="200"),
            _check("c2", _ts(6, 10, 20, 10), {}, session="200"),
        ])
        reconstruct_all(db_conn)
        a1, a2 = _attempts(db_conn)
        assert _checks(db_conn, a2["attempt_id"]) == [
            (3, (0, 0, 0, 0), 1, None, "resumed_attempt", None)
        ]
        assert (_flags(a2), a2["readiness"]) == ({"replay_ok", "noop_move"}, "ready")
        assert (_flags(a1), a1["readiness"]) == ({"replay_ok"}, "ready")

    def test_decode_cause_flags_still_fire_behind_a_structural_cause(self, db_conn, four_shapes_board):
        """Precedence stores resumed_attempt on a resumed attempt's malformed
        check, but the decode outcome still feeds the flags: parse_error,
        limited — what the attempt read before."""
        four_shapes_board(db_conn)
        _seed(db_conn, TWO_ATTEMPTS[:3] + [
            _raw_check("c2", _ts(6, 10, 20, 5), "", session="200"),
        ])
        result = reconstruct_all(db_conn)
        _, a2 = _attempts(db_conn)
        assert _checks(db_conn, a2["attempt_id"]) == [
            (1, (0, 0, 0, 0), 1, None, "resumed_attempt", None)
        ]
        assert (_flags(a2), a2["readiness"]) == ({"parse_error"}, "limited")
        assert (result.resumed_attempt_checks, result.non_eligible) == (1, 1)


# ---------------------------------------------------------------------------
# 2–4. Seeding on (knob 10)
# ---------------------------------------------------------------------------
class TestSeededReplay:
    def test_resumed_attempt_starts_at_the_prior_attempts_last_checked_configuration(self, two_attempts):
        """Attempt 2's initial state is attempt 1's last decodable checked
        configuration (1,1,0,0); m3 takes it to (1,1,2,0); c2 agrees and is
        compared (no cause); seeded = 1 on every configuration of attempt 2;
        attempt 1 is untouched (its states and flags as before; seeded = 0
        on its rows, the pass having seeded). Attempt 2 now reads ready —
        the readiness movement knob 10's Δ ready-share cell reports."""
        result = reconstruct_all(two_attempts, seed_resumed_attempts=True)
        a1, a2 = _attempts(two_attempts)
        assert a2["prior_attempt_id"] == a1["attempt_id"]
        assert _configs(two_attempts, a1["attempt_id"]) == [
            (0, (0, 0, 0, 0), 0, None, None, 0),
            (1, (1, 0, 0, 0), 0, None, None, 0),
            (2, (1, 1, 0, 0), 0, None, None, 0),
            (3, (1, 1, 0, 0), 1, 1, None, 0),
        ]
        assert _configs(two_attempts, a2["attempt_id"]) == [
            (0, (1, 1, 0, 0), 0, None, None, 1),
            (1, (1, 1, 2, 0), 0, None, None, 1),
            (2, (1, 1, 2, 0), 1, 1, None, 1),
        ]
        assert (_flags(a1), a1["readiness"]) == ({"replay_ok"}, "ready")
        assert (_flags(a2), a2["readiness"]) == ({"replay_ok"}, "ready")
        assert (result.resumed_attempts, result.seeded_attempts) == (1, 1)
        assert (result.resumed_attempt_checks, result.non_eligible, result.mismatches) == (0, 0, 0)
        assert _latest_config(two_attempts, "reconstruct")["seed_resumed_attempts"] is True

    def test_prior_attempt_without_a_decodable_check_falls_back_to_s0(self, db_conn, four_shapes_board):
        """Attempt 1's only check is malformed (empty Input), so attempt 2
        seeds s_0 with seeded = 0; its check is then compared from s_0
        (resumed_attempt is not applied under seeding): (0,0,2,0) against
        (1,1,2,0) is a stored mismatch, check_mismatch, limited."""
        four_shapes_board(db_conn)
        _seed(db_conn, [
            _move("m1", _ts(6, 10, 0, 0), 1, 1),
            _move("m2", _ts(6, 10, 0, 5), 2, 1),
            _raw_check("c1", _ts(6, 10, 0, 10), ""),
            _move("m3", _ts(6, 10, 20, 0), 3, 2, session="200"),
            _check("c2", _ts(6, 10, 20, 5), {"1": [1, 2], "2": [3]}, session="200"),
        ])
        result = reconstruct_all(db_conn, seed_resumed_attempts=True)
        a1, a2 = _attempts(db_conn)
        assert a2["prior_attempt_id"] == a1["attempt_id"]
        assert _configs(db_conn, a2["attempt_id"]) == [
            (0, (0, 0, 0, 0), 0, None, None, 0),
            (1, (0, 0, 2, 0), 0, None, None, 0),
            (2, (0, 0, 2, 0), 1, 0, None, 0),
        ]
        assert (_flags(a1), a1["readiness"]) == ({"parse_error"}, "limited")
        assert (_flags(a2), a2["readiness"]) == ({"check_mismatch"}, "limited")
        assert (result.resumed_attempts, result.seeded_attempts, result.mismatches) == (1, 0, 1)
        assert result.resumed_attempt_checks == 0

    def test_seed_is_the_last_decodable_check_not_the_last_state(self, db_conn, four_shapes_board):
        """The seed is the prior attempt's last check whose payload decodes
        (ruling R4a): not its last replayed state, not a later malformed
        check, and not a check with a decode cause. (a) Attempt 1 places
        card 1, checks (1,0,0,0), then places card 2 without checking: the
        last state is (1,1,0,0) but the seed is (1,0,0,0), so attempt 2's
        drag3→drop2 reaches (1,0,2,0) and its check {drop1: [1], drop2: [3]}
        agrees (from (1,1,0,0) it would disagree). (b) A malformed check after
        the decodable one is skipped: the seed is still (1,1,0,0). (c) A prior
        whose only check names an unknown card has no decodable check: s_0,
        seeded = 0, and the check compared from s_0 disagrees."""
        four_shapes_board(db_conn)
        _seed(db_conn, [
            _move("m1", _ts(6, 10, 0, 0), 1, 1),
            _check("c1", _ts(6, 10, 0, 5), {"1": [1]}),
            _move("m2", _ts(6, 10, 0, 10), 2, 1),
            _move("m3", _ts(6, 10, 20, 0), 3, 2, session="200"),
            _check("c2", _ts(6, 10, 20, 5), {"1": [1], "2": [3]}, session="200"),
        ], file_hash="a" * 64)
        _seed(db_conn, [
            _move("b1", _ts(6, 10, 0, 0), 1, 1, student="B"),
            _move("b2", _ts(6, 10, 0, 5), 2, 1, student="B"),
            _check("b3", _ts(6, 10, 0, 10), {"1": [1, 2]}, student="B"),
            _raw_check("b4", _ts(6, 10, 0, 15), "", student="B"),
            _move("b5", _ts(6, 10, 20, 0), 3, 2, session="200", student="B"),
            _check("b6", _ts(6, 10, 20, 5), {"1": [1, 2], "2": [3]}, session="200", student="B"),
        ], file_hash="b" * 64)
        _seed(db_conn, [
            _move("k1", _ts(6, 10, 0, 0), 1, 1, student="C"),
            _check("k2", _ts(6, 10, 0, 5), {"1": [99]}, student="C"),
            _move("k3", _ts(6, 10, 20, 0), 3, 2, session="200", student="C"),
            _check("k4", _ts(6, 10, 20, 5), {"1": [1], "2": [3]}, session="200", student="C"),
        ], file_hash="c" * 64)
        result = reconstruct_all(db_conn, seed_resumed_attempts=True)
        second = {
            r["student_id"]: r
            for r in db_conn.execute("SELECT * FROM dnd_attempts WHERE attempt_index = 2")
        }
        a = second["S"]["attempt_id"]
        assert _configs(db_conn, a)[0] == (0, (1, 0, 0, 0), 0, None, None, 1)
        assert _checks(db_conn, a) == [(2, (1, 0, 2, 0), 1, 1, None, 1)]
        b = second["B"]["attempt_id"]
        assert _configs(db_conn, b)[0] == (0, (1, 1, 0, 0), 0, None, None, 1)
        assert _checks(db_conn, b) == [(2, (1, 1, 2, 0), 1, 1, None, 1)]
        c = second["C"]["attempt_id"]
        assert _configs(db_conn, c)[0] == (0, (0, 0, 0, 0), 0, None, None, 0)
        assert _checks(db_conn, c) == [(2, (0, 0, 2, 0), 1, 0, None, 0)]
        assert (result.resumed_attempts, result.seeded_attempts) == (3, 2)

    def test_three_attempt_chain_seeds_from_the_immediate_prior(self, db_conn, four_shapes_board):
        """Sessions 100, 200, 300: attempt 1 places card 1 and checks
        (1,0,0,0); attempt 2 places card 2 and checks (1,1,0,0); attempt 3
        places card 3 and checks (1,1,2,0). Attempt 3's prior is attempt 2,
        and its seed is attempt 2's last checked configuration (1,1,0,0) —
        from attempt 1's (1,0,0,0) the replay would reach (1,0,2,0) and
        disagree. Seeding off, the chain of prior ids is 1 ← 2 ← 3 and every
        check of attempts 2 and 3 is resumed_attempt."""
        four_shapes_board(db_conn)
        rows = [
            _move("m1", _ts(6, 10, 0, 0), 1, 1),
            _check("c1", _ts(6, 10, 0, 5), {"1": [1]}),
            _move("m2", _ts(6, 10, 20, 0), 2, 1, session="200"),
            _check("c2", _ts(6, 10, 20, 5), {"1": [1, 2]}, session="200"),
            _move("m3", _ts(6, 10, 40, 0), 3, 2, session="300"),
            _check("c3", _ts(6, 10, 40, 5), {"1": [1, 2], "2": [3]}, session="300"),
        ]
        _seed(db_conn, rows)
        result = reconstruct_all(db_conn)
        a1, a2, a3 = _attempts(db_conn)
        assert (a1["prior_attempt_id"], a2["prior_attempt_id"], a3["prior_attempt_id"]) == (
            None, a1["attempt_id"], a2["attempt_id"],
        )
        assert [c[4] for c in _checks(db_conn, a2["attempt_id"])] == ["resumed_attempt"]
        assert [c[4] for c in _checks(db_conn, a3["attempt_id"])] == ["resumed_attempt"]
        assert (result.resumed_attempts, result.resumed_attempt_checks) == (2, 2)
        result = reconstruct_all(db_conn, seed_resumed_attempts=True)
        a1, a2, a3 = _attempts(db_conn)
        assert _configs(db_conn, a3["attempt_id"])[0] == (0, (1, 1, 0, 0), 0, None, None, 1)
        assert _checks(db_conn, a3["attempt_id"]) == [(2, (1, 1, 2, 0), 1, 1, None, 1)]
        assert _checks(db_conn, a2["attempt_id"]) == [(2, (1, 1, 0, 0), 1, 1, None, 1)]
        assert (result.resumed_attempts, result.seeded_attempts) == (2, 2)
        assert [a["readiness"] for a in (a1, a2, a3)] == ["ready"] * 3


# ---------------------------------------------------------------------------
# 5. The logging regime: the cause, the precedence, the tripwire, the import
# ---------------------------------------------------------------------------
class TestLoggingRegime:
    def test_check_before_the_date_is_no_move_logging_and_precedes_resumed_attempt(
        self, db_conn, four_shapes_board
    ):
        """The import logs moves from 2026-02-07. Student A, on the 6th:
        two sessions, a check in each and no move row — both checks are
        no_move_logging, the second one by precedence although its attempt
        is resumed. Student B, on the 7th (the date itself): a placement
        and an agreeing check — eligible, ready. Nothing contradicts the
        row (no move before the date), so the tripwire is silent."""
        four_shapes_board(db_conn)
        _seed(db_conn, [
            _check("a1", _ts(6, 10, 0, 10), {"1": [1]}, student="A"),
            _check("a2", _ts(6, 11, 0, 10), {"1": [1]}, student="A", session="200"),
            _move("b1", _ts(7, 0, 0, 1), 1, 1, student="B"),
            _check("b2", _ts(7, 0, 0, 5), {"1": [1]}, student="B"),
        ])
        assert logging_regimes.add_regime(db_conn, HASH, REGIME_DATE) == "added"
        result = reconstruct_all(db_conn)
        a1, a2 = db_conn.execute(
            "SELECT * FROM dnd_attempts WHERE student_id = 'A' ORDER BY attempt_index"
        ).fetchall()
        [b] = db_conn.execute("SELECT * FROM dnd_attempts WHERE student_id = 'B'").fetchall()
        assert _checks(db_conn, a1["attempt_id"]) == [(1, (0, 0, 0, 0), 1, None, "no_move_logging", None)]
        assert a2["prior_attempt_id"] == a1["attempt_id"]
        assert _checks(db_conn, a2["attempt_id"]) == [(1, (0, 0, 0, 0), 1, None, "no_move_logging", None)]
        assert _checks(db_conn, b["attempt_id"]) == [(2, (1, 0, 0, 0), 1, 1, None, None)]
        # The shadow comparison keeps the flags as they were: a check of a
        # bare board against (1,0,0,0) is a mismatch, so limited; B ready.
        assert (_flags(a1), a1["readiness"]) == ({"check_mismatch"}, "limited")
        assert (_flags(a2), a2["readiness"]) == ({"check_mismatch"}, "limited")
        assert (_flags(b), b["readiness"]) == ({"replay_ok"}, "ready")
        assert (result.no_move_logging_checks, result.resumed_attempt_checks) == (2, 0)
        assert (result.non_eligible, result.mismatches, result.resumed_attempts) == (2, 0, 1)
        digest = logging_regimes.regime_set_sha256(db_conn)
        assert digest is not None and len(digest) == 64
        assert _latest_config(db_conn, "reconstruct")["regime_set_sha256"] == digest

    def test_no_move_logging_precedes_a_decode_cause_and_keeps_its_flag(self, db_conn, four_shapes_board):
        """Student A's only pre-date check is malformed: stored
        no_move_logging by precedence, while the decode outcome still flags
        parse_error (limited). Her second attempt, on the date, is resumed
        and its check stored resumed_attempt."""
        four_shapes_board(db_conn)
        _seed(db_conn, [
            _raw_check("a1", _ts(6, 10, 0, 10), "", student="A"),
            _move("a2", _ts(7, 0, 0, 1), 1, 1, student="A", session="200"),
            _check("a3", _ts(7, 0, 0, 5), {"1": [1]}, student="A", session="200"),
        ])
        logging_regimes.add_regime(db_conn, HASH, REGIME_DATE)
        result = reconstruct_all(db_conn)
        a1, a2 = _attempts(db_conn)
        assert _checks(db_conn, a1["attempt_id"]) == [(1, (0, 0, 0, 0), 1, None, "no_move_logging", None)]
        assert (_flags(a1), a1["readiness"]) == ({"parse_error"}, "limited")
        assert _checks(db_conn, a2["attempt_id"]) == [(2, (1, 0, 0, 0), 1, None, "resumed_attempt", None)]
        assert (_flags(a2), a2["readiness"]) == ({"replay_ok"}, "ready")
        assert (result.no_move_logging_checks, result.resumed_attempt_checks) == (1, 1)

    @pytest.mark.parametrize("row", [
        pytest.param(_move("t1", _ts(6, 10, 0, 0), 1, 0), id="takeback"),
        pytest.param({
            "eid": "r1", "selection": "dragNdrop-reset", "action": "reset", "input": "",
            "feedback": "", "ts": _ts(6, 10, 0, 0), "session": "100", "problem": Q, "student": "S",
        }, id="reset"),
    ])
    def test_tripwire_counts_a_takeback_or_reset_before_the_date(self, db_conn, four_shapes_board, row):
        four_shapes_board(db_conn)
        _seed(db_conn, [row, _check("c1", _ts(7, 0, 0, 5), {}, session="200")])
        logging_regimes.add_regime(db_conn, HASH, REGIME_DATE)
        with pytest.raises(logging_regimes.RegimeError, match=r"1 move/takeback/reset row\(s\) in 1 import\(s\)"):
            reconstruct_all(db_conn)

    def test_no_move_logging_still_applies_under_seeding(self, db_conn, four_shapes_board):
        """Under knob 10 the resumed_attempt cause is not applied but
        no_move_logging is: student A's second check stays no_move_logging."""
        four_shapes_board(db_conn)
        _seed(db_conn, [
            _check("a1", _ts(6, 10, 0, 10), {"1": [1]}, student="A"),
            _check("a2", _ts(6, 11, 0, 10), {"1": [1]}, student="A", session="200"),
        ])
        logging_regimes.add_regime(db_conn, HASH, REGIME_DATE)
        result = reconstruct_all(db_conn, seed_resumed_attempts=True)
        a1, a2 = _attempts(db_conn)
        assert _checks(db_conn, a2["attempt_id"]) == [(1, (1, 0, 0, 0), 1, None, "no_move_logging", 1)]
        assert result.no_move_logging_checks == 2 and result.resumed_attempt_checks == 0

    def test_tripwire_refuses_a_move_before_the_date(self, db_conn, four_shapes_board):
        """A placement on the 6th under a row dated the 7th contradicts the
        row: the pass refuses with the count of such rows and no timestamp,
        records no reconstruct run and writes no attempt."""
        four_shapes_board(db_conn)
        _seed(db_conn, TWO_ATTEMPTS)
        logging_regimes.add_regime(db_conn, HASH, REGIME_DATE)
        with pytest.raises(logging_regimes.RegimeError) as exc:
            reconstruct_all(db_conn)
        message = str(exc.value)
        assert "3 move/takeback/reset row(s) in 1 import(s)" in message
        assert "2026-02" not in message and HASH[:8] not in message
        assert db_conn.execute(
            "SELECT count(*) AS n FROM pipeline_runs WHERE stage = 'reconstruct'"
        ).fetchone()["n"] == 0
        assert db_conn.execute("SELECT count(*) AS n FROM dnd_attempts").fetchone()["n"] == 0
        # The CLI reports the refusal and exits 1.
        # (exercised on a file database below)

    def test_import_without_a_row_is_untouched(self, two_attempts):
        """A regime row keyed on another export changes nothing here."""
        _seed(two_attempts, [_move("z1", _ts(7, 12, 0, 0), 1, 1, student="Z")], file_hash="e" * 64)
        logging_regimes.add_regime(two_attempts, "e" * 64, REGIME_DATE)
        result = reconstruct_all(two_attempts)
        assert (result.no_move_logging_checks, result.resumed_attempt_checks) == (0, 1)

    def test_import_refuses_an_unknown_sha256_and_a_bad_row(self, two_attempts, tmp_path):
        """A sha256 no import carries is refused with the count of known
        imports and never the hash; the table stays empty. A malformed key
        or date is refused too."""
        f = tmp_path / "regimes.csv"
        f.write_text(
            "# test regimes\n"
            "file_sha256,pseudonym,move_logging_from,reason,source_memo,recorded_on\n"
            f"{HASH},Y00-B03-P11,{REGIME_DATE},known,memo.md,2026-10-06\n"
            f"{'0' * 64},Y00-B03-P13,{REGIME_DATE},unknown,memo.md,2026-10-06\n",
            encoding="utf-8",
        )
        with pytest.raises(logging_regimes.RegimeError) as exc:
            logging_regimes.import_regimes(two_attempts, f)
        assert "1 known import(s)" in str(exc.value)
        assert "0000" not in str(exc.value)
        assert logging_regimes.list_regimes(two_attempts) == []
        with pytest.raises(logging_regimes.RegimeError, match="ISO calendar date"):
            logging_regimes.add_regime(two_attempts, HASH, "13 Oct 2025")
        with pytest.raises(logging_regimes.RegimeError, match="real calendar date"):
            logging_regimes.add_regime(two_attempts, HASH, "2026-02-30")
        with pytest.raises(logging_regimes.RegimeError, match="64-digit"):
            logging_regimes.add_regime(two_attempts, "abc", REGIME_DATE)
        # A known row imports; a second import is idempotent; a different
        # date for the same export is refused until the row is removed.
        f.write_text(
            "file_sha256,pseudonym,move_logging_from,reason,source_memo,recorded_on\n"
            f"{HASH},Y00-B03-P11,{REGIME_DATE},known,memo.md,2026-10-06\n",
            encoding="utf-8",
        )
        first = logging_regimes.import_regimes(two_attempts, f)
        assert (first.added, first.unchanged) == (1, 0)
        again = logging_regimes.import_regimes(two_attempts, f)
        assert (again.added, again.unchanged, again.set_sha256) == (0, 1, first.set_sha256)
        [row] = again.rows
        assert (row.file_sha256, row.move_logging_from, row.reason, row.source_memo,
                row.recorded_on, row.imported_from) == (
            HASH, REGIME_DATE, "known", "memo.md", "2026-10-06", "regimes.csv")
        with pytest.raises(logging_regimes.RegimeError, match="already recorded"):
            logging_regimes.add_regime(two_attempts, HASH, "2026-02-08")
        assert logging_regimes.remove_regime(two_attempts, HASH) is True
        assert logging_regimes.regime_set_sha256(two_attempts) is None

    def test_committed_regime_file_carries_the_memo_dates(self):
        """The three Fall 2025 rows, keyed by the export sha256 of the
        committed inventory, dated per the diagnosis memo (M-R 1); no
        Spring 2026 row; no path, no surname."""
        path = Path(logging_regimes.__file__).parent / "logging_regimes" / "ula-proteus.csv"
        rows = logging_regimes.read_regime_file(path)
        assert [(r["file_sha256"][:12], r["pseudonym"], r["move_logging_from"]) for r in rows] == [
            ("893253ba9bde", "Y00-B03-P11", "2025-10-13"),
            ("8d6ce566cc3d", "Y00-B03-P13", "2025-10-13"),
            ("c2827903f85b", "Y00-B03-P21", "2025-10-14"),
        ]
        assert all(len(r["file_sha256"]) == 64 for r in rows)
        assert all(r["source_memo"] == "paper1-replay-mismatch-diagnosis-2026-10-02.md" for r in rows)
        text = path.read_text(encoding="utf-8")
        assert "/" not in "".join(line for line in text.splitlines() if not line.startswith("#"))

    def test_regime_hash_in_reconstruct_config_and_manifest_only(self, two_attempts):
        """Ruling R6a: reconstruct records the regime-set hash beside the
        alias-set hash; score and analyze record the alias hash only; the
        manifest's dnd section carries the rows under logging_regimes
        beside question_aliases, at manifest version 2."""
        _seed(two_attempts, [_move("z1", _ts(7, 12, 0, 0), 1, 1, student="Z")], file_hash="e" * 64)
        logging_regimes.add_regime(two_attempts, "e" * 64, REGIME_DATE, reason="r", source_memo="m.md")
        reconstruct_all(two_attempts)
        score_checks(two_attempts)
        process.analyze_all(two_attempts)
        digest = logging_regimes.regime_set_sha256(two_attempts)
        assert _latest_config(two_attempts, "reconstruct")["regime_set_sha256"] == digest
        assert "regime_set_sha256" not in _latest_config(two_attempts, "score")
        assert "regime_set_sha256" not in _latest_config(two_attempts, "analyze")
        body = manifest.build_manifest(two_attempts)["body"]
        assert body["manifest_version"] == 2
        section = body["services"]["dnd"]
        # account_roles joined the section with dnd/0016 (2026-10-19, the
        # staff-account filter, E-02 PR 1): counts and hashes only, no rows.
        assert set(section) == {"question_aliases", "logging_regimes", "account_roles"}
        assert section["logging_regimes"]["count"] == 1
        assert section["logging_regimes"]["set_sha256"] == digest
        [row] = section["logging_regimes"]["rows"]
        assert (row["file_sha256"], row["move_logging_from"], row["reason"], row["source_memo"]) == (
            "e" * 64, REGIME_DATE, "r", "m.md")
        assert "pseudonym" not in row


# ---------------------------------------------------------------------------
# 6. Alias interplay: attempts under the retired and the successor
#    identifier are not each other's prior
# ---------------------------------------------------------------------------
class TestAliasInterplay:
    def test_attempts_under_two_logged_identifiers_are_independent(self, db_conn, four_shapes_board):
        """Student S places card 1 and checks under Q_OLD (session 100), then
        under Q (session 200), with Q_OLD → Q registered: two attempt
        sequences, each with one first attempt (no prior), each check
        eligible and agreeing under the shared board."""
        four_shapes_board(db_conn)
        _seed(db_conn, [
            _move("o1", _ts(6, 10, 0, 0), 1, 1, q=Q_OLD),
            _check("o2", _ts(6, 10, 0, 5), {"1": [1]}, q=Q_OLD),
            _move("n1", _ts(6, 10, 20, 0), 1, 1, session="200"),
            _check("n2", _ts(6, 10, 20, 5), {"1": [1]}, session="200"),
        ])
        aliases.add_alias(db_conn, Q_OLD, Q)
        result = reconstruct_all(db_conn)
        [old] = _attempts(db_conn, Q_OLD)
        [new] = _attempts(db_conn, Q)
        assert old["prior_attempt_id"] is None and new["prior_attempt_id"] is None
        assert _checks(db_conn, old["attempt_id"]) == [(2, (1, 0, 0, 0), 1, 1, None, None)]
        assert _checks(db_conn, new["attempt_id"]) == [(2, (1, 0, 0, 0), 1, 1, None, None)]
        assert (result.resumed_attempts, result.resumed_attempt_checks) == (0, 0)
        assert {a["readiness"] for a in (old, new)} == {"ready"}
        # Under seeding neither attempt is resumed, so neither is seeded.
        result = reconstruct_all(db_conn, seed_resumed_attempts=True)
        assert (result.resumed_attempts, result.seeded_attempts) == (0, 0)
        [old] = _attempts(db_conn, Q_OLD)
        [new] = _attempts(db_conn, Q)
        assert {c[5] for aid in (old["attempt_id"], new["attempt_id"]) for c in _configs(db_conn, aid)} == {0}


# ---------------------------------------------------------------------------
# 7. Pins: exports, the CLI line, the harness row and seeded_strata.csv, the
#    inferred-key identity
# ---------------------------------------------------------------------------
class TestExportsAndPins:
    def test_export_columns_on_the_reference_trace(self, two_attempts):
        reconstruct_all(two_attempts)
        score_checks(two_attempts)
        process.analyze_all(two_attempts)
        for name in ("attempt_metrics", "modeling_table"):
            header, rows = _export(two_attempts, name)
            assert header[-4:] == ["canonical_question_id", "resumed", "prior_attempt_id", "import_sha256"], name
            a1, a2 = sorted(rows, key=lambda r: int(r["attempt_index"]))
            assert (a1["resumed"], a1["prior_attempt_id"]) == ("0", "")
            assert (a2["resumed"], a2["prior_attempt_id"]) == ("1", a1["attempt_id"])
        header, rows = _export(two_attempts, "checks")
        assert header[-3:] == ["canonical_question_id", "seeded", "import_sha256"]
        by_event = {r["event_id"]: r for r in rows}
        assert (by_event["c1"]["eligible"], by_event["c1"]["anchor_cause"], by_event["c1"]["seeded"]) == ("1", "", "")
        assert (by_event["c2"]["eligible"], by_event["c2"]["anchor_cause"], by_event["c2"]["matches_input"]) == (
            "0", "resumed_attempt", "")
        header, rows = _export(two_attempts, "configurations")
        assert header[-3:] == ["canonical_question_id", "seeded", "import_sha256"]
        assert {r["seeded"] for r in rows} == {""}
        # Seeded: the marker rides every configuration and check row.
        reconstruct_all(two_attempts, seed_resumed_attempts=True)
        _, rows = _export(two_attempts, "configurations")
        assert sorted({(r["attempt_index"], r["seeded"]) for r in rows}) == [("1", "0"), ("2", "1")]
        _, rows = _export(two_attempts, "checks")
        by_event = {r["event_id"]: r for r in rows}
        assert (by_event["c2"]["eligible"], by_event["c2"]["matches_input"], by_event["c2"]["seeded"]) == ("1", "1", "1")

    def test_golden_fixture_has_no_resumed_attempt(self, db_conn, golden_dnd_tab, four_shapes_board):
        """Neither golden fixture holds a resumed attempt (one attempt per
        student): the new columns read 0 / empty on every row and the cause
        vocabulary's two new names never appear."""
        core_ingest.ingest_file(db_conn, golden_dnd_tab)
        derive_events(db_conn)
        four_shapes_board(db_conn)
        result = reconstruct_all(db_conn)
        assert (result.attempts, result.resumed_attempts, result.resumed_attempt_checks,
                result.no_move_logging_checks) == (2, 0, 0, 0)
        _, rows = _export(db_conn, "attempt_metrics")
        assert all((r["resumed"], r["prior_attempt_id"]) == ("0", "") for r in rows)
        _, rows = _export(db_conn, "checks")
        assert all(r["seeded"] == "" for r in rows)
        assert {r["anchor_cause"] for r in rows} == {"", "malformed", "unknown_card"}

    def test_cli_reports_the_new_counts_and_the_seeding_flag(self, file_db, capsys):
        assert cli.main(["--db", str(file_db), "dnd", "reconstruct"]) == 0
        out = capsys.readouterr().out
        assert "resumed:         1  (prior_attempt_id set; every check not already no_move_logging stored resumed_attempt" in out
        assert "no move logging: 0" in out
        assert "resumed attempt: 1" in out
        assert "not comparable:  1" in out
        assert cli.main(["--db", str(file_db), "dnd", "reconstruct", "--seed-resumed"]) == 0
        out = capsys.readouterr().out
        assert "seeded 1 of 1 at the prior attempt's last decodable checked configuration, 0 from s_0" in out
        assert "resumed attempt: 0" in out
        # The regime commands: list (empty), import of the committed file is
        # refused here (none of its exports is imported), remove of a
        # missing row exits 1, a contradicted row refuses the reconstruct.
        assert cli.main(["--db", str(file_db), "dnd", "regime", "list"]) == 0
        assert "Logging regimes: 0" in capsys.readouterr().out
        committed = Path(logging_regimes.__file__).parent / "logging_regimes" / "ula-proteus.csv"
        assert cli.main(["--db", str(file_db), "dnd", "regime", "import", str(committed)]) == 1
        err = capsys.readouterr().err
        assert "1 known import(s)" in err and "893253ba" not in err
        assert cli.main(["--db", str(file_db), "dnd", "regime", "remove", HASH]) == 1
        capsys.readouterr()
        conn = db.connect(file_db)
        logging_regimes.add_regime(conn, HASH, REGIME_DATE)
        conn.close()
        assert cli.main(["--db", str(file_db), "dnd", "regime", "list"]) == 0
        assert f"{HASH[:12]}… logs moves from {REGIME_DATE}" in capsys.readouterr().out
        assert cli.main(["--db", str(file_db), "dnd", "reconstruct"]) == 1
        assert "logging regime contradicted" in capsys.readouterr().err
        assert cli.main(["--db", str(file_db), "dnd", "regime", "remove", HASH]) == 0
        assert "Regime removed" in capsys.readouterr().out
        # A file keyed on an imported export, dated so that nothing precedes
        # it: the import succeeds, lists, and the reconstruct runs.
        regimes = file_db.parent / "regimes.csv"
        regimes.write_text(
            "file_sha256,pseudonym,move_logging_from,reason,source_memo,recorded_on\n"
            f"{HASH},Y00-B03-P11,2026-02-06,test,memo.md,2026-10-06\n",
            encoding="utf-8",
        )
        assert cli.main(["--db", str(file_db), "dnd", "regime", "import", str(regimes)]) == 0
        out = capsys.readouterr().out
        assert "added:     1" in out and f"{HASH[:12]}… logs moves from 2026-02-06" in out
        assert cli.main(["--db", str(file_db), "dnd", "regime", "list"]) == 0
        assert "Logging regimes: 1" in capsys.readouterr().out
        assert cli.main(["--db", str(file_db), "dnd", "reconstruct"]) == 0
        assert "no move logging: 0" in capsys.readouterr().out

    def test_knob_10_row_and_seeded_strata_on_the_reference_trace(self, file_db, tmp_path):
        """Primary: c1 the only eligible anchor (1 of 1 agree; c2 stored
        resumed_attempt); attempts ready 1 of 2. Knob 10: c2 compared from
        the seeded replay and agreeing (2 of 2), attempt 2 ready — Δ
        attempts +1, Δ ready share +0.5, Δ agreement 0 pp; the strata file
        counts the seeded check beside the non-resumed one."""
        conn = db.connect(file_db)
        reconstruct_all(conn)
        score_checks(conn)
        process.analyze_all(conn)
        conn.commit()
        conn.close()
        knob = sensitivity.knob_by_key("k10-seeded-replay")
        out = tmp_path / "out"
        primary, rows = sensitivity.run_all(file_db, out, [knob])
        [d] = rows
        assert (primary.n_reconstructed, primary.n_ready, primary.n_anchors, primary.n_agree) == (2, 1, 1, 1)
        assert (d.alternative.n_reconstructed, d.alternative.n_ready, d.alternative.n_anchors,
                d.alternative.n_agree) == (2, 2, 2, 2)
        assert (d.delta_attempts, d.delta_ready_share, d.delta_agreement_pp) == (1, 0.5, 0.0)
        assert d.knob.group == sensitivity.RECONSTRUCTION and d.knob.number == 10
        config = json.loads((out / knob.key / "config.json").read_text())
        assert config["stages_rerun"] == ["reconstruct", "score", "analyze"]
        assert config["knob"]["reconstruct"] == {"seed_resumed_attempts": True}
        assert config["exports"][-1] == "seeded_strata.csv"
        recon = next(r for r in config["pipeline_runs"] if r["stage"] == "reconstruct")
        assert recon["config"]["seed_resumed_attempts"] is True
        strata = list(csv.DictReader((out / knob.key / "seeded_strata.csv").open(encoding="utf-8")))
        assert [r["stratum"] for r in strata] == ["seeded", "resumed_fallback", "non_resumed"]
        assert [(r["checks"], r["agree"], r["disagree"], r["not_comparable"]) for r in strata] == [
            ("1", "1", "0", "0"), ("0", "0", "0", "0"), ("1", "1", "0", "0"),
        ]
        assert set(strata[0]) >= {f"cause_{c}" for c in qc.ANCHOR_CAUSE_ORDER}
        text = (out / "deltas.csv").read_text()
        record = next(csv.DictReader(io.StringIO(text)))
        assert record["alternative"] == knob.label and record["knob"] == "10"
        meta = json.loads((out / "deltas.json").read_text())
        assert meta["freeze"]["d3_2_knob_10_added"] == "2026-10-05"
        # The primary guard: a database whose newest reconstruct run seeded
        # is not the frozen primary.
        conn = db.connect(file_db)
        reconstruct_all(conn, seed_resumed_attempts=True)
        conn.commit()
        conn.close()
        with pytest.raises(sensitivity.SensitivityError, match="seed_resumed_attempts=True"):
            sensitivity.run_configuration(file_db, tmp_path / "out2", sensitivity.PRIMARY)

    def test_primary_guard_checks_the_regime_hash(self, file_db, tmp_path):
        """A regime recorded after the primary run changes the database's
        regime set: the guard refuses, naming the regime hash and nothing
        else."""
        conn = db.connect(file_db)
        reconstruct_all(conn)
        score_checks(conn)
        process.analyze_all(conn)
        _seed(conn, [_move("z1", _ts(7, 12, 0, 0), 1, 1, student="Z")], file_hash="e" * 64)
        logging_regimes.add_regime(conn, "e" * 64, REGIME_DATE)
        conn.commit()
        conn.close()
        with pytest.raises(sensitivity.SensitivityError) as exc:
            sensitivity.run_configuration(file_db, tmp_path / "out", sensitivity.PRIMARY)
        assert "regime_set_sha256 differs" in str(exc.value)
        assert "alias_set_sha256" not in str(exc.value)

    def test_evidence_report_counts_resumption(self, file_db):
        conn = db.connect(file_db)
        try:
            reconstruct_all(conn)
            report = sensitivity.evidence(conn)
            # A regime row dated so that nothing precedes it: counted as an
            # import under a regime, no check excluded.
            logging_regimes.add_regime(conn, HASH, "2026-02-06")
            reconstruct_all(conn)
            with_regime = sensitivity.evidence(conn)
        finally:
            conn.close()
        assert (report.n_attempts, report.n_attempts_resumed) == (2, 1)
        assert (report.n_checks_resumed_attempt, report.n_checks_no_move_logging,
                report.n_imports_with_regime) == (1, 0, 0)
        assert (with_regime.n_checks_no_move_logging, with_regime.n_imports_with_regime) == (0, 1)
        text = sensitivity.render_evidence(report)
        assert "resumed attempts (a prior attempt by the same student" in text
        assert "knob 10 added 2026-10-05" in text

    def test_inferred_key_is_identical_with_seeding_on_and_off(self, two_attempts):
        """Ruling R5a: validate infers keys from Correct check payloads before
        any attempt exists, so its output is byte-identical under either
        reconstruct configuration (the entry's judgment call 5 is moot)."""
        before = validate.validate_all(two_attempts)
        reconstruct_all(two_attempts)
        off = validate.validate_all(two_attempts)
        reconstruct_all(two_attempts, seed_resumed_attempts=True)
        on = validate.validate_all(two_attempts)
        assert before == off == on
        [r] = on
        assert r.key_status == validate.KEY_MATCH
        assert r.inferred_keys == ((1, 1, 2, 0),)
