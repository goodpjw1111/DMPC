"""Tests for relative-scoring standings recompute (pure, stdlib)."""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(__file__))
from standings import (  # noqa: E402
    compute_challenge_scores, compute_standings, per_case_ranks,
)
import scoring  # noqa: E402


def test_best_cost_gets_top_challenge_score():
    # 3 users, 2 seeds. user a is strictly best on both -> highest challenge score.
    raw = {
        "a": {1: 10.0, 2: 10.0},
        "b": {1: 20.0, 2: 20.0},
        "c": {1: 30.0, 2: 30.0},
    }
    sc = compute_challenge_scores(raw, [1, 2])
    assert sc["a"] > sc["b"] > sc["c"]
    assert sc["a"] == scoring.CHALLENGE_BUDGET           # rank 1 on every case -> full


def test_invalid_case_is_zero_not_floor():
    raw = {"a": {1: None}, "b": {1: 5.0}}   # a invalid on the only case
    sc = compute_challenge_scores(raw, [1])
    assert sc["a"] == 0
    assert sc["b"] == scoring.CHALLENGE_BUDGET


def test_missing_seed_treated_as_invalid():
    raw = {"a": {1: 5.0}, "b": {}}          # b has no result for seed 1
    sc = compute_challenge_scores(raw, [1])
    assert sc["b"] == 0 and sc["a"] == scoring.CHALLENGE_BUDGET


def test_ties_get_equal_scores():
    raw = {"a": {1: 7.0}, "b": {1: 7.0}}
    sc = compute_challenge_scores(raw, [1])
    assert sc["a"] == sc["b"]


def test_standings_total_and_ranks():
    # each part out of 1e6; total = 2:8 weighted blend.
    stepup = {"a": 1000000, "b": 500000, "c": 0}
    challenge = {"a": 1000000, "b": 500000, "c": 1000000}
    st = compute_standings(stepup, challenge)
    by = {s.user_id: s for s in st}
    assert by["a"].total == 1000000 and by["a"].rank == 1   # (2·1e6+8·1e6)/10
    assert by["c"].total == 800000 and by["c"].rank == 2    # (0+8·1e6)/10
    assert by["b"].total == 500000 and by["b"].rank == 3    # (2·5e5+8·5e5)/10
    assert st[0].user_id == "a"                          # sorted best-first


def test_standings_ties_share_rank():
    st = compute_standings({"a": 100, "b": 100}, {"a": 0, "b": 0})
    assert {s.rank for s in st} == {1}                   # both rank 1


def test_per_case_ranks_best_is_one_invalid_last():
    raw = {"a": {1: 10.0}, "b": {1: 5.0}, "c": {1: None}}
    ranks = per_case_ranks(raw, 1)
    assert ranks["b"] == 1                               # lowest cost = rank 1
    assert ranks["a"] == 2
    assert ranks["c"] == 3                               # invalid last


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
