"""Generación de la factura en PDF con QR de pago."""

from __future__ import annotations

import io
from datetime import UTC, date, datetime
from decimal import Decimal
from xml.sax.saxutils import escape  # nosec B406

import qrcode
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.config import Settings
from app.invoices import public_invoice_url
from app.models import Invoice

NAVY = colors.HexColor("#0F172A")
SLATE = colors.HexColor("#475569")
MUTED = colors.HexColor("#64748B")
LINE = colors.HexColor("#E2E8F0")
EMERALD_SOFT = colors.HexColor("#ECFDF5")


def money(cents: int) -> str:
    return f"USD {Decimal(cents) / Decimal(100):,.2f}"


def _date(value: date | datetime | None) -> str:
    if not value:
        return "—"
    if isinstance(value, datetime):
        value = value.astimezone(UTC).date()
    return value.strftime("%d/%m/%Y")


def _quantity(value: Decimal) -> str:
    text = f"{Decimal(value):.3f}".rstrip("0").rstrip(".")
    return text or "0"


def _qr_image(data: str, size: int = 34 * mm) -> Image:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=1,
    )
    qr.add_data(data)
    qr.make(fit=True)
    image = qr.make_image(fill_color="#0F172A", back_color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return Image(buffer, width=size, height=size)


def build_invoice_pdf(invoice: Invoice, settings: Settings) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        title=f"Factura {invoice.number}",
        author=settings.company_legal_name,
        subject=f"Factura {invoice.number}",
    )
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="Small",
            parent=styles["Normal"],
            fontSize=8,
            leading=11,
            textColor=MUTED,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Body",
            parent=styles["Normal"],
            fontSize=9.5,
            leading=13,
            textColor=NAVY,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Right",
            parent=styles["Body"],
            alignment=TA_RIGHT,
        )
    )
    styles.add(
        ParagraphStyle(
            name="H1",
            parent=styles["Normal"],
            fontSize=20,
            leading=24,
            textColor=NAVY,
            spaceAfter=2,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Label",
            parent=styles["Small"],
            textColor=MUTED,
            spaceAfter=1,
        )
    )

    story = []

    company_block = [
        Paragraph(f"<b>{escape(settings.company_legal_name)}</b>", styles["Body"]),
        Paragraph(escape(settings.company_address), styles["Small"]),
        Paragraph(f"ID fiscal: {escape(settings.company_tax_id)}", styles["Small"]),
        Paragraph(escape(settings.company_email), styles["Small"]),
    ]
    if settings.company_phone:
        company_block.append(Paragraph(escape(settings.company_phone), styles["Small"]))
    if settings.company_website:
        company_block.append(Paragraph(escape(settings.company_website), styles["Small"]))

    invoice_block = [
        Paragraph("FACTURA", styles["H1"]),
        Paragraph(f"<b>{escape(invoice.number)}</b>", styles["Right"]),
        Paragraph(f"Emitida: {_date(invoice.issue_date)}", styles["Right"]),
        Paragraph(f"Vence: {_date(invoice.due_date)}", styles["Right"]),
    ]
    status_label = {
        "paid": "PAGADA",
        "overpaid": "PAGADA (EXCESO)",
        "partially_paid": "PAGO PARCIAL",
        "cancelled": "ANULADA",
        "expired": "EXPIRADA",
        "open": "PENDIENTE",
        "draft": "BORRADOR",
    }.get(invoice.status, invoice.status.upper())
    status_hex = "#059669" if invoice.status in {"paid", "overpaid"} else "#B45309"
    invoice_block.append(Paragraph(f'<font color="{status_hex}"><b>{status_label}</b></font>', styles["Right"]))

    header = Table(
        [[company_block, invoice_block]],
        colWidths=[100 * mm, 70 * mm],
    )
    header.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    story.append(header)
    story.append(Spacer(1, 8 * mm))
    story.append(Table([[""]], colWidths=[174 * mm], rowHeights=[0.6], style=TableStyle([("LINEABOVE", (0, 0), (-1, -1), 0.8, LINE)])))
    story.append(Spacer(1, 6 * mm))

    customer_block = [
        Paragraph("FACTURAR A", styles["Label"]),
        Paragraph(f"<b>{escape(invoice.customer_name)}</b>", styles["Body"]),
        Paragraph(escape(invoice.customer_email), styles["Small"]),
    ]
    if invoice.customer_phone:
        customer_block.append(Paragraph(escape(invoice.customer_phone), styles["Small"]))

    meta_block = [
        Paragraph("DETALLE DE PAGO", styles["Label"]),
        Paragraph("USDC en Base + rutas habilitadas", styles["Body"]),
        Paragraph("Los pagos on-chain son finales.", styles["Small"]),
        Paragraph(f"Ref. PayRam: {escape(invoice.payram_reference_id or '—')}", styles["Small"]),
    ]
    story.append(
        Table(
            [[customer_block, meta_block]],
            colWidths=[100 * mm, 70 * mm],
            style=TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ]
            ),
        )
    )
    story.append(Spacer(1, 8 * mm))

    rows = [
        [
            Paragraph("<b>Descripción</b>", styles["Small"]),
            Paragraph("<b>Cant.</b>", styles["Small"]),
            Paragraph("<b>Precio</b>", styles["Right"]),
            Paragraph("<b>Total</b>", styles["Right"]),
        ]
    ]
    for item in invoice.items:
        rows.append(
            [
                Paragraph(escape(item.description), styles["Body"]),
                Paragraph(_quantity(item.quantity), styles["Body"]),
                Paragraph(money(item.unit_price_cents), styles["Right"]),
                Paragraph(money(item.line_total_cents), styles["Right"]),
            ]
        )
    items_table = Table(rows, colWidths=[94 * mm, 18 * mm, 30 * mm, 32 * mm], repeatRows=1)
    items_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F8FAFC")),
                ("TEXTCOLOR", (0, 0), (-1, 0), SLATE),
                ("LINEBELOW", (0, 0), (-1, 0), 0.8, LINE),
                ("LINEBELOW", (0, 1), (-1, -1), 0.4, colors.HexColor("#F1F5F9")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(items_table)
    story.append(Spacer(1, 4 * mm))

    totals_rows = [
        ["Subtotal", money(invoice.subtotal_cents)],
        [f"Impuesto ({invoice.tax_rate_bps / 100:.2f}%)", money(invoice.tax_cents)],
        ["Total", money(invoice.total_cents)],
    ]
    totals = Table(totals_rows, colWidths=[40 * mm, 36 * mm], hAlign="RIGHT")
    totals.setStyle(
        TableStyle(
            [
                ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                ("TEXTCOLOR", (0, 0), (-1, -1), SLATE),
                ("FONTNAME", (0, 2), (-1, 2), "Helvetica-Bold"),
                ("TEXTCOLOR", (0, 2), (-1, 2), NAVY),
                ("LINEABOVE", (0, 2), (-1, 2), 0.8, LINE),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(totals)
    story.append(Spacer(1, 9 * mm))

    pay_url = public_invoice_url(invoice, settings)
    qr = _qr_image(pay_url)
    pay_text = [
        Paragraph("<b>Pagar esta factura</b>", styles["Body"]),
        Paragraph(
            "Abre el link o escanea el QR. Puedes pagar con USDC en Base, "
            "Bitcoin, USDT (Tron), Solana, BNB Chain o tarjeta, según las "
            "rutas habilitadas. La liquidación se recibe en USDC en Base.",
            styles["Small"],
        ),
        Spacer(1, 2 * mm),
        Paragraph(f'<link href="{escape(pay_url)}" color="#059669">{escape(pay_url)}</link>', styles["Small"]),
    ]
    pay_box = Table(
        [[qr, pay_text]],
        colWidths=[40 * mm, 134 * mm],
        style=TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), EMERALD_SOFT),
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#A7F3D0")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        ),
    )
    story.append(pay_box)

    if invoice.notes:
        story.append(Spacer(1, 6 * mm))
        story.append(Paragraph("Notas", styles["Label"]))
        story.append(Paragraph(escape(invoice.notes).replace("\n", "<br/>"), styles["Small"]))

    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(escape(settings.invoice_footer_note), styles["Small"]))

    def _footer(canvas, doc_obj):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(18 * mm, 10 * mm, f"{settings.company_legal_name} · {invoice.number}")
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Página {doc_obj.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()
