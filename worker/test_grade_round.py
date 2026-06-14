"""Driver tests for worker/grade_round.py with a fake conn + injected sandbox runner.

No Postgres, no isolate: a tiny async fake records the writes (and ROLLS BACK on a
failed transaction) so we exercise the real round orchestration — claim -> as-of
selection -> score_round -> atomic transactional write, plus failure/idempotency paths.
Run:  python worker/test_grade_round.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import types
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "judge"))

import grade_round  # noqa: E402  (worker/grade_round.py)
from challenge_grader import CaseOutcome  # noqa: E402
from sandbox import IsolateInternalError  # noqa: E402

KST = timezone(timedelta(hours=9))
SCHED = datetime(2026, 6, 15, 9, 0, tzinfo=KST)
SECRET = "test-secret"

CP = "cp1"          # a challenge problem id
SP = "sp1"          # a stepup problem id


def _round(rtype="interim"):
    return {"id": "round-1", "contest_id": "contest-1", "type": rtype,
            "idem_key": "contest-1:2026-06-15:0900", "scheduled_at": SCHED, "attempts": 1}


def _problems(with_stepup=True):
    ps = [{"id": CP, "kind": "challenge", "problem_key": "example_clean",
           "time_limit_ms": 2000, "memory_limit_mb": 1024,
           "scoring_config": {"seed_range": [1, 100000], "round_seeds": 2, "cost_eps": 0}}]
    if with_stepup:
        ps.append({"id": SP, "kind": "stepup", "problem_key": "example_clean",
                   "time_limit_ms": 1000, "memory_limit_mb": 256, "scoring_config": {}})
    return ps


class _Txn:
    """Snapshot/restore so a raise inside `async with conn.transaction()` reverts ALL
    writes — letting tests prove the round's write is atomic (no partial rows)."""
    def __init__(self, conn): self.conn = conn

    async def __aenter__(self):
        c = self.conn
        self._snap = (list(c.case_results), list(c.standings), list(c.notifs), c.round_status)
        return self

    async def __aexit__(self, exc_type, *rest):
        if exc_type is not None:
            c = self.conn
            cr, st, nf, status = self._snap
            c.case_results, c.standings, c.notifs, c.round_status = cr, st, nf, status
        return False


class FakeConn:
    def __init__(self, *, claim=True, problems=None, reps=None, stepup=None,
                 challenge_budget=1000000, done_tag="UPDATE 1"):
        self._claim = claim
        self._problems = problems if problems is not None else _problems()
        self.reps = reps or {}             # problem_id -> [rep rows]  (mutable for re-grade)
        self._stepup = stepup or {}        # problem_id -> [stepup rows]
        self._budget = challenge_budget
        self._done_tag = done_tag          # set "UPDATE 0" to simulate a lost lease
        self.case_results: list[dict] = []
        self.standings: list[dict] = []
        self.notifs: list[tuple[str, dict]] = []   # (type, payload)
        self.round_status = None
        self.failed_attempts = None        # attempts written on a terminal failure
        self.reps_called_with_ts = []

    def transaction(self): return _Txn(self)

    async def fetchrow(self, q, *a):
        if "UPDATE evaluation_rounds" in q and "status='generating'" in q and "RETURNING" in q:
            return _round() if self._claim else None
        if "FROM contests WHERE id" in q:
            return {"challenge_budget": self._budget}
        return None

    async def fetch(self, q, *a):
        if "FROM problems WHERE contest_id" in q:
            return self._problems
        if "DISTINCT ON (user_id)" in q:                  # REPS_SQL
            self.reps_called_with_ts.append(a[1])
            return self.reps.get(a[0], [])
        if "FROM stepup_submissions" in q:                # STEPUP_SQL
            return self._stepup.get(a[0], [])
        return []

    async def execute(self, q, *a):
        if q.startswith("DELETE FROM case_results"):
            self.case_results = []
        elif q.startswith("DELETE FROM standings"):
            self.standings = []
        elif "INSERT INTO case_results" in q:
            keys = ["round_id", "submission_id", "problem_id", "user_id", "seed",
                    "verdict", "raw_cost", "runtime_ms", "case_score", "case_rank"]
            self.case_results.append(dict(zip(keys, a)))
        elif "INSERT INTO standings" in q:
            keys = ["round_id", "contest_id", "user_id", "stepup", "challenge",
                    "total", "rank"]
            self.standings.append(dict(zip(keys, a)))
        elif "INSERT INTO notifications" in q:
            ntype = "contest_ended" if "contest_ended" in q else "round_published"
            self.notifs.append((ntype, json.loads(a[1])))
        elif "status='done'" in q:
            if self._done_tag != "UPDATE 0":
                self.round_status = "done"
            return self._done_tag
        elif "status='failed'" in q:
            self.round_status = "failed"
            if "attempts=$3" in q:                        # terminal failure burns the budget
                self.failed_attempts = a[2]
        return "UPDATE 1"


class FailingInsertConn(FakeConn):
    """Raises on the 2nd standings INSERT to test mid-transaction rollback."""
    def __init__(self, **kw):
        super().__init__(**kw)
        self._st_inserts = 0

    async def execute(self, q, *a):
        if "INSERT INTO standings" in q:
            self._st_inserts += 1
            if self._st_inserts == 2:
                raise RuntimeError("simulated DB write failure")
        return await super().execute(q, *a)


def _rep(uid, cost):
    return {"id": f"sub-{uid}", "user_id": uid, "language_id": "cpp20",
            "source_text": str(cost), "data_bin": None}


def _runner_costs(problem_key, language_id, source, seeds, limits, data_bin=None, gen_params=None):
    cost = float(source)
    return ([CaseOutcome(seed=s, cost=cost, valid=True, verdict="OK", runtime_ms=5)
             for s in seeds], True, "")


def run(coro): return asyncio.run(coro)


def test_happy_path_writes_standings_and_notifies():
    conn = FakeConn(
        reps={CP: [_rep("A", 10), _rep("B", 20)]},
        stepup={SP: [{"user_id": "A", "mission_seed": 101, "best": 50000},
                     {"user_id": "C", "mission_seed": 101, "best": 30000}]},
    )
    out = run(grade_round.evaluate_round(conn, "round-1", grade_rep=_runner_costs, secret=SECRET))
    assert out == "done" and conn.round_status == "done"
    assert len(conn.case_results) == 4                # 2 users x 2 seeds
    st = {s["user_id"]: s for s in conn.standings}
    assert set(st) == {"A", "B", "C"}
    assert st["A"]["challenge"] > st["B"]["challenge"]
    assert st["A"]["stepup"] == 50000 and st["C"]["stepup"] == 30000 and st["C"]["challenge"] == 0
    assert st["A"]["rank"] == 1
    pub = [n for t, n in conn.notifs if t == "round_published"]
    assert len(pub) == 3 and all(t != "contest_ended" for t, _ in conn.notifs)


def test_as_of_cutoff_is_scheduled_at():
    conn = FakeConn(reps={CP: [_rep("A", 5)]})
    run(grade_round.evaluate_round(conn, "round-1", grade_rep=_runner_costs, secret=SECRET))
    assert conn.reps_called_with_ts == [SCHED]


def test_internal_aborts_round_no_writes():
    def boom(*a): raise IsolateInternalError("isolate XX")
    conn = FakeConn(reps={CP: [_rep("A", 5)]})
    out = run(grade_round.evaluate_round(conn, "round-1", grade_rep=boom, secret=SECRET))
    assert out == "failed" and conn.round_status == "failed"
    assert conn.case_results == [] and conn.standings == [] and conn.notifs == []
    assert conn.failed_attempts is None                # transient -> NOT terminal, retryable


def test_already_claimed_is_noop():
    conn = FakeConn(claim=False)
    out = run(grade_round.evaluate_round(conn, "round-1", grade_rep=_runner_costs, secret=SECRET))
    assert out == "noop" and conn.round_status is None
    assert conn.standings == [] and conn.notifs == []


def test_missing_secret_fails_terminally():
    conn = FakeConn(reps={CP: [_rep("A", 5)]})
    out = run(grade_round.evaluate_round(conn, "round-1", grade_rep=_runner_costs, secret=None))
    assert out == "failed" and conn.round_status == "failed"
    assert conn.failed_attempts == grade_round.MAX_ROUND_ATTEMPTS   # deterministic -> burned


def test_final_round_emits_contest_ended():
    class FinalConn(FakeConn):
        async def fetchrow(self, q, *a):
            if "UPDATE evaluation_rounds" in q and "status='generating'" in q and "RETURNING" in q:
                return _round(rtype="final")
            return await super().fetchrow(q, *a)
    conn = FinalConn(reps={CP: [_rep("A", 5), _rep("B", 9)]})
    out = run(grade_round.evaluate_round(conn, "round-1", grade_rep=_runner_costs, secret=SECRET))
    assert out == "done"
    ended = [n for t, n in conn.notifs if t == "contest_ended"]
    assert len(ended) == 2 and all("final_rank" in n for n in ended)


def test_compile_fail_is_all_invalid_not_abort():
    def cefail(*a): return (None, False, "error: expected ';'")
    conn = FakeConn(reps={CP: [_rep("A", 0)]}, stepup={})
    out = run(grade_round.evaluate_round(conn, "round-1", grade_rep=cefail, secret=SECRET))
    assert out == "done"
    assert all(c["raw_cost"] is None and c["verdict"] == "compile_error"
               for c in conn.case_results)
    assert len(conn.case_results) == 2


def test_partial_write_rolls_back_and_marks_failed():
    # a DB write failure mid-transaction must leave NO partial rows and NOT mark 'done'.
    conn = FailingInsertConn(reps={CP: [_rep("A", 5), _rep("B", 9), _rep("D", 12)]})
    out = run(grade_round.evaluate_round(conn, "round-1", grade_rep=_runner_costs, secret=SECRET))
    assert out == "failed" and conn.round_status == "failed"
    assert conn.case_results == [] and conn.standings == []   # rolled back, no partial rows


def test_regrade_replaces_rows_no_stale():
    # grade once (A,B), then re-grade with a DIFFERENT rep set (B gone): no stale B rows.
    conn = FakeConn(reps={CP: [_rep("A", 10), _rep("B", 20)]}, stepup={})
    run(grade_round.evaluate_round(conn, "round-1", grade_rep=_runner_costs, secret=SECRET))
    assert {s["user_id"] for s in conn.standings} == {"A", "B"}
    conn.reps = {CP: [_rep("A", 10)]}                 # B withdrew / superseded
    run(grade_round.evaluate_round(conn, "round-1", grade_rep=_runner_costs, secret=SECRET))
    assert {s["user_id"] for s in conn.standings} == {"A"}        # DELETE-by-round_id worked
    assert all(c["user_id"] == "A" for c in conn.case_results)    # no stale B case rows


def test_lost_lease_rolls_back_publish():
    # the final ownership-guarded UPDATE affects 0 rows (lease stolen) -> roll back, fail.
    conn = FakeConn(reps={CP: [_rep("A", 5)]}, stepup={}, done_tag="UPDATE 0")
    out = run(grade_round.evaluate_round(conn, "round-1", grade_rep=_runner_costs, secret=SECRET))
    assert out == "failed"
    assert conn.round_status == "failed" and conn.case_results == [] and conn.standings == []


if __name__ == "__main__":
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and isinstance(o, types.FunctionType)]
    failed = 0
    for name, fn in tests:
        try:
            fn(); print(f"  ok   {name}")
        except AssertionError as e:
            failed += 1; print(f"  FAIL {name}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1; print(f"  ERR  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
