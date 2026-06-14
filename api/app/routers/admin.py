"""Admin authoring endpoints — create a contest + its problems (admin-gated).

An authored contest reuses a built-in generator/checker module (problem_key) and
chooses its own seeds/budget/limits/statements; those per-contest scalars live in
`problems.scoring_config` and are merged over the module META by registry.effective_meta,
so the existing Step Up / Challenge graders run it unchanged. Custom generators that
execute admin-supplied code need the sandboxed grader and are NOT enabled here.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db, grading
from ..deps import CurrentUser, require_admin
from ..schedule import KST, contest_window

# judge core (template whitelist comes from the installed problem modules)
sys.path.insert(0, grading._JUDGE)
from registry import list_problem_keys  # noqa: E402

router = APIRouter(prefix="/api/admin", tags=["admin"])

# Built-in generator/checker modules an admin may build a contest on. `clean_robot`
# is parametric (grid/dust ranges per contest); `example_clean` has fixed ranges.
# (Running admin-authored generator CODE needs the sandbox — a separate, gated feature.)
_ALLOWED_TEMPLATES = {"clean_robot", "example_clean"}

MAX_STATEMENT = 100_000
MAX_SEEDS = 50
STEPUP_BUDGET = 1_000_000
GRID_MIN, GRID_MAX = 2, 50           # authored grids stay playable in the in-browser sim


class GenParams(BaseModel):
    hMin: int = 6
    hMax: int = 9
    wMin: int = 6
    wMax: int = 9
    dMin: int = 6
    dMax: int = 10


class StepUpSpec(BaseModel):
    statement_md: str = Field(default="", max_length=MAX_STATEMENT)
    given_seeds: list[int]
    time_limit_ms: int = Field(default=2000, ge=100, le=10_000)
    memory_limit_mb: int = Field(default=1024, ge=64, le=4096)


class ChallengeSpec(BaseModel):
    statement_md: str = Field(default="", max_length=MAX_STATEMENT)
    seed_range: list[int]            # [lo, hi]
    round_seeds: int = Field(default=6, ge=1, le=100)
    cost_eps: float = Field(default=0.0, ge=0)
    time_limit_ms: int = Field(default=2000, ge=100, le=10_000)
    memory_limit_mb: int = Field(default=1024, ge=64, le=4096)


class CreateContestIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    problem_key: str = "clean_robot"
    gen_params: GenParams = Field(default_factory=GenParams)   # grid/dust ranges (parametric)
    stepup: StepUpSpec
    challenge: ChallengeSpec


def validate_create(body: CreateContestIn) -> None:
    """Pure validation (no DB) — raises HTTPException(400) on a bad request.
    Factored out so it is unit-testable without a database."""
    if body.problem_key not in _ALLOWED_TEMPLATES or body.problem_key not in set(list_problem_keys()):
        raise HTTPException(400, f"알 수 없는/사용 불가 문제 템플릿입니다: '{body.problem_key}'")
    seeds = body.stepup.given_seeds
    if not (1 <= len(seeds) <= MAX_SEEDS):
        raise HTTPException(400, f"스텝업 미션 시드는 1~{MAX_SEEDS}개여야 합니다")
    if len(set(seeds)) != len(seeds):
        raise HTTPException(400, "스텝업 미션 시드는 중복될 수 없습니다")
    if any(s < 0 for s in seeds):
        raise HTTPException(400, "스텝업 미션 시드는 음수가 될 수 없습니다")
    rng = body.challenge.seed_range
    if len(rng) != 2 or not (0 <= rng[0] <= rng[1] <= 10_000_000):
        raise HTTPException(400, "챌린지 시드 범위는 [lo, hi] (0 ≤ lo ≤ hi ≤ 1천만) 여야 합니다")
    # the eval picks `round_seeds` DISTINCT seeds from [lo,hi]; if the range is too small
    # the round would fail at eval time (grade_round RoundConfigError). Reject at creation.
    if (rng[1] - rng[0] + 1) < body.challenge.round_seeds:
        raise HTTPException(400, f"시드 범위 [{rng[0]},{rng[1]}]에서 서로 다른 {body.challenge.round_seeds}개 "
                                 "시드를 뽑을 수 없습니다 (범위를 넓히거나 라운드 케이스 수를 줄이세요)")
    g = body.gen_params
    if not (GRID_MIN <= g.hMin <= g.hMax <= GRID_MAX and GRID_MIN <= g.wMin <= g.wMax <= GRID_MAX):
        raise HTTPException(400, f"격자 범위는 {GRID_MIN}~{GRID_MAX}, 최소 ≤ 최대여야 합니다")
    if not (0 <= g.dMin <= g.dMax) or g.dMax >= g.hMax * g.wMax:
        raise HTTPException(400, "먼지 개수 범위가 올바르지 않습니다 (0 ≤ 최소 ≤ 최대 < 격자 칸 수)")


def problem_configs(body: CreateContestIn) -> tuple[dict, dict]:
    """The two `scoring_config` jsonb payloads (Step Up, Challenge). Pure → testable.
    gen_params (grid/dust ranges) drive the parametric generator on BOTH problems."""
    gp = body.gen_params.model_dump()
    su = {"given_seeds": list(body.stepup.given_seeds), "stepup_budget": STEPUP_BUDGET, "gen_params": gp}
    ch = {"seed_range": [body.challenge.seed_range[0], body.challenge.seed_range[1]],
          "round_seeds": body.challenge.round_seeds, "cost_eps": body.challenge.cost_eps,
          "gen_params": gp}
    return su, ch


@router.post("/contests")
async def create_contest(body: CreateContestIn, user: CurrentUser = Depends(require_admin)):
    validate_create(body)
    # dates follow the locked schedule rule: register today -> start D+1 09:00 KST, 3-day window.
    starts_at, ends_at = contest_window(datetime.now(KST).date())
    su_cfg, ch_cfg = problem_configs(body)
    insert_problem = (
        """INSERT INTO problems (contest_id, kind, problem_key, title, statement_md,
               time_limit_ms, memory_limit_mb, simulator_key, scoring_config)
           VALUES ($1,$2,$3,$4,$5,$6,$7,'clean',$8::jsonb)"""
    )
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            c = await conn.fetchrow(
                """INSERT INTO contests (title, status, starts_at, ends_at, created_by)
                   VALUES ($1, 'scheduled', $2, $3, $4) RETURNING id""",
                body.title, starts_at, ends_at, user.id,
            )
            cid = c["id"]
            await conn.execute(
                insert_problem, cid, "stepup", body.problem_key, f"{body.title} — 스텝 업",
                body.stepup.statement_md, body.stepup.time_limit_ms, body.stepup.memory_limit_mb,
                json.dumps(su_cfg),
            )
            await conn.execute(
                insert_problem, cid, "challenge", body.problem_key, f"{body.title} — 챌린지",
                body.challenge.statement_md, body.challenge.time_limit_ms, body.challenge.memory_limit_mb,
                json.dumps(ch_cfg),
            )
    return {"id": str(cid), "status": "scheduled",
            "starts_at": starts_at.isoformat(), "ends_at": ends_at.isoformat()}
