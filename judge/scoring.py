"""
DMPC scoring engine — reference implementation.

Two scoring models, matching the contest spec:

  * Step Up  (만점 1,000,000): partial credit vs a reference/threshold cost.
  * Challenge (만점 1,000,000): relative per-test-case ranking across participants.

Each part is scored out of 1,000,000. The FINAL total = 1,000,000 too, as the 2:8
WEIGHTED blend: total = (2*stepup + 8*challenge) / 10 (see weighted_total).

Conventions
-----------
* All problems are MINIMIZATION: a lower `cost` is better.
* An *invalid* result on a case (TLE / MLE / RE / compile error / illegal or
  wrong-format output) scores 0 for THAT case only — never the whole submission,
  and never the relative-score floor. Invalid is represented as `cost = None`.
* Only one (the latest) submission per participant should be passed in here; the
  caller is responsible for selecting it. Keeping the per-case MAX/rank over the
  latest submission of each competitor bounds recomputation (AHC model).

The Challenge formula was confirmed by the owner as the "corrected" reading with
the 0.5 outer coefficient (rank 1 -> 1,000,000, last place -> 500,000 floor):

    Score_tc = floor( 1e6 * ( 1 - 0.5 * sqrt( (n_lose + 0.5*n_draw) / n_total ) ) )

where, for a given participant on a given case:
    n_total = number of participants with a submission for this problem
    n_lose  = participants whose cost is strictly LOWER (they beat me)
    n_draw  = participants whose cost EQUALS mine (excluding myself)

This module is pure stdlib and has no side effects, so it is trivially testable
and can be reused unchanged by the grader worker and the standings recomputation
service.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

# ---------------------------------------------------------------------------
# Tunables (confirm with spec; kept as named constants, not magic numbers)
# ---------------------------------------------------------------------------

PER_CASE_MAX = 1_000_000          # normalized per-case unit before rescaling
# Each PART is scored out of 1,000,000 (so Step Up and Challenge share the same max,
# shown as "X / 1,000,000"). The 2:8 split is a WEIGHT applied only at the TOTAL.
CHALLENGE_BUDGET = 1_000_000
STEPUP_BUDGET = 1_000_000
STEPUP_WEIGHT = 2                 # total = (2*stepup + 8*challenge) / 10  (both out of 1e6)
CHALLENGE_WEIGHT = 8
RELATIVE_PENALTY_COEFF = 0.5      # the outer 0.5 -> last place floors at 500k
DEFAULT_COST_EPS = 0.0            # tie tolerance; >0 only for float costs


def weighted_total(stepup: int, challenge: int) -> int:
    """Final score out of 1,000,000 = the 2:8 weighted blend of the two parts
    (each itself out of 1,000,000). Integer math so 1e6+1e6 -> exactly 1e6."""
    return (STEPUP_WEIGHT * stepup + CHALLENGE_WEIGHT * challenge) // (STEPUP_WEIGHT + CHALLENGE_WEIGHT)


# A participant's raw outcome on a single test case.
#   cost = None  -> invalid (no usable output); scores 0 on this case.
Cost = Optional[float]


# ---------------------------------------------------------------------------
# Challenge — relative per-test-case scoring
# ---------------------------------------------------------------------------

def challenge_case_score(
    my_cost: Cost,
    all_costs: Sequence[Cost],
    *,
    eps: float = DEFAULT_COST_EPS,
    coeff: float = RELATIVE_PENALTY_COEFF,
) -> int:
    """Relative score (0..1e6) for one participant on one test case.

    Calling convention: `all_costs` is the cost of EVERY participant that
    submitted, INCLUDING this participant exactly once, with invalids as None.
    Invalids inflate n_total but beat nobody, so outperforming a participant who
    failed the case raises your score.

    Returns 0 immediately for an invalid own result.
    """
    if my_cost is None:
        return 0

    n_total = len(all_costs)
    if n_total <= 0:
        return 0

    valid = [c for c in all_costs if c is not None]
    n_lose = sum(1 for c in valid if _lt(c, my_cost, eps))   # they beat me
    n_eq = sum(1 for c in valid if _eq(c, my_cost, eps))     # ties incl. myself
    n_draw = max(n_eq - 1, 0)                                # exclude myself

    ratio = (n_lose + 0.5 * n_draw) / n_total
    ratio = min(max(ratio, 0.0), 1.0)
    return math.floor(PER_CASE_MAX * (1.0 - coeff * math.sqrt(ratio)))


def challenge_score(
    per_case_scores: Iterable[int],
    *,
    budget: int = CHALLENGE_BUDGET,
) -> int:
    """Aggregate per-case relative scores (each 0..1e6) into the Challenge budget.

    Arithmetic mean across the K cases, rescaled to `budget` (default 800,000).
    """
    scores = list(per_case_scores)
    if not scores:
        return 0
    mean = sum(scores) / len(scores)
    return math.floor(budget * mean / PER_CASE_MAX)


# ---------------------------------------------------------------------------
# Step Up — partial credit vs a threshold/reference cost
# ---------------------------------------------------------------------------

def stepup_ratio(cost: Cost, cost_ref: float) -> float:
    """The [0,1] credit fraction = min(Cost'/Cost, 1), with zero-guards.

    * Invalid (cost is None)        -> 0.0.
    * cost <= cost_ref (met/beat)   -> 1.0.
    * cost_ref <= 0                 -> only a cost <= 0 earns full credit, else 0.0.
    * cost <= 0 (perfect/degenerate)-> 1.0.
    """
    if cost is None:
        return 0.0
    if cost_ref <= 0:
        return 1.0 if cost <= 0 else 0.0
    if cost <= 0:
        return 1.0
    return min(cost_ref / cost, 1.0)


def stepup_score(cost: Cost, cost_ref: float, *, budget: int = STEPUP_BUDGET) -> int:
    """Single-case Step Up points = floor( S * stepup_ratio )."""
    return math.floor(budget * stepup_ratio(cost, cost_ref))


def stepup_aggregate(
    cases: Sequence[tuple[Cost, float]], *, budget: int = STEPUP_BUDGET
) -> int:
    """Step Up score over the given (fixed) cases — DEMO/illustration only.

    `cases` = list of (cost, cost_ref). The problem's S budget is shared equally:
    score = floor( S * mean(stepup_ratio_i) ). An invalid case contributes 0.

    NOTE: This mean-then-floor variant is NOT the authoritative scoring path. The
    production Step Up score is computed per-mission with a staircase budget split
    and summed (judge/grader.py: mission_budgets + stepup_problem_score), which is
    what the API/standings use. Do NOT wire this into standings — the two reach the
    budget cap differently (this floors once; the per-mission path sums exact
    staircase budgets to S). Kept for the example pipeline test/demo.
    """
    if not cases:
        return 0
    mean = sum(stepup_ratio(c, r) for c, r in cases) / len(cases)
    return math.floor(budget * mean)


# ---------------------------------------------------------------------------
# Per-problem-set total
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProblemSetScore:
    stepup: int
    challenge: int

    @property
    def total(self) -> int:
        return weighted_total(self.stepup, self.challenge)


def problemset_total(stepup: int, challenge: int) -> ProblemSetScore:
    return ProblemSetScore(stepup=stepup, challenge=challenge)


# ---------------------------------------------------------------------------
# Ranking helper (1-based, ties share the lower rank — "1224" style)
# ---------------------------------------------------------------------------

def rank_by_score(scores_by_user: dict[str, int]) -> dict[str, int]:
    """Map user -> rank (1-based). Higher score = better = lower rank number.
    Ties share the same rank; the next distinct score skips accordingly.
    """
    ranks: dict[str, int] = {}
    ordered = sorted(scores_by_user.items(), key=lambda kv: kv[1], reverse=True)
    prev_score = None
    prev_rank = 0
    for i, (user, score) in enumerate(ordered, start=1):
        if score != prev_score:
            prev_rank = i
            prev_score = score
        ranks[user] = prev_rank
    return ranks


# ---------------------------------------------------------------------------
# float-safe comparisons
# ---------------------------------------------------------------------------

def _lt(a: float, b: float, eps: float) -> bool:
    return a < b - eps


def _eq(a: float, b: float, eps: float) -> bool:
    return abs(a - b) <= eps
