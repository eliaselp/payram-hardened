from __future__ import annotations

import json
from decimal import Decimal

import httpx
import respx

from app.invoices import create_invoice, ensure_payment_link, reconcile_invoice
from app.payram import PayRamClient
from app.pdf import build_invoice_pdf


def test_invoice_totals_and_number(app):
    db = app.state.db.session()
    try:
        invoice = create_invoice(
            db,
            customer_name="ACME",
            customer_email="acme@example.com",
            customer_phone=None,
            description="Proyecto",
            notes="",
            items=[
                {"description": "Diseño", "quantity": 2, "unit_price_cents": 5000},
                {"description": "Hosting", "quantity": 1, "unit_price_cents": 2500},
            ],
            tax_rate_bps=1600,
            prefix="INV",
        )
        assert invoice.total_cents == 14500
        assert invoice.subtotal_cents == 12500
        assert invoice.tax_cents == 2000
        assert invoice.number.startswith("INV-")
        assert invoice.number.endswith("00001")
        assert invoice.public_token
        assert invoice.status == "draft"
        assert len(invoice.items) == 2
    finally:
        db.close()


def test_payram_create_payment_payload(app):
    db = app.state.db.session()
    try:
        invoice = create_invoice(
            db,
            customer_name="ACME",
            customer_email="acme@example.com",
            customer_phone=None,
            description="",
            notes="",
            items=[{"description": "Servicio", "quantity": 1, "unit_price_cents": 12345}],
            tax_rate_bps=0,
        )
        with respx.mock(assert_all_called=True) as mock:
            route = mock.post("https://payram.test/api/v1/payment").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "host": "https://payram.test",
                        "reference_id": "ref-123",
                        "url": "https://payram.test/payments?reference_id=ref-123",
                    },
                )
            )
            attempt = ensure_payment_link(db, invoice, app.state.settings, app.state.payram)
            request = route.calls[0].request
            body = json.loads(request.content)
            assert body["customerEmail"] == "acme@example.com"
            assert body["customerID"].startswith("CUS-")
            assert body["amountInUSD"] == 123.45
            assert body["invoiceID"] == invoice.number
            assert body["expire"].endswith("Z")
            assert attempt.reference_id == "ref-123"
            assert attempt.url.startswith("https://payram.test/payments?")
            assert "reference_id=ref-123" in attempt.url
            assert invoice.status == "open"
            assert invoice.payram_reference_id == "ref-123"
    finally:
        db.close()


def test_partial_payment_new_link_uses_remaining_amount(app):
    db = app.state.db.session()
    try:
        invoice = create_invoice(
            db,
            customer_name="ACME",
            customer_email="acme@example.com",
            customer_phone=None,
            description="",
            notes="",
            items=[{"description": "Servicio", "quantity": 1, "unit_price_cents": 10000}],
            tax_rate_bps=0,
        )
        from tests.helpers import add_attempt

        add_attempt(db, invoice, reference="ref-partial", state="PARTIALLY_FILLED", filled_cents=4000)
        invoice.paid_amount_cents = 4000
        invoice.status = "partially_paid"
        db.add(invoice)
        db.commit()

        with respx.mock(assert_all_called=True) as mock:
            route = mock.post("https://payram.test/api/v1/payment").mock(
                return_value=httpx.Response(
                    200,
                    json={"reference_id": "ref-remaining", "url": "https://payram.test/payments?reference_id=ref-remaining"},
                )
            )
            from app.invoices import ensure_payment_link

            new_attempt = ensure_payment_link(
                db, invoice, app.state.settings, app.state.payram, force_new=True
            )
            body = json.loads(route.calls[0].request.content)
            assert body["amountInUSD"] == 60.0
            assert new_attempt.requested_amount_cents == 6000
            assert new_attempt.reference_id == "ref-remaining"
            assert invoice.status == "partially_paid"
    finally:
        db.close()


def test_payram_client_applies_timeout_and_parses_status():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["API-Key"] == "k"
        return httpx.Response(
            200,
            json={
                "referenceID": "ref-1",
                "paymentState": "FILLED",
                "amountInUSD": "10.00",
                "filledAmountInUSD": "10.00",
                "currency": "USDC",
            },
        )

    client = PayRamClient("https://payram.test", "k", transport=httpx.MockTransport(handler))
    status = client.get_payment("ref-1")
    assert status.state == "FILLED"
    assert status.filled_amount_usd == Decimal("10.00")


def test_reconcile_marks_paid(app):
    db = app.state.db.session()
    try:
        invoice = create_invoice(
            db,
            customer_name="ACME",
            customer_email="acme@example.com",
            customer_phone=None,
            description="",
            notes="",
            items=[{"description": "Servicio", "quantity": 1, "unit_price_cents": 10000}],
            tax_rate_bps=0,
        )
        from tests.helpers import add_attempt

        add_attempt(db, invoice, reference="ref-reconcile")
        with respx.mock(assert_all_called=True) as mock:
            mock.get("https://payram.test/api/v1/payment/reference/ref-reconcile").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "referenceID": "ref-reconcile",
                        "paymentState": "FILLED",
                        "amountInUSD": "100.00",
                        "filledAmountInUSD": "100.00",
                        "currency": "USDC",
                        "paymentInfo": [
                            {"transaction_hash": "0xdeadbeef", "block_number": 1}
                        ],
                    },
                )
            )
            became_paid = reconcile_invoice(db, invoice, app.state.payram)
        assert became_paid is True
        assert invoice.status == "paid"
        assert invoice.paid_amount_cents == 10000
        assert invoice.paid_tx_hash == "0xdeadbeef"
        assert invoice.paid_at is not None
    finally:
        db.close()


def test_pdf_generation(app):
    db = app.state.db.session()
    try:
        invoice = create_invoice(
            db,
            customer_name="ACME",
            customer_email="acme@example.com",
            customer_phone="+15550100",
            description="Proyecto",
            notes="Gracias por su compra.",
            items=[{"description": "Servicio", "quantity": 1, "unit_price_cents": 10000}],
            tax_rate_bps=1600,
        )
        pdf = build_invoice_pdf(invoice, app.state.settings)
        assert pdf.startswith(b"%PDF")
        assert len(pdf) > 1000
    finally:
        db.close()
