from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.invoices import create_invoice
from app.models import Invoice, PaymentAttempt


def create_invoice_record(
    db: Session,
    *,
    customer_email: str = "cliente@example.com",
    total_cents: int = 10_000,
    tax_rate_bps: int = 0,
) -> Invoice:
    # Construye una factura de una línea cuyo total sea exactamente total_cents
    # (sin impuesto) o con el impuesto indicado.
    if tax_rate_bps:
        base = int(total_cents * 10_000 / (10_000 + tax_rate_bps))
        unit = base
    else:
        unit = total_cents
    invoice = create_invoice(
        db,
        customer_name="Cliente Prueba",
        customer_email=customer_email,
        customer_phone="+15550100",
        description="Servicio de prueba",
        notes="Notas de prueba",
        items=[
            {
                "description": "Servicio",
                "quantity": 1,
                "unit_price_cents": unit,
            }
        ],
        tax_rate_bps=tax_rate_bps,
        prefix="INV",
        actor="test",
    )
    return invoice


def add_attempt(
    db: Session,
    invoice: Invoice,
    *,
    reference: str = "ref-test-1",
    state: str = "OPEN",
    filled_cents: int = 0,
    expires_in_days: int | None = 7,
) -> PaymentAttempt:
    expires = (
        datetime.now(UTC) + timedelta(days=expires_in_days)
        if expires_in_days is not None
        else None
    )
    attempt = PaymentAttempt(
        invoice_id=invoice.id,
        reference_id=reference,
        url=f"https://payram.test/payments?reference_id={reference}&host=https%3A%2F%2Fpayram.test",
        state=state,
        requested_amount_cents=invoice.total_cents - invoice.paid_amount_cents,
        filled_amount_cents=filled_cents,
        expires_at=expires,
    )
    invoice.payram_reference_id = reference
    invoice.payram_url = attempt.url
    invoice.last_payment_state = state
    if invoice.status == "draft":
        invoice.status = "open"
    db.add(attempt)
    db.add(invoice)
    db.commit()
    db.refresh(attempt)
    db.refresh(invoice)
    return attempt


def webhook_payload(
    invoice: Invoice,
    reference: str,
    *,
    status: str = "FILLED",
    filled_usd: str | None = None,
    tx_hash: str = "0xabc123",
) -> dict:
    filled = filled_usd if filled_usd is not None else f"{invoice.total_cents / 100:.2f}"
    return {
        "customer_id": "CUS-TEST",
        "invoice_id": invoice.number,
        "reference_id": reference,
        "status": status,
        "amount": filled,
        "currency": "USDC",
        "filled_amount": filled,
        "filled_amount_in_usd": filled,
        "sponsored_amount": "0",
        "sponsored_amount_in_usd": "0",
        "timestamp": 1750340282,
        "payment_info": [
            {
                "source_address": "0x1111111111111111111111111111111111111111",
                "transaction_hash": tx_hash,
                "destination_address": "0x2222222222222222222222222222222222222222",
                "block_number": 123456,
            }
        ],
        "confirmation_current": 12,
        "confirmation_required": 12,
    }


def post_webhook(client: TestClient, payload: dict, key: str = "test-payram-key"):
    raw = json.dumps(payload, separators=(",", ":")).encode()
    signature = "sha256=" + hmac.new(key.encode(), raw, hashlib.sha256).hexdigest()
    return client.post(
        "/webhooks/payram",
        content=raw,
        headers={"X-Payram-Signature": signature, "Content-Type": "application/json"},
    )


def login(client: TestClient, password: str = "admin-secret-123") -> None:
    response = client.get("/login")
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match, response.text[:500]
    response = client.post(
        "/login",
        data={
            "username": "admin",
            "password": password,
            "csrf_token": match.group(1),
            "next": "/admin",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text[:500]
