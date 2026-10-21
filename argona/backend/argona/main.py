"""ASGI application factory."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import public, router
from .config import Settings, get_settings
from .core import Core
from .database.session import run_migrations
from .logging_setup import setup_logging

log = logging.getLogger("argona.main")


class InsecureConfig(RuntimeError):
    pass


def validate_security(settings: Settings) -> None:
    if not settings.is_loopback and not settings.api_token and not settings.allow_insecure_no_auth:
        raise InsecureConfig(f"Refusing to serve on {settings.host} without ARGONA_API_TOKEN. Set a token (recommended) "
                             "or bind to 127.0.0.1.")


def create_app(settings: Settings | None = None, *, core: Core | None = None, run_scheduler: bool = True,
               migrate: bool = True) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level, settings.log_json)
    validate_security(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if migrate:
            run_migrations(settings.resolved_database_url)
        c = core or Core(settings)
        app.state.core = c
        await c.start(run_scheduler=run_scheduler)
        try:
            yield
        finally:
            await c.stop()

    app = FastAPI(title="ARGONA", version="1.0.0", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"], allow_credentials=False)

    @app.middleware("http")
    async def security_headers(request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        return resp

    @app.exception_handler(Exception)
    async def unhandled(request, exc):  # never leak internals / secrets in error bodies
        log.exception("unhandled error on %s", request.url.path)
        return JSONResponse({"detail": "internal error"}, status_code=500)

    app.include_router(public)
    app.include_router(router)
    dist: Path = settings.frontend_dist
    if not (dist / "index.html").exists() and (Path.cwd() / "frontend" / "dist" / "index.html").exists():
        dist = Path.cwd() / "frontend" / "dist"
    if dist.exists() and (dist / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            f = dist / path
            if path and f.is_file() and dist in f.resolve().parents:
                return FileResponse(f)
            return FileResponse(dist / "index.html")
    else:
        @app.get("/", include_in_schema=False)
        async def no_ui():
            return {"detail": "ARGONA API running. Frontend not built: cd frontend && npm ci && npm run build"}
    return app
