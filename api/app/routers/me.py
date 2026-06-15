"""Current-user + nickname endpoints for the SPA."""

from __future__ import annotations

import json

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import db
from ..deps import CurrentUser, get_current_user
from ..nickname import normalize, validate_nickname

router = APIRouter(prefix="/api", tags=["me"])


@router.get("/me")
async def me(user: CurrentUser = Depends(get_current_user)):
    return {
        "id": user.id,
        "email": user.email,
        "nickname": user.nickname,
        "needs_nickname": user.nickname is None,   # first-login -> show setup screen
        "role": user.role,
        "is_tester": user.is_tester,               # may access tester-only (draft) contests
    }


class NicknameIn(BaseModel):
    nickname: str


@router.post("/nickname")
async def set_nickname(body: NicknameIn, user: CurrentUser = Depends(get_current_user)):
    name = normalize(body.nickname)
    err = validate_nickname(name)
    if err:
        raise HTTPException(status_code=422, detail=err)
    try:
        # write-once: only set when still NULL. 0 rows -> nickname already chosen.
        tag = await db.execute(
            "UPDATE users SET nickname = $1 WHERE id = $2 AND nickname IS NULL", name, user.id
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=409, detail="이미 사용 중인 닉네임입니다.") from None
    if isinstance(tag, str) and tag.endswith(" 0"):
        raise HTTPException(status_code=409, detail="닉네임은 한 번만 설정할 수 있습니다.")
    return {"nickname": name}


@router.get("/notifications")
async def notifications(user: CurrentUser = Depends(get_current_user)):
    rows = await db.fetch(
        """SELECT id, type, payload, read_at, created_at FROM notifications
           WHERE user_id=$1 ORDER BY created_at DESC LIMIT 50""",
        user.id,
    )
    # asyncpg returns jsonb as text (no codec registered) — decode so the client gets
    # a real object, not a double-encoded string. (Tolerant if a codec is added later.)
    def _payload(v):
        return json.loads(v) if isinstance(v, str) else v
    return [{"id": str(r["id"]), "type": r["type"], "payload": _payload(r["payload"]),
             "read": r["read_at"] is not None, "created_at": r["created_at"].isoformat()}
            for r in rows]


@router.post("/notifications/read")
async def mark_read(user: CurrentUser = Depends(get_current_user)):
    await db.execute(
        "UPDATE notifications SET read_at=now() WHERE user_id=$1 AND read_at IS NULL", user.id
    )
    return {"ok": True}
