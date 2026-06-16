"""DMPC API entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import db
from .config import get_settings
from .routers import admin, auth, contests, me, registrations, replays, submit
from .security import (
    BodySizeLimitMiddleware, CsrfMiddleware, RateLimitMiddleware, SecurityHeadersMiddleware,
)

logging.basicConfig(level=logging.INFO)
settings = get_settings()  # constructing this fails closed on insecure prod config


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect(settings.database_url)
    try:
        yield
    finally:
        await db.disconnect()


app = FastAPI(title="DMPC API", lifespan=lifespan)

# Middleware added LAST runs OUTERMOST (first on request). Desired request order:
#   BodySizeLimit -> CORS -> RateLimit -> CSRF -> SecurityHeaders -> route
app.add_middleware(SecurityHeadersMiddleware, settings=settings)
app.add_middleware(CsrfMiddleware, settings=settings)
app.add_middleware(RateLimitMiddleware, settings=settings)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,   # explicit allowlist, never "*"
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["content-type", "x-csrf-token"],
)
# Outermost: cap the request body before anything reads/spools it (DoS guard).
app.add_middleware(BodySizeLimitMiddleware)

app.include_router(auth.router)
app.include_router(me.router)
app.include_router(contests.router)
app.include_router(submit.router)
app.include_router(admin.router)
app.include_router(replays.router)
app.include_router(registrations.router)


@app.get("/healthz")
async def healthz():
    # Public, unauthenticated liveness probe — do NOT leak the deployment env
    # (fingerprinting aid). Keep the body minimal.
    # Best-effort DB touch so the keepalive ping (every ~10 min) keeps NEON's compute warm too,
    # not just Render — otherwise the free Neon instance suspends and the first query after an
    # idle period is slow ("오랜만에 접속하면 로딩이 오래"). Liveness still returns ok if it fails.
    try:
        await db.fetchrow("SELECT 1")
    except Exception:  # noqa: BLE001 — never fail liveness on a transient DB hiccup
        pass
    return {"ok": True}
