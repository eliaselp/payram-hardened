"""Cliente HTTP para la API de PayRam.

Solo cubre lo necesario para facturación: crear un link de pago, consultar su
estado y verificar webhooks. Los payouts se operan desde el dashboard o se
pueden añadir después.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import quote, urlencode, urlparse

import httpx

logger = logging.getLogger(__name__)


class PayRamError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, detail: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class PaymentLink:
    reference_id: str
    checkout_url: str
    raw: dict


@dataclass(frozen=True)
class PaymentStatus:
    reference_id: str
    state: str
    amount_usd: Decimal | None
    filled_amount_usd: Decimal | None
    currency: str | None
    raw: dict


def cents_to_usd(cents: int) -> str:
    return f"{Decimal(cents) / Decimal(100):.2f}"


def usd_string_to_cents(value: str | float | Decimal | None) -> int:
    if value is None:
        return 0
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return 0
    return int((amount * 100).quantize(Decimal(1)))


def customer_id_for_email(email: str) -> str:
    normalized = email.strip().lower().encode("utf-8")
    return "CUS-" + hashlib.sha256(normalized).hexdigest()[:16].upper()


def format_expire(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class PayRamClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,
        public_url: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        # Para llamadas API se puede usar la red interna de Docker
        # (http://payram:80), mientras el checkout se construye con la URL
        # pública (https://pay.example.com).
        self.public_url = (public_url or base_url).rstrip("/")
        self.public_host = urlparse(self.public_url).netloc or self.public_url
        self.api_key = api_key
        self.timeout = timeout
        self._transport = transport

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout,
            headers={
                "API-Key": self.api_key,
                "Content-Type": "application/json",
                # Cuando la API se llama por el proxy interno (caddy:8080),
                # PayRam ve el Host público correcto.
                "Host": self.public_host,
            },
            transport=self._transport,
        )

    def create_payment(
        self,
        *,
        customer_email: str,
        customer_id: str,
        amount_cents: int,
        invoice_id: str,
        expire: datetime | None = None,
        currency: str | None = None,
        network: str | None = None,
    ) -> PaymentLink:
        if amount_cents <= 0:
            raise PayRamError("El monto debe ser mayor que cero")
        payload: dict[str, object] = {
            "customerEmail": customer_email,
            "customerID": customer_id,
            "amountInUSD": float(Decimal(amount_cents) / Decimal(100)),
            "invoiceID": invoice_id,
        }
        if expire:
            payload["expire"] = format_expire(expire)
        # Si se envían ambos, PayRam restringe el checkout a esa moneda/red.
        # Para máxima cobertura se omiten y el cliente elige entre las rutas
        # habilitadas en el dashboard.
        if currency:
            payload["currency"] = currency
        if network:
            payload["network"] = network

        with self._client() as client:
            try:
                response = client.post("/api/v1/payment", json=payload)
            except httpx.HTTPError as exc:
                raise PayRamError(f"No se pudo conectar con PayRam: {exc}") from exc

        if response.status_code not in (200, 201):
            detail = response.text[:1000]
            logger.error("PayRam create_payment falló status=%s detail=%s", response.status_code, detail)
            raise PayRamError(
                f"PayRam respondió {response.status_code} al crear el link",
                status_code=response.status_code,
                detail=detail,
            )

        try:
            data = response.json()
            reference_id = str(data["reference_id"])
        except (KeyError, ValueError, TypeError) as exc:
            raise PayRamError("Respuesta inesperada de PayRam al crear el link", detail=response.text[:1000]) from exc

        return PaymentLink(
            reference_id=reference_id,
            checkout_url=self.build_checkout_url(reference_id),
            raw=data,
        )

    def get_payment(self, reference_id: str) -> PaymentStatus:
        with self._client() as client:
            try:
                response = client.get(f"/api/v1/payment/reference/{quote(reference_id, safe='')}")
            except httpx.HTTPError as exc:
                raise PayRamError(f"No se pudo conectar con PayRam: {exc}") from exc

        if response.status_code != 200:
            raise PayRamError(
                f"PayRam respondió {response.status_code} al consultar el pago",
                status_code=response.status_code,
                detail=response.text[:1000],
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise PayRamError("Respuesta no JSON de PayRam", detail=response.text[:1000]) from exc

        state = str(data.get("paymentState") or data.get("status") or "OPEN").upper()
        return PaymentStatus(
            reference_id=str(data.get("referenceID") or data.get("reference_id") or reference_id),
            state=state,
            amount_usd=_to_decimal(data.get("amountInUSD")),
            filled_amount_usd=_to_decimal(data.get("filledAmountInUSD") or data.get("filled_amount_in_usd")),
            currency=data.get("currency"),
            raw=data,
        )

    def get_ticker(self) -> list[dict]:
        """Endpoint público; útil para healthcheck de PayRam."""
        with self._client() as client:
            try:
                response = client.get("/api/v1/ticker")
            except httpx.HTTPError as exc:
                raise PayRamError(f"No se pudo conectar con PayRam: {exc}") from exc
        if response.status_code != 200:
            raise PayRamError(f"PayRam respondió {response.status_code} en /api/v1/ticker")
        try:
            data = response.json()
        except ValueError as exc:
            raise PayRamError("Respuesta no JSON de PayRam") from exc
        return data if isinstance(data, list) else []

    def build_checkout_url(self, reference_id: str) -> str:
        query = urlencode(
            {"reference_id": reference_id, "host": self.public_url},
            quote_via=quote,
        )
        return f"{self.public_url}/payments?{query}"

    @staticmethod
    def verify_webhook(
        raw_body: bytes,
        signature_header: str | None,
        api_key_header: str | None,
        api_key: str,
        require_signature: bool = True,
    ) -> bool:
        if signature_header:
            expected = "sha256=" + hmac.new(
                api_key.encode("utf-8"), raw_body, hashlib.sha256
            ).hexdigest()
            return hmac.compare_digest(signature_header, expected)
        if not require_signature and api_key_header:
            return hmac.compare_digest(api_key_header, api_key)
        return False


def parse_webhook_payload(raw_body: bytes) -> dict:
    try:
        data = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PayRamError("Webhook con JSON inválido") from exc
    if not isinstance(data, dict):
        raise PayRamError("Webhook con payload inesperado")
    return data


def _to_decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
