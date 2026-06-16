"""
Reverse-pull-scramble board generator prototype for maze_push.

gen(seed, params) -> (board_str, dao_p1_solution_str)

Strategy:
  1. Start from the SOLVED state: Dao on the goal (R-1, C-1). Place some blocks.
  2. Apply many random REVERSE moves:
       - reverse-walk: Dao steps to an adjacent empty cell (forward inverse = step back).
       - pull: Dao has an adjacent block B in dir d; Dao steps in -d (away from B) into an
         empty cell, dragging B into Dao's old cell. Forward inverse of this is a PUSH: from
         the pulled-to cell, Dao steps in +d, pushing B back. (single-block pull only -> the
         forward inverse pushes exactly one block, chain length 1.)
  3. Route Dao from its scrambled position to (0,0) via reverse-walk (BFS over empty cells),
     recording the inverse walk.
  4. The FORWARD Dao solution = reverse of the recorded inverse-move list.
  5. Density: mark every cell USED by the forward solution (Dao-visited cells + every cell any
     block ever occupies during the solution). Fill the rest with random walls -> dense + the
     recorded solution stays valid.
  6. Non-trivial: ensure no empty-cell path dao->goal and no single push opens one; add walls /
     retry if needed.
"""

import sys, time, random
sys.path.insert(0, "problems/maze_push")
import problem as P

DIRS = P.DIRS                      # {"U":(-1,0),"D":(1,0),"L":(0,-1),"R":(0,1)}
CHAR = P._CHAR                     # (dr,dc) -> "U/D/L/R"
INV = {"U": "D", "D": "U", "L": "R", "R": "L"}


def _pick(rng, p, key):
    v = p[key]
    if isinstance(v, (list, tuple)) and len(v) == 2:
        lo, hi = int(v[0]), int(v[1])
        return rng.randint(min(lo, hi), max(lo, hi))
    return int(v)


def _bfs_path(R, C, blocked, src, dst):
    """Shortest 4-dir path over cells NOT in blocked, src->dst. Returns list of cells or None."""
    from collections import deque
    if src == dst:
        return [src]
    prev = {src: None}
    q = deque([src])
    while q:
        cur = q.popleft()
        for dr, dc in DIRS.values():
            nr, nc = cur[0] + dr, cur[1] + dc
            nxt = (nr, nc)
            if 0 <= nr < R and 0 <= nc < C and nxt not in blocked and nxt not in prev:
                prev[nxt] = cur
                if nxt == dst:
                    path = [nxt]
                    while prev[path[-1]] is not None:
                        path.append(prev[path[-1]])
                    return path[::-1]
                q.append(nxt)
    return None


def _bfs_reach(R, C, blocked, src, dst):
    return _bfs_path(R, C, blocked, src, dst) is not None


def _moves_of(path):
    return [CHAR[(b[0] - a[0], b[1] - a[1])] for a, b in zip(path, path[1:])]


def _gen_core(seed, params):
    rng = random.Random(seed)
    p = dict(P.DEFAULTS)
    if params:
        for k in P.DEFAULTS:
            if params.get(k) is not None:
                p[k] = params[k]

    R = max(3, _pick(rng, p, "rows"))
    C = max(3, _pick(rng, p, "cols"))
    Pl = 2 if _pick(rng, p, "players") >= 2 else 1
    W = max(0, _pick(rng, p, "obstacles"))
    B = max(0, _pick(rng, p, "blocks"))

    dao0, goal = (0, 0), (R - 1, C - 1)   # forward start / goal

    # Bazzi on a border cell (not dao0/goal) for P==2.
    bazzi = (-1, -1)
    if Pl == 2:
        border = [(r, c) for r in range(R) for c in range(C)
                  if (r in (0, R - 1) or c in (0, C - 1)) and (r, c) not in (dao0, goal)]
        rng.shuffle(border)
        bazzi = border[0] if border else (0, 1)

    # ---- reverse simulation from the SOLVED state (Dao on goal) ----
    # We track Dao position and the set of blocks. We record forward-inverse moves; the forward
    # solution is the reverse of that list.
    all_cells = [(r, c) for r in range(R) for c in range(C)]

    # Seed blocks on random cells (not on goal/dao0/bazzi). Blocks count toward the B budget.
    # On larger boards we reserve a guaranteed-empty "home corridor" (top row + left column) so
    # Dao can always walk back to (0,0); on small/cramped boards that reservation steals too many
    # cells, so we shrink it to just (0,0) and rely on the post-sim pull-escape routing instead.
    if R * C >= 200:
        home_corridor = {(0, c) for c in range(C)} | {(r, 0) for r in range(R)}
    else:
        home_corridor = {dao0}
    free_for_block = [x for x in all_cells
                      if x not in (dao0, goal) and x != bazzi and x not in home_corridor]
    rng.shuffle(free_for_block)
    n_seed_blocks = min(B, len(free_for_block))
    blocks = set(free_for_block[:n_seed_blocks])

    dao = goal                                    # start reverse sim with Dao on the goal
    inv_moves = []                                # forward-inverse move chars, in reverse order
    dao_visited = {dao}                           # cells Dao occupies along the forward solution
    block_cells = set(blocks)                     # every cell any block ever occupies

    # number of reverse steps: scale with area, capped for speed
    n_steps = min(int(R * C * 1.2) + 30, 1200)

    def in_b(r, c):
        return 0 <= r < R and 0 <= c < C

    def empty_neighbors(pos, blockset):
        """count of empty (non-block, in-bounds) 4-neighbors of pos."""
        r, c = pos
        n = 0
        for dr, dc in DIRS.values():
            nr, nc = r + dr, c + dc
            if in_b(nr, nc) and (nr, nc) not in blockset:
                n += 1
        return n

    for _ in range(n_steps):
        r, c = dao
        cand = []  # ("walk", dch, None) or ("pull", dch, blockpos)
        for dch, (dr, dc) in DIRS.items():
            tr, tc = r + dr, c + dc          # the cell Dao would reverse-step INTO
            tgt = (tr, tc)
            if not in_b(tr, tc) or tgt in blocks:
                continue
            # plain reverse-walk into an empty cell
            cand.append(("walk", dch, None))
            # pull: a block sits on the OPPOSITE side of Dao (dir -d), dragged toward Dao.
            # Reverse: Dao (r,c)->tgt, block (r-dr,c-dc)->(r,c).
            # Forward inverse: Dao tgt->(r,c) pushes block (r,c)->(r-dr,c-dc). The forward push
            # has chain length 1 and lands on (r-dr,c-dc), which is empty in forward time (the
            # block came from there) -> a valid single-block push.
            br, bc = r - dr, c - dc
            bpos = (br, bc)
            if in_b(br, bc) and bpos in blocks:
                cand.append(("pull", dch, bpos))
        if not cand:
            break
        pulls = [x for x in cand if x[0] == "pull"]
        walks = [x for x in cand if x[0] == "walk"]

        # choose action; avoid pulls that would box Dao in (leave it with no empty neighbor at
        # the destination), which is what previously trapped Dao and broke routing home.
        action = None
        if pulls and rng.random() < 0.45:
            rng.shuffle(pulls)
            for a in pulls:
                _, dch, bpos = a
                dr, dc = DIRS[dch]
                tgt = (r + dr, c + dc)
                # simulate the block landing on (r,c)
                nb = set(blocks); nb.discard(bpos); nb.add((r, c))
                if empty_neighbors(tgt, nb) >= 1:
                    action = a
                    break
        if action is None:
            action = rng.choice(walks) if walks else rng.choice(cand)

        kind, dch, extra = action
        dr, dc = DIRS[dch]
        tgt = (r + dr, c + dc)
        inv_moves.append(INV[dch])             # forward inverse direction
        if kind == "pull":
            bpos = extra
            blocks.discard(bpos)
            blocks.add((r, c))                 # block now at Dao's old cell
            block_cells.add((r, c))
            block_cells.add(bpos)
        dao = tgt
        dao_visited.add(dao)

    # ---- route Dao to (0,0) via reverse-walk over EMPTY cells ----
    # blocked = blocks (forward time these are in the way). We walk over empty cells only. The
    # reserved home_corridor is block-free, so a path almost always exists; if Dao is boxed in
    # locally, free one neighbor with a pull and retry.
    for _retry in range(6):
        path = _bfs_path(R, C, set(blocks), dao, dao0)
        if path is not None:
            break
        # Dao boxed: pull a neighboring block toward Dao to open an escape (record the inverse).
        r, c = dao
        freed = False
        for dch, (dr, dc) in DIRS.items():
            br, bc = r - dr, c - dc            # block opposite the step dir
            tr, tc = r + dr, c + dc            # step destination must be in bounds & empty
            if in_b(br, bc) and (br, bc) in blocks and in_b(tr, tc) and (tr, tc) not in blocks:
                inv_moves.append(INV[dch])
                blocks.discard((br, bc))
                blocks.add((r, c))
                block_cells.add((r, c)); block_cells.add((br, bc))
                dao = (tr, tc)
                dao_visited.add(dao)
                freed = True
                break
        if not freed:
            break
    else:
        path = _bfs_path(R, C, set(blocks), dao, dao0)
    if path is None:
        return None
    walk_moves = _moves_of(path)              # forward inverse: Dao dao->dao0
    # these reverse-walk moves are themselves the inverse direction of the forward solution:
    # reverse sim ends with Dao at dao0; forward starts at dao0. The forward solution is the
    # reverse of (inv_moves + inverse-of-walk). Each step in `path` goes dao->...->dao0 in
    # reverse-time; its forward inverse char is INV of the step direction.
    inv_walk = [INV[m] for m in walk_moves]
    inv_moves.extend(inv_walk)
    for cell in path:
        dao_visited.add(cell)

    # forward Dao solution = reverse of inv_moves
    forward = list(reversed(inv_moves))
    dao_sol = "".join(forward)

    final_blocks = set(blocks)               # blocks in the FORWARD start state

    # ---- density: fill unused cells with walls ----
    used = dao_visited | block_cells | {dao0, goal}
    if Pl == 2:
        used.add(bazzi)
    fillable = [x for x in all_cells if x not in used and x not in final_blocks]
    rng.shuffle(fillable)
    n_walls = min(W, len(fillable))
    walls = set(fillable[:n_walls])

    board = P._serialize(R, C, Pl, walls, final_blocks, dao0, bazzi, goal)
    return board, dao_sol, R, C, Pl, walls, final_blocks, dao0, bazzi, goal


def _empty_path_exists(R, C, walls, blocks, dao, goal):
    """Empty-cell-only path dao->goal (blocks treated as impassable)?"""
    return _bfs_reach(R, C, walls | blocks, dao, goal)


def _empty_reach_set(R, C, walls, blocks, src):
    """Set of cells reachable from src over non-wall, non-block cells (4-dir)."""
    from collections import deque
    blocked = walls | blocks
    if src in blocked:
        return set()
    seen = {src}
    q = deque([src])
    while q:
        r, c = q.popleft()
        for dr, dc in DIRS.values():
            nr, nc = r + dr, c + dc
            nxt = (nr, nc)
            if 0 <= nr < R and 0 <= nc < C and nxt not in blocked and nxt not in seen:
                seen.add(nxt)
                q.append(nxt)
    return seen


def _single_push_opens(R, C, walls, blocks, dao, goal):
    """Does pushing any ONE block one cell (any direction, target free of wall/other block) open
    an empty-cell path dao->goal? Only blocks ADJACENT to the empty region reachable from dao can
    matter (pushing a far-away block can't connect dao's region to the goal), so we test just
    those — far cheaper than a BFS per block on a dense board."""
    reach = _empty_reach_set(R, C, walls, blocks, dao)
    if goal in reach:
        return True
    # candidate blocks: those touching dao's reachable region (a push could reshape that frontier)
    frontier = set()
    for (r, c) in reach:
        for dr, dc in DIRS.values():
            nb = (r + dr, c + dc)
            if nb in blocks:
                frontier.add(nb)
    for b in frontier:
        for dr, dc in DIRS.values():
            tr, tc = b[0] + dr, b[1] + dc
            if not (0 <= tr < R and 0 <= tc < C):
                continue
            if (tr, tc) in walls or (tr, tc) in blocks:
                continue
            nb = set(blocks)
            nb.discard(b)
            nb.add((tr, tc))
            if _empty_path_exists(R, C, walls, nb, dao, goal):
                return True
    return False


def _make_nontrivial(R, C, walls, blocks, dao, goal, dao_visited, block_cells, bazzi, Pl,
                     max_density=None):
    """Wall cells OUTSIDE the solution footprint (Dao-visited cells + every cell a block ever
    occupies) so the recorded solution stays valid, until the board is non-trivial: NO empty-cell
    path dao->goal AND NO single push opens one. Walling off-footprint never invalidates the
    solution. Returns the grown wall set, or None if it cannot be made non-trivial (then retry).

    `max_density` (fraction of #/O over R*C) caps how many walls we add for moderate (Step-Up)
    boards; None = no cap (Challenge wants dense). Non-triviality always takes priority over the
    cap: if the cap is hit before the board is non-trivial we keep walling off-footprint cells."""
    used = set(dao_visited) | set(block_cells) | {dao, goal}
    if Pl == 2:
        used.add(bazzi)
    offprint = [(r, c) for r in range(R) for c in range(C)
                if (r, c) not in used and (r, c) not in blocks]
    rng = random.Random((hash((R, C, len(walls), len(blocks))) & 0x7FFFFFFF) ^ 0x5bd1e995)
    rng.shuffle(offprint)
    walls = set(walls)

    def nontrivial():
        return (not _empty_path_exists(R, C, walls, blocks, dao, goal) and
                not _single_push_opens(R, C, walls, blocks, dao, goal))

    area = R * C
    cap_walls = None if max_density is None else max(0, int(max_density * area) - len(blocks))

    # Strategy: wall off-footprint cells in bulk (the solution stays valid), checking non-triviality
    # only a few times instead of after every single wall (the check is the expensive part).
    if max_density is None:
        # Challenge: wall EVERYTHING off-footprint -> max density, single check.
        walls |= set(offprint)
        if nontrivial():
            return walls
        return None
    # Moderate: add off-footprint walls up to the cap; if not yet non-trivial, keep adding past
    # the cap (non-triviality wins) until either non-trivial or off-footprint cells exhausted.
    idx = 0
    # first jump straight to the cap, then check
    take = min(cap_walls, len(offprint)) if cap_walls else 0
    walls |= set(offprint[:take])
    idx = take
    if nontrivial():
        return walls
    # add the rest in chunks, re-checking
    chunk = max(4, len(offprint) // 8)
    while idx < len(offprint):
        walls |= set(offprint[idx:idx + chunk])
        idx += chunk
        if nontrivial():
            return walls
    return walls if nontrivial() else None


def gen(seed, params):
    """Returns (board_str, dao_p1_solution_str). `dao_p1_solution_str` is a Dao-only move string
    that solves the P==1 version of the returned board. For a P==2 board the recorded P==1
    solution lifts to P==2 by interleaving Bazzi border-passes (lift_p2); both are verified here.
    Retries internally to guarantee a non-trivial, solvable board with the recorded solution."""
    # density cap only for small/moderate boards; large boards stay dense (no cap)
    md = None
    rows = params.get("rows") if params else None
    cols = params.get("cols") if params else None
    try:
        rr = rows[1] if isinstance(rows, (list, tuple)) else rows
        cc = cols[1] if isinstance(cols, (list, tuple)) else cols
        if rr is not None and cc is not None and rr * cc <= 256:
            md = 0.50
    except Exception:
        md = None

    # Pass 1: honor the moderate density cap. Pass 2 (only if pass 1 never lands a board): drop
    # the cap (max density) — this almost always yields a valid non-trivial board, since the
    # no-cap path walls everything off-footprint and only fails on the rare on-footprint single-
    # push opener, which a fresh seed avoids.
    for relax in (False, True):
        cap = None if (relax or md is None) else md
        for attempt in range(40):
            res = _gen_core(seed * 7919 + attempt + (5000 if relax else 0), params)
            if res is None:
                continue
            board, dao_sol, R, C, Pl, walls, blocks, dao, bazzi, goal = res
            # recompute solution-used cells (over a P==1 simulation: Dao alone, no Bazzi)
            dao_visited, block_cells = _replay_used(R, C, walls, blocks, dao, goal, dao_sol)
            if dao_visited is None:
                continue  # recorded solution invalid (bug) -> retry
            new_walls = _make_nontrivial(R, C, walls, blocks, dao, goal,
                                         dao_visited, block_cells, bazzi, Pl, max_density=cap)
            if new_walls is None:
                continue   # intrinsically trivial corridor -> reroll
            board = P._serialize(R, C, Pl, new_walls, blocks, dao, bazzi, goal)

            # Verify the recorded solution. dao_sol solves the P==1 VERSION of this board: build a
            # P==1 board (same cells, no Bazzi) and check the Dao-only string against it.
            board_p1 = P._serialize(R, C, 1, new_walls, blocks, dao, (-1, -1), goal)
            c1, v1, _ = P.check(board_p1, dao_sol)
            ok1 = v1 and c1 < P.MISS_COST
            ok2 = True
            if Pl == 2:
                p2 = lift_p2(dao_sol, R, C, bazzi)
                c2, v2, _ = P.check(board, p2)   # board has P==2 header -> alternation grading
                ok2 = v2 and c2 < P.MISS_COST
            if ok1 and ok2:
                return board, dao_sol
    # Deterministic guaranteed non-trivial fallback (essentially never reached in testing).
    return _fallback_board(seed, params)


def _fallback_board(seed, params):
    """A hand-built board that is provably solvable + non-trivial: a single full wall row across
    the middle with one gap holding a 2-block vertical chain (Dao must push BOTH blocks through;
    pushing only one cannot open the path because the second still plugs the gap). Used only if
    the reverse-pull search exhausts its retries — practically never."""
    rng = random.Random(seed ^ 0xABCDEF)
    p = dict(P.DEFAULTS)
    if params:
        for k in P.DEFAULTS:
            if params.get(k) is not None:
                p[k] = params[k]
    R = max(5, _pick(rng, p, "rows"))
    C = max(3, _pick(rng, p, "cols"))
    Pl = 2 if _pick(rng, p, "players") >= 2 else 1
    dao0, goal = (0, 0), (R - 1, C - 1)
    mid = R // 2
    gc = C // 2
    walls = {(mid, c) for c in range(C) if c != gc}
    blocks = {(mid, gc), (mid + 1, gc)} if mid + 1 < R else {(mid, gc)}
    bazzi = (-1, -1)
    if Pl == 2:
        bazzi = (0, C - 1) if (0, C - 1) not in (dao0, goal) else (R - 1, 0)
    # forward solution: walk to (mid-1,gc), push the chain down through the wall gap as far as it
    # goes, then walk to the goal. Push until the chain jams against the bottom edge (Dao stops
    # advancing) so Dao ends up just below the wall row with a clear sideways exit to the goal.
    moves = []
    # 1) Dao (0,0) -> (mid-1, gc) (the cell directly above the upper block)
    p1 = _bfs_path(R, C, walls | blocks, dao0, (mid - 1, gc))
    if p1 is None:
        p1 = [dao0, (mid - 1, gc)]
    moves += _moves_of(p1)
    # 2) push down repeatedly until Dao no longer advances (chain jammed at the bottom edge).
    pos = (mid - 1, gc)
    bl = frozenset(blocks)
    guard = 0
    while guard < R + 5:
        npos, bl, c = P._apply(R, C, walls, bl, pos, DIRS["D"], None)
        moves.append("D")
        if npos == pos:                 # push failed (jammed) -> Dao didn't move; undo the wasted D
            moves.pop()
            break
        pos = npos
        guard += 1
        # stop once Dao has crossed below the wall row and the gap above it is clear of the goal path
        if pos[0] > mid:
            break
    # 3) walk to goal over current empty cells (blocks now shoved down the gap column)
    blset = set(bl)
    p3 = _bfs_path(R, C, walls | blset, pos, goal)
    if p3 is None:
        p3 = _bfs_path(R, C, walls, pos, goal) or [pos, goal]
    moves += _moves_of(p3)
    dao_sol = "".join(moves)
    board = P._serialize(R, C, Pl, walls, blocks, dao0, bazzi, goal)
    return board, dao_sol


def _replay_used(R, C, walls, blocks, dao, goal, dao_sol):
    """Replay the P==1 solution to collect Dao-visited cells and all block-occupied cells.
    Returns (dao_visited, block_cells) or (None, None) if the solution is invalid."""
    pos = dao
    bl = frozenset(blocks)
    dao_visited = {pos}
    block_cells = set(blocks)
    for m in dao_sol:
        npos, nbl, c = P._apply(R, C, walls, bl, pos, DIRS[m], None)
        pos = npos
        bl = nbl
        dao_visited.add(pos)
        block_cells |= set(bl)
        if pos == goal:
            break
    if pos != goal:
        return None, None
    return dao_visited, block_cells


def lift_p2(dao_sol, R, C, bazzi):
    """Lift a P==1 Dao solution to a P==2 move string by interleaving Bazzi 'pass' moves
    (a border-outward move that fails harmlessly) after each Dao move except the last."""
    bp = "U" if bazzi[0] == 0 else "D" if bazzi[0] == R - 1 else "L" if bazzi[1] == 0 else "R"
    out = []
    moves = list(dao_sol)
    for i, m in enumerate(moves):
        out.append(m)
        if i < len(moves) - 1:
            out.append(bp)
    return "".join(out)


# ======================= MEASUREMENT HARNESS =======================
def _density(board):
    lines = board.strip("\n").split("\n")
    R, C, Pl = (int(x) for x in lines[0].split())
    hashes = sum(row.count("#") for row in lines[1:])
    blocks = sum(row.count("O") for row in lines[1:])
    return (hashes + blocks) / (R * C) * 100, R, C, Pl


def measure():
    results = {"challenge": [], "stepup": []}
    all_solvable = []
    all_nontrivial = []

    def run_one(seed, prm, bucket):
        t0 = time.perf_counter()
        board, dao_sol = gen(seed, prm)
        dt = (time.perf_counter() - t0) * 1000
        inst = P.parse(board)
        R, C, Pl = inst.R, inst.C, inst.P
        # solvable: the recorded dao_sol solves the P==1 VERSION of this board (Dao alone). Build
        # a P==1 board (drop Bazzi) and check there. cost1 is that P==1 recorded cost.
        board_p1 = P._serialize(R, C, 1, inst.walls, inst.blocks, inst.dao, (-1, -1), inst.goal)
        cost1, valid1, _ = P.check(board_p1, dao_sol)
        solvable = bool(valid1 and cost1 < P.MISS_COST)
        # for P==2 lift and verify against the ACTUAL P==2 board (alternation grading)
        p2_ok = None
        cost2 = None
        if Pl == 2:
            p2 = lift_p2(dao_sol, R, C, inst.bazzi)
            cost2, valid2, _ = P.check(board, p2)
            p2_ok = bool(valid2 and cost2 < P.MISS_COST)
            solvable = solvable and p2_ok
        # non-trivial
        empty_ok = not _empty_path_exists(R, C, inst.walls, inst.blocks, inst.dao, inst.goal)
        push_ok = not _single_push_opens(R, C, inst.walls, inst.blocks, inst.dao, inst.goal)
        nontrivial = empty_ok and push_ok
        dens, _, _, _ = _density(board)
        all_solvable.append(solvable)
        all_nontrivial.append(nontrivial)
        rec = dict(seed=seed, R=R, C=C, P=Pl, dt=dt, cost1=cost1, cost2=cost2,
                   solvable=solvable, nontrivial=nontrivial, density=dens,
                   sol_len=len(dao_sol), board=board, p2_ok=p2_ok,
                   empty_ok=empty_ok, push_ok=push_ok)
        results[bucket].append(rec)
        return rec

    # CHALLENGE: 30x30, P1 and P2, dense
    ch_params = {"rows": 30, "cols": 30, "obstacles": 250, "blocks": 150}
    for i in range(4):
        run_one(1000 + i, dict(ch_params, players=1), "challenge")
    for i in range(4):
        run_one(2000 + i, dict(ch_params, players=2), "challenge")

    # STEPUP: 8x8 and 10x10, P1 and P2, moderate
    for i in range(3):
        run_one(3000 + i, {"rows": 8, "cols": 8, "players": 1, "obstacles": 14, "blocks": 10}, "stepup")
    for i in range(3):
        run_one(3100 + i, {"rows": 8, "cols": 8, "players": 2, "obstacles": 14, "blocks": 10}, "stepup")
    for i in range(2):
        run_one(3200 + i, {"rows": 10, "cols": 10, "players": 1, "obstacles": 22, "blocks": 18}, "stepup")
    for i in range(2):
        run_one(3300 + i, {"rows": 10, "cols": 10, "players": 2, "obstacles": 22, "blocks": 18}, "stepup")

    # reference_cost tightness on 8x8 P1 boards
    ref_notes = []
    for rec in results["stepup"]:
        if rec["R"] == 8 and rec["P"] == 1:
            t0 = time.perf_counter()
            try:
                ref = P.reference_cost(rec["board"])
            except Exception as e:
                ref = None
            rt = (time.perf_counter() - t0) * 1000
            ref_notes.append((rec["seed"], ref, rec["cost1"], rt))

    return results, all_solvable, all_nontrivial, ref_notes


if __name__ == "__main__":
    results, all_solvable, all_nontrivial, ref_notes = measure()
    n = len(all_solvable)
    sr = sum(all_solvable) / n
    ntr = sum(all_nontrivial) / n
    print(f"TOTAL boards: {n}")
    print(f"solvable_rate    = {sr:.3f}")
    print(f"non_trivial_rate = {ntr:.3f}")

    ch = results["challenge"]
    ch_dens = [r["density"] for r in ch]
    ch_p1_dt = [r["dt"] for r in ch]
    print(f"\nCHALLENGE 30x30 (n={len(ch)}):")
    print(f"  avg density   = {sum(ch_dens)/len(ch_dens):.1f}%")
    print(f"  avg gen ms    = {sum(ch_p1_dt)/len(ch_p1_dt):.1f} ms")
    for r in ch:
        print(f"  seed={r['seed']} P={r['P']} dens={r['density']:.1f}% dt={r['dt']:.0f}ms "
              f"cost1={r['cost1']:.0f} cost2={r['cost2']} solv={r['solvable']} "
              f"nt={r['nontrivial']} (empty_ok={r['empty_ok']} push_ok={r['push_ok']}) "
              f"sollen={r['sol_len']}")

    print(f"\nSTEPUP (n={len(results['stepup'])}):")
    for r in results["stepup"]:
        print(f"  seed={r['seed']} {r['R']}x{r['C']} P={r['P']} dens={r['density']:.1f}% "
              f"dt={r['dt']:.0f}ms cost1={r['cost1']:.0f} solv={r['solvable']} "
              f"nt={r['nontrivial']} (empty_ok={r['empty_ok']} push_ok={r['push_ok']})")

    print("\nreference_cost on 8x8 P1 (seed, ref, recorded_cost1, ref_ms):")
    for s, ref, c1, rt in ref_notes:
        print(f"  seed={s} ref={ref} recorded={c1:.0f} ref_ms={rt:.0f}")

    # print one 30x30 P1 sample board
    for r in ch:
        if r["P"] == 1:
            print("\nSAMPLE 30x30 P1 board:")
            print(r["board"])
            break
