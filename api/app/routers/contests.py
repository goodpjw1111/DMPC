"""Contest & problem read endpoints (auth-gated; standings hidden until contest end)."""

from __future__ import annotations

import sys
from fastapi import APIRouter, Depends, HTTPException, Response

from .. import db, grading
from ..deps import CurrentUser, assert_contest_ended, get_current_user

# judge core (registry) for missions/inputs
sys.path.insert(0, grading._JUDGE)
from grader import mission_budgets, mission_weights  # noqa: E402
from registry import effective_meta, load_problem  # noqa: E402
from scoring import weighted_total  # noqa: E402

router = APIRouter(prefix="/api", tags=["contests"])


def _as_dict(v) -> dict:
    """scoring_config arrives as a jsonb dict (asyncpg codec) or a JSON string."""
    import json
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (ValueError, TypeError):
            return {}
    return v or {}

# A problem's statement/inputs are only served once its contest has actually
# started (or finished). Draft/scheduled contests must not leak pre-release data.
_RELEASED = ("live", "ended", "archived")


async def _released_problem(pid: str):
    """Fetch a problem ONLY if its contest is released (started). 404 otherwise —
    closes the pre-start data-leak / IDOR: knowing a problem UUID is not enough."""
    p = await db.fetchrow(
        """SELECT p.*, c.status AS contest_status FROM problems p
           JOIN contests c ON c.id = p.contest_id WHERE p.id = $1""",
        pid,
    )
    if not p or p["contest_status"] not in _RELEASED:
        raise HTTPException(404, "not found")
    return p


@router.get("/contests")
async def list_contests(user: CurrentUser = Depends(get_current_user)):
    rows = await db.fetch(
        """
        SELECT id, title, status, starts_at, ends_at
        FROM contests WHERE status <> 'draft' ORDER BY starts_at DESC
        """
    )
    return [dict(r) for r in rows]


async def _stepup_score(problem_id: str, user_id: str) -> int:
    rows = await db.fetch(
        """SELECT mission_seed, max(score) AS best FROM stepup_submissions
           WHERE problem_id=$1 AND user_id=$2 GROUP BY mission_seed""",
        problem_id, user_id,
    )
    return sum(r["best"] for r in rows)


async def _last_challenge_score(cid: str, user_id: str) -> int:
    """The user's challenge score from the MOST RECENT published evaluation round
    (0 before any eval) — Challenge points only update at an eval (09·18시)."""
    row = await db.fetchrow(
        """SELECT s.challenge_score FROM standings s
           JOIN evaluation_rounds r ON r.id = s.round_id
           WHERE s.contest_id=$1 AND s.user_id=$2
             AND r.status='done' AND r.published_at IS NOT NULL
           ORDER BY r.scheduled_at DESC LIMIT 1""",
        cid, user_id,
    )
    return int(row["challenge_score"]) if row else 0


@router.get("/contests/{cid}")
async def contest_detail(cid: str, user: CurrentUser = Depends(get_current_user)):
    c = await db.fetchrow("SELECT * FROM contests WHERE id=$1 AND status<>'draft'", cid)
    if not c:
        raise HTTPException(404, "not found")
    problems = await db.fetch(
        "SELECT id, kind, title FROM problems WHERE contest_id=$1 ORDER BY kind", cid
    )
    # Step Up = live (each part out of 1e6); Challenge = last evaluated round (0 pre-eval).
    challenge_score = await _last_challenge_score(cid, user.id)
    out = []
    stepup_total = 0
    for p in problems:
        if p["kind"] == "stepup":
            score = await _stepup_score(str(p["id"]), user.id)
            stepup_total += score
        else:
            score = challenge_score      # contest-level challenge shown on the challenge card
        out.append({"id": str(p["id"]), "kind": p["kind"], "title": p["title"], "score": score})
    # total = 2:8 weighted blend of Step Up (live) and Challenge (last eval), out of 1e6.
    # clamp each part to 1e6 (guards >1 problem/kind summing past the cap).
    total = weighted_total(min(stepup_total, 1_000_000), min(challenge_score, 1_000_000))
    return {
        "id": str(c["id"]), "title": c["title"], "status": c["status"],
        "starts_at": c["starts_at"].isoformat(), "ends_at": c["ends_at"].isoformat(),
        "stepup_budget": c["stepup_budget"], "challenge_budget": c["challenge_budget"],
        "total": total, "problems": out,
    }


@router.get("/contests/{cid}/standings")
async def standings(cid: str, user: CurrentUser = Depends(get_current_user)):
    c = await db.fetchrow("SELECT ends_at FROM contests WHERE id=$1", cid)
    if not c:
        raise HTTPException(404, "not found")
    assert_contest_ended(c["ends_at"])      # 403 until the contest ends
    # `standings` is per-evaluation-round (UNIQUE(round_id,user_id)); a multi-day
    # contest has many rounds. Scope to the SINGLE final round that is fully graded
    # (status='done'), else every user appears once per round with stale ranks, or a
    # half-written/failed final round surfaces blank. Empty until the final commits.
    rows = await db.fetch(
        """SELECT u.nickname, s.total_score, s.rank
           FROM standings s JOIN users u ON u.id=s.user_id
           WHERE s.contest_id=$1
             AND s.round_id = (
                 SELECT id FROM evaluation_rounds
                 WHERE contest_id=$1 AND type='final'
                   AND status='done' AND published_at IS NOT NULL
                 ORDER BY scheduled_at DESC LIMIT 1
             )
           ORDER BY s.rank NULLS LAST, s.total_score DESC""",
        cid,
    )
    return [{"nickname": r["nickname"], "score": r["total_score"], "rank": r["rank"]} for r in rows]


@router.get("/contests/{cid}/my-eval")
async def my_eval(cid: str, user: CurrentUser = Depends(get_current_user)):
    """The CALLER'S OWN latest evaluation result (interim or final). Self-only — never
    exposes other contestants, so it is available DURING the contest (opponents stay
    hidden until end via the gated /standings). Returns the user's per-case score/rank
    and their own total/rank for the most recent published round."""
    # gate on _RELEASED (contest actually started), consistent with _released_problem —
    # not merely 'not a draft', so a not-yet-started contest never serves round data.
    c = await db.fetchrow("SELECT id, status FROM contests WHERE id=$1", cid)
    if not c or c["status"] not in _RELEASED:
        raise HTTPException(404, "not found")
    rnd = await db.fetchrow(
        """SELECT id, type, scheduled_at, published_at FROM evaluation_rounds
           WHERE contest_id=$1 AND status='done' AND published_at IS NOT NULL
           ORDER BY scheduled_at DESC LIMIT 1""",
        cid,
    )
    if not rnd:
        return {"round": None, "standing": None, "cases": []}
    mine = await db.fetchrow(
        """SELECT stepup_score, challenge_score, total_score, rank
           FROM standings WHERE round_id=$1 AND user_id=$2""",
        rnd["id"], user.id,
    )
    cases = await db.fetch(
        """SELECT problem_id, seed, verdict, raw_cost, runtime_ms, case_score, case_rank
           FROM case_results WHERE round_id=$1 AND user_id=$2
           ORDER BY problem_id, seed""",
        rnd["id"], user.id,
    )
    return {
        "round": {"id": str(rnd["id"]), "type": rnd["type"],
                  "scheduled_at": rnd["scheduled_at"].isoformat(),
                  "published_at": rnd["published_at"].isoformat()},
        "standing": (None if not mine else {
            "stepup_score": mine["stepup_score"], "challenge_score": mine["challenge_score"],
            "total_score": mine["total_score"], "rank": mine["rank"]}),
        "cases": [{"problem_id": str(r["problem_id"]), "seed": r["seed"],
                   "verdict": r["verdict"], "raw_cost": r["raw_cost"],
                   "runtime_ms": r["runtime_ms"], "case_score": r["case_score"],
                   "case_rank": r["case_rank"]} for r in cases],
    }


def _example_io(mod, meta: dict) -> tuple[str | None, str | None]:
    """(example_input, example_output) for the statement body, generated from a
    representative seed (Step Up: its first given mission; otherwise a fixed seed).
    Output is a reference solution only when the module provides sample_solution().
    Best-effort: any generator/solver error degrades to (None, None)."""
    try:
        seeds = meta.get("given_seeds") or []
        seed = seeds[0] if seeds else 0
        inp = mod.generate(seed, meta.get("gen_params"))
        out = mod.sample_solution(inp) if hasattr(mod, "sample_solution") else None
        return inp, out
    except Exception:                       # noqa: BLE001 — examples are non-critical
        return None, None


@router.get("/problems/{pid}")
async def problem_detail(pid: str, user: CurrentUser = Depends(get_current_user)):
    p = await _released_problem(pid)
    mod = load_problem(p["problem_key"])
    meta = effective_meta(mod.META, _as_dict(p["scoring_config"]))   # authored seeds/budget/params
    base = {
        "id": str(p["id"]), "kind": p["kind"], "title": p["title"],
        "statement_md": p["statement_md"], "time_limit_ms": p["time_limit_ms"],
        "memory_limit_mb": p["memory_limit_mb"], "simulator_key": p["simulator_key"],
    }
    base["example_input"], base["example_output"] = _example_io(mod, meta)
    if p["kind"] == "stepup":
        seeds = meta.get("given_seeds") or []
        budgets = mission_budgets(meta, mission_weights(mod, meta))   # difficulty-weighted, sums to budget
        best = await db.fetch(
            """SELECT mission_seed, max(score) AS best FROM stepup_submissions
               WHERE problem_id=$1 AND user_id=$2 GROUP BY mission_seed""",
            pid, user.id,
        )
        best_by_seed = {r["mission_seed"]: r["best"] for r in best}
        base["missions"] = [
            {"seed": s, "budget": budgets[i], "best_score": best_by_seed.get(s, 0)}
            for i, s in enumerate(seeds)
        ]
    return base


@router.get("/problems/{pid}/missions/{seed}/input")
async def mission_input(pid: str, seed: int, user: CurrentUser = Depends(get_current_user)):
    p = await _released_problem(pid)
    if p["kind"] != "stepup":
        raise HTTPException(404, "not found")
    mod = load_problem(p["problem_key"])
    meta = effective_meta(mod.META, _as_dict(p["scoring_config"]))
    if seed not in (meta.get("given_seeds") or []):
        raise HTTPException(404, "not found")
    return Response(mod.generate(seed, meta.get("gen_params")), media_type="text/plain")
