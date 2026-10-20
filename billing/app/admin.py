"""Panel de administración: login, dashboard y gestión de facturas."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_db
from app.invoices import (
    InvoiceError,
    cancel_invoice,
    create_invoice,
    ensure_payment_link,
    mark_email_error,
    mark_email_sent,
    mark_whatsapp_sent,
    reactivate_invoice,
    reconcile_invoice,
)
from app.mailer import EmailError, send_invoice_email, send_receipt_email
from app.models import AuditLog, Invoice, PaymentAttempt
from app.payram import PayRamClient, PayRamError
from app.pdf import build_invoice_pdf
from app.security import LoginRateLimiter, verify_csrf, verify_password
from app.web import (
    client_ip,
    flash,
    invoice_context,
    local_today,
    redirect,
    render,
    require_admin,
    safe_next,
)
from app.whatsapp import WhatsAppError, click_to_chat_url, send_cloud_text

logger = logging.getLogger(__name__)

router = APIRouter()
public_admin_router = APIRouter()


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_payram(request: Request) -> PayRamClient:
    return request.app.state.payram


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


@public_admin_router.get("/login")
def login_page(request: Request, next: str | None = None):
    if request.session.get("admin"):
        return redirect(safe_next(next))
    return render(request, "login.html", {"next": safe_next(next)})


@public_admin_router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
    next: str = Form("/admin"),
):
    verify_csrf(request, csrf_token)
    settings: Settings = request.app.state.settings
    limiter: LoginRateLimiter = request.app.state.login_limiter
    ip = client_ip(request)
    if limiter.is_blocked(ip):
        flash(request, "Demasiados intentos. Espera unos minutos.", "error")
        return redirect(f"/login?next={next}")

    if not settings.admin_configured:
        flash(request, "La cuenta administradora aún no está configurada.", "error")
        return redirect("/login")

    expected_hash = settings.admin_password_hash
    if not expected_hash and settings.admin_password:
        from app.security import hash_password

        expected_hash = hash_password(settings.admin_password)

    if username.strip() != settings.admin_username or not verify_password(password, expected_hash):
        limiter.record_failure(ip)
        logger.warning("Login fallido desde %s", ip)
        flash(request, "Usuario o contraseña incorrectos.", "error")
        return redirect(f"/login?next={safe_next(next)}")

    limiter.reset(ip)
    request.session.clear()
    request.session["admin"] = True
    request.session["admin_user"] = settings.admin_username
    return redirect(safe_next(next))


@public_admin_router.post("/logout")
def logout(request: Request, csrf_token: str = Form(...)):
    require_admin(request)
    verify_csrf(request, csrf_token)
    request.session.clear()
    return redirect("/login")


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@router.get("/admin")
def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    status: str | None = None,
    q: str | None = None,
    page: int = 1,
):
    require_admin(request)
    settings = get_settings(request)
    page = max(1, page)
    per_page = 25

    conditions = []
    if status and status != "all":
        conditions.append(Invoice.status == status)
    if q:
        needle = f"%{q.strip()}%"
        conditions.append(
            or_(
                Invoice.number.ilike(needle),
                Invoice.customer_name.ilike(needle),
                Invoice.customer_email.ilike(needle),
            )
        )

    count_stmt = select(func.count()).select_from(Invoice)
    if conditions:
        count_stmt = count_stmt.where(*conditions)
    total_count = int(db.scalar(count_stmt) or 0)
    pages = max(1, (total_count + per_page - 1) // per_page)

    stmt = select(Invoice).order_by(Invoice.created_at.desc(), Invoice.id.desc())
    if conditions:
        stmt = stmt.where(*conditions)
    stmt = stmt.offset((page - 1) * per_page).limit(per_page)
    invoices = list(db.scalars(stmt).all())

    active_statuses = ["open", "partially_paid", "paid", "overpaid", "expired"]
    total_invoiced = int(
        db.scalar(
            select(func.coalesce(func.sum(Invoice.total_cents), 0)).where(
                Invoice.status.in_(active_statuses)
            )
        )
        or 0
    )
    collected = int(
        db.scalar(
            select(func.coalesce(func.sum(Invoice.paid_amount_cents), 0)).where(
                Invoice.status.in_(["paid", "overpaid"])
            )
        )
        or 0
    )
    outstanding = int(
        db.scalar(
            select(
                func.coalesce(
                    func.sum(Invoice.total_cents - Invoice.paid_amount_cents), 0
                )
            ).where(Invoice.status.in_(["open", "partially_paid", "expired"]))
        )
        or 0
    )
    status_rows = db.execute(
        select(Invoice.status, func.count()).group_by(Invoice.status)
    ).all()
    counts = {str(row[0]): int(row[1]) for row in status_rows}

    return render(
        request,
        "dashboard.html",
        {
            "invoices": invoices,
            "total_count": total_count,
            "pages": pages,
            "page": page,
            "status": status or "all",
            "q": q or "",
            "stats": {
                "invoiced": total_invoiced,
                "collected": collected,
                "outstanding": outstanding,
                "count": sum(counts.values()),
            },
            "counts": counts,
            "payram_configured": settings.payram_configured,
        },
    )


# ---------------------------------------------------------------------------
# Nueva factura
# ---------------------------------------------------------------------------


def _default_form(settings: Settings) -> dict:
    today = local_today(settings)
    return {
        "customer_name": "",
        "customer_email": "",
        "customer_phone": "",
        "description": "",
        "notes": "",
        "issue_date": today.isoformat(),
        "due_date": (today + timedelta(days=15)).isoformat(),
        "tax_rate": f"{settings.default_tax_rate_bps / 100:.2f}",
    }


def _default_items() -> list[dict[str, str]]:
    return [
        {"description": "", "quantity": "1", "unit_price": ""},
        {"description": "", "quantity": "1", "unit_price": ""},
        {"description": "", "quantity": "1", "unit_price": ""},
    ]


def parse_usd_to_cents(value: str | None) -> int:
    if value is None or str(value).strip() == "":
        return 0
    raw = str(value).strip().replace("USD", "").replace("$", "").replace(" ", "")
    try:
        if "," in raw and "." in raw:
            raw = raw.replace(".", "").replace(",", ".")
        elif "," in raw:
            raw = raw.replace(",", ".")
        amount = Decimal(raw).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise InvoiceError(f"Monto inválido: {value}") from exc
    if amount < 0:
        raise InvoiceError("El monto no puede ser negativo.")
    return int(amount * 100)


def parse_tax_bps(value: str | None) -> int:
    if value is None or str(value).strip() == "":
        return 0
    try:
        rate = Decimal(str(value).replace("%", "").strip())
    except (InvalidOperation, ValueError) as exc:
        raise InvoiceError("Tasa de impuesto inválida.") from exc
    if rate < 0 or rate > 100:
        raise InvoiceError("La tasa de impuesto debe estar entre 0 y 100.")
    return int((rate * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def parse_items(form) -> list[dict]:
    items: list[dict] = []
    for index in range(50):
        description = str(form.get(f"item_description_{index}") or "").strip()
        quantity = str(form.get(f"item_quantity_{index}") or "").strip()
        price = str(form.get(f"item_price_{index}") or "").strip()
        if not description and not price:
            continue
        items.append(
            {
                "description": description,
                "quantity": quantity or "1",
                "unit_price_cents": parse_usd_to_cents(price),
            }
        )
    return items


def _items_from_form(form) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for index in range(50):
        description = str(form.get(f"item_description_{index}") or "")
        quantity = str(form.get(f"item_quantity_{index}") or "1")
        price = str(form.get(f"item_price_{index}") or "")
        if description or price or index < 3:
            rows.append({"description": description, "quantity": quantity, "unit_price": price})
    return rows or _default_items()


@router.get("/admin/invoices/new")
def invoice_new(request: Request):
    require_admin(request)
    settings = get_settings(request)
    return render(
        request,
        "invoice_new.html",
        {
            "form": _default_form(settings),
            "items": _default_items(),
            "error": None,
            "today": local_today(settings).isoformat(),
        },
    )


@router.post("/admin/invoices")
async def invoice_create(request: Request, db: Session = Depends(get_db)):
    require_admin(request)
    settings = get_settings(request)
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))

    try:
        items = parse_items(form)
        issue_date = date.fromisoformat(str(form.get("issue_date") or date.today().isoformat()))
        due_raw = str(form.get("due_date") or "").strip()
        due_date = date.fromisoformat(due_raw) if due_raw else None
        invoice = create_invoice(
            db,
            customer_name=str(form.get("customer_name") or ""),
            customer_email=str(form.get("customer_email") or ""),
            customer_phone=str(form.get("customer_phone") or ""),
            description=str(form.get("description") or ""),
            notes=str(form.get("notes") or ""),
            items=items,
            tax_rate_bps=parse_tax_bps(form.get("tax_rate")),
            issue_date=issue_date,
            due_date=due_date,
            prefix=settings.invoice_prefix,
            actor="admin",
        )
    except (InvoiceError, ValueError) as exc:
        return render(
            request,
            "invoice_new.html",
            {
                "form": dict(form),
                "items": _items_from_form(form),
                "error": str(exc),
                "today": local_today(settings).isoformat(),
            },
            status_code=400,
        )

    link_error = None
    try:
        ensure_payment_link(db, invoice, settings, get_payram(request), actor="admin")
    except InvoiceError as exc:
        link_error = str(exc)
        logger.warning("Factura %s creada sin link: %s", invoice.number, exc)

    if form.get("send_email") and settings.smtp_configured:
        try:
            pdf_bytes = build_invoice_pdf(invoice, settings)
            send_invoice_email(invoice, settings, pdf_bytes)
            mark_email_sent(db, invoice, actor="admin")
            flash(request, f"Factura {invoice.number} creada y enviada por correo.", "success")
        except (EmailError, OSError) as exc:
            mark_email_error(db, invoice, str(exc), actor="admin")
            flash(request, f"Factura creada, pero el correo falló: {exc}", "warning")
    else:
        if link_error:
            flash(
                request,
                f"Factura {invoice.number} creada. El link de pago quedó pendiente: {link_error}",
                "warning",
            )
        else:
            flash(request, f"Factura {invoice.number} creada con link de pago.", "success")

    return redirect(f"/admin/invoices/{invoice.id}")


# ---------------------------------------------------------------------------
# Detalle y acciones
# ---------------------------------------------------------------------------


def _get_invoice(db: Session, invoice_id: int) -> Invoice:
    invoice = db.get(Invoice, invoice_id)
    if not invoice:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Factura no encontrada")
    return invoice


def _detail_context(request: Request, db: Session, invoice: Invoice) -> dict:
    settings = get_settings(request)
    attempts = list(
        db.scalars(
            select(PaymentAttempt)
            .where(PaymentAttempt.invoice_id == invoice.id)
            .order_by(PaymentAttempt.id.desc())
        ).all()
    )
    logs = list(
        db.scalars(
            select(AuditLog)
            .where(AuditLog.invoice_id == invoice.id)
            .order_by(AuditLog.id.desc())
            .limit(25)
        ).all()
    )
    active_attempt = next(
        (
            a
            for a in attempts
            if a.reference_id == invoice.payram_reference_id and a.state == "OPEN"
        ),
        None,
    )
    return {
        **invoice_context(invoice, settings),
        "attempts": attempts,
        "logs": logs,
        "active_attempt": active_attempt,
        "whatsapp_url": click_to_chat_url(invoice, settings),
        "webhook_url": f"{settings.base_url}/webhooks/payram",
        "email_configured": settings.smtp_configured,
        "whatsapp_cloud_configured": settings.whatsapp_cloud_configured,
    }


@router.get("/admin/invoices/{invoice_id}")
def invoice_detail(request: Request, invoice_id: int, db: Session = Depends(get_db)):
    require_admin(request)
    invoice = _get_invoice(db, invoice_id)
    return render(request, "invoice_detail.html", _detail_context(request, db, invoice))


@router.post("/admin/invoices/{invoice_id}/link")
async def invoice_create_link(request: Request, invoice_id: int, db: Session = Depends(get_db)):
    require_admin(request)
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))
    invoice = _get_invoice(db, invoice_id)
    force = str(form.get("force") or "") == "1"
    try:
        attempt = ensure_payment_link(
            db,
            invoice,
            get_settings(request),
            get_payram(request),
            force_new=force,
            actor="admin",
        )
        flash(request, f"Link de pago listo: {attempt.reference_id}", "success")
    except InvoiceError as exc:
        flash(request, str(exc), "error")
    return redirect(f"/admin/invoices/{invoice.id}")


@router.post("/admin/invoices/{invoice_id}/refresh")
async def invoice_refresh(request: Request, invoice_id: int, db: Session = Depends(get_db)):
    require_admin(request)
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))
    invoice = _get_invoice(db, invoice_id)
    try:
        changed = reconcile_invoice(db, invoice, get_payram(request), actor="admin")
        flash(
            request,
            "Pago actualizado." if changed else "Estado sincronizado con PayRam.",
            "success",
        )
    except PayRamError as exc:
        flash(request, f"No se pudo consultar PayRam: {exc}", "error")
    return redirect(f"/admin/invoices/{invoice.id}")


@router.post("/admin/invoices/{invoice_id}/send-email")
async def invoice_send_email(request: Request, invoice_id: int, db: Session = Depends(get_db)):
    require_admin(request)
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))
    invoice = _get_invoice(db, invoice_id)
    settings = get_settings(request)
    try:
        if invoice.status in {"paid", "overpaid"}:
            pdf_bytes = build_invoice_pdf(invoice, settings)
            send_receipt_email(invoice, settings, pdf_bytes)
        else:
            ensure_payment_link(db, invoice, settings, get_payram(request), actor="admin")
            pdf_bytes = build_invoice_pdf(invoice, settings)
            send_invoice_email(invoice, settings, pdf_bytes)
        mark_email_sent(db, invoice, actor="admin")
        flash(request, f"Correo enviado a {invoice.customer_email}.", "success")
    except (InvoiceError, EmailError, OSError) as exc:
        mark_email_error(db, invoice, str(exc), actor="admin")
        flash(request, f"No se pudo enviar el correo: {exc}", "error")
    return redirect(f"/admin/invoices/{invoice.id}")


@router.post("/admin/invoices/{invoice_id}/send-whatsapp")
async def invoice_send_whatsapp(request: Request, invoice_id: int, db: Session = Depends(get_db)):
    require_admin(request)
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))
    invoice = _get_invoice(db, invoice_id)
    settings = get_settings(request)
    try:
        ensure_payment_link(db, invoice, settings, get_payram(request), actor="admin")
        send_cloud_text(invoice, settings)
        mark_whatsapp_sent(db, invoice, actor="admin")
        flash(request, "Mensaje enviado por WhatsApp Cloud API.", "success")
    except (InvoiceError, WhatsAppError) as exc:
        flash(request, f"No se pudo enviar por WhatsApp: {exc}", "error")
    return redirect(f"/admin/invoices/{invoice.id}")


@router.post("/admin/invoices/{invoice_id}/cancel")
async def invoice_cancel(request: Request, invoice_id: int, db: Session = Depends(get_db)):
    require_admin(request)
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))
    invoice = _get_invoice(db, invoice_id)
    try:
        cancel_invoice(db, invoice, actor="admin")
        flash(request, "Factura anulada. El link de PayRam puede seguir activo hasta su expiración.", "warning")
    except InvoiceError as exc:
        flash(request, str(exc), "error")
    return redirect(f"/admin/invoices/{invoice.id}")


@router.post("/admin/invoices/{invoice_id}/reactivate")
async def invoice_reactivate(request: Request, invoice_id: int, db: Session = Depends(get_db)):
    require_admin(request)
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))
    invoice = _get_invoice(db, invoice_id)
    try:
        reactivate_invoice(db, invoice, actor="admin")
        flash(request, "Factura reactivada.", "success")
    except InvoiceError as exc:
        flash(request, str(exc), "error")
    return redirect(f"/admin/invoices/{invoice.id}")


@router.get("/admin/invoices/{invoice_id}/pdf")
def invoice_pdf(request: Request, invoice_id: int, db: Session = Depends(get_db)):
    require_admin(request)
    invoice = _get_invoice(db, invoice_id)
    pdf_bytes = build_invoice_pdf(invoice, get_settings(request))
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{invoice.number}.pdf"'},
    )


# ---------------------------------------------------------------------------
# Estado del sistema
# ---------------------------------------------------------------------------


@router.get("/admin/settings")
def settings_page(request: Request):
    require_admin(request)
    settings = get_settings(request)
    payram_ok = False
    payram_error = None
    payram_pairs = 0
    try:
        probe = PayRamClient(
            settings.payram_base_url,
            settings.payram_api_key,
            timeout=3.0,
            public_url=settings.payram_public_base,
        )
        ticker = probe.get_ticker()
        payram_ok = True
        payram_pairs = len(ticker)
    except PayRamError as exc:
        payram_error = str(exc)

    masked_key = "—"
    if settings.payram_api_key:
        masked_key = "•" * 8 + settings.payram_api_key[-4:]
    return render(
        request,
        "settings.html",
        {
            "warnings": settings.validation_warnings(),
            "payram_ok": payram_ok,
            "payram_error": payram_error,
            "payram_pairs": payram_pairs,
            "masked_key": masked_key,
            "webhook_url": f"{settings.base_url}/webhooks/payram",
        },
    )
