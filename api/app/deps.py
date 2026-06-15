"""Request guards: authentication + deny-by-default authorization helpers.

The authorization rules from the security model live here so every endpoint
reuses the same enforcement instead of re-implementing (and forgetting) it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request

from . import sessions
from .config import Settings, get_settings


@dataclass
class CurrentUser:
    id: str
    email: str
    display_name: str
    nickname: str | None
    role: str
    is_tester: bool = False        # may access TESTER-ONLY (draft) contests

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


async def get_current_user(
    request: Request, settings: Settings = Depends(get_settings)
) -> CurrentUser:
    sid = request.cookies.get(settings.effective_session_cookie)
    if not sid:
        raise HTTPException(status_code=401, detail="not authenticated")
    row = await sessions.lookup_session(sid)
    if row is None:
        raise HTTPException(status_code=401, detail="invalid session")
    email, role = row["email"], row["role"]
    return CurrentUser(
        id=str(row["user_id"]), email=email,
        display_name=row["display_name"], nickname=row["nickname"], role=role,
        is_tester=(role == "admin" or email.lower() in settings.tester_email_set),
    )


async def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.is_admin:
        # 404, not 403: don't reveal that an admin-only resource exists.
        raise HTTPException(status_code=404, detail="not found")
    return user


def assert_owner(resource_user_id: str, user: CurrentUser) -> None:
    """Deny-by-default ownership check. Returns 404 (not 403) on mismatch so we
    never confirm the existence of another user's object."""
    if str(resource_user_id) != user.id and not user.is_admin:
        raise HTTPException(status_code=404, detail="not found")


def assert_contest_ended(ends_at: datetime) -> None:
    """Gate for opponent data (leaderboards, others' replays/ranks). The reveal
    is a SERVER check on the clock, never a UI flag."""
    if ends_at > datetime.now(timezone.utc):
        raise HTTPException(status_code=403, detail="results hidden until contest ends")


# ---------------------------------------------------------------------------
# Guard helpers for the Phase 1+ routes (submissions, standings, replays,
# seeds). They MUST be used by every route that touches per-user or pre-reveal
# data. Pattern: fetch-then-guard in ONE place so no router can select a
# resource by id and forget the check (the #1 IDOR risk).
#
#   sub = await fetch_submission(id)
#   assert_owner(sub["user_id"], user)        # 404 if not mine and not admin
#
#   assert_contest_ended(contest["ends_at"])  # before any opponent/leaderboard read
# ---------------------------------------------------------------------------

def assert_replay_visible(replay: dict, contest: dict, user: CurrentUser) -> None:
    """A replay is readable by others ONLY when shared + moderated + the contest
    has ended. The owner and admins may always read their own."""
    if str(replay["user_id"]) == user.id or user.is_admin:
        return
    visible = (
        replay.get("is_shared") and replay.get("moderated")
        and contest.get("status") == "ended"
    )
    if not visible:
        raise HTTPException(status_code=404, detail="not found")


def assert_seed_revealed(seed: dict) -> None:
    """Interim/final seeds are secret until their reveal_at (or never, for final).
    Blocks contestants from reading the test data before a round runs."""
    reveal_at = seed.get("reveal_at")
    if reveal_at is None or reveal_at > datetime.now(timezone.utc):
        raise HTTPException(status_code=404, detail="not found")
