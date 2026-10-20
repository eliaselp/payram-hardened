"""Webhook firmado de PayRam."""

from __future__ import annotations

import hashlib
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_db
from app.invoices import apply_payment_status, mark_receipt_sent
from app.mailer import send_receipt_email
from app.models import Invoice, PaymentAttempt, WebhookEvent, utcnow
from app.payram import PayRamClient, PayRamError, parse_webhook_payload
from app.pdf import build_invoice_pdf

logger = logging.getLogger(__name__)

router = APIRouter()


def _send_receipt_task(invoice_id: int, session_factory, settings: Settings) -> None:
    db = session_factory()
    try:
        invoice = db.get(Invoice, invoice_id)
        if not invoice or invoice.receipt_sent_at or not settings.smtp_configured:
            return
        pdf_bytes = build_invoice_pdf(invoice, settings)
        send_receipt_email(invoice, settings, pdf_bytes)
        mark_receipt_sent(db, invoice)
        logger.info("Recibo enviado para %s", invoice.number)
    except Exception:
        logger.exception("No se pudo enviar el recibo de la factura %s", invoice_id)
    finally:
        db.close()


@router.post("/webhooks/payram")
async def payram_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    settings: Settings = request.app.state.settings
    if not settings.payram_api_key:
        return JSONResponse({"error": "PayRam no configurado"}, status_code=503)

    raw_body = await request.body()
    if len(raw_body) > settings.payram_max_webhook_bytes:
        return JSONResponse({"error": "Payload demasiado grande"}, status_code=413)

    signature = request.headers.get("x-payram-signature")
    legacy_key = request.headers.get("api-key")
    if not PayRamClient.verify_webhook(
        raw_body,
        signature,
        legacy_key,
        settings.payram_api_key,
        require_signature=settings.payram_webhook_require_signature,
    ):
        logger.warning("Webhook PayRam con firma inválida desde %s", request.client.host if request.client else "?")
        return JSONResponse({"error": "Firma inválida"}, status_code=401)

    try:
        payload = parse_webhook_payload(raw_body)
    except PayRamError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    reference_id = str(payload.get("reference_id") or payload.get("referenceID") or "").strip()
    status = str(payload.get("status") or "OPEN").upper()
    event_hash = hashlib.sha256(raw_body).hexdigest()

    existing = db.scalar(select(WebhookEvent).where(WebhookEvent.event_hash == event_hash))
    if existing and existing.processed_at:
        return JSONResponse({"ok": True, "duplicate": True})

    attempt = db.scalar(
        select(PaymentAttempt).where(PaymentAttempt.reference_id == reference_id)
    ) if reference_id else None
    if not attempt:
        logger.warning("Webhook de referencia desconocida: %s", reference_id)
        event = existing or WebhookEvent(
            event_hash=event_hash,
            reference_id=reference_id or None,
            status=status,
            payload_json=raw_body.decode("utf-8", errors="replace")[:16000],
        )
        event.processed_at = utcnow()
        event.error = "reference_id desconocido"
        db.add(event)
        db.commit()
        return JSONResponse({"ok": True, "ignored": True})

    invoice = db.get(Invoice, attempt.invoice_id)
    if not invoice:
        return JSONResponse({"ok": True, "ignored": True})

    # Defensa en profundidad: la referencia ya vincula el pago a un intento,
    # pero verificamos también que el invoice_id coincida con el número local.
    payload_invoice = payload.get("invoice_id") or payload.get("invoiceID")
    if payload_invoice and str(payload_invoice) != invoice.number:
        logger.error(
            "Webhook con invoice_id inconsistente: payload=%s local=%s ref=%s",
            payload_invoice,
            invoice.number,
            reference_id,
        )
        return JSONResponse({"error": "invoice_id inconsistente"}, status_code=400)

    try:
        became_paid = apply_payment_status(db, invoice, attempt, payload, actor="payram-webhook")
    except Exception:
        db.rollback()
        logger.exception("Error procesando webhook %s para %s", status, invoice.number)
        return JSONResponse({"error": "Error temporal"}, status_code=500)

    event = existing or WebhookEvent(
        event_hash=event_hash,
        reference_id=reference_id,
        invoice_id=invoice.id,
        status=status,
        payload_json=raw_body.decode("utf-8", errors="replace")[:16000],
    )
    event.invoice_id = invoice.id
    event.status = status
    event.processed_at = utcnow()
    event.error = None
    db.add(event)
    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("No se pudo registrar el evento de webhook")

    if became_paid and not invoice.receipt_sent_at and settings.smtp_configured:
        background_tasks.add_task(
            _send_receipt_task,
            invoice.id,
            request.app.state.db.session_factory,
            settings,
        )

    return JSONResponse({"ok": True, "status": invoice.status})
