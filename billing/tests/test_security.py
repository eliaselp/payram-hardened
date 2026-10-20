from __future__ import annotations

import hashlib
import hmac

import pytest

from app.payram import PayRamClient
from app.security import hash_password, verify_password


def test_password_hash_roundtrip():
    encoded = hash_password("una-clave-segura-123")
    assert encoded.startswith("scrypt:")
    assert verify_password("una-clave-segura-123", encoded)
    assert not verify_password("otra-clave", encoded)
    assert not verify_password("", encoded)
    assert not verify_password("x", "formato-invalido")


def test_webhook_signature_valid():
    body = b'{"reference_id":"abc","status":"FILLED"}'
    key = "test-key"
    signature = "sha256=" + hmac.new(key.encode(), body, hashlib.sha256).hexdigest()
    assert PayRamClient.verify_webhook(body, signature, None, key, True)
    assert not PayRamClient.verify_webhook(body, "sha256=deadbeef", None, key, True)
    assert not PayRamClient.verify_webhook(body, None, None, key, True)


def test_webhook_signature_legacy_api_key_only_when_allowed():
    body = b"{}"
    key = "test-key"
    assert PayRamClient.verify_webhook(body, None, key, key, require_signature=False)
    assert not PayRamClient.verify_webhook(body, None, key, key, require_signature=True)
    assert not PayRamClient.verify_webhook(body, None, "wrong", key, require_signature=False)


def test_invoice_rejects_email_header_injection(app):
    from app.invoices import InvoiceError, create_invoice

    db = app.state.db.session()
    try:
        with pytest.raises(InvoiceError):
            create_invoice(
                db,
                customer_name="ACME",
                customer_email="a@b.com\r\nBcc: evil@example.com",
                customer_phone=None,
                description="",
                notes="",
                items=[{"description": "Servicio", "quantity": 1, "unit_price_cents": 1000}],
                tax_rate_bps=0,
            )
    finally:
        db.close()


def test_public_invoice_escapes_html(app, client):
    from tests.helpers import add_attempt, create_invoice_record

    db = app.state.db.session()
    try:
        invoice = create_invoice_record(db, total_cents=1000)
        invoice.customer_name = '<script>alert("xss")</script>'
        db.add(invoice)
        db.commit()
        add_attempt(db, invoice, reference="ref-xss")
        token = invoice.public_token
    finally:
        db.close()

    page = client.get(f"/i/{token}")
    assert page.status_code == 200
    assert "&lt;script&gt;" in page.text
    assert '<script>alert("xss")</script>' not in page.text


def test_health_does_not_leak_error_details(app, client):
    response = client.get("/healthz")
    assert response.status_code == 200
    data = response.json()
    assert set(data) <= {"status", "version", "database", "payram"}
    assert "payram_error" not in data
    assert "database_error" not in data


def test_production_safety_requires_hash_and_signature(settings):
    from app.config import Settings

    base = {
        "_env_file": None,
        "environment": "production",
        "base_url": "https://billing.example.com",
        "allowed_hosts": "billing.example.com",
        "secret_key": "x" * 64,
        "cookie_secure": True,
        "admin_username": "admin",
        "admin_password_hash": settings.admin_password_hash,
        "payram_base_url": "http://caddy:8080",
        "payram_public_url": "https://pay.example.com",
        "payram_api_key": "key",
        "payram_webhook_require_signature": True,
        "debug": False,
    }
    Settings(**base).ensure_production_safety()

    for override in (
        {"admin_password_hash": None},
        {"payram_webhook_require_signature": False},
        {"allowed_hosts": "*"},
        {"payram_public_url": "http://pay.example.com"},
    ):
        candidate = Settings(**{**base, **override})
        with pytest.raises(RuntimeError):
            candidate.ensure_production_safety()
