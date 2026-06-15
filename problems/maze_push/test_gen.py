"""maze_push: checker/solver/generator tests. Run: python problems/maze_push/test_gen.py"""
from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(__file__))
import problem as P  # noqa: E402


def test_straight_line():
    g = "1 5 1\nD...G\n"
    assert P.reference_cost(g) == 4.0, P.reference_cost(g)
    cost, valid, _ = P.check(g, "RRRR")
    assert valid and cost == 4.0, (valid, cost)


def test_push_chain_cost():
    # D O . G .  — Dao must push the block across the row to reach G(0,3).
    g = "1 5 1\nDO.G.\n"
    cost, valid, _ = P.check(g, "RRR")
    # R: push 1 block (1,)->cost2, dao(0,1); R: push ->cost2, dao(0,2); R: push ->cost2, dao(0,3)=G
    assert valid and cost == 6.0, (valid, cost)
    assert P.reference_cost(g) == 6.0, P.reference_cost(g)


def test_fail_into_wall_costs_one():
    # Dao at (0,0), wall to the right; moving R fails but costs 1; D reaches G below.
    g = "2 2 1\nD#\nG.\n"
    cost, valid, _ = P.check(g, "RD")   # R fails(+1), D moves to (1,0)=G(+1) -> cost 2
    assert valid and cost == 2.0, (valid, cost)


def test_not_reached_is_penalty():
    cost, valid, _ = P.check("1 5 1\nD...G\n", "RR")   # stops short
    assert valid and cost == float(P.MISS_COST), (valid, cost)


def test_bad_char_invalid():
    cost, valid, _ = P.check("1 5 1\nD...G\n", "RX")
    assert (not valid) and cost is None, (valid, cost)


def test_generate_solvable_and_consistent_c1():
    params = {"rows": 5, "cols": 5, "players": 1, "obstacles": 2, "blocks": 3}
    for seed in range(6):
        g = P.generate(seed, params)
        assert g.split("\n")[0] == "5 5 1", g.split("\n")[0]
        sol = P.sample_solution(g)
        cost, valid, msg = P.check(g, sol)
        ref = P.reference_cost(g)
        assert valid and cost == ref, (seed, valid, cost, ref, msg)   # solver == checker
        assert cost < P.MISS_COST, (seed, cost)                        # actually reaches G


def test_generate_solvable_and_consistent_c2():
    params = {"rows": 4, "cols": 4, "players": 2, "obstacles": 1, "blocks": 2}
    for seed in range(4):
        g = P.generate(seed, params)
        assert g.split("\n")[0] == "4 4 2", g.split("\n")[0]
        sol = P.sample_solution(g)
        cost, valid, _ = P.check(g, sol)
        assert valid and cost == P.reference_cost(g) and cost < P.MISS_COST, (seed, cost)


def test_feature_ranges_in_generate():
    # dust... here blocks count as a RANGE should vary across seeds (Challenge subtasks)
    counts = set()
    for seed in range(20):
        g = P.generate(seed, {"rows": 6, "cols": 6, "players": 1, "obstacles": [1, 3], "blocks": [2, 5]})
        counts.add(g.count("O"))
    assert len(counts) > 1, counts   # block count actually varies within the range


def test_c2_bazzi_starts_on_border():
    # Bazzi on the border can always "pass" (move outward) -> the P=1 solvability oracle is
    # a valid proof that the C=2 board is solvable too.
    for seed in range(8):
        g = P.generate(seed, {"rows": 7, "cols": 7, "players": 2, "obstacles": 4, "blocks": 4})
        bz = P.parse(g).bazzi
        assert bz[0] in (0, 6) or bz[1] in (0, 6), (seed, bz)


def test_c2_constructively_solvable():
    # the generator's chokepoint corridor yields a VALID C=2 solution (Dao path + Bazzi passes)
    # reaching the goal — the solvability witness (an exact Sokoban solve is intractable here).
    for seed in range(12):
        g = P.generate(seed, {"rows": 8, "cols": 8, "players": 2, "obstacles": 6, "blocks": 7})
        w = P.corridor_witness(P.parse(g))
        assert w, (seed, "no corridor witness")
        cost, valid, _ = P.check(g, w)
        assert valid and cost < P.MISS_COST, (seed, cost, valid)


def test_dao_goal_fixed_corners():
    # Dao always starts top-left (0,0); goal is always bottom-right (N-1, M-1). Only Bazzi roams.
    for seed in range(5):
        for params in ({"rows": 7, "cols": 9, "players": 1, "obstacles": 5, "blocks": 6},
                       {"rows": 12, "cols": 8, "players": 2, "obstacles": 8, "blocks": 12}):
            inst = P.parse(P.generate(seed, params))
            assert inst.dao == (0, 0), inst.dao
            assert inst.goal == (inst.R - 1, inst.C - 1), (inst.goal, inst.R, inst.C)
            if inst.P == 2:
                bz = inst.bazzi
                assert bz not in (inst.dao, inst.goal), bz
                assert bz[0] in (0, inst.R - 1) or bz[1] in (0, inst.C - 1), bz   # on the border


def test_small_boards_require_pushing():
    # The generator prefers boards where reaching the goal REQUIRES a push (no block-free walk).
    # On small boards it can seal them, so nearly all should require pushing.
    from collections import deque

    def reachable_without_push(g):
        inst = P.parse(g)
        blocked = set(inst.walls) | set(inst.blocks)
        seen = {inst.dao}
        q = deque([inst.dao])
        while q:
            cur = q.popleft()
            if cur == inst.goal:
                return True
            for dr, dc in P.DIRS.values():
                n = (cur[0] + dr, cur[1] + dc)
                if 0 <= n[0] < inst.R and 0 <= n[1] < inst.C and n not in blocked and n not in seen:
                    seen.add(n)
                    q.append(n)
        return False

    # a preset-style medium board with enough blocks to seal — most should require a push
    strong = sum(not reachable_without_push(P.generate(s, {"rows": 10, "cols": 10, "players": 1, "obstacles": 10, "blocks": 30})) for s in range(10))
    assert strong >= 6, f"only {strong}/10 medium boards require a push"


def test_block_cannot_overlap_player():
    # a block may NOT be pushed onto the other player (Bazzi) — the push fails, no overlap
    # board: D(0,0) O(0,1) Z(0,2) .(0,3) G(0,4); Dao 'R' would push the block onto Bazzi
    cost, valid, _ = P.check("1 5 2\nDOZ.G\n", "R")
    assert valid and cost == float(P.MISS_COST), (valid, cost)   # blocked push, goal not reached
    # _apply directly: chain end == other player -> failed move, blocks unchanged, cost 1
    assert P._apply(1, 5, set(), frozenset({(0, 1)}), (0, 0), (0, 1), (0, 2)) == ((0, 0), frozenset({(0, 1)}), 1)
    # without an other player the same push succeeds (block advances)
    npos, nb, c = P._apply(1, 5, set(), frozenset({(0, 1)}), (0, 0), (0, 1), None)
    assert npos == (0, 1) and (0, 2) in nb and c == 2, (npos, nb, c)


def test_block_can_pass_through_goal():
    # a block sitting on the goal must be pushable off it so Dao can enter (goal isn't a wall)
    cost, valid, _ = P.check("1 5 1\nDO.G.\n", "RRR")   # block: (0,1)->(0,2)->goal(0,3)->(0,4); Dao ends on goal
    assert valid and cost == 6.0, (valid, cost)


def test_raised_density_places_past_old_max():
    # obstacle/block maxes were raised well past the old 60; a dense board places them in full.
    g = P.generate(1, {"rows": 20, "cols": 20, "players": 1, "obstacles": 150, "blocks": 80})
    assert g.count("#") > 60 and g.count("O") > 60, (g.count("#"), g.count("O"))
    # an exact Sokoban solve is intractable at this density (that's why such boards belong in the
    # Challenge, not Step Up). Solvability is guaranteed by construction — verified via the witness.
    w = P.corridor_witness(P.parse(g))
    assert w is not None
    cost, valid, _ = P.check(g, w)
    assert valid and cost < P.MISS_COST, (cost, valid)


def test_cut_forces_push_all_sizes():
    # the chokepoint barrier guarantees a push is required on boards of ANY size (incl. 30x30),
    # and the corridor witness reaches the goal on every one of them.
    for (R, C, Pl, W, B) in [(7, 7, 1, 5, 8), (10, 10, 2, 10, 20), (20, 20, 1, 80, 80),
                             (30, 30, 1, 100, 200), (30, 30, 2, 150, 250)]:
        for seed in range(3):
            g = P.generate(seed, {"rows": R, "cols": C, "players": Pl, "obstacles": W, "blocks": B})
            inst = P.parse(g)
            assert not P._reachable_without_push(inst.R, inst.C, inst.walls, inst.blocks, inst.dao, inst.goal), (R, C, seed)
            w = P.corridor_witness(inst)
            cost, valid, _ = P.check(g, w or "")
            assert w and valid and cost < P.MISS_COST, (R, C, seed, cost, valid)


def test_stepup_reference_is_consistent():
    # Step Up needs an EXACT full-marks reference; on solver-tractable sizes the optimum is found
    # and the solver agrees with the checker (sample_solution costs exactly reference_cost).
    for (R, C, Pl, W, B) in [(5, 5, 1, 5, 8), (6, 6, 2, 5, 10), (8, 8, 1, 4, 8)]:
        for seed in range(4):
            g = P.generate(seed, {"rows": R, "cols": C, "players": Pl, "obstacles": W, "blocks": B})
            ref = P.reference_cost(g)
            assert ref < P.MISS_COST, (R, C, seed, ref)
            cost, valid, _ = P.check(g, P.sample_solution(g))
            assert valid and cost == ref, (R, C, seed, cost, ref)


if __name__ == "__main__":
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and isinstance(o, types.FunctionType)]
    failed = 0
    for name, fn in tests:
        try:
            fn(); print(f"  ok   {name}")
        except AssertionError as e:
            failed += 1; print(f"  FAIL {name}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1; print(f"  ERR  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
