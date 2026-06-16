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
    reference_cost(input)  -> float      an ACHIEVABLE cost (optimal here) for Step Up
    sample_solution(input) -> str        an optimal move string (example output / solver)
    META: dict

The browser simulator (META.simulator_key = "maze") lets players play the board with
the arrow keys; web/lib/maze.ts mirrors _apply so its cost preview matches scoring.
The server is always the sole judge.
"""

from __future__ import annotations

import heapq
import itertools
import random
import time
from collections import deque

WALL, BLOCK, DAO, BAZZI, GOAL, EMPTY = "#", "O", "D", "Z", "G", "."
DIRS = {"U": (-1, 0), "D": (1, 0), "L": (0, -1), "R": (0, 1)}
MISS_COST = 100_000               # cost when the goal is never reached

DEFAULTS = {"rows": 7, "cols": 7, "players": 1, "obstacles": 5, "blocks": 6}
GEN_NODE_CAP = 1_200              # solvability-check budget per placement during generation.
                                  # Accepted boards are genuinely solvable (a solution was found
                                  # within the cap). High enough to place FULL counts even on a
                                  # dense 30x30 (300 blocks), low enough to stay fast (~1-8s).
REF_NODE_CAP = 250_000            # optimal-cost search budget (Step Up reference)
MEM_STATE_CAP = 170_000           # RAM guard: a dense/large board can grow the Dijkstra `best`/`pq`
                                  # to hundreds of MB (a C=2 fallback hit ~370MB) and OOM-kill a
                                  # 512MB host before the expansion cap bites. Bail past this many
                                  # tracked states (-> witness fallback) — ~95MB peak, still big
                                  # enough for sane Step-Up boards to solve exactly.
SOLVE_DEADLINE_S = 2.5            # wall-clock guard for the reference/example solve: it runs INSIDE
                                  # a web request on a single-core free host, so a multi-second solve
                                  # freezes the API (GIL). Bail past this (-> witness fallback) so the
                                  # page stays responsive; small boards finish well under it (exact).


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

def _apply(R, C, walls, blocks: frozenset, pos, d, other=None):
    """Apply one move of a player at `pos` in direction `d`. `other` is the OTHER player's
    cell (or None). A BLOCK may never overlap a player, so a push whose chain end would land
    on `other` FAILS (like hitting a wall). The two PLAYERS may still share a cell — only
    block<->player overlap is forbidden. Returns (new_pos, new_blocks_or_same, cost); a move
    always costs >= 1 (even a failed one)."""
    dr, dc = d
    nr, nc = pos[0] + dr, pos[1] + dc
    if not (0 <= nr < R and 0 <= nc < C) or (nr, nc) in walls:
        return pos, blocks, 1                      # into wall/edge -> fail, +1
    if (nr, nc) in blocks:
        k, cr, cc = 0, nr, nc
        while (cr, cc) in blocks:                   # length of the contiguous chain
            k += 1
            cr, cc = cr + dr, cc + dc
        if not (0 <= cr < R and 0 <= cc < C) or (cr, cc) in walls or (cr, cc) == other:
            return pos, blocks, 1                   # chain ends at wall/edge/player -> fail, +1
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
        other = bazzi if dao_turn else dao          # a block can't be pushed onto the other player
        npos, blocks, c = _apply(inst.R, inst.C, inst.walls, blocks, pos, DIRS[m], other)
        cost += c
        if dao_turn:
            dao = npos
            if dao == inst.goal:
                return float(cost), True, "ok"
        else:
            bazzi = npos
    return float(MISS_COST), True, "목적지에 도달하지 못했습니다 (100,000)"


# --- solver: optimal cost + move string (Step Up reference / example / gen check) ---

def _solve(inst: Instance, node_cap: int, deadline_s: float | None = None):
    """Dijkstra over (dao, bazzi, blocks, turn). Returns (cost, moves) for the minimum total
    cost to put Dao on the goal, or None if not found within node_cap / MEM_STATE_CAP states /
    deadline_s wall-clock seconds (None = no time limit; generation/tests call it that way)."""
    if inst.dao == inst.goal:
        return 0, ""
    start = (inst.dao, inst.bazzi, frozenset(inst.blocks), 0)
    best = {start: 0}
    cnt = itertools.count()
    pq = [(0, next(cnt), start, "")]
    expanded = 0
    t_end = (time.monotonic() + deadline_s) if deadline_s else None
    while pq:
        cost, _, st, path = heapq.heappop(pq)
        if st[0] == inst.goal:
            return cost, path
        if cost > best.get(st, 1 << 60):
            continue
        expanded += 1
        if expanded > node_cap or len(best) > MEM_STATE_CAP:   # give up (RAM/time) -> witness fallback
            return None
        if t_end is not None and (expanded & 0x7FF) == 0 and time.monotonic() > t_end:
            return None                                         # wall-clock deadline -> witness fallback
        dao, bazzi, blocks, turn = st
        dao_turn = inst.P == 1 or turn == 0
        mover = dao if dao_turn else bazzi
        other = bazzi if dao_turn else dao          # a block can't be pushed onto the other player
        for m, d in DIRS.items():
            npos, nblocks, c = _apply(inst.R, inst.C, inst.walls, blocks, mover, d, other)
            if dao_turn:
                nst = (npos, bazzi, nblocks, 0 if inst.P == 1 else 1)
            else:
                nst = (dao, npos, nblocks, 0)
            nc = cost + c
            if nc < best.get(nst, 1 << 60):
                best[nst] = nc
                heapq.heappush(pq, (nc, next(cnt), nst, path + m))
    return None


_REF_CACHE: dict[str, float] = {}     # reference_cost is deterministic per input + can be costly
                                      # (an exact solve); memoize so re-grading a mission is instant


def reference_cost(input_text: str) -> float:
    """Achievable cost for full marks (Step Up). The exact optimum when the board is small enough
    to solve (true for sensible Step-Up sizes); for a board too dense to solve exactly (Sokoban is
    intractable in general — use such boards in the Challenge, not Step Up) it falls back to the
    constructive corridor solution's cost, an achievable upper bound, instead of the miss penalty.
    Memoized per input: a mission is graded on every submission, so the costly solve runs once."""
    cached = _REF_CACHE.get(input_text)
    if cached is not None:
        return cached
    inst = parse(input_text)
    res = _solve(inst, REF_NODE_CAP, deadline_s=SOLVE_DEADLINE_S)
    if res:
        out = float(res[0])                       # exact optimum (tractable Step-Up sizes)
    else:
        sol = _SOL_CACHE.get(input_text)          # generator's recorded witness (achievable bound)
        out = float(sol[0]) if sol is not None else float(MISS_COST)
    if len(_REF_CACHE) > 1024:
        _REF_CACHE.clear()
    _REF_CACHE[input_text] = out
    return out


def sample_solution(input_text: str) -> str:
    """An (optimal when solvable, else achievable) move string — the statement's example output."""
    inst = parse(input_text)
    res = _solve(inst, REF_NODE_CAP, deadline_s=SOLVE_DEADLINE_S)
    if res:
        return res[1]
    sol = _SOL_CACHE.get(input_text)              # generator's recorded witness
    return sol[1] if sol is not None else ""


def example_inputs(meta: dict, kind: str) -> list[dict]:
    """Statement examples that SATISFY the stated conditions — one per player count (C=1 and C=2).
    Challenge boards use the fixed contest size (N = M = 30); Step Up shows small illustrative
    boards WITH an optimal output. Deterministic seeds keep the cached example stable. Returns
    [{label, input, output|None}]. Used by GET /problems/{pid}/example (overrides the generic one)."""
    if kind == "challenge":
        specs = [
            ("예시 1 — C = 1 (다오 혼자)", 9001, {"rows": 30, "cols": 30, "players": 1, "obstacles": 120, "blocks": 120}),
            ("예시 2 — C = 2 (배찌 도우미)", 9002, {"rows": 30, "cols": 30, "players": 2, "obstacles": 120, "blocks": 120}),
        ]
        return [{"label": lbl, "input": generate(s, p), "output": None} for (lbl, s, p) in specs]
    specs = [
        ("예시 1 — C = 1 (다오 혼자)", 9011, {"rows": 8, "cols": 8, "players": 1, "obstacles": 22, "blocks": 14}),
        ("예시 2 — C = 2 (배찌 도우미)", 9012, {"rows": 7, "cols": 7, "players": 2, "obstacles": 16, "blocks": 10}),
    ]
    res = []
    for (lbl, s, p) in specs:
        inp = generate(s, p)
        res.append({"label": lbl, "input": inp, "output": sample_solution(inp)})
    return res


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


def _bfs_reach(R, C, blocked: set, src, dst) -> bool:
    """4-dir block-free reachability src -> dst, treating every cell in `blocked` as impassable.
    Cheap (O(cells)) — used to PROVE a board solvable by construction without a full Dijkstra,
    which is unreliable on a large barriered board within the generation node cap."""
    if src == dst:
        return True
    seen = {src}
    q = deque([src])
    while q:
        cur = q.popleft()
        for dr, dc in DIRS.values():
            nxt = (cur[0] + dr, cur[1] + dc)
            if 0 <= nxt[0] < R and 0 <= nxt[1] < C and nxt not in blocked and nxt not in seen:
                if nxt == dst:
                    return True
                seen.add(nxt)
                q.append(nxt)
    return False


def _reachable_without_push(R, C, walls, blocks, dao, goal) -> bool:
    """Can Dao reach the goal moving only through EMPTY cells (no pushing)? If so the case is
    WEAK — blocks aren't needed. Used to prefer boards where a push is actually required."""
    return _bfs_reach(R, C, walls | blocks, dao, goal)


_CHAR = {(-1, 0): "U", (1, 0): "D", (0, -1): "L", (0, 1): "R"}


def _bfs_path(R, C, blocked: set, src, dst):
    """Shortest 4-dir block-free path src -> dst as a list of cells (incl. both ends), or None."""
    if src == dst:
        return [src]
    prev = {src: None}
    q = deque([src])
    while q:
        cur = q.popleft()
        for dr, dc in DIRS.values():
            nxt = (cur[0] + dr, cur[1] + dc)
            if 0 <= nxt[0] < R and 0 <= nxt[1] < C and nxt not in blocked and nxt not in prev:
                prev[nxt] = cur
                if nxt == dst:
                    path = [nxt]
                    while prev[path[-1]] is not None:
                        path.append(prev[path[-1]])
                    return path[::-1]
                q.append(nxt)
    return None


def _moves_of(path) -> list:
    return [_CHAR[(b[0] - a[0], b[1] - a[1])] for a, b in zip(path, path[1:])]


# --- organic generator: reverse-pull scramble ------------------------------
# Start from the SOLVED state (Dao on the goal), seed blocks, then apply many random REVERSE moves
# (reverse-walk = Dao steps to an empty cell; PULL = Dao steps AWAY from an adjacent block, dragging
# it into Dao's old cell — its forward inverse is a single-block PUSH). The FORWARD solution is the
# recorded inverse moves reversed. Route Dao home to (0,0) (reverse-walk BFS), then WALL every cell
# the solution never touches -> a dense, natural-looking cave with one winding corridor (no machine-
# made diagonal stripes), GUARANTEED solvable WITH a recorded witness, and made NON-trivial (no
# zero-push path, no single push opens one). Density is a byproduct of walling off the footprint.

_INV = {"U": "D", "D": "U", "L": "R", "R": "L"}
_SOL_CACHE: dict[str, tuple[float, str]] = {}     # board -> (cost, solution): generate() records the
                                                  # witness; reference_cost/sample_solution read it for
                                                  # boards an exact solve can't crack (dense/large).


def _lift_p2(dao_sol: str, R: int, bazzi) -> str:
    """Lift a Dao-only (P=1) solution to P=2: interleave a harmless Bazzi border "pass" (a move
    outward off its border cell — fails at +1, never disturbs the board) after every Dao move but
    the last (Dao reaching the goal on its turn ends the run)."""
    bp = "U" if bazzi[0] == 0 else "D" if bazzi[0] == R - 1 else "L" if bazzi[1] == 0 else "R"
    out = []
    for i, m in enumerate(dao_sol):
        out.append(m)
        if i < len(dao_sol) - 1:
            out.append(bp)
    return "".join(out)


def _empty_reach(R, C, blocked: set, src) -> set:
    seen = {src}
    q = deque([src])
    while q:
        r, c = q.popleft()
        for dr, dc in DIRS.values():
            nxt = (r + dr, c + dc)
            if 0 <= nxt[0] < R and 0 <= nxt[1] < C and nxt not in blocked and nxt not in seen:
                seen.add(nxt); q.append(nxt)
    return seen


def _single_push_trivial(R, C, walls, blocks, dao, goal) -> bool:
    """Does pushing ANY one block (one chain-shift) open a zero-push path dao -> goal? Only blocks
    bordering Dao's empty-reachable region can matter, so test just those (cheap on dense boards)."""
    reach = _empty_reach(R, C, walls | blocks, dao)
    if goal in reach:
        return True
    frontier = {(r + dr, c + dc) for (r, c) in reach for dr, dc in DIRS.values()
                if (r + dr, c + dc) in blocks}
    for b in frontier:
        for dr, dc in DIRS.values():
            cr, cc = b
            while (cr, cc) in blocks:                       # push the whole contiguous chain one cell
                cr, cc = cr + dr, cc + dc
            if not (0 <= cr < R and 0 <= cc < C) or (cr, cc) in walls:
                continue
            nb = set(blocks); nb.discard(b); nb.add((cr, cc))
            if _bfs_reach(R, C, walls | nb, dao, goal):
                return True
    return False


def _nontrivial(R, C, walls, blocks, dao, goal) -> bool:
    return (not _reachable_without_push(R, C, walls, blocks, dao, goal)
            and not _single_push_trivial(R, C, walls, blocks, dao, goal))


def _reverse_pull_core(seed, params):
    """One reverse-pull scramble. Returns (R, C, P, blocks, dao, bazzi, goal, dao_sol, used) or None,
    where `used` = every cell the recorded P=1 solution touches (Dao path + all block positions)."""
    rng = random.Random(seed)
    p = _merged(params)
    R, C = max(3, _pick(rng, p, "rows")), max(3, _pick(rng, p, "cols"))
    P = 2 if _pick(rng, p, "players") >= 2 else 1
    B = max(0, _pick(rng, p, "blocks"))
    W = max(0, _pick(rng, p, "obstacles"))         # wall budget (density target; raised if needed)
    dao0, goal = (0, 0), (R - 1, C - 1)
    if P == 2:
        border = [(r, c) for r in range(R) for c in range(C)
                  if (r in (0, R - 1) or c in (0, C - 1)) and (r, c) not in (dao0, goal)]
        rng.shuffle(border)
        bazzi = border[0] if border else (0, 1)
    else:
        bazzi = (-1, -1)

    all_cells = [(r, c) for r in range(R) for c in range(C)]
    # On big boards keep the top row + left column open so Dao can always reverse-walk home; on
    # small/cramped boards just keep (0,0) and rely on pull-escape.
    home = ({(0, c) for c in range(C)} | {(r, 0) for r in range(R)}) if R * C >= 200 else {dao0}
    free = [x for x in all_cells if x not in (dao0, goal) and x != bazzi and x not in home]
    rng.shuffle(free)
    # cap blocks at ~22% of the area: more than that boxes Dao in during the reverse scramble (he
    # can't move/pull) and the board falls back. Density still comes mostly from WALLS, not blocks.
    blocks = set(free[:min(B, int(0.22 * R * C), len(free))])

    dao = goal                                    # reverse sim starts in the SOLVED state
    inv = []                                      # forward-inverse chars, in scramble order
    visited = {dao}
    used_blocks = set(blocks)                      # every cell any block ever occupies

    def inb(r, c):
        return 0 <= r < R and 0 <= c < C

    def free_nbrs(pos, bset):
        return sum(1 for dr, dc in DIRS.values()
                   if inb(pos[0] + dr, pos[1] + dc) and (pos[0] + dr, pos[1] + dc) not in bset)

    for _ in range(min(int(R * C * 1.2) + 30, 1200)):
        r, c = dao
        cand = []
        for dch, (dr, dc) in DIRS.items():
            if not inb(r + dr, c + dc) or (r + dr, c + dc) in blocks:
                continue
            cand.append(("walk", dch, None))
            if inb(r - dr, c - dc) and (r - dr, c - dc) in blocks:    # pull source opposite the step
                cand.append(("pull", dch, (r - dr, c - dc)))
        if not cand:
            break
        pulls = [x for x in cand if x[0] == "pull"]
        walks = [x for x in cand if x[0] == "walk"]
        action = None
        if pulls and rng.random() < 0.45:           # prefer pulls but never box Dao in
            rng.shuffle(pulls)
            for a in pulls:
                _, dch, bpos = a
                dr, dc = DIRS[dch]
                nb = set(blocks); nb.discard(bpos); nb.add((r, c))
                if free_nbrs((r + dr, c + dc), nb) >= 1:
                    action = a
                    break
        if action is None:
            action = rng.choice(walks) if walks else rng.choice(cand)
        kind, dch, extra = action
        dr, dc = DIRS[dch]
        inv.append(_INV[dch])
        if kind == "pull":
            blocks.discard(extra); blocks.add((r, c))
            used_blocks.add((r, c)); used_blocks.add(extra)
        dao = (r + dr, c + dc)
        visited.add(dao)

    # route Dao home to (0,0) over empty cells; if boxed in, pull a neighbour to escape, then retry.
    path = None
    for _ in range(6):
        path = _bfs_path(R, C, set(blocks), dao, dao0)
        if path is not None:
            break
        r, c = dao
        freed = False
        for dch, (dr, dc) in DIRS.items():
            if (inb(r - dr, c - dc) and (r - dr, c - dc) in blocks
                    and inb(r + dr, c + dc) and (r + dr, c + dc) not in blocks):
                inv.append(_INV[dch]); blocks.discard((r - dr, c - dc)); blocks.add((r, c))
                used_blocks.add((r, c)); used_blocks.add((r - dr, c - dc))
                dao = (r + dr, c + dc); visited.add(dao); freed = True
                break
        if not freed:
            break
    if path is None:
        return None
    inv.extend(_INV[m] for m in _moves_of(path))
    visited.update(path)

    dao_sol = "".join(reversed(inv))              # forward solution = reverse of the inverse moves
    used = visited | used_blocks | {dao0, goal} | ({bazzi} if P == 2 else set())
    return R, C, P, W, blocks, dao0, bazzi, goal, dao_sol, used


def _wall_fill(R, C, blocks, dao, goal, used, max_walls):
    """Wall cells OUTSIDE the solution footprint (`used`) — so the recorded solution stays valid.
    Fills up to `max_walls` SCATTERED cells; if that isn't yet non-trivial, keeps walling (in chunks)
    until it is or the off-footprint is exhausted. Returns (walls, is_nontrivial) — never None, so a
    board that can't be sealed (rare tiny/sparse case) still ships as a valid solvable board."""
    offprint = [(r, c) for r in range(R) for c in range(C)
                if (r, c) not in used and (r, c) not in blocks]
    # scatter the partial fill (small boards) so walls don't bias to the top rows (row-major order);
    # deterministic so generate() stays reproducible. (Large boards wall ALL off-footprint anyway.)
    random.Random((R * 73856093) ^ (C * 19349663) ^ (len(blocks) * 83492791)).shuffle(offprint)
    walls = set(offprint[:min(max_walls, len(offprint))])
    if _nontrivial(R, C, walls, blocks, dao, goal):
        return walls, True
    idx = len(walls)
    chunk = max(4, len(offprint) // 8)
    while idx < len(offprint):                    # keep walling off-footprint until non-trivial
        walls |= set(offprint[idx:idx + chunk]); idx += chunk
        if _nontrivial(R, C, walls, blocks, dao, goal):
            return walls, True
    return walls, _nontrivial(R, C, walls, blocks, dao, goal)


def _fallback_board(seed, params):
    """Last-resort guaranteed-solvable board (reverse-pull practically always succeeds first): a
    single wall row with a one-cell gap holding a block, SOLVED with the exact solver (the board is
    tiny enough to solve). Crash-proof — if the solve somehow fails it returns an empty walkable
    board so generate() never raises."""
    rng = random.Random(seed ^ 0xABCDEF)
    p = _merged(params)
    R, C = max(3, _pick(rng, p, "rows")), max(3, _pick(rng, p, "cols"))
    P = 2 if _pick(rng, p, "players") >= 2 else 1
    dao0, goal = (0, 0), (R - 1, C - 1)
    bazzi = (-1, -1)
    if P == 2:
        bazzi = (0, C - 1) if (0, C - 1) not in (dao0, goal) else (R - 1, 0)
    mid, gc = R // 2, C // 2
    walls = {(mid, c) for c in range(C) if c != gc}
    blocks = {(mid, gc)} if mid + 1 < R else set()       # Dao pushes the gap block to cross
    inst = Instance(R, C, 1, walls, blocks, dao0, bazzi, goal)
    res = _solve(inst, REF_NODE_CAP)
    if not res:                                          # degenerate — ship an empty walkable board
        walls, blocks = set(), set()
        res = _solve(Instance(R, C, 1, walls, blocks, dao0, bazzi, goal), REF_NODE_CAP)
    sol_p1 = res[1] if res else ""
    board = _serialize(R, C, P, walls, blocks, dao0, bazzi, goal)
    sol = sol_p1 if P == 1 else _lift_p2(sol_p1, R, bazzi)
    return board, sol


def generate(seed: int, params: dict | None = None) -> str:
    """An organic, dense, push-required board via reverse-pull scramble (see _reverse_pull_core):
    a natural-looking cave with one winding corridor — NO machine-made diagonal stripes. Solvable BY
    CONSTRUCTION; the recorded witness is cached so reference_cost/sample_solution can use it when an
    exact solve is intractable (dense/large boards). Re-rolls until the board is non-trivial. Tiny
    boards take a moderate density cap (keeps them solver-tractable for a TIGHT Step-Up reference);
    a deterministic fallback (practically never hit) guarantees this never raises."""
    rows, cols = (params or {}).get("rows"), (params or {}).get("cols")
    def _hi(v):
        return v[1] if isinstance(v, (list, tuple)) else v
    small = rows is not None and cols is not None and _hi(rows) * _hi(cols) <= 200

    def _cache(board, cost, sol):
        if len(_SOL_CACHE) > 1024:
            _SOL_CACHE.clear()
        _SOL_CACHE[board] = (float(cost), sol)
        return board

    backup = None                                  # a solvable board (even if it couldn't be sealed)
    for relax in (False, True):                    # pass 2 drops the small-board density cap
        for attempt in range(40):
            res = _reverse_pull_core(seed * 7919 + attempt + (5000 if relax else 0), params)
            if res is None:
                continue
            R, C, P, W, blocks, dao, bazzi, goal, dao_sol, used = res
            # small boards: moderate cap (keeps them solver-tractable + a little open). Big boards:
            # wall EVERY off-footprint cell (max density, no artificial open stripes).
            cap = min(W, int(0.55 * R * C)) if (small and not relax) else R * C
            walls, ntv = _wall_fill(R, C, blocks, dao, goal, used, cap)
            board = _serialize(R, C, P, walls, blocks, dao, bazzi, goal)
            sol = dao_sol if P == 1 else _lift_p2(dao_sol, R, bazzi)
            cost, valid, _ = check(board, sol)
            if not (valid and cost < MISS_COST):
                continue
            if ntv:
                return _cache(board, cost, sol)    # non-trivial (push required) -> ship it
            if backup is None:
                backup = (board, cost, sol)        # solvable but not sealable (rare tiny/sparse board)
    if backup is not None:
        return _cache(*backup)
    board, sol = _fallback_board(seed, params)     # last resort: reverse-pull never yielded a board
    cost, _v, _m = check(board, sol)
    return _cache(board, cost, sol)


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
        {"key": "rows", "label": "행 N", "min": 3, "max": 30, "default": 7},
        {"key": "cols", "label": "열 M", "min": 3, "max": 30, "default": 7},
        {"key": "players", "label": "플레이어 수 C", "min": 1, "max": 2, "default": 1},
        {"key": "obstacles", "label": "장애물 수 W", "min": 0, "max": 400, "default": 5},
        {"key": "blocks", "label": "블럭 수 B", "min": 0, "max": 300, "default": 6},
    ],
    "statement_md": (
        "## 다오와 배찌의 길찾기\n\n"
        "다오(`D`)는 블럭(`O`)이 흩어진 미로에 갇혀 있습니다. **목표 칸(`G`)에 도달**하면 탈출입니다. "
        "이동은 `U`/`D`/`L`/`R` 네 방향이며, 각 이동은 **비용 1** 이 듭니다.\n\n"
        "### 입력\n"
        "```\nN M C\n<N줄의 격자 (각 줄 M글자)>\n```\n"
        "- 첫 줄: **N** 행 수, **M** 열 수, **C** 플레이어 수.\n"
        "- 이어서 N줄, 각 줄 M글자의 격자. 기호: `#` 장애물/벽(고정), `O` 블럭(밀 수 있음), "
        "`D` 다오 시작, `G` 목표, `Z` 배찌 시작(`C=2`일 때만), `.` 빈 칸. **격자 바깥은 벽**으로 취급합니다.\n"
        "- **다오(`D`)는 항상 좌상단 `(0, 0)`에서 시작하고, 목표(`G`)는 항상 우하단 `(N-1, M-1)`에 있습니다** "
        "(코너 → 코너 완주). 배찌(`Z`, `C=2`)의 시작 위치만 매 케이스 무작위입니다.\n\n"
        "**변수 범위**\n"
        "- `3 ≤ N ≤ 30`, `3 ≤ M ≤ 30` — **챌린지에서는 항상 `N = M = 30` (30×30 보드).**\n"
        "- `C ∈ {1, 2}` — 플레이어 수.\n"
        "- `0 ≤ W ≤ 400` — **장애물(벽) 수** = 격자 속 `#`의 개수.\n"
        "- `0 ≤ B ≤ 300` — **블럭 수** = 격자 속 `O`의 개수.\n\n"
        "### 블럭 밀기 규칙\n"
        "- 이동하려는 칸에 블럭이 있으면 **그 방향으로 이어진 블럭들을 한 칸씩 밉니다.** "
        "이어진 블럭이 `k`개면 이 행동의 비용은 **`1 + k`** 입니다 (예: 블럭 3개를 밀면 `1 + 3 = 4`).\n"
        "- **이어진 블럭의 끝이 벽/장애물/격자 밖이거나 다른 플레이어가 있으면** 밀 수 없어 **이동은 실패**하고 "
        "제자리에 머뭅니다. 단, **실패해도 이동 비용 1은 추가**됩니다. (블럭 없이 벽으로 바로 이동해도 실패 + 비용 1.)\n"
        "- **블럭은 어떤 플레이어와도 같은 칸에 있을 수 없습니다** — 다른 플레이어가 있는 칸으로 블럭을 밀 수 없습니다.\n"
        "- 블럭은 **목표 칸(`G`)을 지나갈 수 있습니다** — 목표는 벽이 아니라 블럭 이동을 막지 않습니다. "
        "도달은 **다오가 목표 칸에 설 때** 인정되며, 목표 위에 블럭이 있으면 밀어내고 들어가야 합니다.\n\n"
        "### 출력 / 비용\n"
        "- `U/D/L/R` 로 이루어진 **이동 문자열**을 출력합니다. 다오가 목표에 도달한 시점에서 멈춥니다.\n"
        "- **도달 시 비용 = 그때까지 누적된 총 비용**, **끝까지 도달 못하면 100,000**. 비용이 낮을수록 좋습니다.\n"
        "- 보드는 **항상 해결 가능**하게 주어집니다.\n\n"
        "### C = 2 — 도우미 배찌\n"
        "`C = 2`이면 배찌(`Z`)가 등장합니다. 배찌는 목표에 도달할 필요는 없고, **블럭을 밀어 다오를 돕는** 역할입니다. "
        "두 플레이어는 **번갈아 가며**(다오 → 배찌 → 다오 → …) 움직이고, 위 규칙이 동일하게 적용됩니다. "
        "출력 문자열의 짝수번째(0,2,4,…) 문자는 **다오**, 홀수번째(1,3,5,…)는 **배찌**의 이동입니다. "
        "**다오와 배찌는 서로 위치가 겹칠 수 있지만, 블럭은 어느 플레이어와도 겹칠 수 없습니다**(플레이어가 있는 칸으로는 블럭을 밀 수 없음)."
    ),
}
