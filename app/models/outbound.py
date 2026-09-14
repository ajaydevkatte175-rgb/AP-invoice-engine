import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.base import TenantMixin, TimestampMixin, UUIDMixin


class TenantProfile(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Stores outbound profile settings for each tenant."""

    __tablename__ = "tenant_profile"

    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    tax_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    default_currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    invoice_prefix: Mapped[str] = mapped_column(String(20), default="INV-", nullable=False)
    payment_terms_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    bank_details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    logo_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_tenant_profile_tenant_id"),
    )


class InvoiceCounter(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Dedicated counter row for gapless, concurrency-safe invoice numbering via SELECT ... FOR UPDATE."""

    __tablename__ = "invoice_counters"

    series: Mapped[str] = mapped_column(String(50), default="DEFAULT", nullable=False)
    current_value: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "series", name="uq_invoice_counters_tenant_series"),
    )


class Customer(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Stores outbound accounts receivable customer details."""

    __tablename__ = "customers"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    tax_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    default_currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_customers_tenant_name"),
    )

    # Relationships
    issued_invoices: Mapped[list["IssuedInvoice"]] = relationship(
        back_populates="customer",
    )


class IssuedInvoice(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Stores outbound invoices created and issued to customers."""

    __tablename__ = "issued_invoices"

    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    invoice_number: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)

    # Monies MUST be Numeric(14,2)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    tax_rate: Mapped[Decimal] = mapped_column(
        Numeric(6, 4),
        default=Decimal("0.0000"),
        nullable=False,
    )
    tax_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        default=Decimal("0.00"),
        nullable=False,
    )
    total_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    payment_terms: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="draft", nullable=False)
    pdf_storage_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    __table_args__ = (
        # Mandatory unique constraint per tenant
        UniqueConstraint("tenant_id", "invoice_number", name="uq_issued_invoices_tenant_invoice_num"),
    )

    # Relationships
    customer: Mapped["Customer | None"] = relationship(back_populates="issued_invoices")
    line_items: Mapped[list["IssuedLineItem"]] = relationship(
        back_populates="issued_invoice",
        cascade="all, delete-orphan",
        order_by="IssuedLineItem.line_number",
    )


class IssuedLineItem(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Stores line items for outbound issued invoices."""

    __tablename__ = "issued_line_items"

    issued_invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("issued_invoices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    # Quantities MUST be Numeric(14,4)
    quantity: Mapped[Decimal] = mapped_column(
        Numeric(14, 4),
        default=Decimal("1.0000"),
        nullable=False,
    )
    # Monies MUST be Numeric(14,2)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    tax_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    tax_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)

    # Relationships
    issued_invoice: Mapped["IssuedInvoice"] = relationship(back_populates="line_items")

