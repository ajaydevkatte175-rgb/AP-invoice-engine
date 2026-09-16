from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.inbound import InvoiceResponse


class VendorSummary(BaseModel):
    vendor_name: str
    invoice_count: int = Field(default=0, ge=0)
    total_spend: Decimal = Field(default=Decimal("0.00"), decimal_places=2)
    currency: str = "USD"
    last_invoice_date: date | None = None

    model_config = ConfigDict(from_attributes=True)


class VendorDetail(BaseModel):
    vendor_name: str
    vendor_address: str | None = None
    vendor_tax_id: str | None = None
    invoice_count: int = 0
    total_spend: Decimal = Decimal("0.00")
    currencies: list[str] = Field(default_factory=list)
    recent_invoices: list[InvoiceResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)
