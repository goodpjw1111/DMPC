"""
End-to-end pipeline demo for the example Step Up problem.

  generate(seed) -> run a submission -> check(output) -> cost -> Step Up score

NOTE: this uses a DEV-ONLY local runner (plain subprocess, NO sandbox) just to
show the data flow on any machine. PRODUCTION grading runs the submission through
judge/sandbox.py (isolate, no-net, resource limits) on the dedicated grader host.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "judge"))
from scoring import stepup_aggregate, stepup_ratio  # noqa: E402

import problem  # noqa: E402

HERE = os.path.dirname(__file__)
SOLUTIONS = {
    "sample_solution (greedy)": os.path.join(HERE, "sample_solution.py"),
    "naive_solution (row-major)": os.path.join(HERE, "naive_solution.py"),
}


def dev_run(solution_path: str, stdin_text: str, timeout_s: float = 5.0):
    """DEV-ONLY: run a submission locally. Production uses judge/sandbox.py."""
    t0 = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, solution_path],
        input=stdin_text.encode(), capture_output=True, timeout=timeout_s,
    )
    ms = int((time.perf_counter() - t0) * 1000)
    return proc.stdout.decode("utf-8", "replace"), ms


def main() -> None:
    seeds = problem.META["given_seeds"]
    budget = problem.META["stepup_budget"]
    print(f"\n=== {problem.META['title']}  (Step Up budget {budget:,}) ===")
    print(f"given seeds: {seeds}\n")

    for name, path in SOLUTIONS.items():
        print(f"--- {name} ---")
        print(f"{'seed':>6} {'ref':>5} {'cost':>5} {'ms':>5}  {'valid':>5}  ratio")
        cases = []
        for seed in seeds:
            inp = problem.generate(seed)
            ref = problem.reference_cost(inp)
            out, ms = dev_run(path, inp)
            cost, valid, msg = problem.check(inp, out)
            cases.append((cost, ref))
            shown = "-" if cost is None else f"{int(cost):>5}"
            ratio = stepup_ratio(cost, ref)
            print(f"{seed:>6} {int(ref):>5} {shown} {ms:>5}  {str(valid):>5}  "
                  f"{ratio:.3f}{'' if valid else '  (' + msg + ')'}")
        score = stepup_aggregate(cases, budget=budget)
        pct = 100 * score / budget
        print(f"  => Step Up score: {score:,} / {budget:,}  ({pct:.1f}%)\n")


if __name__ == "__main__":
    main()
