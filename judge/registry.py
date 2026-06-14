"""
Problem registry — load a problem package by key.

A problem lives at `problems/<key>/problem.py` and exposes:
    generate(seed) -> str          # seed -> input text (deterministic)
    check(input, output) -> (cost|None, valid, message)
    reference_cost(input) -> float # achievable reference (Step Up full-marks bound)
    META: dict                     # id, kind, title, limits, given_seeds, budgets...

The grader (Step Up scoring here; Challenge worker on the grader host) and the API
both load problems through this single entry point. Modules are cached after first
load. Pure stdlib so it is unit-testable without the web stack.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from types import ModuleType

PROBLEMS_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "problems"))

_cache: dict[str, ModuleType] = {}


def list_problem_keys() -> list[str]:
    if not os.path.isdir(PROBLEMS_DIR):
        return []
    return sorted(
        name for name in os.listdir(PROBLEMS_DIR)
        if os.path.isfile(os.path.join(PROBLEMS_DIR, name, "problem.py"))
    )


# keys an admin-authored problem may override on top of the filesystem module's META.
# (The generator/checker CODE still comes from the pinned module; only these scalars
# are per-contest.) Step Up: given_seeds + stepup_budget. Challenge config lives in
# scoring_config directly (seed_range/round_seeds/cost_eps) and is read by the worker.
_META_OVERRIDE_KEYS = ("given_seeds", "stepup_budget", "gen_params")


def effective_meta(module_meta: dict, scoring_config: dict | None) -> dict:
    """Module META with the per-problem scoring_config overrides applied. Lets an
    admin-authored contest reuse a built-in generator/checker (problem_key) while
    choosing its own mission seeds and budget — the single source the read/grade
    paths consult for Step Up missions."""
    eff = dict(module_meta)
    cfg = scoring_config or {}
    for k in _META_OVERRIDE_KEYS:
        v = cfg.get(k)
        if v is not None:
            eff[k] = v
    return eff


def load_problem(key: str) -> ModuleType:
    if key in _cache:
        return _cache[key]
    path = os.path.join(PROBLEMS_DIR, key, "problem.py")
    if not os.path.isfile(path):
        raise KeyError(f"unknown problem: {key!r}")
    spec = importlib.util.spec_from_file_location(f"dmpc_problem_{key}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load problem {key!r}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod          # dataclass/typing need the module registered
    spec.loader.exec_module(mod)
    for attr in ("generate", "check", "reference_cost", "META"):
        if not hasattr(mod, attr):
            raise AttributeError(f"problem {key!r} missing {attr!r}")
    _cache[key] = mod
    return mod
