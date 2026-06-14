"""Tests for evaluation-round seed derivation + idempotency keys (pure, stdlib)."""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(__file__))
from eval_round import SLOT_0900, derive_seeds, round_idem_key  # noqa: E402

SECRET = "server-secret-xyz"


def test_idem_key_stable():
    assert round_idem_key("c1", "2026-06-11", SLOT_0900) == "c1:2026-06-11:0900"


def test_seeds_reproducible():
    a = derive_seeds(SECRET, "c1:2026-06-11:0900", "p1", 50, 1, 10**9)
    b = derive_seeds(SECRET, "c1:2026-06-11:0900", "p1", 50, 1, 10**9)
    assert a == b and len(a) == 50


def test_seeds_distinct_and_in_range():
    s = derive_seeds(SECRET, "r", "p", 100, 1000, 2000)
    assert len(s) == 100 and len(set(s)) == 100
    assert all(1000 <= x <= 2000 for x in s)


def test_different_round_or_problem_differ():
    base = derive_seeds(SECRET, "c1:2026-06-11:0900", "p1", 30, 1, 10**9)
    other_round = derive_seeds(SECRET, "c1:2026-06-11:1800", "p1", 30, 1, 10**9)
    other_prob = derive_seeds(SECRET, "c1:2026-06-11:0900", "p2", 30, 1, 10**9)
    assert base != other_round and base != other_prob


def test_secret_changes_seeds():
    a = derive_seeds("secret-A", "r", "p", 30, 1, 10**9)
    b = derive_seeds("secret-B", "r", "p", 30, 1, 10**9)
    assert a != b                         # unpredictable without the secret


def test_small_range_returns_distinct_subset():
    s = derive_seeds(SECRET, "r", "p", 100, 1, 5)   # only 5 possible
    assert len(s) == len(set(s)) <= 5


def test_empty_on_bad_args():
    assert derive_seeds(SECRET, "r", "p", 0, 1, 10) == []
    assert derive_seeds(SECRET, "r", "p", 5, 10, 1) == []


if __name__ == "__main__":
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and isinstance(o, types.FunctionType)]
    failed = 0
    for name, fn in tests:
        try:
            fn(); print(f"  ok   {name}")
        except AssertionError as e:
            failed += 1; print(f"  FAIL {name}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
