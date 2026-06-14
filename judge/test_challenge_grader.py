"""End-to-end Challenge pipeline test (no sandbox): generate -> run -> check ->
relative standings. Uses the example problem + a local subprocess runner.

Run:  python judge/test_challenge_grader.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import types

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "problems", "example_clean"))

from challenge_grader import costs_by_seed, grade_case, grade_cases  # noqa: E402
from registry import load_problem  # noqa: E402
from standings import compute_challenge_scores  # noqa: E402

PROBLEM = load_problem("example_clean")
SEEDS = [101, 102, 103]
EX = os.path.join(os.path.dirname(__file__), "..", "problems", "example_clean")


def make_runner(script: str):
    path = os.path.join(EX, script)
    def run(input_text: str):
        t0 = time.perf_counter()
        proc = subprocess.run([sys.executable, path], input=input_text.encode(),
                              capture_output=True, timeout=10)
        ms = int((time.perf_counter() - t0) * 1000)
        if proc.returncode != 0:
            return ("", ms, "RE")
        return (proc.stdout.decode(), ms, "OK")
    return run


def test_greedy_all_valid():
    outs = grade_cases(PROBLEM, SEEDS, make_runner("sample_solution.py"))
    assert all(o.valid and o.cost is not None and o.verdict == "OK" for o in outs)
    assert all(o.runtime_ms >= 0 for o in outs)


def test_greedy_beats_naive_relative():
    g = grade_cases(PROBLEM, SEEDS, make_runner("sample_solution.py"))
    n = grade_cases(PROBLEM, SEEDS, make_runner("naive_solution.py"))
    raw = {"greedy": costs_by_seed(g), "naive": costs_by_seed(n)}
    sc = compute_challenge_scores(raw, SEEDS)
    assert sc["greedy"] >= sc["naive"]
    # greedy is at least as good on every case
    gc = costs_by_seed(g); nc = costs_by_seed(n)
    assert all(gc[s] <= nc[s] for s in SEEDS)


def test_runtime_error_is_invalid():
    def boom(_inp):
        return ("", 5, "RE")
    o = grade_case(PROBLEM, SEEDS[0], boom)
    assert not o.valid and o.cost is None and o.verdict == "RE"


def test_illegal_output_is_invalid():
    def bad_output(_inp):
        return ("U", 5, "OK")          # 'U' walks off the grid -> illegal
    o = grade_case(PROBLEM, SEEDS[0], bad_output)
    assert not o.valid and o.cost is None and o.verdict == "ILLEGAL"


def test_internal_verdict_is_surfaced_distinctly():
    # A sandbox/infra failure must NOT be folded into the invalid/ILLEGAL bucket:
    # the worker keys re-queue off verdict=='INTERNAL', so it must survive grading.
    def infra_flake(_inp):
        return ("", 7, "INTERNAL")
    o = grade_case(PROBLEM, SEEDS[0], infra_flake)
    assert o.verdict == "INTERNAL" and not o.valid and o.cost is None


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
