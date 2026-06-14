"""Contest registration (참가 신청) — an opt-in RSVP roster.

A user registers for a contest while it is open (scheduled or live, on/after the
registration-open time). The roster is the participant count shown on the contest
page. Submissions are NOT hard-gated on registration (open participation is kept),
so registering is an intent signal + roster, not an access gate.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from .. import db
from ..deps import CurrentUser, get_current_user

router = APIRouter(prefix="/api", tags=["registration"])


def registration_open(status: str, opens_at, now: datetime) -> bool:
    """Registration is open while a contest is scheduled or live and the (optional)
    registration-open time has passed. Ended/archived/draft are closed. Pure → testable."""
    if status not in ("scheduled", "live"):
        return False
    return opens_at is None or now >= opens_at


async def _contest_or_404(cid: str) -> dict:
    c = await db.fetchrow(
        "SELECT id, status, registration_opens_at FROM contests WHERE id=$1 AND status<>'draft'", cid
    )
    if not c:
        raise HTTPException(404, "not found")
    return dict(c)


async def _count(cid: str) -> int:
    row = await db.fetchrow("SELECT count(*) AS n FROM registrations WHERE contest_id=$1", cid)
    return int(row["n"]) if row else 0


@router.get("/contests/{cid}/registration")
async def registration_status(cid: str, user: CurrentUser = Depends(get_current_user)):
    c = await _contest_or_404(cid)
    mine = await db.fetchrow(
        "SELECT 1 FROM registrations WHERE contest_id=$1 AND user_id=$2", cid, user.id
    )
    return {
        "registered": mine is not None,
        "count": await _count(cid),
        "open": registration_open(c["status"], c["registration_opens_at"], datetime.now(timezone.utc)),
    }


@router.post("/contests/{cid}/register")
async def register(cid: str, user: CurrentUser = Depends(get_current_user)):
    c = await _contest_or_404(cid)
    if not registration_open(c["status"], c["registration_opens_at"], datetime.now(timezone.utc)):
        raise HTTPException(403, "지금은 참가 신청을 받지 않습니다")
    await db.execute(
        "INSERT INTO registrations (contest_id, user_id) VALUES ($1,$2) ON CONFLICT DO NOTHING",
        cid, user.id,
    )
    return {"registered": True, "count": await _count(cid)}


@router.post("/contests/{cid}/unregister")
async def unregister(cid: str, user: CurrentUser = Depends(get_current_user)):
    c = await _contest_or_404(cid)
    if c["status"] in ("ended", "archived"):
        raise HTTPException(403, "종료된 대회는 참가 상태를 변경할 수 없습니다")
    await db.execute(
        "DELETE FROM registrations WHERE contest_id=$1 AND user_id=$2", cid, user.id
    )
    return {"registered": False, "count": await _count(cid)}
