from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.security import hash_password


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        environment="local",
        debug=True,
        log_level="WARNING",
        base_url="http://testserver",
        allowed_hosts="testserver,localhost,127.0.0.1",
        secret_key="test-secret-key-" + "x" * 40,
        cookie_secure=False,
        admin_username="admin",
        admin_password_hash=hash_password("admin-secret-123"),
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        auto_create_schema=True,
        invoice_prefix="INV",
        invoice_link_ttl_days=7,
        default_tax_rate_bps=0,
        company_name="Test Company",
        company_legal_name="Test Company S.A.S.",
        company_tax_id="TEST-123",
        company_address="Calle 1, Ciudad",
        company_email="billing@test.local",
        company_phone="+1 555 0100",
        company_website="https://test.local",
        payram_base_url="https://payram.test",
        payram_public_url="",
        payram_api_key="test-payram-key",
        payram_webhook_require_signature=True,
        payram_timeout_seconds=5,
        smtp_host="",
        billing_api_key="test-api-key",
        default_country_code="1",
    )


@pytest.fixture()
def app(settings: Settings):
    application = create_app(settings)
    yield application
    application.state.db.dispose()


@pytest.fixture()
def client(app):
    with TestClient(app) as test_client:
        yield test_client
