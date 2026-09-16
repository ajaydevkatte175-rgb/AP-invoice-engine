import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Customer Schemas
# ---------------------------------------------------------------------------


class CustomerBase(BaseModel):
    name: str = Field(..., max_length=255)
    email: str | None = Field(default=None, max_length=255)
    address: str | None = None
    tax_id: str | None = Field(default=None, max_length=100)
    phone: str | None = Field(default=None, max_length=50)
    default_currency: str = Field(default="USD", min_length=3, max_length=3)
    is_active: bool = True


class CustomerCreate(CustomerBase):
    pass


class CustomerUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=255)
    address: str | None = None
    tax_id: str | None = Field(default=None, max_length=100)
    phone: str | None = Field(default=None, max_length=50)
    default_currency: str | None = Field(default=None, min_length=3, max_length=3)
    is_active: bool | None = None


class CustomerResponse(CustomerBase):
    id: uuid.UUID
    tenant_id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Tenant Profile Schemas
# ---------------------------------------------------------------------------


class TenantProfileBase(BaseModel):
    company_name: str = Field(..., max_length=255)
    address: str | None = None
    tax_id: str | None = Field(default=None, max_length=100)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    default_currency: str = Field(default="USD", min_length=3, max_length=3)
    invoice_prefix: str = Field(default="INV-", max_length=20)
    payment_terms_days: int = Field(default=30, ge=0)
    bank_details: dict[str, Any] | None = None
    logo_url: str | None = Field(default=None, max_length=1024)


class TenantProfileCreate(TenantProfileBase):
    pass


class TenantProfileUpdate(BaseModel):
    company_name: str | None = Field(default=None, max_length=255)
    address: str | None = None
    tax_id: str | None = Field(default=None, max_length=100)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    default_currency: str | None = Field(default=None, min_length=3, max_length=3)
    invoice_prefix: str | None = Field(default=None, max_length=20)
    payment_terms_days: int | None = Field(default=None, ge=0)
    bank_details: dict[str, Any] | None = None
    logo_url: str | None = Field(default=None, max_length=1024)


class TenantProfileResponse(TenantProfileBase):
    id: uuid.UUID
    tenant_id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Issued Invoice Line Item Schemas
# ---------------------------------------------------------------------------


class IssuedLineItemBase(BaseModel):
    line_number: int = Field(..., ge=1)
    description: str
    quantity: Decimal = Field(default=Decimal("1.0000"), ge=Decimal("0.0001"))
    unit_price: Decimal = Field(..., decimal_places=2)
    tax_rate: Decimal | None = Field(default=Decimal("0.0000"), decimal_places=4)


class IssuedLineItemCreate(IssuedLineItemBase):
    # total_amount will be calculated deterministically in Python (Rule 4)
    total_amount: Decimal | None = None


class IssuedLineItemResponse(BaseModel):
    id: uuid.UUID
    issued_invoice_id: uuid.UUID
    tenant_id: uuid.UUID
    line_number: int
    description: str
    quantity: Decimal
    unit_price: Decimal
    total_amount: Decimal
    tax_rate: Decimal | None
    tax_amount: Decimal | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Issued Invoice Schemas
# ---------------------------------------------------------------------------


class IssuedInvoiceCreate(BaseModel):
    customer_id: uuid.UUID | None = None
    invoice_number: str | None = None  # If not provided, auto-generated gapless (Rule 5)
    invoice_date: date
    due_date: date
    currency: str = Field(default="USD", min_length=3, max_length=3)
    tax_rate: Decimal = Field(default=Decimal("0.0000"), decimal_places=4)
    notes: str | None = None
    payment_terms: str | None = None
    line_items: list[IssuedLineItemCreate] = Field(..., min_length=1)


class IssuedInvoiceUpdate(BaseModel):
    customer_id: uuid.UUID | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    currency: str | None = None
    notes: str | None = None
    payment_terms: str | None = None
    status: str | None = None


class IssuedInvoiceStatusUpdate(BaseModel):
    status: str = Field(..., max_length=50)  # draft, issued, paid, cancelled


class IssuedInvoiceResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    customer_id: uuid.UUID | None
    invoice_number: str
    invoice_date: date
    due_date: date
    currency: str
    subtotal: Decimal
    tax_rate: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    notes: str | None
    payment_terms: str | None
    status: str
    pdf_storage_path: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class IssuedInvoiceDetailResponse(IssuedInvoiceResponse):
    customer: CustomerResponse | None = None
    line_items: list[IssuedLineItemResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)
