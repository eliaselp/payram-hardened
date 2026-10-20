"""Enlaces de WhatsApp y envío opcional por Cloud API."""

from __future__ import annotations

import logging
import re
from urllib.parse import quote

import httpx

from app.config import Settings
from app.invoices import public_invoice_url
from app.models import Invoice

logger = logging.getLogger(__name__)


class WhatsAppError(RuntimeError):
    pass


def normalize_phone(phone: str | None, default_country_code: str = "") -> str:
    if not phone:
        return ""
    raw = phone.strip()
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return ""
    if raw.startswith("+"):
        return digits
    if digits.startswith("00"):
        return digits[2:]
    country = re.sub(r"\D", "", default_country_code)
    if country and not digits.startswith(country):
        return country + digits
    return digits


def message_text(invoice: Invoice, settings: Settings) -> str:
    url = public_invoice_url(invoice, settings)
    amount = f"USD {invoice.total_cents / 100:,.2f}"
    return (
        f"Hola {invoice.customer_name}, te envío la factura {invoice.number} "
        f"por {amount}. Puedes pagarla aquí: {url}\n\n"
        f"{settings.payment_methods_note}"
    )


def click_to_chat_url(invoice: Invoice, settings: Settings) -> str:
    phone = normalize_phone(invoice.customer_phone, settings.default_country_code)
    text = quote(message_text(invoice, settings), safe="")
    if phone:
        return f"https://wa.me/{phone}?text={text}"
    return f"https://wa.me/?text={text}"


def send_cloud_text(invoice: Invoice, settings: Settings) -> None:
    """Envía texto por WhatsApp Cloud API.

    Solo funciona dentro de la ventana de atención de 24 h o con una plantilla
    aprobada. Para mensajes iniciados por el negocio, configura una plantilla
    y usa el flujo de plantillas de Meta. El botón wa.me sigue disponible.
    """
    if not settings.whatsapp_cloud_configured:
        raise WhatsAppError("WhatsApp Cloud API no está configurado.")
    phone = normalize_phone(invoice.customer_phone, settings.default_country_code)
    if not phone:
        raise WhatsAppError("La factura no tiene un teléfono válido.")
    url = f"https://graph.facebook.com/v21.0/{settings.whatsapp_phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": phone,
        "type": "text",
        "text": {"preview_url": True, "body": message_text(invoice, settings)},
    }
    try:
        response = httpx.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {settings.whatsapp_cloud_token}"},
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        raise WhatsAppError(f"No se pudo conectar con WhatsApp: {exc}") from exc
    if response.status_code >= 400:
        logger.error("WhatsApp Cloud API error %s: %s", response.status_code, response.text[:800])
        raise WhatsAppError(
            "WhatsApp rechazó el mensaje. Si es el primer contacto, necesitas "
            "una plantilla aprobada por Meta."
        )
