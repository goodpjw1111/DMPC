"""Login / callback / logout. Domain enforcement lives in oidc.verify_id_token."""

from __future__ import annotations

import logging

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel

from .. import db, oidc, sessions
from ..config import Settings, get_settings
from ..security import get_client_ip

router = APIRouter(prefix="/auth", tags=["auth"])
log = logging.getLogger("dmpc.auth")

TX_MAX_AGE = 600  # the login transaction cookie lives 10 minutes


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt="oauth-tx")


@router.get("/login")
async def login(settings: Settings = Depends(get_settings)):
    if not settings.google_client_id:
        raise HTTPException(503, "OAuth not configured")
    state = oidc.new_state()
    nonce = oidc.new_nonce()
    verifier = oidc.new_code_verifier()
    url = oidc.build_authorize_url(
        client_id=settings.google_client_id,
        redirect_uri=settings.google_redirect_uri,
        state=state, nonce=nonce,
        code_challenge=oidc.code_challenge_s256(verifier),
        allowed_domain=settings.allowed_email_domain,
    )
    resp = RedirectResponse(url, status_code=302)
    tx = _serializer(settings).dumps({"state": state, "nonce": nonce, "verifier": verifier})
    # Lax survives Google's top-level redirect back to /auth/callback.
    resp.set_cookie(
        settings.effective_oauth_tx_cookie, tx, max_age=TX_MAX_AGE,
        httponly=True, secure=settings.cookie_secure, samesite="lax", path="/auth",
    )
    return resp


@router.get("/callback")
async def callback(request: Request, settings: Settings = Depends(get_settings),
                   code: str = "", state: str = ""):
    raw = request.cookies.get(settings.effective_oauth_tx_cookie)
    if not raw or not code or not state:
        raise HTTPException(400, "missing login transaction")
    try:
        tx = _serializer(settings).loads(raw, max_age=TX_MAX_AGE)
    except (BadSignature, SignatureExpired):
        raise HTTPException(400, "invalid login transaction")
    if state != tx["state"]:
        raise HTTPException(400, "state mismatch")

    try:
        tokens = await oidc.exchange_code(
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
            redirect_uri=settings.google_redirect_uri,
            code=code, code_verifier=tx["verifier"],
        )
        id_token = tokens.get("id_token")
        if not id_token:
            raise oidc.AuthError("no id_token in token response")
        claims = oidc.verify_id_token(
            id_token, client_id=settings.google_client_id,
            allowed_domain=settings.allowed_email_domain, expected_nonce=tx["nonce"],
            allow_emails=settings.allow_email_set,        # operator-exception addresses
        )
        user = await _upsert_user(claims, settings)
    except oidc.AuthError as e:
        log.warning("login rejected: %s", e.reason)  # detail to logs only
        return RedirectResponse(f"{settings.allowed_origins[0]}/?error=login_denied", status_code=302)

    ip = get_client_ip(request, settings)
    secret = await sessions.create_session(  # fresh secret every login = no fixation
        user["id"], ip_hash=sessions.hash_ip(ip, settings.ip_pepper),
        ua=request.headers.get("user-agent"), ttl_hours=settings.session_ttl_hours,
    )
    resp = RedirectResponse(f"{settings.allowed_origins[0]}/dashboard", status_code=302)
    sessions.set_session_cookie(resp, settings, secret, sessions.new_csrf_token())
    resp.set_cookie(settings.effective_oauth_tx_cookie, "", max_age=0, path="/auth",
                    httponly=True, secure=settings.cookie_secure, samesite="lax")
    return resp


class DevLoginIn(BaseModel):
    email: str


def dev_email_ok(email: str, domain: str,
                 allow_emails: frozenset[str] | set[str] = frozenset()) -> bool:
    """A dev-login email must be a non-empty address in the allowed domain, OR an
    exact allowlist exception (same policy as real login)."""
    email = email.strip().lower()
    if not email or "@" not in email:
        return False
    if email in allow_emails:
        return True
    return (not domain) or email.endswith("@" + domain)


@router.post("/dev-login")
async def dev_login(body: DevLoginIn, request: Request,
                    settings: Settings = Depends(get_settings)):
    """DEV/STAGING ONLY — issue a session for an @{domain} email WITHOUT Google OAuth,
    so the frontend can be tested end-to-end against a real DB. Hard-disabled in prod
    (returns 404), and also refused if Google OAuth IS configured (use real login then)."""
    if settings.is_prod or settings.google_client_id:   # only a real dev box (no OAuth, not prod) may use it
        raise HTTPException(404, "not found")
    if not dev_email_ok(body.email, settings.allowed_email_domain, settings.allow_email_set):
        raise HTTPException(400, f"email must be a @{settings.allowed_email_domain} address")
    email = body.email.strip().lower()
    user = await _upsert_user({"email": email, "sub": "dev:" + email,
                               "name": email.split("@", 1)[0]}, settings)
    ip = get_client_ip(request, settings)
    secret = await sessions.create_session(
        user["id"], ip_hash=sessions.hash_ip(ip, settings.ip_pepper),
        ua=request.headers.get("user-agent"), ttl_hours=settings.session_ttl_hours,
    )
    resp = JSONResponse({"id": str(user["id"]), "email": user["email"]})
    sessions.set_session_cookie(resp, settings, secret, sessions.new_csrf_token())
    return resp


@router.post("/logout")
async def logout(request: Request, settings: Settings = Depends(get_settings)):
    # Best-effort: never require an active session to clear cookies.
    secret = request.cookies.get(settings.effective_session_cookie)
    if secret:
        await sessions.revoke_session(secret)
    resp = Response(status_code=204)
    sessions.clear_session_cookie(resp, settings)
    return resp


def admin_role_for(email: str, admin_emails: set[str]) -> str:
    """Bootstrap role: 'admin' for a configured bootstrap email, else 'student'."""
    return "admin" if email.strip().lower() in admin_emails else "student"


async def _upsert_user(claims: dict, settings: Settings) -> dict:
    """Key the account on the immutable Google subject (`sub`), not the mutable
    email — so a Workspace email rename/reuse can't transfer account ownership.

    A configured bootstrap email is granted 'admin' on every login (idempotent), but
    we NEVER demote: a manually-promoted admin not in the list keeps their role."""
    email = claims["email"].strip().lower()
    sub = claims["sub"]
    name = claims.get("name") or email.split("@", 1)[0]
    role = admin_role_for(email, settings.admin_email_set)
    try:
        row = await db.fetchrow(
            """
            INSERT INTO users (email, google_sub, display_name, role, last_login_at)
            VALUES ($1, $2, $3, $4, now())
            ON CONFLICT (google_sub) DO UPDATE
                SET email = EXCLUDED.email,
                    display_name = EXCLUDED.display_name,
                    -- promote bootstrap admins; otherwise keep the existing role (no demotion)
                    role = CASE WHEN EXCLUDED.role = 'admin' THEN 'admin'::user_role
                                ELSE users.role END,
                    last_login_at = now()
            RETURNING id, email, display_name, role
            """,
            email, sub, name, role,
        )
    except asyncpg.UniqueViolationError:
        # email belongs to a different sub (reused address) — refuse, don't merge.
        raise oidc.AuthError("email already bound to another account") from None
    return dict(row)
