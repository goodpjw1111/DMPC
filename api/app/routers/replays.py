"""Replays (winners' writeups) + 시상 showcase for ENDED contests.

Rules (mirrors the schema + deps.assert_replay_visible):
  * Only a verified FINAL top-3 finisher may write a replay, and only after the
    contest has ended.
  * A replay is visible to OTHERS only when is_shared AND moderated AND the contest
    has ended; the owner and admins can always see their own / any.
  * Editing a replay resets moderation (must be re-approved before it is public).

Bodies are stored as PLAIN TEXT and rendered escaped on the client (no HTML/markdown),
so a shared writeup can never inject script — moderation is a curation step, not the
only XSS defense.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..deps import (
    CurrentUser, assert_contest_ended, assert_replay_visible, get_current_user, require_admin,
)

router = APIRouter(prefix="/api", tags=["replays"])

MAX_BODY = 20_000          # a writeup, not a thesis
TOP_N = 3                  # only the podium may post a 시상 writeup


def is_eligible_rank(rank: int | None) -> bool:
    """Eligible to post a replay = a verified final top-N finisher."""
    return rank is not None and 1 <= rank <= TOP_N


def validate_body(text: str) -> str:
    t = (text or "").strip()
    if not t:
        raise HTTPException(400, "내용을 입력하세요")
    if len(t) > MAX_BODY:
        raise HTTPException(400, f"내용이 너무 깁니다 (최대 {MAX_BODY}자)")
    return t


_FINAL_ROUND_SUBQ = """
    SELECT id FROM evaluation_rounds
    WHERE contest_id=$1 AND type='final' AND status='done' AND published_at IS NOT NULL
    ORDER BY scheduled_at DESC LIMIT 1
"""


async def _contest_or_404(cid: str) -> dict:
    c = await db.fetchrow("SELECT id, status, ends_at FROM contests WHERE id=$1 AND status<>'draft'", cid)
    if not c:
        raise HTTPException(404, "not found")
    return dict(c)


async def _final_rank(cid: str, user_id: str) -> int | None:
    row = await db.fetchrow(
        f"""SELECT rank FROM standings
            WHERE contest_id=$1 AND user_id=$2 AND round_id = ({_FINAL_ROUND_SUBQ})""",
        cid, user_id,
    )
    return None if not row or row["rank"] is None else int(row["rank"])


@router.get("/contests/{cid}/replays")
async def list_replays(cid: str, user: CurrentUser = Depends(get_current_user)):
    """Visible replays for an ended contest: the caller's own + (shared & moderated),
    each with the author's nickname and final rank, ordered by rank."""
    c = await _contest_or_404(cid)
    assert_contest_ended(c["ends_at"])      # 403 until the contest ends
    rows = await db.fetch(
        f"""SELECT r.id, r.user_id, r.body_md, r.is_shared, r.moderated, r.created_at,
                   u.nickname, s.rank
            FROM replays r
            JOIN users u ON u.id = r.user_id
            LEFT JOIN standings s
              ON s.contest_id = r.contest_id AND s.user_id = r.user_id
             AND s.round_id = ({_FINAL_ROUND_SUBQ})
            WHERE r.contest_id = $1
              AND (r.user_id = $2 OR (r.is_shared AND r.moderated))
            ORDER BY s.rank NULLS LAST, r.created_at""",
        cid, user.id,
    )
    return [{"id": str(r["id"]), "nickname": r["nickname"], "rank": r["rank"],
             "body": r["body_md"], "is_shared": r["is_shared"], "moderated": r["moderated"],
             "is_mine": str(r["user_id"]) == user.id,
             "created_at": r["created_at"].isoformat()} for r in rows]


@router.get("/contests/{cid}/replay/me")
async def my_replay(cid: str, user: CurrentUser = Depends(get_current_user)):
    """The caller's own replay + whether they are eligible to post one (final top-3)."""
    c = await _contest_or_404(cid)
    rank = await _final_rank(cid, user.id)
    r = await db.fetchrow(
        "SELECT body_md, is_shared, moderated FROM replays WHERE contest_id=$1 AND user_id=$2",
        cid, user.id,
    )
    ended = c["status"] in ("ended", "archived")
    return {
        "eligible": ended and is_eligible_rank(rank),
        "rank": rank,
        "replay": (None if not r else {"body": r["body_md"], "is_shared": r["is_shared"],
                                       "moderated": r["moderated"]}),
    }


class ReplayIn(BaseModel):
    body: str = Field(max_length=MAX_BODY + 1000)   # hard cap; validate_body trims/limits
    is_shared: bool = False


@router.post("/contests/{cid}/replay")
async def upsert_replay(cid: str, body: ReplayIn, user: CurrentUser = Depends(get_current_user)):
    """Create/replace the caller's own replay. Requires: contest ended + final top-3.
    Any write resets moderation (an edited writeup must be re-approved to stay public)."""
    c = await _contest_or_404(cid)
    if c["status"] not in ("ended", "archived"):
        raise HTTPException(403, "대회가 종료된 뒤에 작성할 수 있습니다")
    rank = await _final_rank(cid, user.id)
    if not is_eligible_rank(rank):
        raise HTTPException(403, "최종 상위 3위만 풀이를 공유할 수 있습니다")
    text = validate_body(body.body)
    row = await db.fetchrow(
        """INSERT INTO replays (contest_id, user_id, body_md, is_shared, moderated)
           VALUES ($1,$2,$3,$4,false)
           ON CONFLICT (contest_id, user_id) DO UPDATE
               SET body_md = EXCLUDED.body_md, is_shared = EXCLUDED.is_shared,
                   moderated = false              -- re-moderate on every edit
           RETURNING id""",
        cid, user.id, text, body.is_shared,
    )
    return {"id": str(row["id"]), "moderated": False, "is_shared": body.is_shared}


class ModerateIn(BaseModel):
    moderated: bool


@router.post("/admin/replays/{rid}/moderate")
async def moderate_replay(rid: str, body: ModerateIn, user: CurrentUser = Depends(require_admin)):
    """Admin: approve/unapprove a replay for public display."""
    tag = await db.execute("UPDATE replays SET moderated=$1 WHERE id=$2", body.moderated, rid)
    if isinstance(tag, str) and tag.endswith(" 0"):
        raise HTTPException(404, "not found")
    return {"id": rid, "moderated": body.moderated}
