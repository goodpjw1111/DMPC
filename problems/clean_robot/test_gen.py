"""clean_robot.generate = BIT-EXACT port of web/lib/sim.ts genClean.

The JS reference strings below were captured from the actual browser JS engine
(mulberry32 + Fisher-Yates), so this test proves the server grid == the in-browser
simulator/preview grid for the same (seed, params).

Run:  python problems/clean_robot/test_gen.py
"""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(__file__))

import problem  # noqa: E402  (the clean_robot module)

A = {"hMin": 6, "hMax": 9, "wMin": 6, "wMax": 9, "dMin": 6, "dMax": 10}
B = {"hMin": 3, "hMax": 5, "wMin": 4, "wMax": 7, "dMin": 1, "dMax": 4}

JS_FIXTURE = {
    (42, "A"): "8 7\n0 0\n...*...\n*......\n.*.....\n.......\n..*..*.\n*....*.\n..*...*\n......*\n",
    (101, "A"): "6 9\n0 0\n.......*.\n*....*...\n.......**\n.....*...\n*...*..**\n.........\n",
    (7, "B"): "3 4\n0 0\n.*..\n..*.\n.**.\n",
    (2026, "B"): "4 5\n0 0\n.....\n*....\n.....\n.....\n",
}


def test_matches_js_fixture():
    for (seed, key), expected in JS_FIXTURE.items():
        params = A if key == "A" else B
        got = problem.generate(seed, params)
        assert got == expected, f"seed={seed} set={key}\n got={got!r}\n exp={expected!r}"


def test_deterministic():
    assert problem.generate(42, A) == problem.generate(42, A)
    assert problem.generate(2026, B) == problem.generate(2026, B)


def test_params_affect_dims_and_dust():
    out = problem.generate(5, {"hMin": 4, "hMax": 4, "wMin": 6, "wMax": 6, "dMin": 2, "dMax": 2})
    assert out.split("\n")[0] == "4 6"        # fixed dims from tight ranges
    assert out.count("*") == 2                # fixed dust count


def test_default_params_when_none():
    out = problem.generate(101)               # DEFAULT_PARAMS
    h, w = map(int, out.split("\n")[0].split())
    assert 6 <= h <= 9 and 6 <= w <= 9


def test_checker_accepts_reference_and_rejects_off_grid():
    inp = problem.generate(7, B)
    ref = problem.reference_cost(inp)
    assert ref >= 0
    cost, valid, _ = problem.check(inp, "U")          # off the grid at (0,0)
    assert not valid and cost is None


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
