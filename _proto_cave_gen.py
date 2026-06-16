"""
Prototype board generator for maze_push: cellular-automata cave + guaranteed connector.

gen(seed, params) -> (board_str, dao_p1_solution_str)

Strategy (organic CA cave + carved push-corridor):
  1. Random ~45% wall fill, a few smoothing iterations (count wall neighbours) -> blobby cave.
     This is the visual texture that makes the board look hand-made / natural.
  2. Carve a wandering empty route dao->goal through the cave (weighted Dijkstra with random
     jitter so it meanders organically rather than running straight).
  3. GUARANTEE a solution + force pushes: split the route into straight RUNS joined by turns.
     On >=2 interior runs put a pushable block; Dao pushes it forward along the run and, at the
     bend, one final push EJECTS it off the route into a side pocket (the route turns there, so
     "straight ahead" is off-route). Dao then follows the turn, advancing monotonically and
     never recrossing an ejected block -> the route stays clear for later on-route walks. Record
     the exact walk+push+eject+walk move string -- guarantees solvability without a hard solve.
  4. Density: fill EVERY non-corridor open cell with a wall in one shot (safe: walks stay on the
     route, all unpushed blocks are ahead, ejected blocks are off-route), then trim walls back
     toward the obstacle budget W. Buried walls (no open neighbour) are removed with no check;
     boundary walls are removed only while non-triviality holds. Top up blocks toward B.
  5. Non-triviality (the trim guard + a final check): with the route the unique dao->goal
     corridor, each block's START cell is a genuine cut -> no zero-push empty path, and with >=2
     separated cuts no single push opens one (the other cut still blocks). Verify-retry on fail.
  6. P=2: interleave a Bazzi border "pass" after each Dao move except the last; verify with P.check.
"""

import sys, os, random, heapq, time
from collections import deque

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "problems", "maze_push"))
import problem as P

DIRS = [(-1, 0), (1, 0), (0, -1), (0, 1)]
CHAR = {(-1, 0): "U", (1, 0): "D", (0, -1): "L", (0, 1): "R"}


def _pick(rng, params, key, default):
    v = params.get(key, default)
    if v is None:
        v = default
    if isinstance(v, (list, tuple)) and len(v) == 2:
        lo, hi = int(v[0]), int(v[1])
        return rng.randint(min(lo, hi), max(lo, hi))
    return int(v)


def _in(R, C, r, c):
    return 0 <= r < R and 0 <= c < C


def _bfs_reach(R, C, blocked, src, dst):
    if src == dst:
        return True
    seen = {src}
    q = deque([src])
    while q:
        r, c = q.popleft()
        for dr, dc in DIRS:
            nr, nc = r + dr, c + dc
            if _in(R, C, nr, nc) and (nr, nc) not in blocked and (nr, nc) not in seen:
                if (nr, nc) == dst:
                    return True
                seen.add((nr, nc))
                q.append((nr, nc))
    return False


def _bfs_path(R, C, blocked, src, dst):
    """Shortest path src->dst avoiding `blocked`, as a list of cells, or None."""
    if src == dst:
        return [src]
    prev = {src: None}
    q = deque([src])
    while q:
        cur = q.popleft()
        for dr, dc in DIRS:
            nr, nc = cur[0] + dr, cur[1] + dc
            nxt = (nr, nc)
            if _in(R, C, nr, nc) and nxt not in blocked and nxt not in prev:
                prev[nxt] = cur
                if nxt == dst:
                    out = [nxt]
                    while prev[out[-1]] is not None:
                        out.append(prev[out[-1]])
                    return out[::-1]
                q.append(nxt)
    return None


def _moves_of(path):
    return [CHAR[(b[0] - a[0], b[1] - a[1])] for a, b in zip(path, path[1:])]


def _ca_cave(rng, R, C, fill, iters):
    """Cellular-automata cave. Returns a set of wall cells (organic blobby look)."""
    wall = [[rng.random() < fill for _ in range(C)] for _ in range(R)]
    for r in range(R):  # border biased toward wall -> closed, organic boundary
        for c in range(C):
            if (r in (0, R - 1) or c in (0, C - 1)) and rng.random() < 0.55:
                wall[r][c] = True
    for _ in range(iters):
        nw = [[False] * C for _ in range(R)]
        for r in range(R):
            for c in range(C):
                cnt = 0
                for dr in (-1, 0, 1):
                    for dc in (-1, 0, 1):
                        if dr == 0 and dc == 0:
                            continue
                        nr, nc = r + dr, c + dc
                        if not _in(R, C, nr, nc) or wall[nr][nc]:
                            cnt += 1  # out-of-bounds counts as wall
                nw[r][c] = cnt >= 5 if wall[r][c] else cnt >= 6  # 4-5 rule
        wall = nw
    return {(r, c) for r in range(R) for c in range(C) if wall[r][c]}


def _route(rng, R, C, walls, dao, goal):
    """Carve a meandering route dao->goal: weighted Dijkstra (open=1, carve-a-wall=3) with
    random jitter so the path wanders organically. Returns the ordered list of route cells."""
    INF = float("inf")
    dist = {dao: 0.0}
    prev = {dao: None}
    pq = [(0.0, rng.random(), dao)]
    while pq:
        d, _, cur = heapq.heappop(pq)
        if cur == goal:
            break
        if d > dist.get(cur, INF):
            continue
        for dr, dc in DIRS:
            nr, nc = cur[0] + dr, cur[1] + dc
            if not _in(R, C, nr, nc):
                continue
            nxt = (nr, nc)
            step = (3.0 if nxt in walls else 1.0) + rng.random() * 1.6
            nd = d + step
            if nd < dist.get(nxt, INF):
                dist[nxt] = nd
                prev[nxt] = cur
                heapq.heappush(pq, (nd, rng.random(), nxt))
    if goal not in prev:
        return None
    path = [goal]
    while prev[path[-1]] is not None:
        path.append(prev[path[-1]])
    return path[::-1]


def gen(seed, params):
    rng = random.Random(seed)
    R = max(3, _pick(rng, params, "rows", 7))
    C = max(3, _pick(rng, params, "cols", 7))
    players = 2 if _pick(rng, params, "players", 1) >= 2 else 1
    W = max(0, _pick(rng, params, "obstacles", 5))
    B = max(0, _pick(rng, params, "blocks", 6))

    dao, goal = (0, 0), (R - 1, C - 1)
    if players == 2:
        border = [(r, c) for r in range(R) for c in range(C)
                  if (r in (0, R - 1) or c in (0, C - 1)) and (r, c) not in (dao, goal)]
        rng.shuffle(border)
        bazzi = border[0] if border else (0, 1)
    else:
        bazzi = (-1, -1)
    occupied = {dao, goal} | ({bazzi} if players == 2 else set())

    for attempt in range(60):
        rng2 = random.Random(seed * 1009 + attempt)
        res = _build_once(rng2, R, C, players, W, B, dao, goal, bazzi, occupied)
        if res is not None:
            return res
    raise RuntimeError(f"gen failed after retries (seed={seed}, {R}x{C} P{players})")


def _build_once(rng, R, C, players, W, B, dao, goal, bazzi, occupied):
    walls = _ca_cave(rng, R, C, fill=0.45, iters=4)
    for cell in occupied:                       # keep player/goal cells open
        walls.discard(cell)
    for (br, bc) in list(occupied):             # and a 1-pocket around them
        for dr, dc in DIRS:
            walls.discard((br + dr, bc + dc))

    route = _route(rng, R, C, walls, dao, goal)
    if route is None:
        return None
    route_set = set(route)
    walls -= route_set                          # carve the route open
    n = len(route)
    if n < 6:
        return None

    # --- pick chokepoints: forward push that EJECTS the block off the route at a bend --------
    # The route is a sequence of straight RUNS joined by turns. On an interior run route[lo..hi]
    # (hi is the bend), put a block on route[start] (start in (lo, hi]) and have Dao push it
    # FORWARD along the run; at the bend cell route[hi] one final push sends the block STRAIGHT
    # into pocket = route[hi] + run_dir, which is OFF the route (the route turns there) -> the
    # route is left fully clear. Dao ends on route[hi] and follows the turn onward, advancing
    # monotonically and never recrossing the ejected block. With the route the unique dao->goal
    # corridor (after wall-fill) and the block's START cell route[start] being a real cut, the
    # board is non-trivial; two such cuts make a single push never open a path.
    # Anchor: (start_idx, hi_idx, pocket_cell). The block is pushed start->...->hi->pocket.
    runs = []
    si = 0
    while si < n - 1:
        d0 = (route[si + 1][0] - route[si][0], route[si + 1][1] - route[si][1])
        e = si + 1
        while e < n - 1 and (route[e + 1][0] - route[e][0], route[e + 1][1] - route[e][1]) == d0:
            e += 1
        runs.append((si, e, d0))                # route[si..e] colinear in direction d0; turn at e
        si = e
    cand = []
    for (lo, hi, d0) in runs:
        if hi >= n - 1:
            continue                            # the final run ends at the goal: no bend to eject at
        if hi - lo < 2:
            continue                            # need behind, >=1 intermediate push, then eject
        pocket = (route[hi][0] + d0[0], route[hi][1] + d0[1])
        if not _in(R, C, *pocket) or pocket in route_set or pocket in occupied:
            continue
        start = lo + 1                          # block starts one cell into the run (behind = route[lo])
        if start >= hi:
            continue
        if route[start] in occupied or route[hi] in occupied:
            continue
        cand.append((start, hi, pocket))
    rng.shuffle(cand)
    want = 2
    chosen = []
    used_pockets = set()
    for (start, hi, pocket) in cand:
        if pocket in used_pockets:
            continue
        if all(abs(start - c_s) >= 3 and (hi < c_s or start > c_hi) for (c_s, c_hi, _) in chosen) \
           or not chosen:
            if all(start > c_hi or hi < c_s for (c_s, c_hi, _) in chosen):  # disjoint ranges
                chosen.append((start, hi, pocket))
                used_pockets.add(pocket)
        if len(chosen) >= want:
            break
    if len(chosen) < want:
        return None                             # need >=2 separable ejectable blocks; re-roll
    chosen.sort()

    blocks = set()
    push_anchors = []   # (start_idx, hi_idx, pocket) — push route[start]..route[hi] then eject->pocket
    keep_open = set(route_set)
    for (start, hi, pocket) in chosen:
        blocks.add(route[start])
        push_anchors.append((start, hi, pocket))
        keep_open.add(pocket)                   # block's final resting cell stays clear

    # corridor must already solve (sanity before densifying)
    if _solve_route(R, C, walls, blocks, dao, goal, push_anchors, route) is None:
        return None

    # --- DENSIFY: seal every non-corridor open cell with a wall in ONE shot. ---------------
    # Why this keeps the corridor solvable WITHOUT a re-solve per cell: walks always stay on
    # the route (kept open). When walking to anchor_k's behind-cell, every still-unpushed block
    # is at anchor_j>=k -- i.e. strictly AHEAD of behind_k along the route -- so the on-route
    # walk never meets one. Off-route walls therefore can't break any walk. Sealing the
    # bypasses makes the corridor the unique dao->goal route -> each block is a genuine cut.
    open_cells = [(r, c) for r in range(R) for c in range(C)
                  if (r, c) not in walls and (r, c) not in blocks
                  and (r, c) not in occupied and (r, c) not in keep_open]
    walls.update(open_cells)

    # Trim walls toward the requested obstacle budget W. Removing a wall only ever HELPS
    # connectivity, so it can only threaten non-triviality (open a bypass / a single-push
    # opening), never solvability. Guard each removal with the FULL _is_nontrivial (the
    # border-blocks optimisation inside keeps it cheap) and check in BATCHES, reverting +
    # retrying singly only when a batch broke non-triviality. Using the full check here (rather
    # than a cheap no-empty-path proxy) keeps the FINAL non-triviality check passing first-try,
    # which avoids whole-board re-rolls -- a far bigger cost than a few extra BFS during trim.
    if len(walls) > W:
        target_remove = len(walls) - W
        removable = [w for w in walls if w not in keep_open]
        # A wall with NO open (non-wall, non-block) 4-neighbour is buried inside a wall mass:
        # removing it cannot change ANY reachability (it touches no traversable cell), so it is
        # ALWAYS safe to delete with no check. Drain these first -- on a dense cave most removals
        # are buried, so this clears the bulk of the budget gap with zero BFS.
        def buried(w):
            r, c = w
            for dr, dc in DIRS:
                nb = (r + dr, c + dc)
                if _in(R, C, *nb) and nb not in walls and nb not in blocks:
                    return False
            return True
        buried_walls = [w for w in removable if buried(w)]
        rng.shuffle(buried_walls)
        removed = 0
        for w in buried_walls:
            if removed >= target_remove:
                break
            walls.discard(w)
            removed += 1
        # The rest (boundary walls touching open cells) DO need the non-triviality guard.
        if removed < target_remove:
            rest = [w for w in walls if w not in keep_open and not buried(w)]
            rng.shuffle(rest)
            i = 0
            batch = max(8, (target_remove - removed) // 8 + 1)
            while removed < target_remove and i < len(rest):
                chunk = rest[i:i + batch]
                i += len(chunk)
                for w in chunk:
                    walls.discard(w)
                if _is_nontrivial(R, C, walls, blocks, dao, goal):
                    removed += len(chunk)
                else:
                    for w in chunk:
                        walls.add(w)
                    for w in chunk:
                        if removed >= target_remove:
                            break
                        walls.discard(w)
                        if _is_nontrivial(R, C, walls, blocks, dao, goal):
                            removed += 1
                        else:
                            walls.add(w)

    # --- top up blocks toward B on remaining open cells (must keep the corridor solvable) ---
    # Adding a block CAN break the corridor (a block dropped on a walk cell, or behind/ahead of
    # an anchor), so these stay guarded by a corridor re-solve.
    def corridor_ok():
        return _solve_route(R, C, walls, blocks, dao, goal, push_anchors, route) is not None

    open2 = [(r, c) for r in range(R) for c in range(C)
             if (r, c) not in walls and (r, c) not in blocks
             and (r, c) not in occupied and (r, c) not in keep_open]
    rng.shuffle(open2)
    for cell in open2:
        if len(blocks) >= B:
            break
        blocks.add(cell)
        if not corridor_ok():
            blocks.discard(cell)

    # --- final verification ----------------------------------------------------
    sol = _solve_route(R, C, walls, blocks, dao, goal, push_anchors, route)
    if sol is None:
        return None
    if not _is_nontrivial(R, C, walls, blocks, dao, goal):
        return None
    p1_board = P._serialize(R, C, 1, walls, blocks, dao, (-1, -1), goal)
    cost, valid, _ = P.check(p1_board, sol)
    if not (valid and cost is not None and cost < P.MISS_COST):
        return None

    board = P._serialize(R, C, players, walls, blocks, dao, bazzi, goal)
    if players == 2:
        bp = "U" if bazzi[0] == 0 else "D" if bazzi[0] == R - 1 else "L" if bazzi[1] == 0 else "R"
        out = []
        for i, m in enumerate(sol):
            out.append(m)
            if i < len(sol) - 1:
                out.append(bp)
        c2, v2, _ = P.check(board, "".join(out))
        if not (v2 and c2 is not None and c2 < P.MISS_COST):
            return None
    return board, sol


def _solve_route(R, C, walls, blocks, dao, goal, push_anchors, route):
    """Reconstruct Dao's walk+push-eject solution. Each anchor is (start, hi, pocket): walk the
    route to route[start-1] (behind the block on route[start]), push the block FORWARD along the
    straight run route[start]..route[hi], then one more push EJECTS it route[hi]->pocket (off the
    route). Dao ends on route[hi] and follows the route's turn. After all anchors, walk to the
    goal. Returns the move string, or None if any segment is unreachable."""
    cur = dao
    cur_blocks = set(blocks)
    moves = []
    ordered = sorted(push_anchors, key=lambda a: a[0])
    for (start, hi, pocket) in ordered:
        bcell = route[start]
        if bcell not in cur_blocks or pocket in walls or pocket in cur_blocks:
            return None
        behind = route[start - 1]
        path = _bfs_path(R, C, walls | cur_blocks, cur, behind)
        if path is None:
            return None
        moves += _moves_of(path)
        # Block sits on route[start]; push it forward through route[start+1..hi] then eject ->pocket.
        # `seq` = the cells the block passes THROUGH (its successive landing cells).
        seq = [route[k] for k in range(start + 1, hi + 1)] + [pocket]
        prev_cell = behind                        # Dao currently here (just before the block)
        bcur = bcell                              # block's current cell
        for nxt in seq:
            if bcur not in cur_blocks or nxt in walls or nxt in cur_blocks:
                return None
            step = (bcur[0] - prev_cell[0], bcur[1] - prev_cell[1])   # Dao steps onto the block cell
            if step not in CHAR:
                return None
            moves.append(CHAR[step])
            cur_blocks.discard(bcur); cur_blocks.add(nxt)
            prev_cell = bcur                       # Dao now stands where the block was
            bcur = nxt
        cur = route[hi]                            # Dao ends on the bend cell (last block-old cell)
    path = _bfs_path(R, C, walls | cur_blocks, cur, goal)
    if path is None:
        return None
    moves += _moves_of(path)
    return "".join(moves)


def _reach_set(R, C, blocked, src):
    """Set of cells reachable from src over non-`blocked` cells (4-dir)."""
    seen = {src}
    q = deque([src])
    while q:
        r, c = q.popleft()
        for dr, dc in DIRS:
            nr, nc = r + dr, c + dc
            if 0 <= nr < R and 0 <= nc < C and (nr, nc) not in blocked and (nr, nc) not in seen:
                seen.add((nr, nc))
                q.append((nr, nc))
    return seen


def _is_nontrivial(R, C, walls, blocks, dao, goal):
    """Non-trivial iff (a) no empty-cell path dao->goal (blocks impassable) AND (b) no single
    geometrically-valid push of any one block opens such a path.

    Speedup: compute Dao's empty-reachable region ONCE. A single push can open a dao->goal path
    only if it VACATES a block cell `b` that borders (or lies in) Dao's region -- otherwise the
    freed cell is somewhere Dao can't reach and can't extend the corridor. So we only run the
    (expensive) post-push reachability for blocks touching Dao's region. Sound: any push that
    truly bridges Dao to the goal must free a cell adjacent to Dao's current reach."""
    blocked = walls | blocks
    if _bfs_reach(R, C, blocked, dao, goal):
        return False
    rset = _reach_set(R, C, blocked, dao)
    if goal in rset:
        return False
    border_blocks = set()
    for (r, c) in rset:                          # blocks adjacent to Dao's empty region
        for dr, dc in DIRS:
            nb = (r + dr, c + dc)
            if nb in blocks:
                border_blocks.add(nb)
    for b in border_blocks:
        for d in DIRS:
            nb = _simulate_single_push(R, C, walls, blocks, b, d)
            if nb is not None and _bfs_reach(R, C, walls | nb, dao, goal):
                return False
    return True


def _simulate_single_push(R, C, walls, blocks, b, d):
    """New blocks set after pushing the chain starting at block b one cell in direction d,
    or None if illegal (chain end hits wall/edge). Conservative: ignores Dao reachability,
    so it counts ANY geometric single push -- a strict over-approximation for non-triviality."""
    dr, dc = d
    cr, cc = b
    while (cr, cc) in blocks:
        cr, cc = cr + dr, cc + dc
    end = (cr, cc)
    if not _in(R, C, end[0], end[1]) or end in walls:
        return None
    nb = set(blocks); nb.discard(b); nb.add(end)
    return nb


# ----------------------------------------------------------------------------
# Empirical measurement harness
# ----------------------------------------------------------------------------

def _density(board):
    body = board.strip().split("\n")[1:]
    wc = sum(ch == "#" for line in body for ch in line)
    oc = sum(ch == "O" for line in body for ch in line)
    inst = P.parse(board)
    return (wc + oc) / (inst.R * inst.C)


def _measure():
    results = []
    dense30 = []

    def evaluate(board, sol):
        # `sol` is the P=1 Dao-only string. For a P=2 board, lift it (interleave a Bazzi
        # border pass after each Dao move except the last) before checking.
        inst = P.parse(board)
        check_sol = sol
        if inst.P == 2:
            bz = inst.bazzi
            bp = "U" if bz[0] == 0 else "D" if bz[0] == inst.R - 1 else "L" if bz[1] == 0 else "R"
            out = []
            for i, m in enumerate(sol):
                out.append(m)
                if i < len(sol) - 1:
                    out.append(bp)
            check_sol = "".join(out)
        cost, valid, _ = P.check(board, check_sol)
        solvable = bool(valid and cost is not None and cost < P.MISS_COST)
        nt = _is_nontrivial(inst.R, inst.C, inst.walls, inst.blocks, inst.dao, inst.goal)
        return solvable, nt, _density(board), cost

    print("=== CHALLENGE 30x30 (obstacles=250, blocks=150) ===")
    for players in (1, 2):
        for k in range(4):
            seed = 1000 + players * 100 + k
            t0 = time.perf_counter()
            board, sol = gen(seed, {"rows": 30, "cols": 30, "players": players,
                                    "obstacles": 250, "blocks": 150})
            ms = (time.perf_counter() - t0) * 1000
            s, nt, dens, cost = evaluate(board, sol)
            rec = dict(size="30x30", P=players, seed=seed, solv=s, nt=nt, dens=dens, ms=ms, cost=cost)
            results.append(rec); dense30.append(rec)
            print(f"  P{players} seed{seed}: solv={s} nt={nt} dens={dens:.1%} ms={ms:.0f} cost={cost}")

    print("=== STEPUP 8x8 & 10x10 (moderate) ===")
    for (rr, cc) in ((8, 8), (10, 10)):
        for players in (1, 2):
            for k in range(2):
                seed = 2000 + rr * 10 + players * 3 + k
                t0 = time.perf_counter()
                board, sol = gen(seed, {"rows": rr, "cols": cc, "players": players,
                                        "obstacles": rr * cc // 3, "blocks": rr * cc // 6})
                ms = (time.perf_counter() - t0) * 1000
                s, nt, dens, cost = evaluate(board, sol)
                ref = None
                if rr <= 8:
                    try:
                        ref = P.reference_cost(board)
                    except Exception as e:
                        ref = f"ERR {e}"
                rec = dict(size=f"{rr}x{cc}", P=players, seed=seed, solv=s, nt=nt,
                           dens=dens, ms=ms, cost=cost, ref=ref)
                results.append(rec)
                print(f"  {rr}x{cc} P{players} seed{seed}: solv={s} nt={nt} dens={dens:.1%} "
                      f"ms={ms:.0f} cost={cost} ref={ref}")

    sr = sum(r["solv"] for r in results) / len(results)
    nr = sum(r["nt"] for r in results) / len(results)
    avg_dens = sum(r["dens"] for r in dense30) / len(dense30)
    avg_ms = sum(r["ms"] for r in dense30) / len(dense30)
    print("\n=== SUMMARY ===")
    print(f"N={len(results)} solvable_rate={sr:.3f} non_trivial_rate={nr:.3f}")
    print(f"avg_density_30={avg_dens:.1%} avg_ms_30={avg_ms:.0f}")
    costs30_p1 = [r["cost"] for r in dense30 if r["P"] == 1]
    print(f"30x30 P1 costs: {sorted(costs30_p1)}")
    return results, dense30


if __name__ == "__main__":
    _measure()
