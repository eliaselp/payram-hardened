"""Endpoints de salud y metadatos."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import text

from app.payram import PayRamClient, PayRamError

logger = logging.getLogger(__name__)

router = APIRouter()

APP_VERSION = "1.0.0"


@router.get("/healthz")
def healthz(request: Request):
    db_ok = True
    try:
        with request.app.state.db.session() as session:
            session.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        db_ok = False

    settings = request.app.state.settings
    payram_status = "not_configured"
    if settings.payram_configured:
        client = PayRamClient(
            settings.payram_base_url,
            settings.payram_api_key,
            timeout=2.5,
        )
        try:
            client.get_ticker()
            payram_status = "ok"
        except PayRamError:
            payram_status = "degraded"

    payload = {
        "status": "ok" if db_ok else "error",
        "version": APP_VERSION,
        "database": "ok" if db_ok else "error",
        "payram": payram_status,
    }
    return JSONResponse(
        payload,
        status_code=200 if db_ok else 503,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/robots.txt", response_class=PlainTextResponse)
def robots():
    return "User-agent: *\nDisallow: /\n"
