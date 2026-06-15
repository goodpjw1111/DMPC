"""
"다오와 배찌의 길찾기" — a Sokoban-variant minimum-cost reach problem (heuristic).

Dao must reach the goal cell in a maze of pushable blocks. Moving into a block pushes
the whole contiguous chain of blocks one step (if the cell after the chain is free);
if the chain ends at a wall/edge the move FAILS but still costs 1. C=2 adds a helper,
Bazzi, who never needs to reach the goal but can push blocks to help; the two move in
strict alternation (Dao, Bazzi, Dao, ...). Lower total cost is better; never reaching
the goal scores a fixed 100,000 penalty cost.

Contract (shared by every problem module):
    generate(seed, params) -> str        params: rows/cols/players/obstacles/blocks
    check(input, output)   -> (cost|None, valid, message)
    reference_cost(input)  -> float      an ACHIEVABLE (non-optimal) cost for Step Up
    sample_solution(input) -> str        a valid (non-optimal) move string (example output)
    META: dict

This module ships only a NON-optimal baseline solver (_baseline). The optimal-cost solver
is deliberately kept OUT of this public file — on a public repo it would let a contestant
copy a top Challenge solution — and lives author-only in solver.py (gitignored). Step Up
stays correct because the reference cost only has to be ACHIEVABLE (submit the reference
output -> full marks), not minimal.

The browser simulator (META.simulator_key = "maze") lets players play the board with
the arrow keys; web/lib/maze.ts mirrors _apply so its cost preview matches scoring.
The server is always the sole judge.
"""

from __future__ import annotations

import random
from collections import deque

WALL, BLOCK, DAO, BAZZI, GOAL, EMPTY = "#", "O", "D", "Z", "G", "."
DIRS = {"U": (-1, 0), "D": (1, 0), "L": (0, -1), "R": (0, 1)}
MISS_COST = 100_000               # cost when the goal is never reached

DEFAULTS = {"rows": 7, "cols": 7, "players": 1, "obstacles": 5, "blocks": 6}
GEN_NODE_CAP = 20_000             # solvability search budget during generation
REF_NODE_CAP = 250_000            # optimal-cost search budget (Step Up reference)


# --- parse / serialize ------------------------------------------------------

class Instance:
    __slots__ = ("R", "C", "P", "walls", "blocks", "dao", "bazzi", "goal")

    def __init__(self, R, C, P, walls, blocks, dao, bazzi, goal):
        self.R, self.C, self.P = R, C, P
        self.walls, self.blocks = walls, blocks
        self.dao, self.bazzi, self.goal = dao, bazzi, goal


def parse(text: str) -> Instance:
    it = iter(text.strip("\n").split("\n"))
    R, C, P = (int(x) for x in next(it).split())
    walls, blocks = set(), set()
    dao = goal = None
    bazzi = (-1, -1)
    for r in range(R):
        row = next(it)
        for c in range(C):
            ch = row[c] if c < len(row) else EMPTY
            if ch == WALL:
                walls.add((r, c))
            elif ch == BLOCK:
                blocks.add((r, c))
            elif ch == DAO:
                dao = (r, c)
            elif ch == BAZZI:
                bazzi = (r, c)
            elif ch == GOAL:
                goal = (r, c)
    return Instance(R, C, P, walls, blocks, dao, bazzi, goal)


def _serialize(R, C, P, walls, blocks, dao, bazzi, goal) -> str:
    grid = [[EMPTY] * C for _ in range(R)]
    for (r, c) in walls:
        grid[r][c] = WALL
    for (r, c) in blocks:
        grid[r][c] = BLOCK
    grid[goal[0]][goal[1]] = GOAL
    if P == 2:
        grid[bazzi[0]][bazzi[1]] = BAZZI
    grid[dao[0]][dao[1]] = DAO        # Dao drawn last (start cell is shown as D)
    lines = [f"{R} {C} {P}"]
    lines += ["".join(row) for row in grid]
    return "\n".join(lines) + "\n"


# --- one move (shared by checker + solver) ----------------------------------

def _apply(R, C, walls, blocks: frozenset, pos, d):
    """Apply one move of a player at `pos` in direction `d`. Returns
    (new_pos, new_blocks_or_same, cost). Players are transparent (only walls/edge and
    blocks interact). A move always costs >= 1 (even a failed one)."""
    dr, dc = d
    nr, nc = pos[0] + dr, pos[1] + dc
    if not (0 <= nr < R and 0 <= nc < C) or (nr, nc) in walls:
        return pos, blocks, 1                      # into wall/edge -> fail, +1
    if (nr, nc) in blocks:
        k, cr, cc = 0, nr, nc
        while (cr, cc) in blocks:                   # length of the contiguous chain
            k += 1
            cr, cc = cr + dr, cc + dc
        if not (0 <= cr < R and 0 <= cc < C) or (cr, cc) in walls:
            return pos, blocks, 1                   # chain ends at wall/edge -> fail, +1
        nb = set(blocks)
        nb.discard((nr, nc))                        # near block vacates the cell Dao enters
        nb.add((cr, cc))                            # far end advances into the free cell
        return (nr, nc), frozenset(nb), 1 + k       # push k blocks: cost 1 + k
    return (nr, nc), blocks, 1                       # empty cell -> move, +1


# --- checker: score a submitted move string ---------------------------------

def check(input_text: str, output_text: str) -> tuple[float | None, bool, str]:
    inst = parse(input_text)
    moves = [ch for ch in output_text.strip() if not ch.isspace()]
    cap = inst.R * inst.C * (len(inst.blocks) + 1) * 8 + 1000
    if len(moves) > cap:
        return None, False, "too many moves"
    for m in moves:
        if m not in DIRS:
            return None, False, f"bad move char {m!r}"
    dao, bazzi, blocks = inst.dao, inst.bazzi, frozenset(inst.blocks)
    if dao == inst.goal:                             # never generated this way, but be safe
        return 0.0, True, "ok"
    cost = 0
    for i, m in enumerate(moves):
        dao_turn = inst.P == 1 or i % 2 == 0
        pos = dao if dao_turn else bazzi
        npos, blocks, c = _apply(inst.R, inst.C, inst.walls, blocks, pos, DIRS[m])
        cost += c
        if dao_turn:
            dao = npos
            if dao == inst.goal:
                return float(cost), True, "ok"
        else:
            bazzi = npos
    return float(MISS_COST), True, "목적지에 도달하지 못했습니다 (100,000)"


# --- baseline solver: a VALID but NON-OPTIMAL solution (gen oracle / Step Up reference) ---
# The optimal-cost solver is intentionally NOT in this public module (it would hand a
# contestant a top Challenge solution to copy). It lives author-only in solver.py
# (gitignored) for verifying optimal cost / tuning difficulty. See the module docstring.

def _baseline(inst: Instance, node_cap: int):
    """COMPLETE but NON-OPTIMAL solver — BFS by move count over (dao, bazzi, blocks, turn).
    Returns (cost, moves) for SOME move string that puts Dao on the goal, or None if none
    is found within node_cap. Used as (1) the generation solvability oracle and (2) the
    Step Up reference (an ACHIEVABLE cost — submitting this output earns full marks).
    Cost counts push-chain length, so fewest-moves != least-cost: this is deliberately
    suboptimal, so copying it does NOT win the relative-scored Challenge."""
    if inst.dao == inst.goal:
        return 0, ""
    start = (inst.dao, inst.bazzi, frozenset(inst.blocks), 0)
    seen = {start}
    q = deque([(start, "", 0)])
    expanded = 0
    while q:
        st, path, cost = q.popleft()
        if st[0] == inst.goal:
            return cost, path
        expanded += 1
        if expanded > node_cap:
            return None
        dao, bazzi, blocks, turn = st
        dao_turn = inst.P == 1 or turn == 0
        mover = dao if dao_turn else bazzi
        for m, d in DIRS.items():
            npos, nblocks, c = _apply(inst.R, inst.C, inst.walls, blocks, mover, d)
            if dao_turn:
                nst = (npos, bazzi, nblocks, 0 if inst.P == 1 else 1)
            else:
                nst = (dao, npos, nblocks, 0)
            if nst in seen:
                continue
            seen.add(nst)
            q.append((nst, path + m, cost + c))
    return None


def reference_cost(input_text: str) -> float:
    """An ACHIEVABLE (non-optimal) cost — full marks at or below this (Step Up). Generation
    verified solvability with the same model, so the baseline finds it; fall back defensively."""
    inst = parse(input_text)
    res = _baseline(inst, REF_NODE_CAP)
    return float(res[0]) if res else float(MISS_COST)


def sample_solution(input_text: str) -> str:
    """A valid (non-optimal) move string — a baseline example output, NOT an optimal one."""
    inst = parse(input_text)
    res = _baseline(inst, REF_NODE_CAP)
    return res[1] if res else ""


# --- generator: solvable-by-construction (add only if still solvable) -------

def _merged(params: dict | None) -> dict:
    p = dict(DEFAULTS)
    if params:
        for k in DEFAULTS:
            if params.get(k) is not None:
                p[k] = params[k]
    return p


def _pick(rng: random.Random, p: dict, key: str) -> int:
    v = p[key]
    if isinstance(v, (list, tuple)) and len(v) == 2:
        lo, hi = int(v[0]), int(v[1])
        return rng.randint(min(lo, hi), max(lo, hi))
    return int(v)


def generate(seed: int, params: dict | None = None) -> str:
    rng = random.Random(seed)
    p = _merged(params)
    R, C = max(3, _pick(rng, p, "rows")), max(3, _pick(rng, p, "cols"))
    P = 2 if _pick(rng, p, "players") >= 2 else 1
    W, B = max(0, _pick(rng, p, "obstacles")), max(0, _pick(rng, p, "blocks"))

    cells = [(r, c) for r in range(R) for c in range(C)]
    rng.shuffle(cells)
    dao, goal = cells[0], cells[1]
    bazzi = cells[2] if P == 2 else (-1, -1)
    occupied = {dao, goal} | ({bazzi} if P == 2 else set())
    walls: set = set()
    blocks: set = set()

    def solvable() -> bool:
        inst = Instance(R, C, P, walls, blocks, dao, bazzi, goal)
        return _baseline(inst, GEN_NODE_CAP) is not None

    def place(target_set: set, count: int) -> None:
        cand = [x for x in cells if x not in walls and x not in blocks and x not in occupied]
        rng.shuffle(cand)
        done, i = 0, 0
        while done < count and i < len(cand):
            x = cand[i]
            i += 1
            target_set.add(x)
            if solvable():            # keep only placements that preserve solvability
                done += 1
            else:
                target_set.discard(x)

    place(walls, W)                   # obstacles first, then pushable blocks
    place(blocks, B)
    return _serialize(R, C, P, walls, blocks, dao, bazzi, goal)


META = {
    "id": "maze-push",
    "kind": "challenge",
    "title": "다오와 배찌의 길찾기",
    "time_limit_ms": 2000,
    "memory_limit_mb": 1024,
    "simulator_key": "maze",          # web/lib/maze.ts + MazeStepSim/MazeChallengeSim
    "stepup_budget": 1000000,
    "given_seeds": [101, 102, 103],
    "gen_params": DEFAULTS,
    "feature_schema": [
        {"key": "rows", "label": "행 R", "min": 3, "max": 30, "default": 7},
        {"key": "cols", "label": "열 W", "min": 3, "max": 30, "default": 7},
        {"key": "players", "label": "플레이어 수 C", "min": 1, "max": 2, "default": 1},
        {"key": "obstacles", "label": "장애물 수", "min": 0, "max": 60, "default": 5},
        {"key": "blocks", "label": "블럭 수", "min": 0, "max": 60, "default": 6},
    ],
    "statement_md": (
        "## 다오와 배찌의 길찾기\n\n"
        "다오(`D`)는 블럭(`O`)이 흩어진 미로에 갇혀 있습니다. **목표 칸(`G`)에 도달**하면 탈출입니다. "
        "이동은 `U`/`D`/`L`/`R` 네 방향이며, 각 이동은 **비용 1** 이 듭니다.\n\n"
        "### 입력\n"
        "```\nR C P\n<R줄의 격자 (각 C칸)>\n```\n"
        "- `R C P` — 행, 열, 플레이어 수(1 또는 2)\n"
        "- 격자 기호: `#` 벽/장애물(고정), `O` 블럭(밀 수 있음), `D` 다오 시작, "
        "`G` 목표, `Z` 배찌 시작(`P=2`일 때만), `.` 빈 칸. **격자 바깥은 벽**으로 취급합니다.\n\n"
        "### 블럭 밀기 규칙\n"
        "- 이동하려는 칸에 블럭이 있으면 **그 방향으로 이어진 블럭들을 한 칸씩 밉니다.** "
        "이어진 블럭이 `k`개면 이 행동의 비용은 **`1 + k`** 입니다 (예: 블럭 3개를 밀면 `1 + 3 = 4`).\n"
        "- **이어진 블럭의 끝이 벽/장애물/격자 밖**이면 밀 수 없어 **이동은 실패**하고 제자리에 머뭅니다. "
        "단, **실패해도 이동 비용 1은 추가**됩니다. (블럭 없이 벽으로 바로 이동해도 실패 + 비용 1.)\n\n"
        "### 출력 / 비용\n"
        "- `U/D/L/R` 로 이루어진 **이동 문자열**을 출력합니다. 다오가 목표에 도달한 시점에서 멈춥니다.\n"
        "- **도달 시 비용 = 그때까지 누적된 총 비용**, **끝까지 도달 못하면 100,000**. 비용이 낮을수록 좋습니다.\n"
        "- 보드는 **항상 해결 가능**하게 주어집니다.\n\n"
        "### C = 2 — 도우미 배찌\n"
        "`P=2`이면 배찌(`Z`)가 등장합니다. 배찌는 목표에 도달할 필요는 없고, **블럭을 밀어 다오를 돕는** 역할입니다. "
        "두 플레이어는 **번갈아 가며**(다오 → 배찌 → 다오 → …) 움직이고, 위 규칙이 동일하게 적용됩니다. "
        "출력 문자열의 짝수번째(0,2,4,…) 문자는 **다오**, 홀수번째(1,3,5,…)는 **배찌**의 이동입니다. "
        "**다오와 배찌는 위치가 겹칠 수 있습니다.**"
    ),
}
