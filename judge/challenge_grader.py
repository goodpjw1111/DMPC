"""
Challenge grading pipeline: generate -> run (sandbox) -> checker -> per-case cost.

The untrusted-execution step is INJECTED as `run(input) -> (stdout, runtime_ms,
verdict)`. In production the worker passes a runner backed by judge/sandbox.py
(isolate, no-net, 2s/1024MB). In tests we pass a plain local runner, so the whole
pipeline (generator -> output -> trusted checker -> cost) is verifiable without a
sandbox. The per-case costs then feed standings.compute_challenge_scores for the
relative leaderboard.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from types import ModuleType

# run(input_text) -> (stdout, runtime_ms, verdict). verdict: OK|TLE|MLE|RE|CE
SourceRunner = Callable[[str], tuple[str, int, str]]


@dataclass(frozen=True)
class CaseOutcome:
    seed: int
    cost: float | None     # None when the run failed or output was illegal
    valid: bool
    verdict: str           # OK | TLE | MLE | RE | CE | ILLEGAL
    runtime_ms: int


def grade_case(problem: ModuleType, seed: int, run: SourceRunner,
               gen_params: dict | None = None) -> CaseOutcome:
    inp = problem.generate(seed, gen_params)
    stdout, runtime_ms, verdict = run(inp)
    if verdict != "OK":
        return CaseOutcome(seed, None, False, verdict, runtime_ms)
    cost, valid, _msg = problem.check(inp, stdout)
    return CaseOutcome(seed, cost if valid else None, valid,
                       "OK" if valid else "ILLEGAL", runtime_ms)


def grade_cases(problem: ModuleType, seeds: list[int], run: SourceRunner,
                gen_params: dict | None = None) -> list[CaseOutcome]:
    """Grade one submission across all seeds. Compile once before calling (the
    runner is expected to already hold a compiled artifact). `gen_params` are the
    per-contest generator params (admin-authored parametric problems)."""
    return [grade_case(problem, s, run, gen_params) for s in seeds]


def costs_by_seed(outcomes: list[CaseOutcome]) -> dict[int, float | None]:
    """Shape one submission's outcomes for standings.compute_challenge_scores."""
    return {o.seed: o.cost for o in outcomes}
