"""API process: `uvicorn app.main:app`."""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect

from app.api import (
    activity,
    analytics,
    articles,
    assignments,
    auth,
    clusters,
    geography,
    images,
    news,
    public,
    publishing,
    research,
    search,
    settings as settings_api,
    sources,
    system,
    users,
    videos,
)
from app.api.deps_limits import api_limiter
from app.articles.video import FRAME_SOURCES
from app.core.config import VERSION, get_settings
from app.core.db import SessionLocal, get_engine
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging
from app.services.bootstrap import bootstrap

log = logging.getLogger("newswire.api")
API_PREFIX = "/api/v1"

ROUTERS = [auth, users, sources, news, geography, assignments, research, clusters, images, articles, publishing,
           public, analytics, system, settings_api, activity, search, videos]

API_CSP = "default-src 'none'; frame-ancestors 'none'"
PREVIEW_CSP = ("default-src 'none'; img-src 'self' https: data:; media-src 'self' https:; style-src 'unsafe-inline'; "
               f"frame-ancestors 'self'; frame-src {FRAME_SOURCES}")


def _startup() -> None:
    insp = inspect(get_engine())
    if not insp.has_table("users"):
        raise RuntimeError("The database schema is missing. Run `alembic upgrade head` in backend/ first.")
    db = SessionLocal()
    try:
        bootstrap(db)
    finally:
        db.close()


def create_app(*, run_startup: bool = True) -> FastAPI:
    settings = get_settings()
    scheduler = None

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        nonlocal scheduler
        configure_logging("api")
        if run_startup:
            _startup()
            if settings.embedded_scheduler:
                from app.scheduler.runner import build_scheduler

                scheduler = build_scheduler(blocking=False)
                scheduler.start()
                log.info("Embedded scheduler started (single-process mode)")
        log.info("Samyabadi API %s started (%s)", VERSION, settings.env)
        yield
        if scheduler is not None:
            scheduler.shutdown(wait=False)

    app = FastAPI(
        title="Samyabadi News Automation Tool API",
        version=VERSION,
        description="Self-hosted news intelligence, research, editorial and publishing platform. "
        "All endpoints except /health, /auth/setup-status, /auth/setup, /auth/login and /public/* need a session cookie; "
        "unsafe methods also need the X-CSRF-Token header.",
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    install_error_handlers(app)
    for module in ROUTERS:
        app.include_router(module.router, prefix=API_PREFIX)

    settings.images_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/media", StaticFiles(directory=settings.images_dir), name="media")

    @app.middleware("http")
    async def guard(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        path = request.url.path
        if path.startswith("/api/") and path != f"{API_PREFIX}/health":
            ip = request.client.host if request.client else "unknown"
            allowed, retry_after = api_limiter.hit(f"api:{ip}")
            if not allowed:
                return JSONResponse(
                    {"error": {"code": "rate_limited", "message": "Too many requests", "details": {"retry_after": retry_after}}},
                    status_code=429, headers={"Retry-After": str(int(retry_after) + 1)},
                )
        t0 = time.monotonic()
        response = await call_next(request)
        ms = int((time.monotonic() - t0) * 1000)
        response.headers["X-Request-ID"] = rid
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        if path.startswith("/media/"):
            response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
            response.headers.setdefault("Cache-Control", "public, max-age=86400")
        elif path.startswith(API_PREFIX):
            if path.endswith("/preview"):
                response.headers["Content-Security-Policy"] = PREVIEW_CSP
                response.headers["X-Frame-Options"] = "SAMEORIGIN"
            else:
                response.headers.setdefault("Content-Security-Policy", API_CSP)
                response.headers["X-Frame-Options"] = "DENY"
            response.headers.setdefault("Cache-Control", "no-store")
        if settings.cookie_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if ms > 2000:
            log.warning("Slow request %s %s took %d ms", request.method, path, ms, extra={"request_id": rid})
        return response

    return app


app = create_app()
