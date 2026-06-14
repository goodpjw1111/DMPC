"""
Example Step Up problem — "청소 로봇" (cleaning robot).

A reference problem to exercise the whole Phase-1 pipeline:
    generator (seed -> input) -> solution -> checker (output -> cost) -> scoring.

PROBLEM
-------
A robot starts at the top-left of an H×W grid. Some cells are dirty (`*`).
The robot moves with U/D/L/R; it cleans a cell by stepping on it. Output a move
string that cleans EVERY dirty cell. Minimize the number of moves (= cost).
A move off the grid, or leaving any dirty cell uncleaned, makes the output
INVALID (no score on that case). Lower cost is better -> minimization.

This is genuinely heuristic: choosing the visiting order is a small TSP, so a
naive order loses to a good one, and beating the reference order earns full marks.

A problem is just: a generator, a checker, a reference-cost function, and the
metadata below. The grader calls these; nothing here trusts user output.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

# Move deltas in (row, col). Row 0 is the top.
MOVES = {"U": (-1, 0), "D": (1, 0), "L": (0, -1), "R": (0, 1)}


@dataclass
class Instance:
    h: int
    w: int
    start: tuple[int, int]
    dirty: frozenset[tuple[int, int]]


# --- metadata (mirrors a row in the `problems` table) ----------------------
META = {
    "id": "example-clean",
    "kind": "stepup",
    "title": "청소 로봇 (Step Up 예시)",
    "time_limit_ms": 1000,
    "memory_limit_mb": 256,
    "simulator_key": "clean",        # web/simulators/clean.* renders this
    "stepup_budget": 1000000,
    # The Step Up data is FIXED and given up-front (small limits). These seeds
    # are the "주어진 데이터"; cost_ref per case = the reference order's length.
    "given_seeds": [101, 102, 103],
    "statement_md": (
        "## 청소 로봇\n\n로봇이 H×W 격자의 좌상단(0,0)에서 시작합니다. `*`는 먼지입니다.\n"
        "`U/D/L/R`로 이동하며 칸을 밟으면 청소됩니다. **모든 먼지를 청소**하는 "
        "이동 문자열을 출력하되 **이동 수(=비용)를 최소화**하세요.\n\n"
        "격자 밖으로 나가거나 먼지를 남기면 해당 케이스는 무효(0점)입니다."
    ),
}


# --- generator: seed -> deterministic input --------------------------------

def make_instance(seed: int) -> Instance:
    rng = random.Random(seed)              # fixed PRNG => reproducible from seed
    h = rng.randint(6, 9)
    w = rng.randint(6, 9)
    start = (0, 0)
    cells = [(r, c) for r in range(h) for c in range(w) if (r, c) != start]
    k = rng.randint(6, 10)
    dirty = frozenset(rng.sample(cells, k))
    return Instance(h, w, start, dirty)


def generate(seed: int, params: dict | None = None) -> str:
    # `params` is accepted (and ignored) so every problem module shares one generate
    # signature; example_clean uses FIXED ranges. Parametric problems (clean_robot) use it.
    inst = make_instance(seed)
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


# --- checker: (input, output) -> (cost, valid) -----------------------------

def check(input_text: str, output_text: str) -> tuple[float | None, bool, str]:
    """Returns (cost, valid, message). cost=None when invalid (=> 0 points)."""
    inst = parse(input_text)
    moves = [ch for ch in output_text.strip() if not ch.isspace()]

    # hard cap to stop pathological huge outputs from eating the checker
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
        missing = len(inst.dirty - visited)
        return None, False, f"{missing} dirty cell(s) left"
    return float(len(moves)), True, "ok"


# --- reference cost: the baseline a full-marks solution must match/beat -----

def _manhattan(a: tuple[int, int], b: tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def reference_cost(input_text: str) -> float:
    """Nearest-neighbour tour length from the start over all dirty cells.

    INVARIANT (Step Up full-marks guarantee): this is the cost of an ACHIEVABLE
    reference solution (sample_solution.py emits exactly this tour), so a player
    can always reach full marks by reproducing it. Beating it (e.g. 2-opt) also
    caps at full. Never set cost_ref below an achievable cost."""
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
