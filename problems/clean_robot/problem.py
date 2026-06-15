"""
Parametric "청소 로봇" (cleaning robot) — the authorable version of example_clean.

Same rules as example_clean, but the grid/dust RANGES are per-contest params (from
the admin authoring form), and the generator is a BIT-EXACT port of the frontend
`genClean` (web/lib/sim.ts: mulberry32 PRNG + Fisher-Yates). That guarantees the
in-browser simulator / preview show the SAME grid the server grades.

generate(seed, params) -> str       params = {hMin,hMax,wMin,wMax,dMin,dMax}
check(input, output)   -> (cost|None, valid, message)
reference_cost(input)  -> float
"""

from __future__ import annotations

import math
from dataclasses import dataclass

MOVES = {"U": (-1, 0), "D": (1, 0), "L": (0, -1), "R": (0, 1)}

# defaults match web/lib/sim.ts DEFAULT_GEN (used when a param is absent).
DEFAULT_PARAMS = {"hMin": 6, "hMax": 9, "wMin": 6, "wMax": 9, "dMin": 6, "dMax": 10}


@dataclass
class Instance:
    h: int
    w: int
    start: tuple[int, int]
    dirty: frozenset[tuple[int, int]]


META = {
    "id": "clean-robot",
    "kind": "stepup",
    "title": "청소 로봇 (파라미터)",
    "time_limit_ms": 2000,
    "memory_limit_mb": 1024,
    "simulator_key": "clean",
    "stepup_budget": 1000000,
    "given_seeds": [101, 102, 103],     # overridden per-contest via scoring_config
    "gen_params": DEFAULT_PARAMS,
    # Authorable per-instance features. Step Up cases set EXACT values; Challenge
    # subtasks set RANGES over them. The admin form renders an input per entry and
    # feeds the values to generate() as params (exact -> a pinned single-value range).
    "feature_schema": [
        {"key": "h", "label": "행 N", "min": 2, "max": 50, "default": 8},
        {"key": "w", "label": "열 M", "min": 2, "max": 50, "default": 8},
        {"key": "dust", "label": "먼지 수", "min": 1, "max": 2400, "default": 8},
    ],
    "statement_md": (
        "## 청소 로봇\n\n로봇이 H×W 격자의 좌상단(0,0)에서 시작합니다. `*`는 먼지입니다.\n"
        "`U/D/L/R`로 이동하며 칸을 밟으면 청소됩니다. **모든 먼지를 청소**하는 이동 문자열을 "
        "출력하되 **이동 수(=비용)를 최소화**하세요.\n\n격자 밖으로 나가거나 먼지를 남기면 무효(0점)입니다."
    ),
}


# --- generator: BIT-EXACT port of web/lib/sim.ts genClean -------------------
# JS uses unsigned 32-bit (>>> and bitwise only) + IEEE-754 doubles, so masking to
# 0xFFFFFFFF + Python float division reproduces the exact same sequence.

def _imul(x: int, y: int) -> int:
    return (x * y) & 0xFFFFFFFF


def _mulberry32(seed: int):
    a = seed & 0xFFFFFFFF

    def rnd() -> float:
        nonlocal a
        a = (a + 0x6D2B79F5) & 0xFFFFFFFF
        t = _imul(a ^ (a >> 15), 1 | a)
        t = ((t + _imul(t ^ (t >> 7), 61 | t)) & 0xFFFFFFFF) ^ t
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296.0

    return rnd


def _pin(p: dict, lokey: str, hikey: str, val) -> None:
    # a feature value is either EXACT (scalar -> pinned single value, Step Up cases) or a
    # RANGE [lo, hi] (Challenge subtasks: the seed picks a value within it).
    if isinstance(val, (list, tuple)) and len(val) == 2:
        p[lokey], p[hikey] = int(val[0]), int(val[1])
    else:
        p[lokey] = p[hikey] = int(val)


def _merged_params(params: dict | None) -> dict:
    p = dict(DEFAULT_PARAMS)
    if params:
        # per-feature value: scalar (exact) or [lo, hi] (range). h/w/dust map to the
        # generator's hMin/hMax, wMin/wMax, dMin/dMax.
        if params.get("h") is not None:
            _pin(p, "hMin", "hMax", params["h"])
        if params.get("w") is not None:
            _pin(p, "wMin", "wMax", params["w"])
        if params.get("dust") is not None:
            _pin(p, "dMin", "dMax", params["dust"])
        for k in DEFAULT_PARAMS:                  # explicit ranges (hMin/hMax/...) still win
            if params.get(k) is not None:
                p[k] = int(params[k])
    return p


def make_instance(seed: int, params: dict | None = None) -> Instance:
    p = _merged_params(params)
    rnd = _mulberry32(seed)

    def ri(lo: int, hi: int) -> int:
        return lo + math.floor(rnd() * (hi - lo + 1))

    h = ri(p["hMin"], p["hMax"])
    w = ri(p["wMin"], p["wMax"])
    cells = [(r, c) for r in range(h) for c in range(w) if not (r == 0 and c == 0)]
    for i in range(len(cells) - 1, 0, -1):       # Fisher-Yates (same call order as JS)
        j = math.floor(rnd() * (i + 1))
        cells[i], cells[j] = cells[j], cells[i]
    k = min(ri(p["dMin"], p["dMax"]), len(cells))
    dirty = frozenset(cells[:k])
    return Instance(h, w, (0, 0), dirty)


def generate(seed: int, params: dict | None = None) -> str:
    inst = make_instance(seed, params)
    grid = [["." for _ in range(inst.w)] for _ in range(inst.h)]
    for (r, c) in inst.dirty:
        grid[r][c] = "*"
    lines = [f"{inst.h} {inst.w}", f"{inst.start[0]} {inst.start[1]}"]
    lines += ["".join(row) for row in grid]
    return "\n".join(lines) + "\n"


def parse(input_text: str) -> Instance:
    it = iter(input_text.split("\n"))
    h, w = map(int, next(it).split())
    sr, sc = map(int, next(it).split())
    dirty = set()
    for r in range(h):
        row = next(it)
        for c, ch in enumerate(row):
            if ch == "*":
                dirty.add((r, c))
    return Instance(h, w, (sr, sc), frozenset(dirty))


# --- checker + reference cost (identical rules to example_clean) ------------

def check(input_text: str, output_text: str) -> tuple[float | None, bool, str]:
    inst = parse(input_text)
    moves = [ch for ch in output_text.strip() if not ch.isspace()]
    if len(moves) > inst.h * inst.w * (len(inst.dirty) + 1) * 4:
        return None, False, "too many moves"
    r, c = inst.start
    visited = {(r, c)}
    for m in moves:
        if m not in MOVES:
            return None, False, f"bad move char {m!r}"
        dr, dc = MOVES[m]
        r, c = r + dr, c + dc
        if not (0 <= r < inst.h and 0 <= c < inst.w):
            return None, False, "moved off the grid"
        visited.add((r, c))
    if not inst.dirty.issubset(visited):
        return None, False, f"{len(inst.dirty - visited)} dirty cell(s) left"
    return float(len(moves)), True, "ok"


def _manhattan(a: tuple[int, int], b: tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def reference_cost(input_text: str) -> float:
    inst = parse(input_text)
    pos = inst.start
    remaining = set(inst.dirty)
    total = 0
    while remaining:
        nxt = min(remaining, key=lambda d: _manhattan(pos, d))
        total += _manhattan(pos, nxt)
        pos = nxt
        remaining.discard(nxt)
    return float(total)


def sample_solution(input_text: str) -> str:
    """A valid reference OUTPUT (nearest-neighbour tour — the same order reference_cost
    scores), so the statement can show a full-marks example. Optional module contract:
    the API surfaces this as the example output when a problem provides it."""
    inst = parse(input_text)
    pos = inst.start
    remaining = set(inst.dirty)

    def _walk(a: tuple[int, int], b: tuple[int, int]) -> str:
        r, c = a
        tr, tc = b
        s = ("D" * (tr - r)) if tr >= r else ("U" * (r - tr))
        s += ("R" * (tc - c)) if tc >= c else ("L" * (c - tc))
        return s

    out = []
    while remaining:
        nxt = min(remaining, key=lambda d: _manhattan(pos, d))
        out.append(_walk(pos, nxt))
        pos = nxt
        remaining.discard(nxt)
    return "".join(out)
