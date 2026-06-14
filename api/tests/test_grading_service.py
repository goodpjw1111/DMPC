"""Verify the Step Up submit flow (grade -> insert -> notify) with a fake conn.

No Postgres needed: a tiny async fake records inserts/notifications and aggregates
best-per-mission, so we exercise the real grading glue on any machine.
Run:  python api/tests/test_grading_service.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import types

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "app"))                 # import grading
sys.path.insert(0, os.path.join(HERE, "..", ".."))                 # repo root
sys.path.insert(0, os.path.join(HERE, "..", "..", "problems", "example_clean"))

import grading  # noqa: E402  (api/app/grading.py)
from grader import mission_budget, mission_weights  # noqa: E402
from registry import load_problem  # noqa: E402

import sample_solution  # noqa: E402

PROBLEM_ROW = {"id": "11111111-1111-1111-1111-111111111111", "problem_key": "example_clean"}
USER = "22222222-2222-2222-2222-222222222222"
MOD = load_problem("example_clean")
SEEDS = MOD.META["given_seeds"]
PM = mission_budget(MOD.META, SEEDS[0], mission_weights(MOD, MOD.META))   # difficulty-weighted


class FakeConn:
    def __init__(self):
        self.inserts = []   # (problem_id,user_id,seed,output,cost,valid,score)
        self.notifs = []    # (user_id, payload_json)

    async def fetchrow(self, q, *args):
        if "INSERT INTO stepup_submissions" in q:
            self.inserts.append(args)
            return {"id": f"sub-{len(self.inserts)}"}
        return None

    async def execute(self, q, *args):
        if "notifications" in q:
            self.notifs.append(args)
        return "INSERT 0 1"

    async def fetch(self, q, *args):
        best: dict[int, int] = {}
        for a in self.inserts:
            seed, score = a[2], a[6]
            best[seed] = max(best.get(seed, 0), score)
        return [{"mission_seed": s, "best": b} for s, b in best.items()]


def run(coro):
    return asyncio.run(coro)


def test_greedy_submit_records_and_scores_full():
    conn = FakeConn()
    out = sample_solution.solve(MOD.generate(SEEDS[0]))
    r = run(grading.submit_stepup(conn, user_id=USER, problem=PROBLEM_ROW,
                                  mission_seed=SEEDS[0], output=out))
    assert r.valid and r.score == PM and r.ratio == 1.0
    assert len(conn.inserts) == 1 and len(conn.notifs) == 1
    payload = json.loads(conn.notifs[0][1])
    assert payload["kind"] == "stepup" and payload["score"] == PM


def test_invalid_mission_seed_rejected():
    conn = FakeConn()
    try:
        run(grading.submit_stepup(conn, user_id=USER, problem=PROBLEM_ROW,
                                  mission_seed=999999, output="DD"))
    except grading.SubmitError:
        assert not conn.inserts          # nothing recorded on a bad request
        return
    raise AssertionError("expected SubmitError")


def test_empty_output_rejected():
    conn = FakeConn()
    try:
        run(grading.submit_stepup(conn, user_id=USER, problem=PROBLEM_ROW,
                                  mission_seed=SEEDS[0], output="   "))
    except grading.SubmitError:
        return
    raise AssertionError("expected SubmitError")


def test_invalid_output_records_zero():
    conn = FakeConn()
    r = run(grading.submit_stepup(conn, user_id=USER, problem=PROBLEM_ROW,
                                  mission_seed=SEEDS[0], output="U"))  # off-grid
    assert not r.valid and r.score == 0
    assert len(conn.inserts) == 1 and len(conn.notifs) == 1   # still recorded


def test_problem_score_sums_best_per_mission():
    conn = FakeConn()
    for s in SEEDS:
        out = sample_solution.solve(MOD.generate(s))
        run(grading.submit_stepup(conn, user_id=USER, problem=PROBLEM_ROW,
                                  mission_seed=s, output=out))
    total = run(grading.stepup_problem_score_for_user(
        conn, problem_id=PROBLEM_ROW["id"], user_id=USER))
    assert total == MOD.META["stepup_budget"]    # flawless solve = exactly the budget


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
