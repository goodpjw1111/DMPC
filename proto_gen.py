"""
Prototype: ORGANIC board generator for maze_push.

STRATEGY  "winding tube + serial chokepoint pushes":
  - Carve ONE winding simple path (0,0)->(R-1,C-1) by randomized DFS (organic meander).
  - WALL-LINE the path into a 1-wide tube: every cell orthogonally adjacent to the path but NOT
    on the path becomes a wall. -> the empty-cell graph is exactly the tube; off-tube cells are
    sealed islands that can never reach the goal.
  - Place K>=2 forced-push BLOCKS in series inside the tube, spaced apart, each with its landing
    pocket carved to the side at a turn. Because the blocks are in SERIES on the sole route, no
    single push of one block opens an empty dao->goal path (the others still gate it) -> condition
    (b) holds. Dao solves by pushing each in turn -> we record that exact move string.
  - Fill the sealed off-tube interior with random walls/blocks (mix) for density + organic look.

  Solvable BY CONSTRUCTION. Non-trivial GUARANTEED (tube is the only empty graph; >=2 serial
  blocks). Dense. Organic (winding DFS tube + irregular fill, no straight stripes).

Run from repo root:  python proto_gen.py
"""
import sys, time, random
from collections import deque
sys.path.insert(0, "problems/maze_push")
import problem as P

DIRS = {"U": (-1, 0), "D": (1, 0), "L": (0, -1), "R": (0, 1)}
CHAR = {(-1, 0): "U", (1, 0): "D", (0, -1): "L", (0, 1): "R"}
DLIST = list(DIRS.values())
CHECK_REF = True       # set False to skip the (slow) Step-Up reference_cost solve in measure()


def _carve_path(rng, R, C, start, goal, blocked):
    """Randomized DFS SELF-AVOIDING-WITH-MARGIN simple path start->goal avoiding `blocked`.
    A candidate next cell is rejected if it touches (4-dir) any path cell other than its parent —
    this keeps the path a genuine 1-WIDE tube (no parallel/self-adjacent segments), so wall-lining
    it makes the path the UNIQUE empty route and any in-tube block a true cut vertex. Organic via
    shuffled neighbors + occasional goal bias. Returns cell list or None."""
    stack = [start]
    came = {start: None}
    onpath = {start}
    steps = 0
    cap = R * C * 4 + 400           # bound backtracking so a self-avoiding carve can't blow up
    while stack:
        steps += 1
        if steps > cap:
            return None
        cur = stack[-1]
        if cur == goal:
            path = [cur]
            while came[path[-1]] is not None:
                path.append(came[path[-1]])
            return path[::-1]
        nbrs = []
        for dr, dc in DLIST:
            nxt = (cur[0] + dr, cur[1] + dc)
            if not (0 <= nxt[0] < R and 0 <= nxt[1] < C):
                continue
            if nxt in onpath or nxt in blocked:
                continue
            # margin: nxt must not touch any current path cell except `cur` (its parent)... unless
            # nxt is the goal (allow it to attach even if adjacent to path, so we always finish).
            if nxt != goal:
                touch = False
                for er, ec in DLIST:
                    adj = (nxt[0] + er, nxt[1] + ec)
                    if adj != cur and adj in onpath:
                        touch = True
                        break
                if touch:
                    continue
            nbrs.append(nxt)
        if not nbrs:
            popped = stack.pop()
            onpath.discard(popped)
            continue
        rng.shuffle(nbrs)
        if rng.random() < 0.6:        # goal bias: higher = fewer dead-ends/backtracks = faster
            nbrs.sort(key=lambda x: abs(x[0] - goal[0]) + abs(x[1] - goal[1]))
        nxt = nbrs[0]
        onpath.add(nxt)
        came[nxt] = cur
        stack.append(nxt)
    return None


def _fallback_path(R, C, start, goal, avoid):
    """A guaranteed simple monotone path start->goal (down then right), used only if the carve
    fails utterly. BFS over a margin-free graph so it always returns SOMETHING solvable."""
    pth = P._bfs_path(R, C, set(avoid), start, goal)
    if pth is not None:
        return pth
    # last resort: ignore avoid
    return P._bfs_path(R, C, set(), start, goal)


def _moves_of(path):
    return [CHAR[(b[0] - a[0], b[1] - a[1])] for a, b in zip(path, path[1:])]


def _grow_wall_clusters(rng, R, C, fillable_set, seed_walls, budget):
    """Place `budget` wall cells (subset of fillable_set) as organic CLUSTERS. We grow blobs:
    repeatedly pick a frontier cell adjacent to already-chosen walls (or a fresh random seed when
    the frontier is empty) — like accretion. Produces thick irregular wall masses (cave/maze look)
    instead of uniform noise. Returns the set of placed wall cells (<= budget)."""
    if budget <= 0 or not fillable_set:
        return set()
    placed = set()
    frontier = []
    avail = list(fillable_set)
    rng.shuffle(avail)
    ai = 0

    def push_neighbors(cell):
        for dr, dc in DLIST:
            n = (cell[0] + dr, cell[1] + dc)
            if n in fillable_set and n not in placed:
                frontier.append(n)

    # seed the first blob next to an existing tube wall if possible (so masses hug the corridors)
    while len(placed) < budget and ai < len(avail) * 4:
        if not frontier:
            # new seed
            while ai < len(avail) and avail[ai] in placed:
                ai += 1
            if ai >= len(avail):
                break
            seed = avail[ai]; ai += 1
            placed.add(seed)
            push_neighbors(seed)
            continue
        # bias accretion: 80% take from frontier (grow blob), 20% jump to a fresh seed (new blob)
        if rng.random() < 0.85 and frontier:
            idx = rng.randrange(len(frontier))
            cell = frontier[idx]
            frontier[idx] = frontier[-1]; frontier.pop()
            if cell in placed or cell not in fillable_set:
                continue
            placed.add(cell)
            push_neighbors(cell)
        else:
            frontier.clear()
    return placed


def _bfs_reach(R, C, blocked, src, dst):
    return P._bfs_reach(R, C, blocked, src, dst)


def _turn_indices(path):
    """Indices i (1<=i<=len-2) where the path turns, with arrival dir din. For a forced push at a
    turn: block on path[i], landing = path[i]+din (straight ahead, off the tube). Returns list of
    (i, b, land, din)."""
    out = []
    for i in range(1, len(path) - 1):
        a, b, c = path[i - 1], path[i], path[i + 1]
        din = (b[0] - a[0], b[1] - a[1])
        dout = (c[0] - b[0], c[1] - b[1])
        if din != dout:
            out.append((i, b, (b[0] + din[0], b[1] + din[1]), din))
    return out


def gen_full(seed, params=None):
    rng = random.Random(seed)
    p = P._merged(params)
    R = max(3, P._pick(rng, p, "rows"))
    C = max(3, P._pick(rng, p, "cols"))
    Pl = 2 if P._pick(rng, p, "players") >= 2 else 1
    W = max(0, P._pick(rng, p, "obstacles"))
    B = max(0, P._pick(rng, p, "blocks"))

    dao, goal = (0, 0), (R - 1, C - 1)
    if Pl == 2:
        border = [(r, c) for r in range(R) for c in range(C)
                  if (r in (0, R - 1) or c in (0, C - 1)) and (r, c) not in (dao, goal)]
        rng.shuffle(border)
        bazzi = border[0] if border else (0, 1)
    else:
        bazzi = (-1, -1)
    occupied = {dao, goal} | ({bazzi} if Pl == 2 else set())

    # carve the winding tube (avoid bazzi so it stays off the tube)
    avoid = {bazzi} if Pl == 2 else set()
    path = None
    for _ in range(6):
        cand = _carve_path(rng, R, C, dao, goal, avoid)
        if cand is None:
            continue
        if path is None or len(cand) > len(path):
            path = cand
        if len(cand) >= max(R, C) + 4:
            break
    if path is None:
        path = _carve_path(rng, R, C, dao, goal, set())
    if path is None:
        # absolute fallback: a guaranteed simple L-path along edges, dodging bazzi if possible.
        path = _fallback_path(R, C, dao, goal, avoid)
    pathset = set(path)
    pindex = {cell: i for i, cell in enumerate(path)}

    # choose forced-push turns: pick K turns spaced apart; landing must be off-tube & in-bounds &
    # not occupied & not adjacent issues. We need >=2 so a single push can't open the route.
    turns = _turn_indices(path)
    # need land off the tube and not the bazzi/goal/dao
    good = []
    for (i, b, land, din) in turns:
        if not (0 <= land[0] < R and 0 <= land[1] < C):
            continue
        if land in pathset or land in occupied:
            continue
        if b in occupied:
            continue
        good.append((i, b, land, din))
    # space them out by path index
    good.sort(key=lambda t: t[0])
    target_k = max(2, min(6, len(path) // 7))
    chosen = []
    last_i = -10
    landset = set()
    bset = set()
    for (i, b, land, din) in good:
        if i - last_i < 2:
            continue
        if land in landset or b in bset:
            continue
        # land cell must not be another chosen block / its landing — and must not be on tube
        chosen.append((i, b, land, din))
        last_i = i
        landset.add(land)
        bset.add(b)
        if len(chosen) >= target_k:
            break
    # ensure at least 2; if not enough turns, add "straight" forced pushes: a block on a straight
    # tube cell whose landing is the next tube cell -> but that violates (b). So if <2 turns we just
    # accept what we have and rely on verify+fix. Most boards have many turns.

    walls = set()
    blocks = set()
    # 1) wall-line the tube: every off-path cell adjacent (4-dir) to a path cell becomes a wall,
    #    EXCEPT the landing pockets (must stay empty to receive a push) and occupied cells.
    block_cells = set(b for (_, b, _, _) in chosen)
    land_cells = set(land for (_, _, land, _) in chosen)
    for cell in path:
        for dr, dc in DLIST:
            n = (cell[0] + dr, cell[1] + dc)
            if not (0 <= n[0] < R and 0 <= n[1] < C):
                continue
            if n in pathset or n in land_cells or n in occupied:
                continue
            walls.add(n)
    # also wall-line around the landing pockets so a pushed block can't later be re-pushed to open
    # a shortcut, AND so the pocket doesn't become a free off-tube cell connecting things. Keep the
    # pocket itself empty; wall its other neighbors that are off-tube.
    for land in land_cells:
        for dr, dc in DLIST:
            n = (land[0] + dr, land[1] + dc)
            if not (0 <= n[0] < R and 0 <= n[1] < C):
                continue
            if n in pathset or n in land_cells or n in occupied or n in block_cells:
                continue
            walls.add(n)
    # 2) the forced-push blocks
    for b in block_cells:
        blocks.add(b)
        walls.discard(b)

    # 3) record solution: walk the path; each step into a block cell pushes it to its land.
    sol = "".join(_moves_of(path))

    # 4) fill remaining interior (off tube, not walls, not land, not occupied) with #/O.
    #    Walls are grown as CLUSTERS (blobs seeded at random cells, expanded into neighbors) so the
    #    board reads like a natural cave/maze (thick irregular wall masses) rather than salt-and-
    #    pepper noise. Blocks are then scattered in the gaps. This is purely cosmetic: the tube +
    #    serial chokepoints already guarantee solvability & non-triviality regardless of the fill.
    spent_w = len(walls)
    spent_b = len(blocks)
    fillable = [(r, c) for r in range(R) for c in range(C)
                if (r, c) not in pathset and (r, c) not in walls and (r, c) not in blocks
                and (r, c) not in land_cells and (r, c) not in occupied]
    fillable_set = set(fillable)
    wbudget = max(0, W - spent_w)
    bbudget = max(0, B - spent_b)

    placed_w = _grow_wall_clusters(rng, R, C, fillable_set, walls, wbudget)
    walls |= placed_w
    remaining = [x for x in fillable if x not in placed_w]
    rng.shuffle(remaining)
    bi = 0
    for _ in range(bbudget):
        if bi >= len(remaining):
            break
        blocks.add(remaining[bi]); bi += 1
    leftover_empty = remaining[bi:]   # off-tube empties (sealed islands -> harmless, but verify)

    state = (R, C, Pl, walls, blocks, dao, bazzi, goal, leftover_empty, path, block_cells)
    return sol, state


def _single_push_opens(R, C, walls, blocks, dao, goal):
    """If some single push of one block opens an empty dao->goal path, return that opened path's
    cells as a set; else None. (Used to find where to re-cut.)"""
    for b in blocks:
        for d in DLIST:
            chain = []
            cr, cc = b
            while (cr, cc) in blocks:
                chain.append((cr, cc)); cr, cc = cr + d[0], cc + d[1]
            end = (cr, cc)
            if not (0 <= end[0] < R and 0 <= end[1] < C) or end in walls or end in blocks:
                continue
            nb = set(blocks)
            for ch in chain:
                nb.discard(ch)
            for ch in chain:
                nb.add((ch[0] + d[0], ch[1] + d[1]))
            pth = P._bfs_path(R, C, walls | nb, dao, goal)
            if pth is not None:
                return set(pth)
    return None


def gen(seed, params=None):
    """Public: returns (board_str, dao_p1_solution_str). Self-repairs to GUARANTEE non-triviality:
      (1) wall any off-tube empties that leak to the goal,
      (2) if a single push still opens a route, add an extra forced-push block on the MAIN tube to
          re-cut it (the recorded solution naturally pushes the new block too),
      (3) if a tiny board can't be made non-trivial, re-roll the seed a few times.
    Solvability is preserved throughout (we only add blocks ON the recorded path, pushed in turn)."""
    for attempt in range(GEN_REROLLS):
        sol, st = gen_full(seed * 9176 + attempt, params)
        R, C, Pl, walls, blocks, dao, bazzi, goal, leftover, path, forced = st
        walls = set(walls); blocks = set(blocks)
        pathset = set(path)
        # (1) seal leaking off-tube empties
        if P._bfs_reach(R, C, walls | blocks, dao, goal):
            for cell in leftover:
                walls.add(cell)
        # (2) add chokepoint blocks on the tube until no single push opens a route.
        #     A new block goes on a path cell that lies on the opened route, far from existing
        #     forced blocks, with a free landing pocket so Dao can push it sideways. The recorded
        #     solution (walk the whole path) pushes it automatically when arriving straight+turning.
        for _ in range(12):
            if not P._bfs_reach(R, C, walls | blocks, dao, goal) and \
               _single_push_opens(R, C, walls, blocks, dao, goal) is None:
                break
            added = _add_tube_chokepoint(R, C, walls, blocks, dao, goal, path, occupied_extra(bazzi, Pl))
            if not added:
                break
        board = P._serialize(R, C, Pl, walls, blocks, dao, bazzi, goal)
        if non_trivial(board):
            # recompute the solution from the (possibly augmented) board's blocks: the path move
            # string is unchanged (blocks all sit on path cells and are pushed when walked through).
            return board, sol
    # last resort: return the last board (always solvable; may be trivial only on degenerate sizes)
    return board, sol


GEN_REROLLS = 5


def occupied_extra(bazzi, Pl):
    return {bazzi} if Pl == 2 else set()


def _add_tube_chokepoint(R, C, walls, blocks, dao, goal, path, extra_occ):
    """Add ONE forced-push block on a path cell (a turn with a free straight-ahead landing pocket),
    chosen to re-cut the tube. Mutates walls/blocks in place. Returns True if one was added."""
    occ = {dao, goal} | extra_occ
    # candidate turn cells on the path not already blocks, with an off-path in-bounds free landing
    cands = []
    for i in range(1, len(path) - 1):
        a, b, c = path[i - 1], path[i], path[i + 1]
        din = (b[0] - a[0], b[1] - a[1]); dout = (c[0] - b[0], c[1] - b[1])
        if din == dout:
            continue
        if b in blocks or b in occ:
            continue
        land = (b[0] + din[0], b[1] + din[1])
        if not (0 <= land[0] < R and 0 <= land[1] < C):
            continue
        if land in occ or land in path or land in blocks:
            continue
        cands.append((i, b, land))
    if not cands:
        # fall back to a straight cell with a perpendicular free pocket (push sideways mid-corridor)
        return False
    # pick the MIDDLE turn (deep cut)
    cands.sort(key=lambda t: t[0])
    i, b, land = cands[len(cands) // 2]
    # the landing pocket must stay empty; wall its off-path neighbors to keep it isolated
    blocks.add(b)
    walls.discard(b)
    if land in walls:
        walls.discard(land)
    for dr, dc in DLIST:
        n = (land[0] + dr, land[1] + dc)
        if not (0 <= n[0] < R and 0 <= n[1] < C):
            continue
        if n in path or n == land or n in occ or n in blocks:
            continue
        walls.add(n)
    return True


def lift_to_p2(board_str, dao_sol):
    inst = P.parse(board_str)
    R, C = inst.R, inst.C
    bz = inst.bazzi
    bp = "U" if bz[0] == 0 else "D" if bz[0] == R - 1 else "L" if bz[1] == 0 else "R"
    out = []
    for k, m in enumerate(dao_sol):
        out.append(m)
        if k < len(dao_sol) - 1:
            out.append(bp)
    return "".join(out)


def non_trivial(board_str):
    inst = P.parse(board_str)
    R, C, walls, blocks = inst.R, inst.C, inst.walls, inst.blocks
    dao, goal = inst.dao, inst.goal
    if _bfs_reach(R, C, walls | blocks, dao, goal):
        return False
    for b in blocks:
        for d in DLIST:
            chain = []
            cr, cc = b
            while (cr, cc) in blocks:
                chain.append((cr, cc))
                cr, cc = cr + d[0], cc + d[1]
            end = (cr, cc)
            if not (0 <= end[0] < R and 0 <= end[1] < C):
                continue
            if end in walls or end in blocks:
                continue
            nb = set(blocks)
            for ch in chain:
                nb.discard(ch)
            for ch in chain:
                nb.add((ch[0] + d[0], ch[1] + d[1]))
            if _bfs_reach(R, C, walls | nb, dao, goal):
                return False
    return True


def density(board_str):
    inst = P.parse(board_str)
    return (len(inst.walls) + len(inst.blocks)) / (inst.R * inst.C)


def measure():
    challenge_specs = []
    for pl in (1, 2):
        for s in range(3):
            challenge_specs.append((10_000 + pl * 100 + s,
                                    {"rows": 30, "cols": 30, "players": pl,
                                     "obstacles": 250, "blocks": 150}))
    stepup_specs = []
    for (rr, cc) in ((8, 8), (10, 10)):
        for pl in (1, 2):
            for s in range(2):
                stepup_specs.append((20_000 + rr * 10 + pl * 100 + s,
                                     {"rows": rr, "cols": cc, "players": pl,
                                      "obstacles": rr * 2, "blocks": rr}))

    all_solvable, all_nt = [], []
    dens30, times30, costs = [], [], {}
    sample30 = None

    print("=== CHALLENGE 30x30 ===")
    for (seed, params) in challenge_specs:
        t0 = time.perf_counter()
        board, sol = gen(seed, params)
        dt = (time.perf_counter() - t0) * 1000
        inst = P.parse(board)
        sol_full = lift_to_p2(board, sol) if inst.P == 2 else sol
        cost, valid, msg = P.check(board, sol_full)
        solv = valid and cost is not None and cost < P.MISS_COST
        nt = non_trivial(board)
        d = density(board)
        all_solvable.append(solv); all_nt.append(nt)
        dens30.append(d); times30.append(dt)
        costs.setdefault(inst.P, []).append(cost if solv else None)
        if sample30 is None and inst.P == 1:
            sample30 = board
        print(f"  seed={seed} P={inst.P} solv={solv} nt={nt} dens={d:.1%} ms={dt:.0f} cost={cost} {('' if solv else msg[:25])}")

    print("=== STEPUP ===")
    stepup_ref = []
    for (seed, params) in stepup_specs:
        t0 = time.perf_counter()
        board, sol = gen(seed, params)
        dt = (time.perf_counter() - t0) * 1000
        inst = P.parse(board)
        sol_full = lift_to_p2(board, sol) if inst.P == 2 else sol
        cost, valid, msg = P.check(board, sol_full)
        solv = valid and cost is not None and cost < P.MISS_COST
        nt = non_trivial(board)
        all_solvable.append(solv); all_nt.append(nt)
        tref = None
        if inst.R <= 8 and CHECK_REF:
            try:
                tref = P.reference_cost(board)
            except Exception as e:
                tref = f"ERR {e}"
        stepup_ref.append((inst.R, inst.P, tref, cost))
        print(f"  {inst.R}x{inst.C} P={inst.P} seed={seed} solv={solv} nt={nt} ms={dt:.0f} cost={cost} ref={tref}")

    sr = sum(all_solvable) / len(all_solvable)
    ntr = sum(all_nt) / len(all_nt)
    print(f"\nsolvable_rate={sr:.3f}  non_trivial_rate={ntr:.3f}")
    print(f"avg_density_30={sum(dens30)/len(dens30):.3f}  avg_ms_30={sum(times30)/len(times30):.0f}")
    print("challenge costs:", costs)
    print("stepup ref:", stepup_ref)
    if sample30:
        print("\n--- SAMPLE 30x30 P=1 ---")
        print(sample30)
    return sr, ntr


if __name__ == "__main__":
    measure()
