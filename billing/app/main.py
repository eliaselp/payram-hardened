"""Punto de entrada de la aplicación."""

from __future__ import annotations

import logging
import secrets
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import admin, api, health, public, webhooks
from app.config import Settings, get_settings
from app.db import Database
from app.payram import PayRamClient
from app.security import LoginRateLimiter
from app.web import LoginRequired, login_redirect, render

BASE_DIR = Path(__file__).resolve().parent
logger = logging.getLogger(__name__)


def _configure_logging(level: str) -> None:
    if logging.getLogger().handlers:
        logging.getLogger().setLevel(level.upper())
        return
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)-8s %(name)s :: %(message)s",
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.ensure_production_safety()
    _configure_logging(settings.log_level)

    database = Database(settings.database_url)
    if settings.auto_create_schema:
        database.create_all()

    app = FastAPI(
        title="PayRam Billing",
        version=health.APP_VERSION,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )
    app.state.settings = settings
    app.state.db = database
    app.state.payram = PayRamClient(
        settings.payram_base_url,
        settings.payram_api_key,
        timeout=settings.payram_timeout_seconds,
        public_url=settings.payram_public_base,
    )
    app.state.login_limiter = LoginRateLimiter(
        settings.login_max_attempts,
        settings.login_window_seconds,
    )

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie=settings.session_cookie_name,
        max_age=settings.session_max_age_seconds,
        same_site="lax",
        https_only=settings.secure_cookies,
    )
    if settings.allowed_hosts_list and settings.allowed_hosts_list != ["*"]:
        app.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=settings.allowed_hosts_list,
        )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or secrets.token_hex(8)
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("Error no controlado request_id=%s path=%s", request_id, request.url.path)
            raise
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )
        if settings.is_production:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        if request.url.path.startswith("/i/"):
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["X-Robots-Tag"] = "noindex, nofollow"
        elif request.url.path.startswith(("/admin", "/login", "/api/")):
            response.headers["Cache-Control"] = "no-store, max-age=0"
        return response

    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    app.include_router(webhooks.router)
    app.include_router(health.router)
    app.include_router(api.router)
    app.include_router(public.router)
    app.include_router(admin.public_admin_router)
    app.include_router(admin.router)

    @app.exception_handler(LoginRequired)
    async def _login_required(request: Request, _exc: LoginRequired):
        return login_redirect(request)

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(request: Request, exc: StarletteHTTPException):
        if exc.status_code == 404 and not request.url.path.startswith(("/api/", "/webhooks/")):
            return render(request, "404.html", {}, status_code=404)
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    @app.get("/")
    def root():
        return RedirectResponse("/admin", status_code=302)

    for warning in settings.validation_warnings():
        logger.warning("Configuración: %s", warning)
    logger.info(
        "PayRam Billing %s iniciado (env=%s, base_url=%s)",
        health.APP_VERSION,
        settings.environment,
        settings.base_url,
    )
    return app


app = create_app()
