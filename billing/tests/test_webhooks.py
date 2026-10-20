from __future__ import annotations

from sqlalchemy import select

from app.models import Invoice, PaymentAttempt
from tests.helpers import (
    add_attempt,
    create_invoice_record,
    post_webhook,
    webhook_payload,
)


def _load(app, invoice_id: int) -> Invoice:
    db = app.state.db.session()
    try:
        invoice = db.get(Invoice, invoice_id)
        assert invoice is not None
        # Fuerza la carga de relaciones antes de cerrar la sesión.
        _ = invoice.items
        _ = invoice.payments
        db.expunge(invoice)
        return invoice
    finally:
        db.close()


def _load_attempt(app, reference: str) -> PaymentAttempt:
    db = app.state.db.session()
    try:
        attempt = db.scalar(
            select(PaymentAttempt).where(PaymentAttempt.reference_id == reference)
        )
        assert attempt is not None
        db.expunge(attempt)
        return attempt
    finally:
        db.close()


def test_webhook_filled_is_idempotent(app, client):
    db = app.state.db.session()
    try:
        invoice = create_invoice_record(db, total_cents=10000)
        add_attempt(db, invoice, reference="ref-hook-1")
        invoice_id = invoice.id
    finally:
        db.close()

    payload = webhook_payload(
        _load(app, invoice_id), "ref-hook-1", status="FILLED", filled_usd="100.00", tx_hash="0xfeed"
    )
    response = post_webhook(client, payload)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "paid"

    loaded = _load(app, invoice_id)
    assert loaded.status == "paid"
    assert loaded.paid_amount_cents == 10000
    assert loaded.paid_tx_hash == "0xfeed"
    assert loaded.paid_at is not None

    duplicate = post_webhook(client, payload)
    assert duplicate.status_code == 200
    assert duplicate.json().get("duplicate") is True

    # Un OPEN tardío no puede degradar un pago confirmado.
    regression = post_webhook(
        client,
        webhook_payload(_load(app, invoice_id), "ref-hook-1", status="OPEN", filled_usd="0.00"),
    )
    assert regression.status_code == 200
    loaded = _load(app, invoice_id)
    assert loaded.status == "paid"
    assert loaded.paid_amount_cents == 10000
    attempt = _load_attempt(app, "ref-hook-1")
    assert attempt.state == "FILLED"


def test_webhook_partial_then_full(app, client):
    db = app.state.db.session()
    try:
        invoice = create_invoice_record(db, total_cents=10000)
        add_attempt(db, invoice, reference="ref-hook-2")
        invoice_id = invoice.id
    finally:
        db.close()

    partial = post_webhook(
        client,
        webhook_payload(_load(app, invoice_id), "ref-hook-2", status="PARTIALLY_FILLED", filled_usd="40.00"),
    )
    assert partial.status_code == 200
    loaded = _load(app, invoice_id)
    assert loaded.status == "partially_paid"
    assert loaded.paid_amount_cents == 4000

    # Un OPEN tardío no debe borrar el pago parcial ya detectado.
    late_open = post_webhook(
        client,
        webhook_payload(_load(app, invoice_id), "ref-hook-2", status="OPEN", filled_usd="0.00"),
    )
    assert late_open.status_code == 200
    loaded = _load(app, invoice_id)
    assert loaded.status == "partially_paid"
    assert loaded.paid_amount_cents == 4000

    full = post_webhook(
        client,
        webhook_payload(_load(app, invoice_id), "ref-hook-2", status="FILLED", filled_usd="100.00"),
    )
    assert full.status_code == 200
    loaded = _load(app, invoice_id)
    assert loaded.status == "paid"
    assert loaded.paid_amount_cents == 10000


def test_webhook_invalid_signature(app, client):
    db = app.state.db.session()
    try:
        invoice = create_invoice_record(db, total_cents=10000)
        add_attempt(db, invoice, reference="ref-hook-3")
        payload = webhook_payload(invoice, "ref-hook-3")
        invoice_id = invoice.id
    finally:
        db.close()

    response = client.post(
        "/webhooks/payram",
        json=payload,
        headers={"X-Payram-Signature": "sha256=deadbeef"},
    )
    assert response.status_code == 401
    assert _load(app, invoice_id).status == "open"


def test_webhook_unknown_reference_is_acknowledged(app, client):
    payload = {
        "reference_id": "does-not-exist",
        "status": "FILLED",
        "filled_amount_in_usd": "10.00",
    }
    response = post_webhook(client, payload)
    assert response.status_code == 200
    assert response.json().get("ignored") is True


def test_webhook_rejects_invoice_id_mismatch(app, client):
    db = app.state.db.session()
    try:
        invoice = create_invoice_record(db, total_cents=10000)
        add_attempt(db, invoice, reference="ref-mismatch")
        invoice_id = invoice.id
    finally:
        db.close()

    payload = webhook_payload(_load(app, invoice_id), "ref-mismatch")
    payload["invoice_id"] = "INV-OTRA"
    response = post_webhook(client, payload)
    assert response.status_code == 400
    loaded = _load(app, invoice_id)
    assert loaded.status == "open"
    assert loaded.paid_amount_cents == 0
