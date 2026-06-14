"""
Standings / relative-scoring recompute — the Challenge leaderboard math.

Challenge scoring is RELATIVE per test case: a user's per-case score depends on
the whole field's costs on that case (scoring.challenge_case_score), so standings
must be recomputed over everyone's latest results together. This module turns the
stored raw per-case costs into Challenge scores, combines with Step Up scores, and
ranks — all pure, so it is unit-tested without a DB and reused by the scheduler's
scoring stage and the final-evaluation reveal.

Conventions (match scoring.py / grader.py):
  * cost = None  -> invalid on that case (0 there; never the relative floor).
  * Only the latest submission per user should be passed in (caller selects it).
  * Challenge per-case max = 1e6, aggregated (mean) and rescaled to the 800k budget.
"""

from __future__ import annotations

from dataclasses import dataclass

import scoring

Cost = float | None


def compute_challenge_scores(
    raw: dict[str, dict[int, Cost]], seeds: list[int], *,
    budget: int = scoring.CHALLENGE_BUDGET, eps: float = scoring.DEFAULT_COST_EPS,
) -> dict[str, int]:
    """raw[user][seed] = cost (None=invalid). Returns user -> Challenge score (0..budget).

    A user missing a seed is treated as invalid (None) on that case.
    """
    users = list(raw.keys())
    if not users or not seeds:
        return {u: 0 for u in users}

    per_user_cases: dict[str, list[int]] = {u: [] for u in users}
    for seed in seeds:
        all_costs: list[Cost] = [raw[u].get(seed) for u in users]
        for u in users:
            per_user_cases[u].append(
                scoring.challenge_case_score(raw[u].get(seed), all_costs, eps=eps)
            )
    return {u: scoring.challenge_score(per_user_cases[u], budget=budget) for u in users}


@dataclass(frozen=True)
class Standing:
    user_id: str
    stepup: int
    challenge: int
    total: int
    rank: int


def compute_standings(
    stepup: dict[str, int], challenge: dict[str, int],
) -> list[Standing]:
    """Combine Step Up + Challenge into ranked total standings.

    Users present in either map are included (missing -> 0). Ranks share on ties
    (1,1,3,4 style) and the list is sorted best-first.
    """
    users = set(stepup) | set(challenge)
    # each part is capped at its budget (1e6) so summing across >1 problem of a kind
    # can never push a part — or the weighted total — past 1,000,000.
    su = {u: min(stepup.get(u, 0), scoring.STEPUP_BUDGET) for u in users}
    ch = {u: min(challenge.get(u, 0), scoring.CHALLENGE_BUDGET) for u in users}
    # total = 2:8 weighted blend of the two parts (each out of 1e6) -> out of 1e6.
    totals = {u: scoring.weighted_total(su[u], ch[u]) for u in users}
    ranks = scoring.rank_by_score(totals)
    out = [
        Standing(user_id=u, stepup=su[u], challenge=ch[u], total=totals[u], rank=ranks[u])
        for u in users
    ]
    out.sort(key=lambda s: (s.rank, s.user_id))
    return out


def per_case_ranks(raw: dict[str, dict[int, Cost]], seed: int, *,
                   eps: float = scoring.DEFAULT_COST_EPS) -> dict[str, int]:
    """For one case, each user's rank (1-based; lower cost = better). Invalid = last.
    Used by the 'interim evaluation' per-case rank view.

    Competition ranking (1,1,3 style): rank = 1 + (# competitors STRICTLY better).
    Ties — including float costs equal within `eps` — share a rank, consistent with
    challenge_case_score's n_lose/n_draw (which also uses eps). Invalids tie last.
    At eps=0 this is identical to exact-equality ranking.
    """
    costs = {u: raw[u].get(seed) for u in raw}
    ranks: dict[str, int] = {}
    for u, c in costs.items():
        if c is None:
            # invalid: behind every valid competitor; invalids tie each other.
            better = sum(1 for d in costs.values() if d is not None)
        else:
            better = sum(1 for d in costs.values()
                         if d is not None and scoring._lt(d, c, eps))
        ranks[u] = better + 1
    return ranks
