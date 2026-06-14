"""
Server-side sessions + CSRF.

The cookie holds a high-entropy SECRET; the DB stores only sha256(secret), so a
read-only DB leak cannot reconstruct a usable cookie. A fresh secret is minted on
every login (session-id regeneration -> kills fixation). CSRF uses double-submit:
a non-HttpOnly csrf cookie whose value must be echoed in X-CSRF-Token on every
state-changing request (paired with the Origin check in security.py).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Response

from . import db
from .config import Settings


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def hash_ip(ip: str | None, pepper: str) -> str | None:
    if not ip:
        return None
    return hashlib.sha256((pepper + "|" + ip).encode()).hexdigest()[:32]


async def create_session(user_id: str, *, ip_hash: str | None, ua: str | None,
                         ttl_hours: int) -> str:
    """Returns the raw session secret to put in the cookie. Only its hash is stored."""
    secret = secrets.token_urlsafe(32)
    await db.execute(
        """
        INSERT INTO sessions (user_id, token_sha256, expires_at, ip_hash, user_agent)
        VALUES ($1, $2, $3, $4, $5)
        """,
        user_id, _sha256(secret), _now() + timedelta(hours=ttl_hours), ip_hash, ua,
    )
    # opportunistic cheap cleanup of expired rows (free-tier storage hygiene)
    await db.execute("DELETE FROM sessions WHERE expires_at <= now()")
    return secret


async def lookup_session(secret: str):
    row = await db.fetchrow(
        """
        SELECT s.id, s.user_id, s.expires_at,
               u.email, u.display_name, u.nickname, u.role, u.is_disabled
        FROM sessions s JOIN users u ON u.id = s.user_id
        WHERE s.token_sha256 = $1
        """,
        _sha256(secret),
    )
    if not row or row["is_disabled"] or row["expires_at"] <= _now():
        return None
    return row


async def revoke_session(secret: str) -> None:
    await db.execute("DELETE FROM sessions WHERE token_sha256 = $1", _sha256(secret))


# --- cookies ---------------------------------------------------------------

def set_session_cookie(resp: Response, s: Settings, session_secret: str,
                       csrf_token: str) -> None:
    samesite = s.cookie_samesite.lower()
    resp.set_cookie(
        key=s.effective_session_cookie, value=session_secret,
        max_age=s.session_ttl_hours * 3600,
        httponly=True, secure=s.cookie_secure, samesite=samesite, path="/",
    )
    # CSRF cookie is readable by JS (double-submit) — NOT HttpOnly by design.
    resp.set_cookie(
        key=s.effective_csrf_cookie, value=csrf_token,
        max_age=s.session_ttl_hours * 3600,
        httponly=False, secure=s.cookie_secure, samesite=samesite, path="/",
    )


def clear_session_cookie(resp: Response, s: Settings) -> None:
    # Mirror the attributes used when setting, or the browser may ignore the
    # deletion (a __Host-/Secure/SameSite=None cookie must be cleared the same way).
    samesite = s.cookie_samesite.lower()
    resp.set_cookie(s.effective_session_cookie, "", max_age=0, path="/",
                    httponly=True, secure=s.cookie_secure, samesite=samesite)
    resp.set_cookie(s.effective_csrf_cookie, "", max_age=0, path="/",
                    httponly=False, secure=s.cookie_secure, samesite=samesite)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(24)


def csrf_ok(cookie_val: str | None, header_val: str | None) -> bool:
    if not cookie_val or not header_val:
        return False
    return hmac.compare_digest(cookie_val, header_val)
