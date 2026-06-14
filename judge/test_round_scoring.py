"""Pure round-assembly tests for judge/round_scoring.py (no DB, no sandbox).

Run:  python judge/test_round_scoring.py
"""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(__file__))

import scoring  # noqa: E402
from round_scoring import CaseRaw, score_round  # noqa: E402

P1 = "prob-challenge-1"
P2 = "prob-challenge-2"


def _case(pid, user, seed, cost, verdict="OK"):
    return CaseRaw(problem_id=pid, user_id=user, submission_id=f"sub-{user}-{pid}",
                   seed=seed, cost=cost, verdict=verdict)


def test_better_cost_beats_worse_per_case():
    # two users, one seed: A cost 10 (better, minimization) vs B cost 20.
    raws = [_case(P1, "A", 1, 10.0), _case(P1, "B", 1, 20.0)]
    res = score_round(raws, {P1: [1]}, {"A": 0, "B": 0})
    by = {(c.user_id): c for c in res.cases}
    assert by["A"].case_rank == 1 and by["B"].case_rank == 2
    assert by["A"].case_score > by["B"].case_score
    # rank-1 of 2 with the loser below => A is full 1e6, B is penalized.
    assert by["A"].case_score == scoring.PER_CASE_MAX


def test_challenge_total_rescaled_to_budget():
    # A wins both seeds -> per-case 1e6 each -> mean 1e6 -> full challenge budget.
    raws = [_case(P1, "A", 1, 1.0), _case(P1, "B", 1, 9.0),
            _case(P1, "A", 2, 1.0), _case(P1, "B", 2, 9.0)]
    res = score_round(raws, {P1: [1, 2]}, {})
    st = {s.user_id: s for s in res.standings}
    assert st["A"].challenge == scoring.CHALLENGE_BUDGET   # full challenge = 1e6
    # no Step Up here -> total is the 0.8 Challenge weight = 800,000.
    assert st["A"].total == scoring.weighted_total(0, st["A"].challenge) == 800000
    assert st["A"].rank == 1
    assert st["B"].challenge < st["A"].challenge


def test_stepup_only_user_appears_in_standings():
    # C never submitted Challenge but has Step Up points -> must rank.
    raws = [_case(P1, "A", 1, 5.0), _case(P1, "B", 1, 6.0)]
    res = score_round(raws, {P1: [1]}, {"C": 120000, "A": 0, "B": 0})
    users = {s.user_id for s in res.standings}
    assert "C" in users
    st = {s.user_id: s for s in res.standings}
    assert st["C"].stepup == 120000 and st["C"].challenge == 0


def test_invalid_cost_scores_zero_and_ranks_last():
    raws = [_case(P1, "A", 1, 10.0),
            _case(P1, "B", 1, None, verdict="RE")]
    res = score_round(raws, {P1: [1]}, {})
    by = {c.user_id: c for c in res.cases}
    assert by["B"].case_score == 0 and by["A"].case_rank == 1 and by["B"].case_rank == 2
    # A beat one invalid of a field of 2 -> still full marks on the case.
    assert by["A"].case_score == scoring.PER_CASE_MAX


def test_all_invalid_seed_ties_everyone():
    raws = [_case(P1, "A", 1, None, "RE"), _case(P1, "B", 1, None, "TLE")]
    res = score_round(raws, {P1: [1]}, {})
    by = {c.user_id: c for c in res.cases}
    assert by["A"].case_score == 0 and by["B"].case_score == 0
    assert by["A"].case_rank == by["B"].case_rank   # all tie when nobody is valid


def test_challenge_summed_across_problems():
    # A wins P1, B wins P2 -> each gets full budget on their problem, summed.
    raws = [_case(P1, "A", 1, 1.0), _case(P1, "B", 1, 9.0),
            _case(P2, "A", 1, 9.0), _case(P2, "B", 1, 1.0)]
    res = score_round(raws, {P1: [1], P2: [1]}, {})
    st = {s.user_id: s for s in res.standings}
    # symmetric: both win one problem fully, lose the other -> equal totals, tie rank 1.
    assert st["A"].challenge == st["B"].challenge
    assert st["A"].rank == 1 and st["B"].rank == 1


def test_multiple_challenge_problems_clamped_to_budget():
    # A aces TWO challenge problems; each rescales to 1e6, but the part (and total)
    # must be capped at 1,000,000 — never 2,000,000 / 1,600,000.
    raws = [_case(P1, "A", 1, 1.0), _case(P1, "B", 1, 9.0),
            _case(P2, "A", 1, 1.0), _case(P2, "B", 1, 9.0)]
    res = score_round(raws, {P1: [1], P2: [1]}, {})
    st = {s.user_id: s for s in res.standings}
    assert st["A"].challenge == scoring.CHALLENGE_BUDGET     # clamped to 1e6, not 2e6
    assert st["A"].total <= 1_000_000
    assert st["A"].total == scoring.weighted_total(0, scoring.CHALLENGE_BUDGET) == 800000


def test_eps_makes_near_equal_costs_a_draw():
    # costs differ by 1e-9; with eps that's a draw (half penalty), not a strict loss.
    raws = [_case(P1, "A", 1, 5.0), _case(P1, "B", 1, 5.0 + 1e-9)]
    strict = score_round(raws, {P1: [1]}, {})
    drawn = score_round(raws, {P1: [1]}, {}, eps_by_problem={P1: 1e-6})
    s_by = {c.user_id: c for c in strict.cases}
    d_by = {c.user_id: c for c in drawn.cases}
    # strict: A strictly beats B; drawn: they tie -> equal case scores + ranks.
    assert s_by["A"].case_score >= s_by["B"].case_score
    assert d_by["A"].case_score == d_by["B"].case_score
    assert d_by["A"].case_rank == d_by["B"].case_rank


def test_rerun_is_byte_identical():
    raws = [_case(P1, "A", 1, 3.0), _case(P1, "B", 1, 4.0),
            _case(P1, "A", 2, 2.0), _case(P1, "B", 2, 2.0)]
    a = score_round(raws, {P1: [1, 2]}, {"A": 10, "B": 20})
    b = score_round(raws, {P1: [1, 2]}, {"A": 10, "B": 20})
    assert a.cases == b.cases
    assert a.standings == b.standings


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
