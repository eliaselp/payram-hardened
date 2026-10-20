"""Configuración central de la aplicación.

Todo se controla por variables de entorno para que el mismo artefacto sirva en
local, staging y producción. No se guardan secretos en el código.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Aplicación -----------------------------------------------------
    environment: Literal["local", "staging", "production"] = "local"
    debug: bool = False
    log_level: str = "INFO"
    base_url: str = "http://localhost:8000"
    allowed_hosts: str = "localhost,127.0.0.1,testserver"
    secret_key: str = "dev-only-change-this-secret-key"
    cookie_secure: bool | None = None
    session_max_age_seconds: int = 8 * 60 * 60
    display_timezone: str = "UTC"
    trust_proxy_headers: bool = True

    # --- Administración -------------------------------------------------
    admin_username: str = "admin"
    # En producción se usa ADMIN_PASSWORD_HASH. ADMIN_PASSWORD queda solo
    # para desarrollo local y se hashea en memoria al arrancar.
    admin_password: str | None = None
    admin_password_hash: str | None = None
    login_max_attempts: int = 6
    login_window_seconds: int = 15 * 60

    # --- Base de datos --------------------------------------------------
    database_url: str = "sqlite:///./data/billing.db"
    auto_create_schema: bool = True

    # --- Facturación ----------------------------------------------------
    invoice_prefix: str = "INV"
    invoice_link_ttl_days: int = 7
    default_tax_rate_bps: int = 0
    company_name: str = "Mi Empresa"
    company_legal_name: str = "Mi Empresa S.A.S."
    company_tax_id: str = "NIT / RUC / RFC"
    company_address: str = "Dirección fiscal"
    company_email: str = "facturacion@example.com"
    company_phone: str = ""
    company_website: str = ""
    invoice_footer_note: str = (
        "Pago en criptomonedas y tarjeta. Las rutas habilitadas liquidan en "
        "USDC en la red Base. Los pagos on-chain son finales."
    )
    payment_methods_note: str = (
        "Paga con USDC en Base, Bitcoin, USDT (Tron), Solana, BNB Chain o "
        "tarjeta. Las rutas habilitadas se liquidan en USDC en Base."
    )

    # --- PayRam ---------------------------------------------------------
    payram_base_url: str = "https://pay.example.com"
    # URL pública usada para construir los links de checkout. Si está vacía,
    # se usa payram_base_url. Sirve cuando PayRam está detrás de un proxy.
    payram_public_url: str = ""
    payram_api_key: str = ""
    payram_webhook_require_signature: bool = True
    payram_timeout_seconds: float = 15.0
    payram_max_webhook_bytes: int = 1_048_576
    # Perfil informativo para la UI/documentación. El sistema no restringe
    # métodos en PayRam; eso se configura en el dashboard de PayRam.
    strict_base_settlement: bool = True

    # --- Correo ---------------------------------------------------------
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = "facturacion@example.com"
    smtp_from_name: str = "Facturación"
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    smtp_timeout_seconds: float = 20.0

    # --- WhatsApp -------------------------------------------------------
    # El botón wa.me siempre está disponible. El envío automático por Cloud
    # API es opcional y requiere una plantilla aprobada para iniciar conversación.
    whatsapp_cloud_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_template_name: str = ""
    whatsapp_template_language: str = "es"
    default_country_code: str = ""

    # --- API interna ----------------------------------------------------
    billing_api_key: str = ""

    @field_validator("base_url", "payram_base_url", "payram_public_url", mode="after")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("secret_key")
    @classmethod
    def _secret_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("SECRET_KEY no puede estar vacío")
        return value

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def secure_cookies(self) -> bool:
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.base_url.startswith("https://")

    @property
    def session_cookie_name(self) -> str:
        # __Host- exige Secure, Path=/ y sin Domain. Solo en producción HTTPS.
        return "__Host-pb_session" if self.is_production else "pb_session"

    @property
    def payram_public_base(self) -> str:
        return (self.payram_public_url or self.payram_base_url).rstrip("/")

    @property
    def allowed_hosts_list(self) -> list[str]:
        return [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_from_email)

    @property
    def whatsapp_cloud_configured(self) -> bool:
        return bool(self.whatsapp_cloud_token and self.whatsapp_phone_number_id)

    @property
    def payram_configured(self) -> bool:
        return bool(self.payram_base_url and self.payram_api_key)

    @property
    def admin_configured(self) -> bool:
        return bool(self.admin_password_hash or self.admin_password)

    def validation_warnings(self) -> list[str]:
        warnings: list[str] = []
        if self.is_production:
            if self.secret_key.startswith("dev-only"):
                warnings.append("SECRET_KEY sigue siendo el valor de desarrollo.")
            if not self.base_url.startswith("https://"):
                warnings.append("BASE_URL debería usar HTTPS en producción.")
            if not self.secure_cookies:
                warnings.append("Las cookies de sesión no están marcadas como Secure.")
            if not self.admin_password_hash:
                warnings.append("ADMIN_PASSWORD_HASH no está configurado.")
            if not self.payram_api_key:
                warnings.append("PAYRAM_API_KEY aún no está configurado.")
            if self.debug:
                warnings.append("DEBUG debe ser false en producción.")
            if not self.payram_webhook_require_signature:
                warnings.append("El webhook debe exigir firma en producción.")
            if "*" in self.allowed_hosts_list:
                warnings.append("ALLOWED_HOSTS no debe contener '*' en producción.")
            if self.admin_password and not self.admin_password_hash:
                warnings.append("ADMIN_PASSWORD en claro no debe usarse en producción.")
            if not self.payram_public_base.startswith("https://"):
                warnings.append("PAYRAM_PUBLIC_URL debe usar HTTPS en producción.")
        if not self.smtp_configured:
            warnings.append("SMTP no configurado: el envío de correo está deshabilitado.")
        if not self.billing_api_key:
            warnings.append("BILLING_API_KEY vacío: la API de integración está deshabilitada.")
        return warnings

    def ensure_production_safety(self) -> None:
        """Falla rápido ante configuraciones peligrosas en producción."""
        if not self.is_production:
            return
        if self.secret_key.startswith("dev-only") or len(self.secret_key) < 32:
            raise RuntimeError("Configura un SECRET_KEY aleatorio de al menos 32 caracteres.")
        parsed = urlparse(self.base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise RuntimeError("BASE_URL debe ser una URL HTTPS válida en producción.")
        if not self.secure_cookies:
            raise RuntimeError("COOKIE_SECURE debe ser true en producción.")
        if self.debug:
            raise RuntimeError("DEBUG debe ser false en producción.")
        if not self.admin_password_hash:
            raise RuntimeError("ADMIN_PASSWORD_HASH es obligatorio en producción.")
        if self.admin_password:
            raise RuntimeError("No uses ADMIN_PASSWORD en claro en producción; usa ADMIN_PASSWORD_HASH.")
        if not self.allowed_hosts_list or "*" in self.allowed_hosts_list:
            raise RuntimeError("ALLOWED_HOSTS debe listar dominios explícitos en producción.")
        if not self.payram_webhook_require_signature:
            raise RuntimeError("PAYRAM_WEBHOOK_REQUIRE_SIGNATURE debe ser true en producción.")
        if not self.payram_public_base.startswith("https://"):
            raise RuntimeError("PAYRAM_PUBLIC_URL debe ser HTTPS en producción.")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
