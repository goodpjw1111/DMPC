"""
Step Up grading service — NO sandbox needed.

Step Up = the user submits an OUTPUT for a given mission (test case). We regenerate
the mission input from its seed, run the trusted per-problem checker to get the
cost, and score it. Each mission is worth `stepup_budget / num_missions`; the
problem's Step Up score is the sum of the user's best per-mission scores.

Challenge grading (code execution) lives in judge/sandbox.py on the grader host;
this module is pure (no untrusted code runs here), so it is unit-tested directly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import ModuleType

import scoring


@dataclass(frozen=True)
class MissionResult:
    seed: int
    cost: float | None     # None = invalid output (0 score on this mission)
    valid: bool
    ref: float             # reference (achievable) cost — full marks at cost <= ref
    ratio: float           # min(ref/cost, 1) in [0,1]
    score: int             # floor(mission_budget * ratio)
    mission_budget: int
    message: str


def mission_budgets(meta: dict, weights: list[float] | None = None) -> list[int]:
    """Per-mission point budgets for the given_seeds, in order.

    Harder missions are worth MORE: each mission's share of `stepup_budget` is
    proportional to its difficulty `weights` (e.g. the achievable reference cost from
    `mission_weights`). A cumulative-weight staircase keeps the budgets summing EXACTLY
    to stepup_budget (no remainder dropped). With no/invalid weights this degrades to
    an even split (the original behaviour)."""
    seeds = meta.get("given_seeds") or []
    n = max(1, len(seeds))
    b = meta.get("stepup_budget", scoring.STEPUP_BUDGET)
    w = [max(float(x), 0.0) for x in (weights or [])]
    if len(w) != n or sum(w) <= 0:
        w = [1.0] * n                                  # even fallback
    total = sum(w)
    out, prev, cum = [], 0, 0.0
    for i in range(n):
        cum += w[i]
        cut = int(b * cum / total)                     # floor; final cut == b -> sums to b
        out.append(cut - prev)
        prev = cut
    return out


def mission_budget(meta: dict, seed: int, weights: list[float] | None = None) -> int:
    """The point budget of the mission identified by `seed`."""
    seeds = meta.get("given_seeds") or []
    budgets = mission_budgets(meta, weights)
    idx = seeds.index(seed) if seed in seeds else 0
    return budgets[idx] if idx < len(budgets) else (budgets[0] if budgets else 0)


def mission_weights(problem: ModuleType, meta: dict) -> list[float]:
    """Difficulty weight per mission = its achievable REFERENCE COST (the optimal
    solution's cost). Harder instances need more moves -> higher reference cost ->
    a larger share of the budget. Robust to a generator/checker hiccup (falls back to
    weight 1 for that mission)."""
    seeds = meta.get("given_seeds") or []
    gp = meta.get("gen_params")
    out: list[float] = []
    for s in seeds:
        try:
            out.append(max(float(problem.reference_cost(problem.generate(s, gp))), 1.0))
        except Exception:                              # noqa: BLE001 — never fail grading on weighting
            out.append(1.0)
    return out


def grade_stepup_mission(problem: ModuleType, seed: int, output: str,
                         meta: dict | None = None) -> MissionResult:
    """Grade one submitted output against one mission. Trusted: no user code runs.
    `meta` overrides problem.META (admin-authored given_seeds/stepup_budget/gen_params)
    so both the generated input and the mission budget reflect the per-contest config."""
    m = meta or problem.META
    inp = problem.generate(seed, m.get("gen_params"))
    cost, valid, message = problem.check(inp, output)
    ref = float(problem.reference_cost(inp))
    ratio = scoring.stepup_ratio(cost, ref)
    pm = mission_budget(m, seed, mission_weights(problem, m))   # difficulty-weighted
    return MissionResult(
        seed=seed, cost=cost, valid=valid, ref=ref, ratio=ratio,
        score=math.floor(pm * ratio), mission_budget=pm, message=message,
    )


def stepup_problem_score(best_mission_scores: list[int]) -> int:
    """A user's Step Up score for the problem = sum of their best per-mission scores.
    With staircase budgets, a full solve sums to exactly stepup_budget."""
    return sum(best_mission_scores)
