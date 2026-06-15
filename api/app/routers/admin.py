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
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import ci_dispatch, db, grading
from ..deps import CurrentUser, require_admin
from ..schedule import KST, contest_window

# judge core (template whitelist comes from the installed problem modules)
sys.path.insert(0, grading._JUDGE)
from registry import list_problem_keys, load_problem  # noqa: E402

router = APIRouter(prefix="/api/admin", tags=["admin"])

# An admin may build a contest on ANY installed problem module (problems/<key>/problem.py,
# auto-discovered by the registry). Each module's META carries its own simulator_key and,
# if parametric, a gen_params block — so a NEW problem plugs in without editing this file.
# (Running admin-authored generator CODE still needs the sandbox — a separate, gated feature.)
MAX_STATEMENT = 100_000
MAX_SEEDS = 50
STEPUP_BUDGET = 1_000_000
GRID_MIN, GRID_MAX = 2, 50           # authored grids stay playable in the in-browser sim


def _template_meta(problem_key: str) -> dict:
    """META of an installed problem template, or HTTP 400 if it isn't one. Pure (no DB):
    load_problem only imports the filesystem module, so this stays unit-testable."""
    if problem_key not in set(list_problem_keys()):
        raise HTTPException(400, f"알 수 없는/사용 불가 문제 템플릿입니다: '{problem_key}'")
    return dict(load_problem(problem_key).META)


def _validate_features(meta: dict, feature_list: list[dict]) -> None:
    """Validate authored EXACT features against the problem's META feature_schema
    (each declared key present + within [min, max]). Generic — works for any problem
    that declares a feature_schema."""
    schema = meta.get("feature_schema")
    if not schema:
        raise HTTPException(400, "이 문제 템플릿은 케이스별 피처 저작을 지원하지 않습니다 (feature_schema 없음)")
    for feats in feature_list:
        for spec in schema:
            key = spec["key"]
            if key not in feats:
                raise HTTPException(400, f"피처 '{spec.get('label', key)}' 값이 필요합니다")
            v, lo, hi = feats[key], spec.get("min"), spec.get("max")
            if (lo is not None and v < lo) or (hi is not None and v > hi):
                raise HTTPException(400, f"피처 '{spec.get('label', key)}'는 {lo}~{hi} 범위여야 합니다 (입력: {v})")


def _validate_feature_ranges(meta: dict, ranges_list: list[dict]) -> None:
    """Validate per-feature [min, max] RANGES against the problem's feature_schema
    (Challenge subtasks). A fixed feature is min == max."""
    schema = meta.get("feature_schema")
    if not schema:
        raise HTTPException(400, "이 문제 템플릿은 피처 범위 저작을 지원하지 않습니다 (feature_schema 없음)")
    for feats in ranges_list:
        for spec in schema:
            key = spec["key"]
            rng = feats.get(key)
            if not (isinstance(rng, (list, tuple)) and len(rng) == 2):
                raise HTTPException(400, f"피처 '{spec.get('label', key)}'는 [최소, 최대] 범위여야 합니다")
            lo, hi = int(rng[0]), int(rng[1])
            smin, smax = spec.get("min"), spec.get("max")
            if (smin is not None and lo < smin) or (smax is not None and hi > smax) or lo > hi:
                raise HTTPException(400, f"피처 '{spec.get('label', key)}' 범위는 {smin}~{smax}, 최소 ≤ 최대여야 합니다")


class GenParams(BaseModel):
    hMin: int = 6
    hMax: int = 9
    wMin: int = 6
    wMax: int = 9
    dMin: int = 6
    dMax: int = 10


class StepUpMissionSpec(BaseModel):
    seed: int = Field(ge=0)
    score: int = Field(ge=0, le=STEPUP_BUDGET)
    features: dict[str, int] = Field(default_factory=dict)   # exact per-case features (problem-specific)


class StepUpSpec(BaseModel):
    statement_md: str = Field(default="", max_length=MAX_STATEMENT)
    given_seeds: list[int] = []                  # legacy path (ranges + difficulty-weighted budgets)
    missions: list[StepUpMissionSpec] = []       # new path (exact features + author-set scores; sum==budget)
    time_limit_ms: int = Field(default=2000, ge=100, le=10_000)
    memory_limit_mb: int = Field(default=1024, ge=64, le=4096)


class ChallengeSubtaskSpec(BaseModel):
    # A Challenge subtask = a part with per-feature RANGES and a seed RANGE: each evaluation
    # draws ONE fresh seed (1 seed/eval) and the instance's features are drawn within the
    # ranges. A "fixed" feature is just a range with min == max.
    name: str = Field(default="", max_length=60)
    features: dict[str, list[int]] = Field(default_factory=dict)   # per-feature [min, max]
    seed_lo: int = Field(default=0, ge=0, le=10_000_000)
    seed_hi: int = Field(default=1_000_000, ge=0, le=10_000_000)
    budget: int = Field(ge=0, le=STEPUP_BUDGET)


class ChallengeSpec(BaseModel):
    statement_md: str = Field(default="", max_length=MAX_STATEMENT)
    seed_range: list[int]            # [lo, hi]
    round_seeds: int = Field(default=6, ge=1, le=100)
    cost_eps: float = Field(default=0.0, ge=0)
    subtasks: list[ChallengeSubtaskSpec] = []    # condition-based subtasks (each its own field + budget)
    time_limit_ms: int = Field(default=2000, ge=100, le=10_000)
    memory_limit_mb: int = Field(default=1024, ge=64, le=4096)


class CreateContestIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    problem_key: str = "clean_robot"
    gen_params: GenParams = Field(default_factory=GenParams)   # grid/dust ranges (parametric)
    stepup: StepUpSpec
    challenge: ChallengeSpec
    start_now: bool = False        # admin TEST switch: go live immediately (else the D+1 rule)
    draft: bool = False            # TESTER-ONLY: create as a private draft (only testers/admins see it)


def validate_create(body: CreateContestIn) -> None:
    """Pure validation (no DB) — raises HTTPException(400) on a bad request.
    Factored out so it is unit-testable without a database."""
    meta = _template_meta(body.problem_key)
    # Step Up: authored per-case (exact features + author scores) OR legacy (seeds + ranges).
    sm = body.stepup.missions
    if sm:
        if not (1 <= len(sm) <= MAX_SEEDS):
            raise HTTPException(400, f"스텝업 케이스는 1~{MAX_SEEDS}개여야 합니다")
        case_seeds = [m.seed for m in sm]
        if len(set(case_seeds)) != len(case_seeds):
            raise HTTPException(400, "스텝업 케이스 시드는 중복될 수 없습니다")
        if sum(m.score for m in sm) != STEPUP_BUDGET:
            raise HTTPException(400, f"스텝업 케이스 점수 합이 정확히 {STEPUP_BUDGET:,}이어야 합니다 "
                                     f"(현재 {sum(m.score for m in sm):,})")
        _validate_features(meta, [m.features for m in sm])
    else:
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
    cst = body.challenge.subtasks
    if cst:
        if sum(s.budget for s in cst) != STEPUP_BUDGET:
            raise HTTPException(400, f"챌린지 서브태스크 배점 합이 정확히 {STEPUP_BUDGET:,}이어야 합니다 "
                                     f"(현재 {sum(s.budget for s in cst):,})")
        _validate_feature_ranges(meta, [s.features for s in cst])   # per-feature [min,max] within schema
        for s in cst:
            if not (0 <= s.seed_lo <= s.seed_hi <= 10_000_000):
                raise HTTPException(400, f"서브태스크 '{s.name or '?'}' 시드 범위가 올바르지 않습니다 (0 ≤ 시작 ≤ 끝 ≤ 1천만)")
    else:
        # the eval picks `round_seeds` DISTINCT seeds from [lo,hi]; if the range is too small
        # the round would fail at eval time (grade_round RoundConfigError). Reject at creation.
        if (rng[1] - rng[0] + 1) < body.challenge.round_seeds:
            raise HTTPException(400, f"시드 범위 [{rng[0]},{rng[1]}]에서 서로 다른 {body.challenge.round_seeds}개 "
                                     "시드를 뽑을 수 없습니다 (범위를 넓히거나 라운드 케이스 수를 줄이세요)")
    # grid/dust bounds only apply to parametric grid-family templates (those whose META
    # declares gen_params); other problems ignore body.gen_params, so don't gate on it.
    if "gen_params" in meta:
        g = body.gen_params
        if not (GRID_MIN <= g.hMin <= g.hMax <= GRID_MAX and GRID_MIN <= g.wMin <= g.wMax <= GRID_MAX):
            raise HTTPException(400, f"격자 범위는 {GRID_MIN}~{GRID_MAX}, 최소 ≤ 최대여야 합니다")
        if not (0 <= g.dMin <= g.dMax) or g.dMax >= g.hMax * g.wMax:
            raise HTTPException(400, "먼지 개수 범위가 올바르지 않습니다 (0 ≤ 최소 ≤ 최대 < 격자 칸 수)")


def problem_configs(body: CreateContestIn) -> tuple[dict, dict]:
    """The two `scoring_config` jsonb payloads (Step Up, Challenge). Pure → testable."""
    gp = body.gen_params.model_dump()
    if body.stepup.missions:
        # authored per-case: each case has exact features + its own score (sum==budget).
        # given_seeds mirrors the case seeds so the seed-membership/read paths are unchanged.
        su = {
            "given_seeds": [m.seed for m in body.stepup.missions],
            "stepup_budget": STEPUP_BUDGET,
            "stepup_missions": [
                {"seed": m.seed, "score": m.score, "features": dict(m.features)}
                for m in body.stepup.missions
            ],
        }
    else:
        su = {"given_seeds": list(body.stepup.given_seeds), "stepup_budget": STEPUP_BUDGET, "gen_params": gp}
    if body.challenge.subtasks:
        ch = {
            "cost_eps": body.challenge.cost_eps,
            "challenge_subtasks": [
                {"name": s.name, "features": dict(s.features),
                 "seed_lo": s.seed_lo, "seed_hi": s.seed_hi, "budget": s.budget}
                for s in body.challenge.subtasks
            ],
        }
    else:
        ch = {"seed_range": [body.challenge.seed_range[0], body.challenge.seed_range[1]],
              "round_seeds": body.challenge.round_seeds, "cost_eps": body.challenge.cost_eps,
              "gen_params": gp}
    return su, ch


@router.get("/templates")
async def list_templates(user: CurrentUser = Depends(require_admin)):
    """Installed problem templates an admin can build a contest on (auto-discovered).
    The authoring form picks problem_key from here; simulator_key tells the client which
    in-browser simulator to render (null = no browser sim → submit directly)."""
    out = []
    for key in list_problem_keys():
        try:
            meta = load_problem(key).META
        except Exception:                       # a broken module shouldn't hide the rest
            continue
        out.append({
            "problem_key": key,
            "title": meta.get("title", key),
            "kind": meta.get("kind"),
            "simulator_key": meta.get("simulator_key"),
            "parametric": "gen_params" in meta,
            "feature_schema": meta.get("feature_schema") or [],
        })
    return out


@router.post("/contests")
async def create_contest(body: CreateContestIn, user: CurrentUser = Depends(require_admin)):
    validate_create(body)
    # the client-side simulator comes from the problem module's META, not a hardcoded
    # value — a new problem ships its own simulator_key (or null = no in-browser sim).
    sim_key = _template_meta(body.problem_key).get("simulator_key")
    # dates: normal contests follow the locked rule (register today -> start D+1 09:00 KST,
    # 3-day window). start_now is an admin TEST switch — create it live NOW for a 3-day window
    # so the whole submit→grade→standings flow can be exercised without waiting for tomorrow.
    if body.draft:                    # tester-only private draft (hidden from participants)
        starts_at = datetime.now(KST)
        ends_at = starts_at + timedelta(days=3)
        status = "draft"
    elif body.start_now:
        starts_at = datetime.now(KST)
        ends_at = starts_at + timedelta(days=3)
        status = "live"
    else:
        starts_at, ends_at = contest_window(datetime.now(KST).date())
        status = "scheduled"
    su_cfg, ch_cfg = problem_configs(body)
    insert_problem = (
        """INSERT INTO problems (contest_id, kind, problem_key, title, statement_md,
               time_limit_ms, memory_limit_mb, simulator_key, scoring_config)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb)"""
    )
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            c = await conn.fetchrow(
                """INSERT INTO contests (title, status, starts_at, ends_at, created_by)
                   VALUES ($1, $5, $2, $3, $4) RETURNING id""",
                body.title, starts_at, ends_at, user.id, status,
            )
            cid = c["id"]
            await conn.execute(
                insert_problem, cid, "stepup", body.problem_key, f"{body.title} — 스텝 업",
                body.stepup.statement_md, body.stepup.time_limit_ms, body.stepup.memory_limit_mb,
                sim_key, json.dumps(su_cfg),
            )
            await conn.execute(
                insert_problem, cid, "challenge", body.problem_key, f"{body.title} — 챌린지",
                body.challenge.statement_md, body.challenge.time_limit_ms, body.challenge.memory_limit_mb,
                sim_key, json.dumps(ch_cfg),
            )
    return {"id": str(cid), "status": status,
            "starts_at": starts_at.isoformat(), "ends_at": ends_at.isoformat()}


@router.post("/contests/{cid}/evaluate-now")
async def evaluate_now(cid: str, user: CurrentUser = Depends(require_admin)):
    """Admin TEST helper: create an immediate interim evaluation round (scheduled_at=now)
    so the NEXT grader tick scores the current Challenge field — no waiting for 09/18 KST.
    The actual grading runs on the grader (GitHub Actions `evals` workflow); this only
    enqueues the round. Uses a unique 'manual:<ts>' idem_key so it never collides with the
    scheduled 09/18 rounds and can be fired repeatedly."""
    c = await db.fetchrow("SELECT id, status FROM contests WHERE id=$1", cid)
    if not c:
        raise HTTPException(404, "대회를 찾을 수 없습니다")
    if c["status"] not in ("draft", "live", "ended"):
        raise HTTPException(400, "초안(draft)·진행 중(live)·종료(ended) 대회만 평가할 수 있습니다")
    if not await db.fetchrow("SELECT 1 FROM problems WHERE contest_id=$1 AND kind='challenge'", cid):
        raise HTTPException(400, "이 대회에는 챌린지 문제가 없습니다")
    now = datetime.now(KST)
    row = await db.fetchrow(
        """INSERT INTO evaluation_rounds (contest_id, type, idem_key, scheduled_at, status)
           VALUES ($1, 'interim', $2, $3, 'pending') RETURNING id""",
        cid, f"manual:{now.isoformat()}", now,
    )
    await ci_dispatch.fire("evals")          # run the grader NOW (no-op unless configured)
    return {"round_id": str(row["id"]), "scheduled_at": now.isoformat()}


@router.post("/contests/{cid}/end")
async def end_contest(cid: str, user: CurrentUser = Depends(require_admin)):
    """Admin: end a contest NOW and (re)grade its FINAL round so the ranking tab and
    replays open. Sets status='ended' + ends_at=now, then enqueues/refreshes the single
    type='final' round; the grader publishes it -> final standings (the ranking)."""
    c = await db.fetchrow("SELECT id, status FROM contests WHERE id=$1", cid)
    if not c:
        raise HTTPException(404, "대회를 찾을 수 없습니다")
    now = datetime.now(KST)
    idem = f"{cid}:{now.date().isoformat()}:final"
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute("UPDATE contests SET status='ended', ends_at=$2 WHERE id=$1", cid, now)
            existing = await conn.fetchrow(
                "SELECT id FROM evaluation_rounds WHERE contest_id=$1 AND type='final'", cid)
            if existing:                      # re-grade the existing final (only one per contest)
                rid = existing["id"]
                await conn.execute(
                    "UPDATE evaluation_rounds SET status='pending', scheduled_at=$2, claimed_at=NULL, "
                    "claimed_by=NULL, attempts=0, published_at=NULL WHERE id=$1", rid, now)
            else:
                row = await conn.fetchrow(
                    "INSERT INTO evaluation_rounds (contest_id, type, idem_key, scheduled_at, status) "
                    "VALUES ($1,'final',$2,$3,'pending') RETURNING id", cid, idem, now)
                rid = row["id"]
    await ci_dispatch.fire("evals")
    return {"status": "ended", "final_round_id": str(rid), "ends_at": now.isoformat()}


@router.post("/contests/{cid}/publish")
async def publish_contest(cid: str, user: CurrentUser = Depends(require_admin)):
    """Promote a TESTER-ONLY draft to a live, public contest (start now, 3-day window).
    Used after testers have verified a new problem privately."""
    c = await db.fetchrow("SELECT id, status FROM contests WHERE id=$1", cid)
    if not c:
        raise HTTPException(404, "대회를 찾을 수 없습니다")
    if c["status"] != "draft":
        raise HTTPException(400, "초안(draft) 대회만 공개할 수 있습니다")
    now = datetime.now(KST)
    ends_at = now + timedelta(days=3)
    await db.execute("UPDATE contests SET status='live', starts_at=$2, ends_at=$3 WHERE id=$1",
                     cid, now, ends_at)
    return {"status": "live", "starts_at": now.isoformat(), "ends_at": ends_at.isoformat()}


@router.post("/contests/{cid}/delete")
async def delete_contest(cid: str, user: CurrentUser = Depends(require_admin)):
    """Admin: permanently DELETE a contest and everything under it — problems, submissions,
    Step Up submissions, evaluation rounds, case_results, standings, registrations, replays
    — all removed via ON DELETE CASCADE in one statement. IRREVERSIBLE; intended for clearing
    out test contests. (notifications are not FK-linked to a contest, so any already-sent ones
    remain — harmless.) POST (not DELETE) so it rides the existing CSRF-protected path."""
    tag = await db.execute("DELETE FROM contests WHERE id=$1", cid)
    if isinstance(tag, str) and tag.endswith(" 0"):
        raise HTTPException(404, "대회를 찾을 수 없습니다")
    return {"deleted": True, "id": cid}
