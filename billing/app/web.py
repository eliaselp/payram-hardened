"""Helpers de plantillas, flashes y autenticación web."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import Settings
from app.models import Invoice
from app.security import ensure_csrf_token

templates = Jinja2Templates(
    env=Environment(
        loader=FileSystemLoader(str(Path(__file__).resolve().parent / "templates")),
        autoescape=select_autoescape(["html", "xml"]),
    )
)


class LoginRequired(Exception):
    pass


STATUS_LABELS = {
    "draft": "Borrador",
    "open": "Pendiente",
    "partially_paid": "Pago parcial",
    "paid": "Pagada",
    "overpaid": "Pagada (exceso)",
    "cancelled": "Anulada",
    "expired": "Expirada",
}

STATUS_CLASSES = {
    "draft": "neutral",
    "open": "warning",
    "partially_paid": "warning",
    "paid": "success",
    "overpaid": "success",
    "cancelled": "danger",
    "expired": "danger",
}


def money(cents: int | None) -> str:
    if cents is None:
        return "—"
    return f"USD {Decimal(cents) / Decimal(100):,.2f}"


def quantity(value: Decimal | float | None) -> str:
    if value is None:
        return "—"
    text = f"{Decimal(str(value)):.3f}".rstrip("0").rstrip(".")
    return text or "0"


def percent(bps: int | None) -> str:
    if bps is None:
        return "—"
    return f"{bps / 100:.2f}%"


def _tz(settings: Settings) -> ZoneInfo:
    try:
        return ZoneInfo(settings.display_timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def format_date(value: date | datetime | None, settings: Settings | None = None) -> str:
    if not value:
        return "—"
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        if settings:
            value = value.astimezone(_tz(settings))
        return value.strftime("%d/%m/%Y")
    return value.strftime("%d/%m/%Y")


def local_today(settings: Settings) -> date:
    try:
        return datetime.now(_tz(settings)).date()
    except (ZoneInfoNotFoundError, ValueError):
        return date.today()


def format_datetime(value: datetime | None, settings: Settings | None = None) -> str:
    if not value:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    if settings:
        value = value.astimezone(_tz(settings))
    return value.strftime("%d/%m/%Y %H:%M")


def status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status)


def status_class(status: str) -> str:
    return STATUS_CLASSES.get(status, "neutral")


def flash(request: Request, message: str, category: str = "info") -> None:
    request.session.setdefault("_flashes", []).append(
        {"message": message, "category": category}
    )


def pop_flashes(request: Request) -> list[dict[str, str]]:
    return request.session.pop("_flashes", [])


def client_ip(request: Request) -> str:
    settings: Settings = request.app.state.settings
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def safe_next(value: str | None, default: str = "/admin") -> str:
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return default


def require_admin(request: Request) -> None:
    if not request.session.get("admin"):
        raise LoginRequired()


def redirect(url: str, status_code: int = 303) -> RedirectResponse:
    return RedirectResponse(url=url, status_code=status_code)


def login_redirect(request: Request) -> RedirectResponse:
    target = request.url.path
    if request.url.query:
        target += "?" + request.url.query
    return redirect(f"/login?next={quote(target, safe='')}")


def render(
    request: Request,
    name: str,
    context: dict[str, Any] | None = None,
    *,
    status_code: int = 200,
):
    settings: Settings = request.app.state.settings
    ctx: dict[str, Any] = {
        "request": request,
        "settings": settings,
        "csrf_token": ensure_csrf_token(request),
        "flashes": pop_flashes(request),
        "status_label": status_label,
        "status_class": status_class,
        "money": money,
        "quantity": quantity,
        "percent": percent,
        "format_date": lambda value: format_date(value, settings),
        "format_datetime": lambda value: format_datetime(value, settings),
        "today": date.today(),
    }
    if context:
        ctx.update(context)
    return templates.TemplateResponse(
        request=request,
        name=name,
        context=ctx,
        status_code=status_code,
    )


def invoice_context(invoice: Invoice, settings: Settings) -> dict[str, Any]:
    from app.invoices import public_invoice_url

    return {
        "invoice": invoice,
        "public_url": public_invoice_url(invoice, settings),
    }
