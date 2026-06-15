"""Submission endpoints — Step Up (graded inline) and Challenge (enqueued for the worker)."""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from .. import ci_dispatch, db, grading
from ..deps import CurrentUser, assert_owner, get_current_user

# judge core (languages) — same path grading.py registers.
sys.path.insert(0, grading._JUDGE)
from languages import enabled_languages  # noqa: E402

router = APIRouter(prefix="/api", tags=["submit"])

ENABLED_LANGUAGE_IDS = {l.id for l in enabled_languages()}

MAX_OUTPUT = 2_000_000     # 2 MB cap on a submitted Step Up output
MAX_SOURCE = 1_000_000     # 1 MB Challenge source-code cap
MAX_DATA_BIN = 10_000_000  # 10 MB optional data.bin cap (read by the program via file I/O)
MAX_BODY = MAX_SOURCE + MAX_DATA_BIN + 200_000  # + slack for multipart framing/other fields
CHALLENGE_COOLDOWN_S = 5    # per-user min seconds between Challenge submits to a problem (anti-flood)


_RELEASED = ("live", "ended", "archived")        # contest has started (statement/data public)
_PRE_RELEASE = ("draft", "scheduled")            # not started — must not confirm a problem exists


async def _problem_live(pid: str, kind: str, *, tester: bool = False):
    p = await db.fetchrow(
        """SELECT p.*, c.status AS contest_status FROM problems p
           JOIN contests c ON c.id = p.contest_id WHERE p.id = $1""",
        pid,
    )
    if not p or p["kind"] != kind:
        raise HTTPException(404, "not found")
    # a pre-release contest must 404 (knowing a UUID must not confirm the problem exists);
    # a released-but-closed contest (ended/archived) gives an informative 403. A TESTER may
    # submit to a draft/scheduled contest (private testing of a new problem).
    if p["contest_status"] in _PRE_RELEASE:
        if tester:
            return p
        raise HTTPException(404, "not found")
    if p["contest_status"] != "live":
        raise HTTPException(403, "contest is not accepting submissions")
    return p


async def _released_problem(pid: str, kind: str, *, tester: bool = False):
    """Read-side gate: a problem is visible only once its contest is released (started) —
    or, for a tester, even a draft/scheduled one. 404 otherwise — closes the pre-release
    existence/IDOR leak on the list endpoints (mirrors contests._released_problem)."""
    p = await db.fetchrow(
        """SELECT p.kind, c.status AS contest_status FROM problems p
           JOIN contests c ON c.id = p.contest_id WHERE p.id = $1""",
        pid,
    )
    if not p or p["kind"] != kind or (p["contest_status"] not in _RELEASED and not tester):
        raise HTTPException(404, "not found")
    return p


async def _assert_registered(contest_id, user: CurrentUser) -> None:
    """Submitting requires 참가 신청 (registration) — you must join the contest before its
    problems open. Admins/testers bypass (they verify the contest). 403 otherwise."""
    if user.is_tester:
        return
    row = await db.fetchrow(
        "SELECT 1 FROM registrations WHERE contest_id=$1 AND user_id=$2", contest_id, user.id
    )
    if row is None:
        raise HTTPException(403, "먼저 대회에 참가 신청을 해야 문제를 풀 수 있습니다.")


# --- Step Up: submit an OUTPUT for a mission (graded instantly) -------------

class StepUpIn(BaseModel):
    mission_seed: int
    output: str = Field(max_length=MAX_OUTPUT)


@router.post("/problems/{pid}/stepup/submit")
async def stepup_submit(pid: str, body: StepUpIn, user: CurrentUser = Depends(get_current_user)):
    p = await _problem_live(pid, "stepup", tester=user.is_tester)
    await _assert_registered(p["contest_id"], user)
    try:
        async with db.pool().acquire() as conn:
            async with conn.transaction():
                res = await grading.submit_stepup(
                    conn, user_id=user.id, problem=dict(p),
                    mission_seed=body.mission_seed, output=body.output,
                )
    except grading.SubmitError as e:
        raise HTTPException(400, str(e))
    return grading.result_dict(res)


@router.get("/problems/{pid}/stepup/submissions")
async def stepup_submissions(pid: str, mission_seed: int | None = None,
                             user: CurrentUser = Depends(get_current_user)):
    await _released_problem(pid, "stepup", tester=user.is_tester)   # 404 on a pre-release/unknown problem
    if mission_seed is not None:
        rows = await db.fetch(
            """SELECT id, mission_seed, cost, valid, score, created_at
               FROM stepup_submissions WHERE problem_id=$1 AND user_id=$2 AND mission_seed=$3
               ORDER BY created_at DESC""",
            pid, user.id, mission_seed,
        )
    else:
        rows = await db.fetch(
            """SELECT id, mission_seed, cost, valid, score, created_at
               FROM stepup_submissions WHERE problem_id=$1 AND user_id=$2
               ORDER BY created_at DESC""",
            pid, user.id,
        )
    return [{"id": str(r["id"]), "mission_seed": r["mission_seed"], "cost": r["cost"],
             "valid": r["valid"], "score": r["score"],
             "created_at": r["created_at"].isoformat()} for r in rows]


@router.get("/stepup_submissions/{sid}")
async def stepup_submission_detail(sid: str, user: CurrentUser = Depends(get_current_user)):
    r = await db.fetchrow("SELECT * FROM stepup_submissions WHERE id=$1", sid)
    if not r:
        raise HTTPException(404, "not found")
    assert_owner(r["user_id"], user)          # 404 if not mine
    return {"id": str(r["id"]), "mission_seed": r["mission_seed"], "cost": r["cost"],
            "valid": r["valid"], "score": r["score"], "output_text": r["output_text"],
            "created_at": r["created_at"].isoformat()}


# --- Challenge: submit CODE (+ optional data.bin) -> stored + enqueued -------
# multipart/form-data: the program reads its test input via stdin and the OPTIONAL
# uploaded data.bin (<=10MB) as a file in its working directory; source <=1MB.

@router.post("/problems/{pid}/challenge/submit")
async def challenge_submit(
    pid: str,
    request: Request,
    language_id: str = Form(...),
    source: str = Form(...),
    data: UploadFile | None = File(None),
    user: CurrentUser = Depends(get_current_user),
):
    # reject an over-large body up front (before buffering the whole multipart).
    clen = request.headers.get("content-length")
    if clen and clen.isdigit() and int(clen) > MAX_BODY:
        raise HTTPException(413, "request body exceeds the size limit (source 1MB + data.bin 10MB)")
    p = await _problem_live(pid, "challenge", tester=user.is_tester)
    await _assert_registered(p["contest_id"], user)
    # per-user cooldown: each accepted submission enqueues a grader job, so cap the rate a
    # single user can flood the (free-tier) grader, independent of the per-IP rate limiter.
    recent = await db.fetchrow(
        "SELECT created_at FROM submissions WHERE problem_id=$1 AND user_id=$2 "
        "ORDER BY created_at DESC LIMIT 1", pid, user.id)
    if recent is not None:
        age = (datetime.now(timezone.utc) - recent["created_at"]).total_seconds()
        if age < CHALLENGE_COOLDOWN_S:
            raise HTTPException(429, f"제출이 너무 빠릅니다. {max(1, int(CHALLENGE_COOLDOWN_S - age) + 1)}초 후 다시 시도하세요.")
    if language_id not in ENABLED_LANGUAGE_IDS:
        # never enqueue an unknown/disabled language: it can't compile/run on the
        # grader image and would burn a worker claim only to error out.
        raise HTTPException(400, f"language '{language_id}' is not available")
    src = source.encode()
    if len(src) > MAX_SOURCE:
        raise HTTPException(413, "source code exceeds the 1MB limit")

    data_bin = await data.read() if data is not None else None
    if data_bin is not None and len(data_bin) > MAX_DATA_BIN:
        raise HTTPException(413, "data.bin exceeds the 10MB limit")

    row = await db.fetchrow(
        """INSERT INTO submissions
             (problem_id, user_id, language_id, source_text, source_sha256, code_bytes,
              data_bin, data_sha256, data_bytes, state)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,'queued') RETURNING id""",
        pid, user.id, language_id, source,
        hashlib.sha256(src).hexdigest(), len(src),
        data_bin,
        hashlib.sha256(data_bin).hexdigest() if data_bin is not None else None,
        len(data_bin) if data_bin is not None else None,
    )
    # nudge the grader to run NOW (no-op unless GITHUB_DISPATCH_TOKEN is set); the worker
    # also picks this up via PG SKIP LOCKED on the next scheduled tick regardless.
    await ci_dispatch.fire("grade-samples")
    return {"submission_id": str(row["id"]), "state": "queued"}


@router.get("/problems/{pid}/challenge/submissions")
async def challenge_submissions(pid: str, user: CurrentUser = Depends(get_current_user)):
    """The caller's OWN challenge submissions for a problem (newest first). The most
    recent one is what the next evaluation round grades (grade_round picks the latest
    per user); sample_results are the per-sample COSTS shown before the eval scores it."""
    await _released_problem(pid, "challenge", tester=user.is_tester)   # 404 on a pre-release/unknown problem
    # `user_id=$2` is the ownership guard (own rows only). Deliberately does NOT select
    # source_text / data_bin — a list view never ships the code or the uploaded blob.
    rows = await db.fetch(
        """SELECT id, language_id, state, code_bytes, data_bytes,
                  sample_score_sum, sample_results, created_at
           FROM submissions WHERE problem_id=$1 AND user_id=$2
           ORDER BY created_at DESC LIMIT 50""",
        pid, user.id,
    )
    def _samples(v):
        return json.loads(v) if isinstance(v, str) else v
    return [{"id": str(r["id"]), "language_id": r["language_id"], "state": r["state"],
             "code_bytes": r["code_bytes"], "data_bytes": r["data_bytes"],
             "sample_score_sum": r["sample_score_sum"],
             "sample_results": _samples(r["sample_results"]),
             "created_at": r["created_at"].isoformat()} for r in rows]
