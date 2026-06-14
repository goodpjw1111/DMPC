"""
Google OIDC — login with **server-side** domain enforcement.

THE TRAP this module exists to avoid: the `hd` (hosted-domain) *request*
parameter is only a hint. A client can strip or change it. Enforcement happens
HERE, on the verified ID token, on the server:

    1. signature verified against Google's JWKS (RS256)
    2. aud == our client id, exp not passed   (PyJWT)
    3. iss in Google's set                     (check_claims)
    4. email_verified is true                  (check_claims)
    5. the `hd` claim == dimigo.hs.kr          (check_claims)
    6. AND the email's domain == dimigo.hs.kr  (check_claims, defense in depth)
    7. nonce matches the one we issued         (check_claims)

`check_claims` is intentionally pure stdlib so it is unit-tested with crafted
claim dicts and no network — it is the highest-value security function here.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from urllib.parse import urlencode

# Google's endpoints are stable; documented in its OIDC discovery doc.
AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
JWKS_URI = "https://www.googleapis.com/oauth2/v3/certs"
VALID_ISSUERS = {"https://accounts.google.com", "accounts.google.com"}


class AuthError(Exception):
    """Login rejected. `reason` is for server logs only — never shown to users."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# --- PKCE ------------------------------------------------------------------

def new_code_verifier() -> str:
    return _b64url(secrets.token_bytes(32))


def code_challenge_s256(verifier: str) -> str:
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def new_state() -> str:
    return _b64url(secrets.token_bytes(24))


def new_nonce() -> str:
    return _b64url(secrets.token_bytes(24))


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


# --- authorize URL ---------------------------------------------------------

def build_authorize_url(
    *, client_id: str, redirect_uri: str, state: str, nonce: str,
    code_challenge: str, allowed_domain: str,
) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "hd": allowed_domain,        # hint only — re-verified server-side
        "prompt": "select_account",
        "access_type": "online",
    }
    return f"{AUTH_ENDPOINT}?{urlencode(params)}"


# --- the pure, unit-tested claim check ------------------------------------

def check_claims(claims: dict, *, allowed_domain: str, expected_nonce: str,
                 allow_emails: frozenset[str] | set[str] = frozenset()) -> None:
    """Raise AuthError unless the verified ID-token claims satisfy every rule.
    Assumes signature/aud/exp were already verified by the JWT decode step.

    `allow_emails` are EXACT operator-exception addresses (e.g. a personal Gmail) that
    may log in despite not being in `allowed_domain`; for those we skip the domain/hd
    gate but still require a Google-VERIFIED email (so no unverified address slips in)."""
    if claims.get("iss") not in VALID_ISSUERS:
        raise AuthError(f"bad iss: {claims.get('iss')!r}")

    if not claims.get("sub"):
        raise AuthError("no sub")  # the immutable account identity

    if claims.get("email_verified") not in (True, "true"):
        raise AuthError("email not verified")

    email = (claims.get("email") or "").strip().lower()
    if "@" not in email:
        raise AuthError("no email claim")

    if email in allow_emails:
        pass            # explicit allowlist exception — domain/hd gate skipped
    else:
        email_domain = email.rsplit("@", 1)[-1]
        if email_domain != allowed_domain.lower():
            raise AuthError(f"email domain {email_domain!r} != {allowed_domain!r}")
        # Defense in depth: the Workspace hosted-domain claim must ALSO match, so a
        # personal Gmail that somehow carries a look-alike email is still rejected.
        hd = (claims.get("hd") or "").strip().lower()
        if hd != allowed_domain.lower():
            raise AuthError(f"hd claim {hd!r} != {allowed_domain!r}")

    if not expected_nonce or claims.get("nonce") != expected_nonce:
        raise AuthError("nonce mismatch")


# --- network steps (exercised at runtime; thin wrappers over httpx/PyJWT) ---

async def exchange_code(
    *, client_id: str, client_secret: str, redirect_uri: str,
    code: str, code_verifier: str,
) -> dict:
    import httpx

    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "code_verifier": code_verifier,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(TOKEN_ENDPOINT, data=data)
    if resp.status_code != 200:
        raise AuthError(f"token endpoint {resp.status_code}: {resp.text[:200]}")
    return resp.json()


_jwk_client = None  # cached across requests (keys rarely rotate)


def _get_jwk_client():
    """Lazily build ONE PyJWKClient (cached, with a timeout) so we don't fetch
    Google's JWKS on every login. Lazy import keeps PyJWT off the test path."""
    global _jwk_client
    if _jwk_client is None:
        import jwt  # PyJWT
        _jwk_client = jwt.PyJWKClient(
            JWKS_URI, cache_keys=True, lifespan=3600, timeout=5
        )
    return _jwk_client


def verify_id_token(id_token: str, *, client_id: str, allowed_domain: str,
                    expected_nonce: str,
                    allow_emails: frozenset[str] | set[str] = frozenset()) -> dict:
    """Verify signature + aud + exp via PyJWT/JWKS, then enforce check_claims.
    Returns the validated claims on success; raises AuthError otherwise."""
    import jwt  # PyJWT

    try:
        signing_key = _get_jwk_client().get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],          # pinned: no alg confusion / 'none'
            audience=client_id,
            options={"require": ["exp", "iat", "aud", "sub"], "verify_iss": False},
        )
    except Exception as e:  # noqa: BLE001 — any decode failure = auth failure
        raise AuthError(f"jwt verify failed: {type(e).__name__}") from e

    check_claims(claims, allowed_domain=allowed_domain, expected_nonce=expected_nonce,
                 allow_emails=allow_emails)
    return claims
