"""maze_push: checker/solver/generator tests. Run: python problems/maze_push/test_gen.py"""
from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(__file__))
import problem as P  # noqa: E402


def test_straight_line():
    g = "1 5 1\nD...G\n"
    assert P.reference_cost(g) == 4.0, P.reference_cost(g)
    cost, valid, _ = P.check(g, "RRRR")
    assert valid and cost == 4.0, (valid, cost)


def test_push_chain_cost():
    # D O . G .  — Dao must push the block across the row to reach G(0,3).
    g = "1 5 1\nDO.G.\n"
    cost, valid, _ = P.check(g, "RRR")
    # R: push 1 block (1,)->cost2, dao(0,1); R: push ->cost2, dao(0,2); R: push ->cost2, dao(0,3)=G
    assert valid and cost == 6.0, (valid, cost)
    assert P.reference_cost(g) == 6.0, P.reference_cost(g)


def test_fail_into_wall_costs_one():
    # Dao at (0,0), wall to the right; moving R fails but costs 1; D reaches G below.
    g = "2 2 1\nD#\nG.\n"
    cost, valid, _ = P.check(g, "RD")   # R fails(+1), D moves to (1,0)=G(+1) -> cost 2
    assert valid and cost == 2.0, (valid, cost)


def test_not_reached_is_penalty():
    cost, valid, _ = P.check("1 5 1\nD...G\n", "RR")   # stops short
    assert valid and cost == float(P.MISS_COST), (valid, cost)


def test_bad_char_invalid():
    cost, valid, _ = P.check("1 5 1\nD...G\n", "RX")
    assert (not valid) and cost is None, (valid, cost)


def test_generate_solvable_and_consistent_c1():
    params = {"rows": 5, "cols": 5, "players": 1, "obstacles": 2, "blocks": 3}
    for seed in range(6):
        g = P.generate(seed, params)
        assert g.split("\n")[0] == "5 5 1", g.split("\n")[0]
        sol = P.sample_solution(g)
        cost, valid, msg = P.check(g, sol)
        ref = P.reference_cost(g)
        assert valid and cost == ref, (seed, valid, cost, ref, msg)   # solver == checker
        assert cost < P.MISS_COST, (seed, cost)                        # actually reaches G


def test_generate_solvable_and_consistent_c2():
    params = {"rows": 4, "cols": 4, "players": 2, "obstacles": 1, "blocks": 2}
    for seed in range(4):
        g = P.generate(seed, params)
        assert g.split("\n")[0] == "4 4 2", g.split("\n")[0]
        sol = P.sample_solution(g)
        cost, valid, _ = P.check(g, sol)
        assert valid and cost == P.reference_cost(g) and cost < P.MISS_COST, (seed, cost)


def test_feature_ranges_in_generate():
    # dust... here blocks count as a RANGE should vary across seeds (Challenge subtasks)
    counts = set()
    for seed in range(20):
        g = P.generate(seed, {"rows": 6, "cols": 6, "players": 1, "obstacles": [1, 3], "blocks": [2, 5]})
        counts.add(g.count("O"))
    assert len(counts) > 1, counts   # block count actually varies within the range


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
