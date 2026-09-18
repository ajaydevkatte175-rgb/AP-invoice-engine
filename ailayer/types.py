import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")

MessageRole = Literal["system", "user", "assistant"]


class ImageContent(BaseModel):
    """Encapsulates image data for multimodal vision prompts."""

    media_type: str = Field(
        default="image/png",
        description="MIME type: image/jpeg, image/png, image/webp, image/gif",
    )
    data_base64: str = Field(..., description="Base64-encoded image bytes")


class LLMMessage(BaseModel):
    """Standardized representation of a conversation turn."""

    role: MessageRole
    content: str
    images: list[ImageContent] = Field(default_factory=list)


class LLMRequest(BaseModel):
    """Unified payload submitted to an LLM provider."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    messages: list[LLMMessage]
    system: str | None = None
    model: str | None = None
    max_tokens: int = 4096
    temperature: float = 0.0
    call_type: str = "general"
    tenant_id: uuid.UUID | None = None


class TokenUsage(BaseModel):
    """Token consumption reported by the provider."""

    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class LLMResponse(BaseModel):
    """Standardized response produced by the AI layer."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    text: str
    model: str
    usage: TokenUsage
    cost_usd: Decimal = Field(default=Decimal("0.000000"))
    duration_ms: int = 0
    raw_response: Any = None


class StructuredExtractionResult[T](BaseModel):
    """Container for the output of a structured extraction / repair cycle."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    parsed: T | None = None
    raw_text: str
    attempts: int = 1
    repair_history: list[str] = Field(default_factory=list)
    success: bool = True
    error: str | None = None


# ---------------------------------------------------------------------------
# Strict Extraction Schemas (Money is Decimal, Quantities are Decimal)
# ---------------------------------------------------------------------------


class ExtractedLineItem(BaseModel):
    """Extracted line item from supplier invoice."""

    line_number: int | None = None
    description: str = Field(..., min_length=1)
    quantity: Decimal = Field(default=Decimal("1.0000"))
    unit_price: Decimal = Field(default=Decimal("0.00"))
    line_total: Decimal = Field(default=Decimal("0.00"))
    confidence: float | None = None


class ExtractedInvoice(BaseModel):
    """Complete extracted invoice matching strict Pydantic requirements.

    Constraint: System prompt instructs model to report printed numbers exactly as shown
    without performing arithmetic corrections.
    """

    vendor_name: str = Field(..., min_length=1)
    vendor_tax_id: str | None = None
    vendor_address: str | None = None
    invoice_number: str = Field(..., min_length=1)
    invoice_date: date | None = None
    due_date: date | None = None
    currency: str = Field(default="USD", max_length=3)
    subtotal: Decimal | None = None
    tax_amount: Decimal | None = None
    total_amount: Decimal
    tax_breakdown: dict[str, Any] | None = None
    line_items: list[ExtractedLineItem] = Field(default_factory=list)
    notes: str | None = None
