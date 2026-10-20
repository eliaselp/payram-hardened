"""Envío de correo transaccional por SMTP."""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import Settings
from app.invoices import public_invoice_url
from app.models import Invoice

logger = logging.getLogger(__name__)

_env = Environment(
    loader=FileSystemLoader(str(Path(__file__).resolve().parent / "templates")),
    autoescape=select_autoescape(["html", "xml"]),
)


class EmailError(RuntimeError):
    pass


def _from_header(settings: Settings) -> str:
    return formataddr((settings.smtp_from_name, settings.smtp_from_email))


def _send(message: EmailMessage, settings: Settings) -> None:
    if not settings.smtp_configured:
        raise EmailError("SMTP no está configurado. Configura SMTP_HOST y SMTP_FROM_EMAIL.")
    try:
        if settings.smtp_use_ssl:
            with smtplib.SMTP_SSL(
                settings.smtp_host,
                settings.smtp_port,
                timeout=settings.smtp_timeout_seconds,
                context=ssl.create_default_context(),
            ) as smtp:
                if settings.smtp_username:
                    smtp.login(settings.smtp_username, settings.smtp_password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(
                settings.smtp_host,
                settings.smtp_port,
                timeout=settings.smtp_timeout_seconds,
            ) as smtp:
                smtp.ehlo()
                if settings.smtp_use_tls:
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.ehlo()
                if settings.smtp_username:
                    smtp.login(settings.smtp_username, settings.smtp_password)
                smtp.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        logger.exception("Fallo enviando correo a %s", message["To"])
        raise EmailError(f"No se pudo enviar el correo: {exc}") from exc


def send_invoice_email(invoice: Invoice, settings: Settings, pdf_bytes: bytes) -> None:
    pay_url = public_invoice_url(invoice, settings)
    html = _env.get_template("email/invoice.html").render(
        invoice=invoice,
        settings=settings,
        pay_url=pay_url,
    )
    text = _env.get_template("email/invoice.txt").render(
        invoice=invoice,
        settings=settings,
        pay_url=pay_url,
    )
    message = EmailMessage()
    message["Subject"] = f"Factura {invoice.number} — {settings.company_name}"
    message["From"] = _from_header(settings)
    message["To"] = invoice.customer_email
    if settings.company_email:
        message["Reply-To"] = settings.company_email
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    message.add_attachment(
        pdf_bytes,
        maintype="application",
        subtype="pdf",
        filename=f"{invoice.number}.pdf",
    )
    _send(message, settings)


def send_receipt_email(invoice: Invoice, settings: Settings, pdf_bytes: bytes | None = None) -> None:
    pay_url = public_invoice_url(invoice, settings)
    html = _env.get_template("email/receipt.html").render(
        invoice=invoice,
        settings=settings,
        pay_url=pay_url,
    )
    text = _env.get_template("email/receipt.txt").render(
        invoice=invoice,
        settings=settings,
        pay_url=pay_url,
    )
    message = EmailMessage()
    message["Subject"] = f"Pago recibido — Factura {invoice.number}"
    message["From"] = _from_header(settings)
    message["To"] = invoice.customer_email
    if settings.company_email:
        message["Reply-To"] = settings.company_email
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    if pdf_bytes:
        message.add_attachment(
            pdf_bytes,
            maintype="application",
            subtype="pdf",
            filename=f"{invoice.number}-pagada.pdf",
        )
    _send(message, settings)
