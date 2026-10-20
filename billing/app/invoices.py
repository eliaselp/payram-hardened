"""Lógica de negocio de facturas y links de pago."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from email_validator import EmailNotValidError, validate_email
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AuditLog, Invoice, InvoiceItem, PaymentAttempt, utcnow
from app.payram import (
    PaymentStatus,
    PayRamClient,
    PayRamError,
    customer_id_for_email,
    usd_string_to_cents,
)

logger = logging.getLogger(__name__)

MAX_ITEMS = 50
MIN_TOTAL_CENTS = 100
MAX_TOTAL_CENTS = 100_000_000  # USD 1,000,000


class InvoiceError(ValueError):
    """Error de negocio mostrable al usuario."""


def ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def audit(
    db: Session,
    action: str,
    *,
    invoice_id: int | None = None,
    detail: str = "",
    actor: str = "system",
) -> None:
    db.add(AuditLog(actor=actor, action=action, invoice_id=invoice_id, detail=detail[:4000]))


def _compute_totals(items: list[dict[str, Any]], tax_rate_bps: int) -> tuple[list[dict], int, int, int]:
    if not items:
        raise InvoiceError("La factura necesita al menos una línea.")
    if len(items) > MAX_ITEMS:
        raise InvoiceError(f"Máximo {MAX_ITEMS} líneas por factura.")
    if tax_rate_bps < 0 or tax_rate_bps > 10_000:
        raise InvoiceError("La tasa de impuesto debe estar entre 0% y 100%.")

    normalized: list[dict] = []
    subtotal = 0
    for index, item in enumerate(items):
        description = str(item.get("description") or "").strip()
        if not description:
            raise InvoiceError(f"La línea {index + 1} no tiene descripción.")
        if len(description) > 500:
            raise InvoiceError(f"La descripción de la línea {index + 1} es demasiado larga.")
        try:
            quantity = Decimal(str(item.get("quantity", "1"))).quantize(Decimal("0.001"))
        except Exception as exc:
            raise InvoiceError(f"Cantidad inválida en la línea {index + 1}.") from exc
        if quantity <= 0:
            raise InvoiceError(f"La cantidad de la línea {index + 1} debe ser mayor que cero.")
        try:
            unit_price_cents = int(item.get("unit_price_cents", 0))
        except (TypeError, ValueError) as exc:
            raise InvoiceError(f"Precio inválido en la línea {index + 1}.") from exc
        if unit_price_cents < 0:
            raise InvoiceError(f"El precio de la línea {index + 1} no puede ser negativo.")
        line_total = int(
            (quantity * Decimal(unit_price_cents)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        )
        subtotal += line_total
        normalized.append(
            {
                "description": description,
                "quantity": quantity,
                "unit_price_cents": unit_price_cents,
                "line_total_cents": line_total,
            }
        )

    tax_cents = int(
        (Decimal(subtotal) * Decimal(tax_rate_bps) / Decimal(10_000)).quantize(
            Decimal(1), rounding=ROUND_HALF_UP
        )
    )
    total = subtotal + tax_cents
    if total < MIN_TOTAL_CENTS:
        raise InvoiceError("El total mínimo de una factura es USD 1.00.")
    if total > MAX_TOTAL_CENTS:
        raise InvoiceError("El total supera el máximo permitido por esta instancia.")
    return normalized, subtotal, tax_cents, total


def create_invoice(
    db: Session,
    *,
    customer_name: str,
    customer_email: str,
    customer_phone: str | None,
    description: str,
    notes: str,
    items: list[dict[str, Any]],
    tax_rate_bps: int,
    issue_date: date | None = None,
    due_date: date | None = None,
    external_id: str | None = None,
    prefix: str = "INV",
    actor: str = "admin",
) -> Invoice:
    customer_name = customer_name.strip()
    customer_email = customer_email.strip().lower()
    prefix = (prefix or "INV").strip().upper() or "INV"
    if not customer_name:
        raise InvoiceError("El nombre del cliente es obligatorio.")
    if "\r" in customer_email or "\n" in customer_email:
        raise InvoiceError("El correo del cliente no es válido.")
    try:
        validated = validate_email(customer_email, check_deliverability=False)
        customer_email = validated.normalized.lower()
    except EmailNotValidError as exc:
        raise InvoiceError("El correo del cliente no es válido.") from exc
    if external_id:
        external_id = external_id.strip()[:120] or None

    normalized_items, subtotal, tax_cents, total = _compute_totals(items, tax_rate_bps)
    issue_date = issue_date or date.today()
    if due_date and due_date < issue_date:
        raise InvoiceError("La fecha de vencimiento no puede ser anterior a la de emisión.")

    invoice = Invoice(
        number=f"TMP-{uuid.uuid4().hex}",
        customer_name=customer_name,
        customer_email=customer_email,
        customer_phone=(customer_phone or "").strip() or None,
        description=(description or "").strip()[:500],
        notes=(notes or "").strip()[:4000],
        subtotal_cents=subtotal,
        tax_rate_bps=tax_rate_bps,
        tax_cents=tax_cents,
        total_cents=total,
        status="draft",
        issue_date=issue_date,
        due_date=due_date,
        external_id=external_id,
    )
    for position, item in enumerate(normalized_items):
        invoice.items.append(
            InvoiceItem(
                position=position,
                description=item["description"],
                quantity=item["quantity"],
                unit_price_cents=item["unit_price_cents"],
                line_total_cents=item["line_total_cents"],
            )
        )

    db.add(invoice)
    try:
        db.flush()
        invoice.number = f"{prefix}-{issue_date.year}-{invoice.id:05d}"
        audit(db, "invoice.created", invoice_id=invoice.id, detail=invoice.number, actor=actor)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if external_id:
            raise InvoiceError("Ya existe una factura con ese identificador externo.") from exc
        logger.exception("No se pudo crear la factura")
        raise InvoiceError("No se pudo crear la factura por un conflicto de datos.") from exc

    db.refresh(invoice)
    return invoice


def public_invoice_url(invoice: Invoice, settings: Settings) -> str:
    return f"{settings.base_url}/i/{invoice.public_token}"


def pdf_url(invoice: Invoice, settings: Settings) -> str:
    return f"{public_invoice_url(invoice, settings)}/pdf"


def _active_open_attempt(invoice: Invoice) -> PaymentAttempt | None:
    now = utcnow()
    for attempt in reversed(invoice.payments):
        expires = ensure_utc(attempt.expires_at)
        if attempt.state in {"OPEN", "PARTIALLY_FILLED"} and (
            expires is None or expires > now + timedelta(minutes=2)
        ):
            return attempt
    return None


def ensure_payment_link(
    db: Session,
    invoice: Invoice,
    settings: Settings,
    client: PayRamClient,
    *,
    force_new: bool = False,
    actor: str = "admin",
) -> PaymentAttempt:
    if invoice.status in {"paid", "overpaid"}:
        raise InvoiceError("La factura ya está pagada.")
    if invoice.status == "cancelled" and not force_new:
        raise InvoiceError("La factura está anulada. Reactívala antes de generar un link.")

    existing = _active_open_attempt(invoice)
    if existing and not force_new:
        return existing

    if not settings.payram_api_key:
        raise InvoiceError(
            "PayRam aún no está configurado (PAYRAM_API_KEY). Configúralo y reinicia el servicio."
        )

    remaining_cents = invoice.total_cents - invoice.paid_amount_cents
    if remaining_cents < MIN_TOTAL_CENTS:
        raise InvoiceError("El saldo pendiente es menor a USD 1.00; no se puede generar otro link.")

    now = utcnow()
    expire = now + timedelta(days=max(1, settings.invoice_link_ttl_days))
    try:
        link = client.create_payment(
            customer_email=invoice.customer_email,
            customer_id=customer_id_for_email(invoice.customer_email),
            amount_cents=remaining_cents,
            invoice_id=invoice.number,
            expire=expire,
        )
    except PayRamError as exc:
        audit(
            db,
            "payment_link.failed",
            invoice_id=invoice.id,
            detail=f"{exc} {exc.detail or ''}".strip(),
            actor=actor,
        )
        db.commit()
        raise InvoiceError(f"No se pudo crear el link de pago: {exc}") from exc

    attempt = PaymentAttempt(
        invoice_id=invoice.id,
        reference_id=link.reference_id,
        url=link.checkout_url,
        state="OPEN",
        requested_amount_cents=remaining_cents,
        expires_at=expire,
        payload_json=str(link.raw)[:8000],
    )
    db.add(attempt)

    invoice.payram_reference_id = link.reference_id
    invoice.payram_url = link.checkout_url
    invoice.last_payment_state = "OPEN"
    if invoice.status in {"draft", "expired"}:
        invoice.status = "open"
    if invoice.status == "cancelled" and force_new:
        invoice.status = "open"
        invoice.cancelled_at = None
    audit(db, "payment_link.created", invoice_id=invoice.id, detail=link.reference_id, actor=actor)
    db.commit()
    db.refresh(attempt)
    return attempt


def _payment_info(payload: dict[str, Any]) -> list[dict[str, Any]]:
    info = payload.get("payment_info") or payload.get("paymentInfo") or []
    return info if isinstance(info, list) else []


def apply_payment_status(
    db: Session,
    invoice: Invoice,
    attempt: PaymentAttempt,
    payload: dict[str, Any],
    *,
    actor: str = "payram",
) -> bool:
    """Aplica un estado de PayRam y recalcula la factura.

    Devuelve True si la factura pasa a pagada en esta llamada.
    Es idempotente: repetir el mismo webhook no duplica montos.
    """
    state = str(payload.get("status") or payload.get("paymentState") or "OPEN").upper()
    if state not in {"OPEN", "PARTIALLY_FILLED", "FILLED", "OVER_FILLED", "CANCELLED"}:
        logger.warning("Estado PayRam desconocido: %s", state)
        return False

    # Un pago ya confirmado no puede volver a OPEN/PARTIAL/CANCELLED por un
    # evento tardío o duplicado. El dinero ya está on-chain.
    if attempt.state in {"FILLED", "OVER_FILLED"} and state in {
        "OPEN",
        "PARTIALLY_FILLED",
        "CANCELLED",
    }:
        logger.warning(
            "Ignorando regresión %s -> %s para %s", attempt.state, state, attempt.reference_id
        )
        return False
    if attempt.state == "PARTIALLY_FILLED" and state == "OPEN":
        logger.warning("Ignorando OPEN tardío para pago parcial %s", attempt.reference_id)
        return False

    filled_usd = (
        payload.get("filled_amount_in_usd")
        or payload.get("filledAmountInUSD")
        or payload.get("filledAmount")
    )
    filled_cents = usd_string_to_cents(filled_usd)
    if state in {"FILLED", "OVER_FILLED"} and filled_cents == 0:
        # Fallback: algunos payloads solo traen el monto solicitado.
        filled_cents = usd_string_to_cents(
            payload.get("amountInUSD") or payload.get("amount") or (attempt.requested_amount_cents / 100)
        )

    attempt.state = state
    # El monto recibido es monotónico: un evento tardío nunca debe borrar
    # dinero ya detectado.
    attempt.filled_amount_cents = max(attempt.filled_amount_cents, filled_cents)
    attempt.last_checked_at = utcnow()
    info = _payment_info(payload)
    if info:
        tx = info[-1].get("transaction_hash") or info[-1].get("transactionHash")
        if tx:
            attempt.tx_hash = str(tx)[:120]
    if payload.get("currency"):
        invoice.paid_currency = str(payload["currency"])[:16]
    attempt.payload_json = str(payload)[:16000]

    paid_cents = sum(max(0, p.filled_amount_cents) for p in invoice.payments)
    already_paid = invoice.status in {"paid", "overpaid"}
    became_paid = False

    invoice.paid_amount_cents = paid_cents
    invoice.last_payment_state = state

    if paid_cents >= invoice.total_cents and invoice.total_cents > 0:
        new_status = "paid" if paid_cents == invoice.total_cents else "overpaid"
        if invoice.status != new_status:
            audit(
                db,
                "invoice.paid",
                invoice_id=invoice.id,
                detail=f"{state} paid_cents={paid_cents} ref={attempt.reference_id}",
                actor=actor,
            )
        invoice.status = new_status
        if not invoice.paid_at:
            invoice.paid_at = utcnow()
        if not invoice.paid_tx_hash and attempt.tx_hash:
            invoice.paid_tx_hash = attempt.tx_hash
        became_paid = not already_paid
    elif paid_cents > 0:
        invoice.status = "partially_paid"
    elif state == "CANCELLED":
        if not already_paid:
            expires = ensure_utc(attempt.expires_at)
            if (expires and expires <= utcnow()) or (
                invoice.due_date and invoice.due_date < date.today()
            ):
                invoice.status = "expired"
            else:
                invoice.status = "cancelled"
    else:
        if invoice.status == "draft":
            invoice.status = "open"

    db.add(attempt)
    db.add(invoice)
    db.commit()
    return became_paid


def reconcile_invoice(
    db: Session,
    invoice: Invoice,
    client: PayRamClient,
    *,
    actor: str = "reconcile",
) -> bool:
    # Revisa los intentos todavía abiertos, no solo el último. Un cliente pudo
    # haber pagado un link anterior después de que se generara uno nuevo.
    attempts = [
        attempt
        for attempt in invoice.payments
        if attempt.state in {"OPEN", "PARTIALLY_FILLED"}
    ]
    attempts.sort(key=lambda attempt: attempt.id, reverse=True)
    if not attempts:
        return False

    for attempt in attempts[:5]:
        status: PaymentStatus = client.get_payment(attempt.reference_id)
        payload: dict[str, Any] = dict(status.raw)
        payload["status"] = status.state
        if status.filled_amount_usd is not None:
            payload["filled_amount_in_usd"] = str(status.filled_amount_usd)
        if status.amount_usd is not None:
            payload["amountInUSD"] = str(status.amount_usd)
        if status.currency:
            payload["currency"] = status.currency
        became_paid = apply_payment_status(db, invoice, attempt, payload, actor=actor)
        if became_paid or invoice.status in {"paid", "overpaid"}:
            return became_paid
    return False


def cancel_invoice(db: Session, invoice: Invoice, *, actor: str = "admin") -> None:
    if invoice.status in {"paid", "overpaid"}:
        raise InvoiceError("No se puede anular una factura pagada.")
    invoice.status = "cancelled"
    invoice.cancelled_at = utcnow()
    audit(db, "invoice.cancelled", invoice_id=invoice.id, detail=invoice.number, actor=actor)
    db.commit()


def reactivate_invoice(db: Session, invoice: Invoice, *, actor: str = "admin") -> None:
    if invoice.status in {"paid", "overpaid"}:
        raise InvoiceError("Una factura pagada no se puede reactivar.")
    invoice.status = "open" if invoice.payram_url else "draft"
    invoice.cancelled_at = None
    audit(db, "invoice.reactivated", invoice_id=invoice.id, detail=invoice.number, actor=actor)
    db.commit()


def mark_email_sent(db: Session, invoice: Invoice, *, actor: str = "admin") -> None:
    invoice.email_sent_at = utcnow()
    invoice.sent_at = invoice.sent_at or utcnow()
    invoice.email_last_error = None
    audit(db, "invoice.email_sent", invoice_id=invoice.id, detail=invoice.customer_email, actor=actor)
    db.commit()


def mark_email_error(db: Session, invoice: Invoice, error: str, *, actor: str = "admin") -> None:
    invoice.email_last_error = error[:2000]
    audit(db, "invoice.email_failed", invoice_id=invoice.id, detail=error[:2000], actor=actor)
    db.commit()


def mark_whatsapp_sent(db: Session, invoice: Invoice, *, actor: str = "admin") -> None:
    invoice.whatsapp_sent_at = utcnow()
    audit(db, "invoice.whatsapp_sent", invoice_id=invoice.id, detail=invoice.customer_phone or "", actor=actor)
    db.commit()


def mark_receipt_sent(db: Session, invoice: Invoice) -> None:
    invoice.receipt_sent_at = utcnow()
    db.commit()
