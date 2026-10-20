"""Página pública de factura y redirección al checkout de PayRam."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_db
from app.invoices import (
    InvoiceError,
    ensure_payment_link,
    public_invoice_url,
)
from app.models import Invoice
from app.pdf import build_invoice_pdf
from app.web import render

logger = logging.getLogger(__name__)

router = APIRouter()


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _get_invoice(db: Session, token: str) -> Invoice:
    invoice = db.scalar(select(Invoice).where(Invoice.public_token == token))
    if not invoice:
        raise HTTPException(status_code=404, detail="Factura no encontrada")
    return invoice


def _public_headers(response):
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@router.get("/i/{token}")
def public_invoice(request: Request, token: str, db: Session = Depends(get_db)):
    invoice = _get_invoice(db, token)
    response = render(
        request,
        "public_invoice.html",
        {
            "invoice": invoice,
            "public_url": public_invoice_url(invoice, _settings(request)),
            "can_pay": invoice.status in {"open", "partially_paid", "draft"},
            "remaining_cents": max(0, invoice.total_cents - invoice.paid_amount_cents),
        },
    )
    return _public_headers(response)


@router.get("/i/{token}/pay")
def public_pay(request: Request, token: str, db: Session = Depends(get_db)):
    invoice = _get_invoice(db, token)
    if invoice.status in {"paid", "overpaid", "cancelled", "expired"}:
        return RedirectResponse(f"/i/{token}", status_code=302)
    try:
        attempt = ensure_payment_link(
            db,
            invoice,
            _settings(request),
            request.app.state.payram,
            actor="customer",
        )
    except InvoiceError as exc:
        logger.warning("No se pudo generar link para %s: %s", invoice.number, exc)
        response = render(
            request,
            "public_invoice.html",
            {
                "invoice": invoice,
                "public_url": public_invoice_url(invoice, _settings(request)),
                "can_pay": False,
                "remaining_cents": max(0, invoice.total_cents - invoice.paid_amount_cents),
                "payment_error": (
                    "No se pudo iniciar el pago en este momento. "
                    "Intenta de nuevo o contacta al emisor de la factura."
                ),
            },
            status_code=502,
        )
        return _public_headers(response)

    return RedirectResponse(attempt.url, status_code=302)


@router.get("/i/{token}/pdf")
def public_pdf(request: Request, token: str, db: Session = Depends(get_db)):
    invoice = _get_invoice(db, token)
    pdf_bytes = build_invoice_pdf(invoice, _settings(request))
    response = Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{invoice.number}.pdf"'},
    )
    return _public_headers(response)


@router.get("/i/{token}/status")
def public_status(request: Request, token: str, db: Session = Depends(get_db)):
    invoice = _get_invoice(db, token)
    return JSONResponse(
        {
            "number": invoice.number,
            "status": invoice.status,
            "total_cents": invoice.total_cents,
            "paid_cents": invoice.paid_amount_cents,
            "remaining_cents": max(0, invoice.total_cents - invoice.paid_amount_cents),
            "paid_at": invoice.paid_at.isoformat() if invoice.paid_at else None,
        },
        headers={"Cache-Control": "no-store"},
    )

