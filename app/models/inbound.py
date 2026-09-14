import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.base import TenantMixin, TimestampMixin, UUIDMixin


class Document(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Stores uploaded document metadata for inbound accounts payable invoices."""

    __tablename__ = "documents"

    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="uploaded", nullable=False)

    # Relationships
    invoices: Mapped[list["Invoice"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
    )


class Invoice(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Stores extracted inbound invoice header information."""

    __tablename__ = "invoices"

    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    invoice_number: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    invoice_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    vendor_name: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    vendor_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    vendor_tax_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    customer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    customer_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    customer_tax_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)

    # Monies MUST be Numeric(14,2)
    subtotal: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    tax_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    amount_paid: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        default=Decimal("0.00"),
        nullable=False,
    )
    balance_due: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)

    # Multi-tax line breakdowns stored as JSONB
    tax_breakdown: Mapped[dict | list | None] = mapped_column(JSONB, nullable=True)

    status: Mapped[str] = mapped_column(String(50), default="pending_validation", nullable=False)
    extraction_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    raw_extraction: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        Index("ix_invoices_tenant_vendor_invoice_num", "tenant_id", "vendor_name", "invoice_number"),
    )

    # Relationships
    document: Mapped["Document | None"] = relationship(back_populates="invoices")
    line_items: Mapped[list["LineItem"]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="LineItem.line_number",
    )
    validation_flags: Mapped[list["ValidationFlag"]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
    )
    review_items: Mapped[list["ReviewItem"]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
    )


class LineItem(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Stores extracted line items for inbound invoices."""

    __tablename__ = "line_items"

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"),
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
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    raw_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Relationships
    invoice: Mapped["Invoice"] = relationship(back_populates="line_items")


class ValidationFlag(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Stores deterministic validation results for an invoice."""

    __tablename__ = "validation_flags"

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    rule_name: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)  # WARNING, ERROR, CRITICAL
    message: Mapped[str] = mapped_column(Text, nullable=False)
    field_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    expected_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    actual_value: Mapped[str | None] = mapped_column(Text, nullable=True)

    is_resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    resolved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    invoice: Mapped["Invoice"] = relationship(back_populates="validation_flags")


class ReviewItem(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Stores items queued for human review and corrections."""

    __tablename__ = "review_items"

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(50), default="pending", nullable=False)
    priority: Mapped[str] = mapped_column(String(20), default="normal", nullable=False)
    assigned_to: Mapped[str | None] = mapped_column(String(100), nullable=True)
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    corrections: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    invoice: Mapped["Invoice"] = relationship(back_populates="review_items")

