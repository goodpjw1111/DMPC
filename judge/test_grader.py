"""Integration test: registry -> generate -> checker -> Step Up score.

Exercises the real example problem end-to-end (no sandbox), proving the Step Up
grading path the API will call.
Run:  python judge/test_grader.py
"""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "problems", "example_clean"))

from grader import (  # noqa: E402
    grade_stepup_mission, mission_budget, mission_budgets, mission_weights, stepup_problem_score,
)
from registry import list_problem_keys, load_problem  # noqa: E402

import naive_solution  # noqa: E402
import sample_solution  # noqa: E402

PROBLEM = load_problem("example_clean")
SEEDS = PROBLEM.META["given_seeds"]


def test_registry_lists_example():
    assert "example_clean" in list_problem_keys()


def test_registry_rejects_unknown():
    try:
        load_problem("does_not_exist")
    except KeyError:
        return
    raise AssertionError("expected KeyError")


def test_greedy_full_marks_each_mission():
    weights = mission_weights(PROBLEM, PROBLEM.META)
    for seed in SEEDS:
        pm = mission_budget(PROBLEM.META, seed, weights)   # difficulty-weighted budget
        out = sample_solution.solve(PROBLEM.generate(seed))
        r = grade_stepup_mission(PROBLEM, seed, out)
        assert r.valid and r.cost == r.ref          # greedy == reference
        assert r.ratio == 1.0 and r.score == pm == r.mission_budget


def test_harder_mission_worth_more_and_sums_to_budget():
    # budgets follow difficulty (reference cost): the mission with the higher reference
    # cost gets the larger budget, and the weighted budgets still sum to the full budget.
    weights = mission_weights(PROBLEM, PROBLEM.META)
    budgets = mission_budgets(PROBLEM.META, weights)
    assert sum(budgets) == PROBLEM.META["stepup_budget"]
    # ordering: sort by weight asc -> budgets must be non-decreasing in that order
    order = sorted(range(len(weights)), key=lambda i: weights[i])
    ranked = [budgets[i] for i in order]
    assert ranked == sorted(ranked)
    # and they are genuinely different (the demo seeds are not all equally hard)
    assert len(set(weights)) > 1 and max(budgets) > min(budgets)


def test_greedy_problem_total_is_full_budget():
    # A flawless Step Up solve scores EXACTLY the budget (no remainder dropped).
    scores = [grade_stepup_mission(PROBLEM, s, sample_solution.solve(PROBLEM.generate(s))).score
              for s in SEEDS]
    assert stepup_problem_score(scores) == PROBLEM.META["stepup_budget"]


def test_naive_is_valid_but_partial():
    total = 0
    budget = PROBLEM.META["stepup_budget"]
    any_partial = False
    for seed in SEEDS:
        out = naive_solution.solve(PROBLEM.generate(seed))
        r = grade_stepup_mission(PROBLEM, seed, out)
        assert r.valid
        total += r.score
        if r.score < r.mission_budget:
            any_partial = True
    assert any_partial and total < budget and total > 0


def test_invalid_output_scores_zero():
    r = grade_stepup_mission(PROBLEM, SEEDS[0], "")          # cleans nothing
    assert not r.valid and r.cost is None and r.score == 0


def test_off_grid_scores_zero():
    r = grade_stepup_mission(PROBLEM, SEEDS[0], "U")          # off the top edge
    assert not r.valid and r.score == 0


def test_mission_budgets_sum_to_full_budget():
    # staircase split: budgets sum EXACTLY to the budget, each within 1 of even.
    budget = PROBLEM.META["stepup_budget"]
    budgets = mission_budgets(PROBLEM.META)
    assert len(budgets) == len(SEEDS)
    assert sum(budgets) == budget
    even = budget // len(SEEDS)
    assert all(even <= b <= even + 1 for b in budgets)


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
