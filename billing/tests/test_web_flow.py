from __future__ import annotations

import re

import httpx
import respx
from sqlalchemy import select

from app.models import Invoice, PaymentAttempt
from tests.helpers import add_attempt, create_invoice_record, login


def test_public_invoice_and_pay_redirect(app, client):
    db = app.state.db.session()
    try:
        invoice = create_invoice_record(db, total_cents=5000)
        add_attempt(db, invoice, reference="ref-public-1")
        token = invoice.public_token
        number = invoice.number
    finally:
        db.close()

    page = client.get(f"/i/{token}")
    assert page.status_code == 200
    assert number in page.text
    assert "Pagar ahora" in page.text
    assert page.headers["x-robots-tag"].startswith("noindex")

    pay = client.get(f"/i/{token}/pay", follow_redirects=False)
    assert pay.status_code == 302
    assert pay.headers["location"].startswith("https://payram.test/payments?")

    pdf = client.get(f"/i/{token}/pdf")
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF")

    status = client.get(f"/i/{token}/status")
    assert status.status_code == 200
    assert status.json()["status"] == "open"
    assert status.json()["remaining_cents"] == 5000


def test_paid_invoice_cannot_redirect_to_checkout(app, client):
    db = app.state.db.session()
    try:
        invoice = create_invoice_record(db, total_cents=5000)
        add_attempt(db, invoice, reference="ref-public-2")
        invoice.status = "paid"
        invoice.paid_amount_cents = 5000
        db.add(invoice)
        db.commit()
        token = invoice.public_token
    finally:
        db.close()

    pay = client.get(f"/i/{token}/pay", follow_redirects=False)
    assert pay.status_code == 302
    assert pay.headers["location"] == f"/i/{token}"


def test_admin_login_dashboard_and_create_invoice(app, client):
    login(client)
    dashboard = client.get("/admin")
    assert dashboard.status_code == 200
    assert "Facturas" in dashboard.text

    settings_page = client.get("/admin/settings")
    assert settings_page.status_code == 200
    assert "PayRam" in settings_page.text

    page = client.get("/admin/invoices/new")
    match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert match
    csrf = match.group(1)

    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://payram.test/api/v1/payment").mock(
            return_value=httpx.Response(
                200,
                json={
                    "host": "https://payram.test",
                    "reference_id": "ref-admin-1",
                    "url": "https://payram.test/payments?reference_id=ref-admin-1",
                },
            )
        )
        response = client.post(
            "/admin/invoices",
            data={
                "csrf_token": csrf,
                "customer_name": "Cliente Admin",
                "customer_email": "admin-client@example.com",
                "customer_phone": "+15550123",
                "description": "Servicio admin",
                "issue_date": "2026-10-19",
                "due_date": "2026-11-03",
                "tax_rate": "16",
                "item_description_0": "Consultoría",
                "item_quantity_0": "2",
                "item_price_0": "50.00",
                "item_description_1": "",
                "item_quantity_1": "1",
                "item_price_1": "",
                "item_description_2": "",
                "item_quantity_2": "1",
                "item_price_2": "",
                "notes": "Gracias",
            },
            follow_redirects=False,
        )
    assert response.status_code == 303, response.text[:1000]
    assert response.headers["location"].startswith("/admin/invoices/")

    db = app.state.db.session()
    try:
        invoice = db.scalar(
            select(Invoice).where(Invoice.customer_email == "admin-client@example.com")
        )
        assert invoice is not None
        assert invoice.total_cents == 11600
        assert invoice.status == "open"
        attempt = db.scalar(
            select(PaymentAttempt).where(PaymentAttempt.invoice_id == invoice.id)
        )
        assert attempt is not None
        assert attempt.reference_id == "ref-admin-1"
        invoice_id = invoice.id
    finally:
        db.close()

    detail = client.get(f"/admin/invoices/{invoice_id}")
    assert detail.status_code == 200
    assert "ref-admin-1" in detail.text

    pdf = client.get(f"/admin/invoices/{invoice_id}/pdf")
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF")

    logout = client.post(
        "/logout",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert logout.status_code == 303
    assert client.get("/admin", follow_redirects=False).status_code == 303


def test_api_create_invoice_and_idempotency(app, client):
    payload = {
        "customer_name": "Cliente API",
        "customer_email": "api-client@example.com",
        "customer_phone": "+15550000",
        "description": "Integración",
        "items": [
            {"description": "Servicio API", "quantity": 1, "unit_price": 99.50}
        ],
        "tax_rate": 0,
        "external_id": "ext-001",
        "send_email": False,
    }
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post("https://payram.test/api/v1/payment").mock(
            return_value=httpx.Response(
                200,
                json={
                    "host": "https://payram.test",
                    "reference_id": "ref-api-1",
                    "url": "https://payram.test/payments?reference_id=ref-api-1",
                },
            )
        )
        response = client.post(
            "/api/v1/invoices",
            headers={"X-API-Key": "test-api-key"},
            json=payload,
        )
        assert response.status_code == 201, response.text
        data = response.json()
        assert data["total_usd"] == "99.50"
        assert data["payment"]["reference_id"] == "ref-api-1"
        assert data["payment"]["checkout_url"].startswith("https://payram.test/payments?")
        assert data["urls"]["public"].startswith("http://testserver/i/")

        again = client.post(
            "/api/v1/invoices",
            headers={"X-API-Key": "test-api-key"},
            json=payload,
        )
        assert again.status_code == 201
        assert again.json()["number"] == data["number"]
        assert route.call_count == 1

    unauthorized = client.post("/api/v1/invoices", json=payload)
    assert unauthorized.status_code == 401

    fetched = client.get(
        f"/api/v1/invoices/{data['number']}",
        headers={"X-API-Key": "test-api-key"},
    )
    assert fetched.status_code == 200
    assert fetched.json()["number"] == data["number"]


def test_public_payment_error_is_generic(app, client):
    from tests.helpers import create_invoice_record

    app.state.settings.payram_api_key = ""
    db = app.state.db.session()
    try:
        invoice = create_invoice_record(db, total_cents=1000)
        token = invoice.public_token
    finally:
        db.close()

    response = client.get(f"/i/{token}/pay")
    assert response.status_code == 502
    assert "No se pudo iniciar el pago" in response.text
    assert "PAYRAM_API_KEY" not in response.text
    assert "payram.test" not in response.text
    assert "No se pudo conectar" not in response.text
