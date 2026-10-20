"""Modelos de datos de facturación."""

from __future__ import annotations

import secrets
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_public_token() -> str:
    return secrets.token_urlsafe(24)


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    number: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    public_token: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, default=new_public_token
    )
    external_id: Mapped[str | None] = mapped_column(String(120), unique=True, index=True, nullable=True)

    customer_name: Mapped[str] = mapped_column(String(200))
    customer_email: Mapped[str] = mapped_column(String(320), index=True)
    customer_phone: Mapped[str | None] = mapped_column(String(40), nullable=True)

    description: Mapped[str] = mapped_column(String(500), default="")
    notes: Mapped[str] = mapped_column(Text, default="")

    subtotal_cents: Mapped[int] = mapped_column(Integer, default=0)
    tax_rate_bps: Mapped[int] = mapped_column(Integer, default=0)
    tax_cents: Mapped[int] = mapped_column(Integer, default=0)
    total_cents: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(3), default="USD")

    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    issue_date: Mapped[date] = mapped_column(Date, default=date.today)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Último intento de pago (PayRam). Los intentos históricos viven en payment_attempts.
    payram_reference_id: Mapped[str | None] = mapped_column(String(120), index=True, nullable=True)
    payram_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_payment_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    paid_amount_cents: Mapped[int] = mapped_column(Integer, default=0)
    paid_currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    paid_tx_hash: Mapped[str | None] = mapped_column(String(120), nullable=True)

    email_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    email_last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    whatsapp_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    receipt_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    items: Mapped[list[InvoiceItem]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="InvoiceItem.position",
    )
    payments: Mapped[list[PaymentAttempt]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="PaymentAttempt.id",
    )

    __table_args__ = (
        Index("ix_invoices_status_created", "status", "created_at"),
    )


class InvoiceItem(Base):
    __tablename__ = "invoice_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str] = mapped_column(String(500))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=Decimal(1))
    unit_price_cents: Mapped[int] = mapped_column(Integer, default=0)
    line_total_cents: Mapped[int] = mapped_column(Integer, default=0)

    invoice: Mapped[Invoice] = relationship(back_populates="items")


class PaymentAttempt(Base):
    __tablename__ = "payment_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    reference_id: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    url: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(32), default="OPEN", index=True)
    requested_amount_cents: Mapped[int] = mapped_column(Integer, default=0)
    filled_amount_cents: Mapped[int] = mapped_column(Integer, default=0)
    tx_hash: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    invoice: Mapped[Invoice] = relationship(back_populates="payments")


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    reference_id: Mapped[str | None] = mapped_column(String(120), index=True, nullable=True)
    invoice_id: Mapped[int | None] = mapped_column(
        ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payload_json: Mapped[str] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String(80), default="system")
    action: Mapped[str] = mapped_column(String(80), index=True)
    invoice_id: Mapped[int | None] = mapped_column(
        ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True
    )
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
