"""
Time-limit calibration to the NYPC reference machine.

THE PROBLEM: the real NYPC judge is an AWS c7a.2xlarge — AMD EPYC 9R14 (Zen4
"Genoa") @ 3.7 GHz, dedicated x86 cores, 2 s / 1024 MB per run. We cannot rent
that silicon 24/7 for free, and slower/free machines would make the 2 s limit
mean something different (heuristic scores are single-thread-bound).

THE FIX (what real judges do — DMOJ per-judge multiplier, Codeforces/ICPC tuning):
don't match the CPU, match the *computational budget*. Measure how much slower
this grader is than c7a on a fixed benchmark suite, then scale the CPU-time limit:

    effective_cpu_limit = base_limit (2 s) x factor          factor = our_time / c7a_time

So a solution that fits in 2 s on c7a gets the equivalent CPU budget here. Always
measure CPU time (isolate --time), never wall time. See docs/GRADING_ENV.md for the
full strategy, the hardware-tuning checklist, and how to get the real c7a cheaply.

This module is pure-stdlib so the factor math is unit-tested without a toolchain.
"""

from __future__ import annotations

import os
import statistics

DEFAULT_FACTOR = 1.0

# Median CPU-time (ms) per calibration kernel measured ONCE on a real c7a.large
# (single-thread-identical to c7a.2xlarge) with `g++ -O2 -std=gnu++20`, performance
# governor, turbo OFF. Run judge/calib_kernel.cpp there and paste the numbers here.
# Left as None until measured — see docs/GRADING_ENV.md §"Calibration recipe".
NYPC_BASELINE_MS: dict[str, float | None] = {
    "compute": None,   # tight integer/branch loop
    "memory": None,    # strided array sweep (bandwidth-sensitive)
    "sort": None,      # std::sort / hashing pass
    "simd": None,      # float SIMD loop (x86 AVX vs others)
}

# Rough starting factors for common FREE machines vs c7a (from research; replace
# with a measured factor before any real contest). factor = c7a_speed / machine_speed.
REFERENCE_FACTORS = {
    "c7a": 1.00,                 # the target itself (spot via credits)
    "github-actions-x86": 1.5,   # EPYC 7763 Milan runners (~0.68x) — free public repos
    "oracle-a1-arm": 1.9,        # Ampere A1 (~0.52x) — ARM, samples only (not scored x86)
}


def compute_factor(local_ms: dict[str, float],
                   baseline_ms: dict[str, float | None] = NYPC_BASELINE_MS) -> float:
    """factor = median over kernels of (local_time / c7a_time). Falls back to
    DEFAULT_FACTOR if the baseline hasn't been measured yet."""
    ratios = [
        local_ms[k] / b
        for k, b in baseline_ms.items()
        if b and local_ms.get(k)
    ]
    return statistics.median(ratios) if ratios else DEFAULT_FACTOR


def effective_time_ms(base_ms: int, factor: float, *,
                      safety: float = 1.05, cap_ms: int | None = None) -> int:
    """Scale the c7a-equivalent CPU budget to this machine. `safety` adds a small
    margin so borderline-accepted solutions aren't unfairly TLE'd; `cap_ms` bounds
    the worst case so a mis-measured factor can't hang the queue."""
    eff = int(round(base_ms * max(factor, 0.0) * safety))
    if cap_ms is not None:
        eff = min(eff, cap_ms)
    return max(eff, 1)


def load_factor() -> float:
    """Read the measured factor from DMPC_CALIBRATION_FACTOR (set per grader host).
    Defaults to 1.0 (no scaling) so a misconfigured grader fails *toward the real
    2 s limit* rather than silently handing out a huge budget."""
    raw = os.environ.get("DMPC_CALIBRATION_FACTOR", "").strip()
    if not raw:
        return DEFAULT_FACTOR
    try:
        f = float(raw)
    except ValueError:
        return DEFAULT_FACTOR
    return f if f > 0 else DEFAULT_FACTOR
