"""API JSON para integrar la facturación con otros sistemas."""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_db
from app.invoices import (
    InvoiceError,
    create_invoice,
    ensure_payment_link,
    pdf_url,
    public_invoice_url,
    reconcile_invoice,
)
from app.mailer import EmailError, send_invoice_email
from app.models import Invoice
from app.payram import PayRamError
from app.pdf import build_invoice_pdf
from app.security import verify_api_key
from app.web import local_today

router = APIRouter(prefix="/api/v1")


class InvoiceItemIn(BaseModel):
    description: str = Field(min_length=1, max_length=500)
    quantity: Decimal = Field(default=Decimal(1), gt=0, le=1_000_000)
    unit_price: Decimal = Field(ge=0, le=1_000_000)


class InvoiceIn(BaseModel):
    customer_name: str = Field(min_length=1, max_length=200)
    customer_email: EmailStr
    customer_phone: str | None = Field(default=None, max_length=40)
    description: str = Field(default="", max_length=500)
    notes: str = Field(default="", max_length=4000)
    items: list[InvoiceItemIn] = Field(min_length=1, max_length=50)
    tax_rate: Decimal = Field(default=Decimal(0), ge=0, le=100)
    issue_date: date | None = None
    due_date: date | None = None
    external_id: str | None = Field(default=None, max_length=120)
    send_email: bool = False


def require_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    settings: Settings = request.app.state.settings
    if not settings.billing_api_key:
        raise HTTPException(status_code=503, detail="La API está deshabilitada (BILLING_API_KEY vacío).")
    if not verify_api_key(x_api_key, settings.billing_api_key):
        raise HTTPException(status_code=401, detail="API key inválida.")


def _usd(cents: int) -> str:
    return f"{Decimal(cents) / Decimal(100):.2f}"


def invoice_to_dict(invoice: Invoice, settings: Settings) -> dict:
    return {
        "number": invoice.number,
        "external_id": invoice.external_id,
        "status": invoice.status,
        "customer": {
            "name": invoice.customer_name,
            "email": invoice.customer_email,
            "phone": invoice.customer_phone,
        },
        "currency": invoice.currency,
        "subtotal_usd": _usd(invoice.subtotal_cents),
        "tax_rate_percent": f"{invoice.tax_rate_bps / 100:.2f}",
        "tax_usd": _usd(invoice.tax_cents),
        "total_usd": _usd(invoice.total_cents),
        "paid_usd": _usd(invoice.paid_amount_cents),
        "issue_date": invoice.issue_date.isoformat(),
        "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
        "created_at": invoice.created_at.isoformat() if invoice.created_at else None,
        "paid_at": invoice.paid_at.isoformat() if invoice.paid_at else None,
        "payment": {
            "reference_id": invoice.payram_reference_id,
            "checkout_url": invoice.payram_url,
            "state": invoice.last_payment_state,
        },
        "urls": {
            "public": public_invoice_url(invoice, settings),
            "pdf": pdf_url(invoice, settings),
        },
        "items": [
            {
                "description": item.description,
                "quantity": str(item.quantity),
                "unit_price_usd": _usd(item.unit_price_cents),
                "line_total_usd": _usd(item.line_total_cents),
            }
            for item in invoice.items
        ],
    }


@router.get("/health")
def api_health():
    return {"ok": True}


@router.post("/invoices", status_code=201, dependencies=[Depends(require_api_key)])
def api_create_invoice(
    payload: InvoiceIn,
    request: Request,
    db: Session = Depends(get_db),
):
    settings: Settings = request.app.state.settings
    if payload.external_id:
        existing = db.scalar(select(Invoice).where(Invoice.external_id == payload.external_id))
        if existing:
            return invoice_to_dict(existing, settings)

    items = [
        {
            "description": item.description,
            "quantity": item.quantity,
            "unit_price_cents": int(
                (item.unit_price * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)
            ),
        }
        for item in payload.items
    ]
    try:
        invoice = create_invoice(
            db,
            customer_name=payload.customer_name,
            customer_email=str(payload.customer_email),
            customer_phone=payload.customer_phone,
            description=payload.description,
            notes=payload.notes,
            items=items,
            tax_rate_bps=int(
                (payload.tax_rate * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)
            ),
            issue_date=payload.issue_date or local_today(settings),
            due_date=payload.due_date,
            external_id=payload.external_id,
            prefix=settings.invoice_prefix,
            actor="api",
        )
    except InvoiceError as exc:
        if payload.external_id:
            existing = db.scalar(select(Invoice).where(Invoice.external_id == payload.external_id))
            if existing:
                return invoice_to_dict(existing, settings)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    link_error = None
    try:
        ensure_payment_link(
            db,
            invoice,
            settings,
            request.app.state.payram,
            actor="api",
        )
    except InvoiceError as exc:
        link_error = str(exc)

    email_sent = False
    email_error = None
    if payload.send_email and settings.smtp_configured:
        try:
            pdf_bytes = build_invoice_pdf(invoice, settings)
            send_invoice_email(invoice, settings, pdf_bytes)
            from app.invoices import mark_email_sent

            mark_email_sent(db, invoice, actor="api")
            email_sent = True
        except (EmailError, OSError) as exc:
            email_error = str(exc)
    elif payload.send_email:
        email_error = "SMTP no configurado"

    result = invoice_to_dict(invoice, settings)
    result["email_sent"] = email_sent
    result["email_error"] = email_error
    result["payment_link_error"] = link_error
    return result


@router.get("/invoices/{number}", dependencies=[Depends(require_api_key)])
def api_get_invoice(
    number: str,
    request: Request,
    db: Session = Depends(get_db),
):
    invoice = db.scalar(select(Invoice).where(Invoice.number == number.upper()))
    if not invoice:
        raise HTTPException(status_code=404, detail="Factura no encontrada.")
    return invoice_to_dict(invoice, request.app.state.settings)


@router.post("/invoices/{number}/link", dependencies=[Depends(require_api_key)])
def api_create_link(
    number: str,
    request: Request,
    force_new: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    invoice = db.scalar(select(Invoice).where(Invoice.number == number.upper()))
    if not invoice:
        raise HTTPException(status_code=404, detail="Factura no encontrada.")
    try:
        attempt = ensure_payment_link(
            db,
            invoice,
            request.app.state.settings,
            request.app.state.payram,
            force_new=force_new,
            actor="api",
        )
    except InvoiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "number": invoice.number,
        "reference_id": attempt.reference_id,
        "checkout_url": attempt.url,
        "expires_at": attempt.expires_at.isoformat() if attempt.expires_at else None,
    }


@router.post("/invoices/{number}/refresh", dependencies=[Depends(require_api_key)])
def api_refresh_invoice(
    number: str,
    request: Request,
    db: Session = Depends(get_db),
):
    invoice = db.scalar(select(Invoice).where(Invoice.number == number.upper()))
    if not invoice:
        raise HTTPException(status_code=404, detail="Factura no encontrada.")
    try:
        reconcile_invoice(db, invoice, request.app.state.payram, actor="api")
    except PayRamError as exc:
        raise HTTPException(status_code=502, detail=f"PayRam: {exc}") from exc
    return invoice_to_dict(invoice, request.app.state.settings)


@router.get("/invoices/{number}/pdf", dependencies=[Depends(require_api_key)])
def api_invoice_pdf(
    number: str,
    request: Request,
    db: Session = Depends(get_db),
):
    invoice = db.scalar(select(Invoice).where(Invoice.number == number.upper()))
    if not invoice:
        raise HTTPException(status_code=404, detail="Factura no encontrada.")
    pdf_bytes = build_invoice_pdf(invoice, request.app.state.settings)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{invoice.number}.pdf"'},
    )
