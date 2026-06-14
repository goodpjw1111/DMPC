"""Tests for the time-limit calibration math (pure, stdlib)."""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(__file__))
from calibration import (  # noqa: E402
    DEFAULT_FACTOR,
    compute_factor,
    effective_time_ms,
    load_factor,
)

BASE = {"compute": 100.0, "memory": 200.0, "sort": 50.0, "simd": 80.0}


def test_factor_is_one_when_identical():
    assert compute_factor(dict(BASE), BASE) == 1.0


def test_factor_is_median_ratio():
    # local is 2x slower on every kernel -> factor 2.0
    local = {k: v * 2 for k, v in BASE.items()}
    assert compute_factor(local, BASE) == 2.0


def test_factor_uses_median_not_outlier():
    # three kernels ~1.5x, one wild outlier 10x -> median stays ~1.5
    local = {"compute": 150, "memory": 300, "sort": 75, "simd": 800}
    assert 1.4 <= compute_factor(local, BASE) <= 1.6


def test_factor_falls_back_when_baseline_unmeasured():
    unmeasured = {"compute": None, "memory": None, "sort": None, "simd": None}
    assert compute_factor({"compute": 100}, unmeasured) == DEFAULT_FACTOR


def test_effective_time_scales_and_adds_safety():
    # 2000ms base, 1.5x slower, default 1.05 safety -> 3150
    assert effective_time_ms(2000, 1.5) == 3150


def test_effective_time_factor_one_keeps_budget():
    assert effective_time_ms(2000, 1.0, safety=1.0) == 2000


def test_effective_time_respects_cap():
    assert effective_time_ms(2000, 9.0, cap_ms=8000) == 8000


def test_effective_time_never_zero():
    assert effective_time_ms(2000, 0.0) >= 1


def test_load_factor_default(monkeypatch=None):
    os.environ.pop("DMPC_CALIBRATION_FACTOR", None)
    assert load_factor() == DEFAULT_FACTOR


def test_load_factor_reads_env():
    os.environ["DMPC_CALIBRATION_FACTOR"] = "1.8"
    try:
        assert load_factor() == 1.8
    finally:
        os.environ.pop("DMPC_CALIBRATION_FACTOR", None)


def test_load_factor_rejects_garbage_and_nonpositive():
    for bad in ["abc", "-2", "0"]:
        os.environ["DMPC_CALIBRATION_FACTOR"] = bad
        try:
            assert load_factor() == DEFAULT_FACTOR
        finally:
            os.environ.pop("DMPC_CALIBRATION_FACTOR", None)


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
