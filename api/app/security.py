"""Security middleware: response headers, CSRF/Origin enforcement, rate limiting."""

from __future__ import annotations

import time
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .config import Settings
from .sessions import csrf_ok

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# Global request-body cap (matches submit.py MAX_BODY: 1MB source + 10MB data.bin + slack).
MAX_REQUEST_BODY = 11_200_000


def get_client_ip(request: Request, settings: Settings) -> str:
    """Trusted client IP. Raw X-Forwarded-For is NEVER honored (spoofable); only
    the configured proxy header (e.g. cf-connecting-ip) is, else the socket peer."""
    if settings.trusted_ip_header:
        val = request.headers.get(settings.trusted_ip_header)
        if val:
            return val.split(",")[0].strip()
    return request.client.host if request.client else "0.0.0.0"


def _origin_of(url: str | None) -> str | None:
    if not url:
        return None
    p = urlsplit(url)
    if not p.scheme or not p.netloc:
        return None
    return f"{p.scheme}://{p.netloc}"


class BodySizeLimitMiddleware:
    """Reject oversized request bodies at the ASGI layer, BEFORE Starlette spools a
    multipart upload to disk/RAM. Counts ACTUAL bytes off the receive stream and 413s as
    soon as the cap is crossed, so a missing/spoofed Content-Length (chunked upload) can't
    bypass it the way a header check can. The buffer never exceeds the cap (~11MB), so it is
    safe to hold; every POST here is small, so full buffering adds no meaningful latency.
    Pure ASGI (not BaseHTTPMiddleware) so it can short-circuit before the body is consumed."""

    def __init__(self, app, max_body: int = MAX_REQUEST_BODY):
        self.app = app
        self.max_body = max_body

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method", "GET") in SAFE_METHODS:
            await self.app(scope, receive, send)
            return
        messages: list[dict] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                messages.append(message)
                if message["type"] == "http.disconnect":
                    break
                continue
            total += len(message.get("body", b""))
            if total > self.max_body:
                await JSONResponse({"error": "request body too large"}, status_code=413)(scope, receive, send)
                return
            messages.append(message)
            if not message.get("more_body", False):
                break
        i = 0

        async def replay():
            nonlocal i
            if i < len(messages):
                m = messages[i]
                i += 1
                return m
            return {"type": "http.disconnect"}

        await self.app(scope, replay, send)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings: Settings):
        super().__init__(app)
        self.s = settings

    async def dispatch(self, request: Request, call_next):
        resp: Response = await call_next(request)
        h = resp.headers
        h["X-Content-Type-Options"] = "nosniff"
        h["Referrer-Policy"] = "strict-origin-when-cross-origin"
        h["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        h["X-Frame-Options"] = "DENY"
        csp = (
            "default-src 'self'; frame-ancestors 'none'; object-src 'none'; "
            "base-uri 'none'; form-action 'self'"
        )
        h["Content-Security-Policy-Report-Only" if self.s.csp_report_only
          else "Content-Security-Policy"] = csp
        if self.s.cookie_secure:
            h["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
        return resp


class CsrfMiddleware(BaseHTTPMiddleware):
    """Origin/Referer check + double-submit CSRF on state-changing requests.
    Fails CLOSED: a state-changing request with no recognizable same-origin
    signal is rejected, not waved through. The OAuth callback is a GET (safe).
    """

    def __init__(self, app, settings: Settings):
        super().__init__(app)
        self.s = settings

    async def dispatch(self, request: Request, call_next):
        if request.method not in SAFE_METHODS:
            origin = request.headers.get("origin") or _origin_of(request.headers.get("referer"))
            if origin not in self.s.allowed_origins:
                return JSONResponse({"error": "bad origin"}, status_code=403)
            cookie_val = request.cookies.get(self.s.effective_csrf_cookie)
            header_val = request.headers.get("x-csrf-token")
            if not csrf_ok(cookie_val, header_val):
                return JSONResponse({"error": "csrf"}, status_code=403)
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """In-process token-bucket limiter keyed on trusted client IP. Strict on the
    auth path (which triggers outbound Google calls), looser elsewhere. This is a
    per-process backstop; put Cloudflare rate limiting in front for real DDoS."""

    def __init__(self, app, settings: Settings,
                 auth_per_min: int = 10, general_per_min: int = 120):
        super().__init__(app)
        self.s = settings
        self.limits = {"auth": auth_per_min / 60.0, "general": general_per_min / 60.0}
        self.caps = {"auth": auth_per_min, "general": general_per_min}
        self._buckets: dict[tuple[str, str], tuple[float, float]] = {}

    def _classify(self, path: str) -> str:
        return "auth" if path.startswith("/auth/") else "general"

    def _allow(self, key: tuple[str, str]) -> bool:
        now = time.monotonic()
        bucket = self._classify(key[1])
        tokens, last = self._buckets.get(key, (float(self.caps[bucket]), now))
        tokens = min(self.caps[bucket], tokens + (now - last) * self.limits[bucket])
        if tokens < 1.0:
            self._buckets[key] = (tokens, now)
            return False
        self._buckets[key] = (tokens - 1.0, now)
        if len(self._buckets) > 50_000:           # crude unbounded-growth guard
            self._buckets.clear()
        return True

    async def dispatch(self, request: Request, call_next):
        ip = get_client_ip(request, self.s)
        if not self._allow((ip, request.url.path)):
            return JSONResponse({"error": "rate limited"}, status_code=429)
        return await call_next(request)
