import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Document Schemas
# ---------------------------------------------------------------------------


class DocumentBase(BaseModel):
    filename: str = Field(..., max_length=255)
    mime_type: str = Field(..., max_length=100)
    file_size_bytes: int = Field(..., ge=0)


class DocumentCreate(DocumentBase):
    storage_path: str = Field(..., max_length=1024)
    sha256_hash: str = Field(..., min_length=64, max_length=64)
    page_count: int | None = Field(default=None, ge=1)
    status: str = Field(default="uploaded", max_length=50)


class DocumentResponse(DocumentBase):
    id: uuid.UUID
    tenant_id: uuid.UUID
    storage_path: str
    sha256_hash: str
    page_count: int | None
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Line Item Schemas
# ---------------------------------------------------------------------------


class LineItemBase(BaseModel):
    line_number: int = Field(..., ge=1)
    description: str
    quantity: Decimal = Field(default=Decimal("1.0000"), ge=Decimal("0.0001"))
    unit_price: Decimal = Field(..., decimal_places=2)
    total_amount: Decimal = Field(..., decimal_places=2)
    tax_rate: Decimal | None = Field(default=None, decimal_places=4)
    tax_amount: Decimal | None = Field(default=None, decimal_places=2)
    confidence: Decimal | None = Field(default=None, ge=Decimal("0.0"), le=Decimal("1.0"))
    raw_data: dict[str, Any] | None = None


class LineItemCreate(LineItemBase):
    pass


class LineItemUpdate(BaseModel):
    description: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    total_amount: Decimal | None = None
    tax_rate: Decimal | None = None
    tax_amount: Decimal | None = None


class LineItemResponse(LineItemBase):
    id: uuid.UUID
    invoice_id: uuid.UUID
    tenant_id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Validation Flag Schemas
# ---------------------------------------------------------------------------


class ValidationFlagResponse(BaseModel):
    id: uuid.UUID
    invoice_id: uuid.UUID
    tenant_id: uuid.UUID
    rule_name: str
    severity: str
    message: str
    field_name: str | None = None
    expected_value: str | None = None
    actual_value: str | None = None
    is_resolved: bool
    resolved_by: str | None = None
    resolved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Review Item Schemas
# ---------------------------------------------------------------------------


class ReviewItemResponse(BaseModel):
    id: uuid.UUID
    invoice_id: uuid.UUID
    tenant_id: uuid.UUID
    status: str
    priority: str
    assigned_to: str | None = None
    review_reason: str | None = None
    reviewer_notes: str | None = None
    corrections: dict[str, Any] | None = None
    resolved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ReviewItemUpdate(BaseModel):
    status: str | None = Field(default=None, max_length=50)
    priority: str | None = Field(default=None, max_length=20)
    assigned_to: str | None = None
    reviewer_notes: str | None = None
    corrections: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Invoice Schemas
# ---------------------------------------------------------------------------


class InvoiceBase(BaseModel):
    invoice_number: str | None = Field(default=None, max_length=100)
    invoice_date: date | None = None
    due_date: date | None = None
    vendor_name: str | None = Field(default=None, max_length=255)
    vendor_address: str | None = None
    vendor_tax_id: str | None = Field(default=None, max_length=100)
    customer_name: str | None = Field(default=None, max_length=255)
    customer_address: str | None = None
    customer_tax_id: str | None = Field(default=None, max_length=100)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    subtotal: Decimal | None = Field(default=None, decimal_places=2)
    tax_amount: Decimal | None = Field(default=None, decimal_places=2)
    total_amount: Decimal | None = Field(default=None, decimal_places=2)
    amount_paid: Decimal = Field(default=Decimal("0.00"), decimal_places=2)
    balance_due: Decimal | None = Field(default=None, decimal_places=2)
    tax_breakdown: dict[str, Any] | list[Any] | None = None


class InvoiceCreate(InvoiceBase):
    document_id: uuid.UUID | None = None
    status: str = Field(default="pending_validation", max_length=50)
    extraction_confidence: Decimal | None = None
    raw_extraction: dict[str, Any] | None = None
    line_items: list[LineItemCreate] = Field(default_factory=list)


class InvoiceUpdate(BaseModel):
    invoice_number: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    vendor_name: str | None = None
    vendor_address: str | None = None
    vendor_tax_id: str | None = None
    customer_name: str | None = None
    customer_address: str | None = None
    customer_tax_id: str | None = None
    currency: str | None = None
    subtotal: Decimal | None = None
    tax_amount: Decimal | None = None
    total_amount: Decimal | None = None
    amount_paid: Decimal | None = None
    balance_due: Decimal | None = None
    tax_breakdown: dict[str, Any] | list[Any] | None = None
    status: str | None = None


class InvoiceStatusUpdate(BaseModel):
    status: str = Field(..., max_length=50)
    notes: str | None = None


class InvoiceResponse(InvoiceBase):
    id: uuid.UUID
    document_id: uuid.UUID | None
    tenant_id: uuid.UUID
    status: str
    extraction_confidence: Decimal | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class InvoiceDetailResponse(InvoiceResponse):
    document: DocumentResponse | None = None
    line_items: list[LineItemResponse] = Field(default_factory=list)
    validation_flags: list[ValidationFlagResponse] = Field(default_factory=list)
    review_items: list[ReviewItemResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)
