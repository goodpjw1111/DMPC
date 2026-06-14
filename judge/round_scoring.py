"""
Evaluation-round scoring — PURE assembly of a round's results (no DB, no sandbox).

A round's driver (worker/grade_round.py) gathers, AS OF the round's scheduled_at:
  * the raw per-case outcome of every contestant's representative submission on the
    round's hidden Challenge seeds (cost or None=invalid, plus verdict/runtime/mem), and
  * each contestant's Step Up score (sum of best-per-mission, already absolute).

This module turns that into:
  * Challenge `case_results` rows  — per (user, seed): the RELATIVE per-case score
    (0..1e6) and per-case rank, computed over the WHOLE field on that seed.
  * `standings` rows               — per user: stepup + challenge totals and rank.

Relative scoring is PER PROBLEM (each problem has its own field denominator), so
Challenge per-case scores are computed independently per problem and the per-user
Challenge totals are SUMMED across Challenge problems before combining with Step Up.

Everything here is deterministic and side-effect free, so a re-run with the same
inputs yields byte-identical rows (the idempotency the round pipeline relies on).
Step Up is NOT recomputed here — it is finalized at submit time; we only carry the
snapshot total into the standings totals.
"""

from __future__ import annotations

from dataclasses import dataclass

import scoring
from standings import compute_challenge_scores, compute_standings, per_case_ranks

Cost = float | None


@dataclass(frozen=True)
class CaseRaw:
    """One contestant's raw outcome on one hidden Challenge seed (driver-supplied)."""
    problem_id: str
    user_id: str
    submission_id: str
    seed: int
    cost: Cost            # None = invalid on this case (0 there; never the floor)
    verdict: str          # sandbox verdict (OK|TLE|MLE|RE|CE|ILLEGAL) — carried through
    runtime_ms: int | None = None
    memory_kb: int | None = None


@dataclass(frozen=True)
class CaseScored:
    """A CaseRaw enriched with the relative per-case score + rank (-> case_results)."""
    problem_id: str
    user_id: str
    submission_id: str
    seed: int
    cost: Cost
    verdict: str
    runtime_ms: int | None
    memory_kb: int | None
    case_score: int       # relative 0..1e6 (pre-rescale; the 800k rescale is per-user)
    case_rank: int         # 1-based; lower cost = better; invalid ties last


@dataclass(frozen=True)
class RoundResult:
    cases: list[CaseScored]
    standings: list  # list[standings.Standing]


def _raw_by_problem(
    case_raws: list[CaseRaw],
) -> dict[str, dict[str, dict[int, Cost]]]:
    """Group into raw[problem][user][seed] = cost. The set of users present per
    problem IS the relative field for that problem (a user with no representative
    submission for a problem simply has no rows here, so they don't inflate n_total)."""
    out: dict[str, dict[str, dict[int, Cost]]] = {}
    for cr in case_raws:
        out.setdefault(cr.problem_id, {}).setdefault(cr.user_id, {})[cr.seed] = cr.cost
    return out


def _score_seed_group(users_raw, seeds, budget, eps, pid,
                      challenge_by_user, score_at, rank_at) -> None:
    """Score ONE relative field (a problem's flat seed list, or a subtask's seed pool):
    add each user's relative total (mean per-case score rescaled to `budget`) and record
    the per-(user,seed) relative score + rank. Pure aggregation into the passed dicts."""
    per_user = compute_challenge_scores(users_raw, seeds, budget=budget, eps=eps)
    for u, v in per_user.items():
        challenge_by_user[u] = challenge_by_user.get(u, 0) + v
    for seed in seeds:
        all_costs = [users_raw[u].get(seed) for u in users_raw]
        ranks = per_case_ranks(users_raw, seed, eps=eps)
        for u in users_raw:
            score_at[(pid, u, seed)] = scoring.challenge_case_score(
                users_raw[u].get(seed), all_costs, eps=eps
            )
            rank_at[(pid, u, seed)] = ranks[u]


def score_round(
    case_raws: list[CaseRaw],
    seeds_by_problem: dict[str, list[int]],
    stepup_by_user: dict[str, int],
    *,
    challenge_budget: int = scoring.CHALLENGE_BUDGET,
    eps_by_problem: dict[str, float] | None = None,
    subtasks_by_problem: dict[str, list[dict]] | None = None,
) -> RoundResult:
    """Assemble a round's Challenge case rows + combined standings.

    `case_raws`        every (rep-user, seed) outcome the driver ran, per problem.
    `seeds_by_problem` the hidden seed list per Challenge problem (order = scoring order).
    `stepup_by_user`   per-user Step Up total AS OF the round (already summed over
                       all Step Up problems; absolute points). Users with only Step
                       Up still appear in standings via the union.
    `eps_by_problem`   optional per-problem cost tolerance (float-cost ties); 0.0 default.
    `subtasks_by_problem` optional per-problem list of subtasks `[{seeds, budget, eps}]`.
                       When set for a problem, that problem is scored PER SUBTASK
                       (each its own relative field + budget) and the per-subtask
                       scores are SUMMED — the subtask budgets sum to challenge_budget.
                       Absent -> the flat (single-field) path (backward compatible).
    """
    eps_by_problem = eps_by_problem or {}
    subtasks_by_problem = subtasks_by_problem or {}
    raw_by_problem = _raw_by_problem(case_raws)

    # Per-user Challenge total = sum over Challenge problems of that problem's relative score.
    challenge_by_user: dict[str, int] = {}
    # Per (problem,user,seed): relative case score + rank, to enrich the case rows.
    score_at: dict[tuple[str, str, int], int] = {}
    rank_at: dict[tuple[str, str, int], int] = {}

    for pid, users_raw in raw_by_problem.items():
        subs = subtasks_by_problem.get(pid)
        if subs:
            for st in subs:
                _score_seed_group(users_raw, st["seeds"], int(st["budget"]),
                                  float(st.get("eps", scoring.DEFAULT_COST_EPS)), pid,
                                  challenge_by_user, score_at, rank_at)
        else:
            _score_seed_group(users_raw, seeds_by_problem.get(pid, []), challenge_budget,
                              eps_by_problem.get(pid, scoring.DEFAULT_COST_EPS), pid,
                              challenge_by_user, score_at, rank_at)

    cases = [
        CaseScored(
            problem_id=cr.problem_id, user_id=cr.user_id, submission_id=cr.submission_id,
            seed=cr.seed, cost=cr.cost, verdict=cr.verdict,
            runtime_ms=cr.runtime_ms, memory_kb=cr.memory_kb,
            case_score=score_at.get((cr.problem_id, cr.user_id, cr.seed), 0),
            case_rank=rank_at.get((cr.problem_id, cr.user_id, cr.seed), 1),
        )
        for cr in case_raws
    ]

    standing_rows = compute_standings(stepup_by_user, challenge_by_user)
    return RoundResult(cases=cases, standings=standing_rows)
