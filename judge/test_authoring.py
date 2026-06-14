"""Authored-problem support: effective_meta merges per-contest scoring_config over the
module META, and Step Up grading honors the AUTHORED seeds + budget (not the module's).

This is what makes an admin-authored contest (built on a built-in generator/checker)
gradeable with its own missions. Pure stdlib — no DB / web stack.

Run:  python judge/test_authoring.py
"""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "problems", "example_clean"))

from grader import grade_stepup_mission, mission_budget, mission_budgets, mission_weights  # noqa: E402
from registry import effective_meta, load_problem  # noqa: E402

import sample_solution  # noqa: E402

PROBLEM = load_problem("example_clean")


def test_effective_meta_overrides_seeds_and_budget():
    cfg = {"given_seeds": [501, 502], "stepup_budget": 1_000_000}
    eff = effective_meta(PROBLEM.META, cfg)
    assert eff["given_seeds"] == [501, 502]
    assert eff["stepup_budget"] == 1_000_000
    # untouched module keys survive (generator/checker identity, title, limits)
    assert eff["title"] == PROBLEM.META["title"]


def test_effective_meta_none_or_empty_is_module_default():
    assert effective_meta(PROBLEM.META, None)["given_seeds"] == PROBLEM.META["given_seeds"]
    assert effective_meta(PROBLEM.META, {})["given_seeds"] == PROBLEM.META["given_seeds"]
    # a partial config only overrides the keys it sets
    eff = effective_meta(PROBLEM.META, {"stepup_budget": 12345})
    assert eff["stepup_budget"] == 12345
    assert eff["given_seeds"] == PROBLEM.META["given_seeds"]


def test_authored_budgets_staircase_sum_to_authored_budget():
    # two authored missions, authored budget 1,000,000 -> staircase splits exactly.
    eff = effective_meta(PROBLEM.META, {"given_seeds": [7, 8, 9, 10], "stepup_budget": 1_000_000})
    budgets = mission_budgets(eff)
    assert len(budgets) == 4
    assert sum(budgets) == 1_000_000


def test_authored_seed_full_marks_with_authored_budget():
    # grade an AUTHORED seed (not in the module's given_seeds) with the reference solution
    # -> full marks, and the mission budget equals the authored staircase budget.
    authored_seeds = [777, 888]
    eff = effective_meta(PROBLEM.META, {"given_seeds": authored_seeds, "stepup_budget": 1_000_000})
    seed = authored_seeds[0]
    out = sample_solution.solve(PROBLEM.generate(seed))     # achievable reference -> full marks
    r = grade_stepup_mission(PROBLEM, seed, out, meta=eff)
    assert r.valid
    assert r.mission_budget == mission_budget(eff, seed, mission_weights(PROBLEM, eff))
    assert r.score == r.mission_budget          # reference solution earns the full mission budget


def test_default_meta_path_unchanged():
    # no meta arg -> behaves exactly as before (module given_seeds/budget).
    seed = PROBLEM.META["given_seeds"][0]
    out = sample_solution.solve(PROBLEM.generate(seed))
    r = grade_stepup_mission(PROBLEM, seed, out)
    assert r.valid and r.score == r.mission_budget
    assert r.mission_budget == mission_budget(PROBLEM.META, seed, mission_weights(PROBLEM, PROBLEM.META))


def test_parametric_clean_robot_uses_authored_gen_params():
    # the parametric problem: authored grid/dust ranges drive generation AND grading.
    cr = load_problem("clean_robot")
    params = {"hMin": 4, "hMax": 4, "wMin": 5, "wMax": 5, "dMin": 3, "dMax": 3}
    eff = effective_meta(cr.META, {"given_seeds": [10, 20], "stepup_budget": 1_000_000, "gen_params": params})
    seed = 10
    inp = cr.generate(seed, params)
    assert inp.split("\n")[0] == "4 5"            # authored dims, not the module default
    assert inp.count("*") == 3                     # authored dust count
    out = sample_solution.solve(inp)               # generic clean-robot solver -> full marks
    r = grade_stepup_mission(cr, seed, out, meta=eff)   # regenerates with eff["gen_params"]
    assert r.valid and r.score == r.mission_budget


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
