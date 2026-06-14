"""Deterministic pipeline test (no subprocess): generator + solvers + checker + scoring.

Run:  python problems/example_clean/test_pipeline.py
"""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "judge"))
from scoring import stepup_aggregate  # noqa: E402

import naive_solution  # noqa: E402
import problem  # noqa: E402
import sample_solution  # noqa: E402

SEEDS = problem.META["given_seeds"]
BUDGET = problem.META["stepup_budget"]


def _cases(solver):
    cases = []
    for seed in SEEDS:
        inp = problem.generate(seed)
        out = solver(inp)
        cost, valid, _ = problem.check(inp, out)
        ref = problem.reference_cost(inp)
        cases.append((cost, ref, valid))
    return cases


def test_generator_is_deterministic():
    assert problem.generate(101) == problem.generate(101)


def test_greedy_is_valid_and_matches_reference():
    for seed in SEEDS:
        inp = problem.generate(seed)
        out = sample_solution.solve(inp)
        cost, valid, msg = problem.check(inp, out)
        assert valid, msg
        assert cost == problem.reference_cost(inp)  # greedy == reference order


def test_greedy_earns_full_marks():
    cases = [(c, r) for c, r, _ in _cases(sample_solution.solve)]
    assert stepup_aggregate(cases, budget=BUDGET) == BUDGET


def test_naive_is_valid_but_partial():
    cases = _cases(naive_solution.solve)
    assert all(v for *_, v in cases)                       # cleans everything
    pairs = [(c, r) for c, r, _ in cases]
    score = stepup_aggregate(pairs, budget=BUDGET)
    assert 0 < score < BUDGET                              # partial, not full
    # and it is genuinely worse on at least one given case
    assert any(c > r for c, r, _ in cases)


def test_invalid_output_scores_zero():
    inp = problem.generate(SEEDS[0])
    cost, valid, _ = problem.check(inp, "")                # cleans nothing
    assert not valid and cost is None
    assert stepup_aggregate([(cost, problem.reference_cost(inp))], budget=BUDGET) == 0


def test_off_grid_is_invalid():
    inp = problem.generate(SEEDS[0])           # start at (0,0)
    cost, valid, msg = problem.check(inp, "U")  # immediately off the top edge
    assert not valid and cost is None


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
