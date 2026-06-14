"""Tests for the contest scheduling rule (pure, stdlib)."""

from __future__ import annotations

import os
import sys
import types
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from schedule import KST, contest_window, eval_times, final_eval_time  # noqa: E402


def test_starts_next_day_0900_kst():
    start, end = contest_window(date(2026, 6, 10))
    assert start == datetime(2026, 6, 11, 9, 0, tzinfo=KST)


def test_ends_three_days_after_start_0900():
    start, end = contest_window(date(2026, 6, 10))
    assert end == datetime(2026, 6, 14, 9, 0, tzinfo=KST)
    assert end - start == timedelta(days=3)


def test_eval_times_are_0900_and_1800():
    start, end = contest_window(date(2026, 6, 10))
    evs = eval_times(start, end)
    for t, _ in evs:
        assert t.hour in (9, 18) and t.minute == 0
        assert start <= t <= end


def test_seven_evals_last_is_final():
    start, end = contest_window(date(2026, 6, 10))
    evs = eval_times(start, end)
    # 09&18 on days 11,12,13 (=6) + final 09:00 on day 14 (=1) = 7
    assert len(evs) == 7
    finals = [t for t, f in evs if f]
    assert finals == [end]                       # exactly one final, at end
    assert evs[-1] == (end, True)
    assert evs[0][0] == start and evs[0][1] is False


def test_no_eval_after_end():
    start, end = contest_window(date(2026, 6, 10))
    evs = eval_times(start, end)
    # 18:00 on the final day is AFTER end (09:00) -> excluded
    assert all(t <= end for t, _ in evs)


def test_final_eval_time_is_end():
    start, end = contest_window(date(2026, 1, 1))
    assert final_eval_time(start, end) == end


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
