"""
Shared isolate-backed grading step (Linux grader host only).

Compiles a submission once inside an isolate box, then runs the trusted per-problem
checker over a given seed list via judge/challenge_grader.grade_cases (the runner is
injected, so the same pipeline is unit-tested without a sandbox). Used by BOTH the
sample-grading worker (worker.py) and the round evaluator (grade_round.py), so the
sandbox call lives in one place.

Raises IsolateInternalError if isolate itself fails/hangs (infra, not the user's
code) — callers re-queue (sample) or abort+reschedule the round (round), never
penalize the contestant.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "judge"))
from challenge_grader import grade_cases  # noqa: E402
from sandbox import (  # noqa: E402
    CaseInput, IsolateBox, IsolateInternalError, Limits, Verdict,
    compile_solution, run_case,
)


def run_over_seeds(box_id: int, problem, lang, source: bytes, seeds: list[int],
                   base: Limits, data_bin: bytes | None = None,
                   gen_params: dict | None = None):
    """Compile once in a fresh box, then grade each seed. Returns
    (outcomes | None, compiled_ok, compile_log). If the submission included a
    data.bin (<=10MB), it is placed in the box as "data.bin" so the program can read
    it via file I/O. Raises IsolateInternalError on sandbox/infra failure."""
    with IsolateBox(box_id) as box:
        compiled = compile_solution(box, lang, source)
        if compiled.verdict == Verdict.INTERNAL:
            detail = (compiled.message or compiled.stderr_tail or "").strip()
            raise IsolateInternalError(
                "isolate failure during compile" + (f": {detail[:300]}" if detail else ""))
        if compiled.verdict == Verdict.COMPILE_ERROR:
            return None, False, compiled.stderr_tail

        if data_bin:
            box.put("data.bin", data_bin)        # available to the solution as ./data.bin

        def runner(input_text: str):
            res = run_case(box, lang, CaseInput(seed=0, stdin=input_text.encode()), base)
            if res.verdict == Verdict.INTERNAL:
                # surface as an exception so callers don't fold infra into a None cost.
                detail = (res.message or "").strip()
                raise IsolateInternalError(
                    "isolate failure during run" + (f": {detail[:300]}" if detail else ""))
            return (res.stdout.decode("utf-8", "replace"), res.time_ms, res.verdict)

        return grade_cases(problem, seeds, runner, gen_params), True, ""
