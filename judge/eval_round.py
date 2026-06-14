"""
Evaluation-round orchestration helpers (pure, stdlib).

Each interim/final evaluation runs on a FRESH, hidden seed set. The seeds must be:
  * reproducible (so a crashed round re-runs identically -> idempotent), and
  * unpredictable to contestants until the round runs (so they can't overfit).
We derive them deterministically from a server-side secret + the round identity,
so storing just the (contest, date, slot) lets us regenerate the exact set, yet
nobody can predict it without the secret.

Used by the 09:00/18:00 KST scheduler (see api/app/schedule.py for the timing).
"""

from __future__ import annotations

import hashlib

# Slots within a contest day; the final evaluation reuses the "final" slot.
SLOT_0900 = "0900"
SLOT_1800 = "1800"
SLOT_FINAL = "final"


def round_idem_key(contest_id: str, date_iso: str, slot: str) -> str:
    """Stable identity for an evaluation round; UNIQUE(contest, idem_key) makes a
    re-fired/retried round a no-op instead of a double-count."""
    return f"{contest_id}:{date_iso}:{slot}"


def derive_seeds(secret: str, idem_key: str, problem_key: str,
                 k: int, lo: int, hi: int) -> list[int]:
    """k distinct seeds in [lo, hi], deterministic from (secret, round, problem).

    Same inputs -> same seeds (idempotent re-run). Without `secret`, the set is
    unpredictable. If the range is smaller than k, returns as many distinct as fit.
    """
    if hi < lo or k <= 0:
        return []
    span = hi - lo + 1
    seeds: list[int] = []
    seen: set[int] = set()
    i = 0
    limit = k * 200 + 1000
    base = f"{secret}|{idem_key}|{problem_key}|"
    while len(seeds) < k and i < limit:
        digest = hashlib.sha256(f"{base}{i}".encode()).digest()
        s = lo + (int.from_bytes(digest[:8], "big") % span)
        if s not in seen:
            seen.add(s)
            seeds.append(s)
        i += 1
    return seeds
