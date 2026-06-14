"""Boundary + edge-case tests for the DMPC scoring engine.

Run:  python -m pytest judge/test_scoring.py -q
or:   python judge/test_scoring.py   (falls back to a tiny built-in runner)
"""

from __future__ import annotations

import math

from scoring import (
    CHALLENGE_BUDGET,
    PER_CASE_MAX,
    STEPUP_BUDGET,
    challenge_case_score,
    challenge_score,
    rank_by_score,
    stepup_aggregate,
    stepup_score,
    weighted_total,
)


# ---------------------------------------------------------------------------
# Challenge — per-case relative score
# ---------------------------------------------------------------------------

def test_rank1_is_full():
    # I am strictly the best; nobody beats me, no ties.
    assert challenge_case_score(10, [10, 20, 30, 40]) == 1_000_000


def test_last_place_floors_at_500k():
    # Everyone else beats me (lower cost). n_lose = n_total-1, ratio -> ~1.
    # The 0.5 coefficient floors the score at 500,000, never 0.
    s = challenge_case_score(100, [1, 2, 3, 100])
    assert s == math.floor(1e6 * (1 - 0.5 * math.sqrt(3 / 4)))
    assert 500_000 <= s < 600_000


def test_absolute_last_among_many_approaches_500k():
    others = list(range(1, 1000))  # 999 people beat me
    s = challenge_case_score(10**9, others + [10**9])
    # ratio = 999/1000 ~ 1 -> ~500k
    assert 500_000 <= s <= 520_000


def test_solo_participant_is_full():
    assert challenge_case_score(42, [42]) == 1_000_000


def test_all_tied_get_same_midhigh_score():
    # 4 people all equal: each has n_lose=0, n_draw=3, ratio=1.5/4=0.375
    costs = [50, 50, 50, 50]
    expected = math.floor(1e6 * (1 - 0.5 * math.sqrt(1.5 / 4)))
    for _ in costs:
        assert challenge_case_score(50, costs) == expected


def test_tie_is_half_penalty_of_a_loss():
    # me=10. One opponent beats me (5), one ties (10).
    # n_total=3, n_lose=1, n_draw=1 -> ratio=(1+0.5)/3=0.5
    s = challenge_case_score(10, [5, 10, 10])
    assert s == math.floor(1e6 * (1 - 0.5 * math.sqrt(0.5)))


def test_invalid_own_result_is_zero_not_floor():
    # An invalid result scores 0 on the case — NOT the 500k relative floor.
    assert challenge_case_score(None, [None, 1, 2, 3]) == 0


def test_invalids_count_in_n_total_but_beat_nobody():
    # me=10, opponents: one invalid, two worse. n_total=4, n_lose=0, n_draw=0.
    # Being best among the valid + outperforming a failer keeps me at full.
    assert challenge_case_score(10, [10, None, 20, 30]) == 1_000_000


def test_empty_field_is_zero():
    assert challenge_case_score(10, []) == 0


def test_float_costs_with_epsilon_ties():
    # Two opponents are better than me by less than eps. Without eps they count
    # as losses; with eps they become ties (half penalty), so my score rises.
    my_cost = 1.0
    costs = [0.9999999, 0.9999998, 1.0]
    no_eps = challenge_case_score(my_cost, costs, eps=0.0)      # 2 losses
    with_eps = challenge_case_score(my_cost, costs, eps=1e-6)   # 2 ties
    assert with_eps > no_eps  # ties penalised less than losses


# ---------------------------------------------------------------------------
# Challenge — aggregation into the 800k budget
# ---------------------------------------------------------------------------

def test_aggregate_full_marks_maps_to_budget():
    assert challenge_score([PER_CASE_MAX] * 5) == CHALLENGE_BUDGET


def test_aggregate_is_mean_rescaled():
    per_case = [1_000_000, 500_000, 0]
    expected = math.floor(CHALLENGE_BUDGET * (sum(per_case) / 3) / PER_CASE_MAX)
    assert challenge_score(per_case) == expected


def test_aggregate_empty_is_zero():
    assert challenge_score([]) == 0


# ---------------------------------------------------------------------------
# Step Up — partial credit vs threshold
# ---------------------------------------------------------------------------

def test_stepup_full_when_meeting_threshold():
    assert stepup_score(80, 100) == STEPUP_BUDGET   # cost < ref
    assert stepup_score(100, 100) == STEPUP_BUDGET  # cost == ref


def test_stepup_partial_is_hyperbolic():
    # cost = 2*ref -> half credit.
    assert stepup_score(200, 100) == math.floor(STEPUP_BUDGET * 0.5)
    # cost = 4*ref -> quarter credit.
    assert stepup_score(400, 100) == math.floor(STEPUP_BUDGET * 0.25)


def test_stepup_cost_zero_is_full():
    assert stepup_score(0, 100) == STEPUP_BUDGET


def test_stepup_ref_zero_only_zero_cost_scores():
    assert stepup_score(0, 0) == STEPUP_BUDGET
    assert stepup_score(5, 0) == 0


def test_stepup_invalid_is_zero():
    assert stepup_score(None, 100) == 0


def test_stepup_huge_cost_is_zero_floor():
    assert stepup_score(10**18, 100) == 0


def test_stepup_never_exceeds_budget():
    for cost in [0, 1, 50, 99, 100, 1000]:
        assert 0 <= stepup_score(cost, 100) <= STEPUP_BUDGET


def test_stepup_aggregate_all_full_is_budget():
    assert stepup_aggregate([(80, 100), (100, 100), (50, 100)]) == STEPUP_BUDGET


def test_stepup_aggregate_invalid_case_drags_mean():
    # one full (ratio 1), one invalid (ratio 0) -> mean 0.5 -> half budget.
    assert stepup_aggregate([(80, 100), (None, 100)]) == STEPUP_BUDGET // 2


def test_stepup_aggregate_empty_is_zero():
    assert stepup_aggregate([]) == 0


# ---------------------------------------------------------------------------
# Ranking (ties share rank)
# ---------------------------------------------------------------------------

def test_rank_ties_share_then_skip():
    ranks = rank_by_score({"a": 900, "b": 900, "c": 800, "d": 700})
    assert ranks == {"a": 1, "b": 1, "c": 3, "d": 4}


# ---------------------------------------------------------------------------
# A realistic end-to-end: total never exceeds 1,000,000
# ---------------------------------------------------------------------------

def test_full_problemset_caps_at_million():
    stepup = stepup_score(80, 100)           # full Step Up = 1,000,000
    field = [10, 10, 20, 30]                 # I (10) am tied-best
    per_case = [challenge_case_score(10, field) for _ in range(10)]
    challenge = challenge_score(per_case)    # <= 1,000,000
    # each part is out of 1e6; the WEIGHTED total (2:8) never exceeds 1e6.
    assert weighted_total(stepup, challenge) <= 1_000_000
    assert stepup == STEPUP_BUDGET == 1_000_000


def test_weighted_total_2_8_blend():
    assert weighted_total(1_000_000, 1_000_000) == 1_000_000   # both full -> full
    assert weighted_total(1_000_000, 0) == 200_000             # Step Up only -> 20%
    assert weighted_total(0, 1_000_000) == 800_000             # Challenge only -> 80%


# ---------------------------------------------------------------------------
# Tiny fallback runner so the file works without pytest installed.
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import types

    tests = [
        (name, obj)
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and isinstance(obj, types.FunctionType)
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ok   {name}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {name}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ERR  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
