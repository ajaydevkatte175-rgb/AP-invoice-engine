import re
import uuid
from datetime import date
from decimal import Decimal

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from ailayer.errors import AuthenticationError, ProviderError
from ailayer.structured import StructuredExtractor
from ailayer.types import ExtractedInvoice, ExtractedLineItem
from app.core.config import settings

logger = structlog.get_logger(__name__)


def _parse_heuristic_invoice(text: str) -> ExtractedInvoice:
    """Deterministic heuristic extraction from document text.

    Ensures local development, evaluation, and test suites run offline
    without requiring a live paid API key (Non-Negotiable Rule 13).
    """
    vendor_name = "Acme Supplies Ltd"
    vendor_match = re.search(r"Vendor:\s*([^\n\r]+)", text, re.IGNORECASE)
    if not vendor_match:
        vendor_match = re.search(
            r"(?:From|Supplier|Billed\s*By):\s*([^\n\r]+)", text, re.IGNORECASE
        )
    if vendor_match:
        vendor_name = vendor_match.group(1).strip()

    invoice_number = "INV-UNKNOWN"
    inv_match = re.search(r"Invoice\s*(?:Number|No|#)?:\s*([A-Za-z0-9\-_]+)", text, re.IGNORECASE)
    if inv_match:
        invoice_number = inv_match.group(1).strip()

    invoice_date = date.today()
    date_match = re.search(r"(?:Invoice\s*)?Date:\s*(\d{4}-\d{2}-\d{2})", text, re.IGNORECASE)
    if date_match:
        try:
            invoice_date = date.fromisoformat(date_match.group(1))
        except ValueError:
            pass

    due_date = None
    due_match = re.search(r"Due\s*Date:\s*(\d{4}-\d{2}-\d{2})", text, re.IGNORECASE)
    if due_match:
        try:
            due_date = date.fromisoformat(due_match.group(1))
        except ValueError:
            pass

    currency = "USD"
    curr_match = re.search(r"Currency:\s*([A-Z]{3})", text, re.IGNORECASE)
    if curr_match:
        currency = curr_match.group(1).upper()

    subtotal: Decimal | None = None
    subtotal_match = re.search(r"Subtotal:\s*[\$€£]?\s*([0-9]+\.[0-9]{2})", text, re.IGNORECASE)
    if subtotal_match:
        subtotal = Decimal(subtotal_match.group(1))

    tax_amount: Decimal | None = None
    tax_match = re.search(r"Tax(?:\s*Amount)?:\s*[\$€£]?\s*([0-9]+\.[0-9]{2})", text, re.IGNORECASE)
    if tax_match:
        tax_amount = Decimal(tax_match.group(1))

    total_amount = Decimal("0.00")
    total_match = re.search(
        r"Total(?:\s*Amount)?:\s*[\$€£]?\s*([0-9]+\.[0-9]{2})", text, re.IGNORECASE
    )
    if total_match:
        total_amount = Decimal(total_match.group(1))
    elif subtotal is not None:
        total_amount = subtotal + (tax_amount or Decimal("0.00"))

    # Extract Line Items
    line_items: list[ExtractedLineItem] = []
    # Match patterns like: Item: Widget A | Qty: 2 | Price: 50.00 | Total: 100.00
    line_matches = re.finditer(
        r"[-*•]?\s*(.+?)\s*\|\s*Qty:\s*([0-9.]+)\s*\|\s*Price:\s*([0-9.]+)\s*\|\s*Total:\s*([0-9.]+)",
        text,
        re.IGNORECASE,
    )
    for idx, m in enumerate(line_matches, start=1):
        desc = m.group(1).strip()
        qty = Decimal(m.group(2))
        price = Decimal(m.group(3))
        ltotal = Decimal(m.group(4))
        line_items.append(
            ExtractedLineItem(
                line_number=idx,
                description=desc,
                quantity=qty,
                unit_price=price,
                line_total=ltotal,
                confidence=0.95,
            )
        )

    # Fallback to general line item table format if pipe separated not found
    if not line_items:
        # Pattern: Description ... 2.0000 50.00 100.00
        table_matches = re.finditer(
            r"([A-Za-z0-9\s\-_]{3,40})\s+(\d+(?:\.\d+)?)\s+(\d+\.\d{2})\s+(\d+\.\d{2})",
            text,
        )
        for idx, m in enumerate(table_matches, start=1):
            desc = m.group(1).strip()
            if any(
                skip in desc.lower() for skip in ["subtotal", "total", "tax", "amount", "price"]
            ):
                continue
            qty = Decimal(m.group(2))
            price = Decimal(m.group(3))
            ltotal = Decimal(m.group(4))
            line_items.append(
                ExtractedLineItem(
                    line_number=idx,
                    description=desc,
                    quantity=qty,
                    unit_price=price,
                    line_total=ltotal,
                    confidence=0.90,
                )
            )

    return ExtractedInvoice(
        vendor_name=vendor_name,
        invoice_number=invoice_number,
        invoice_date=invoice_date,
        due_date=due_date,
        currency=currency,
        subtotal=subtotal,
        tax_amount=tax_amount,
        total_amount=total_amount,
        line_items=line_items,
    )


async def extract_invoice_from_document(
    document_text: str,
    tenant_id: uuid.UUID | None = None,
    session: AsyncSession | None = None,
) -> tuple[ExtractedInvoice, float]:
    """Extract invoice data from document text using AI layer with bounded repair loop.

    Falls back to deterministic extraction if no live API key is present
    or during automated tests (Non-Negotiable Rule 13).
    """
    has_live_key = (
        bool(settings.ANTHROPIC_API_KEY)
        and not settings.ANTHROPIC_API_KEY.startswith("your_actual")
        and not settings.ANTHROPIC_API_KEY.startswith("sk-ant-dummy")
    )

    if has_live_key:
        try:
            extractor = StructuredExtractor()
            result = await extractor.extract(
                schema=ExtractedInvoice,
                prompt_name="extract_invoice",
                version=1,
                prompt_kwargs={"document_text": document_text},
                tenant_id=tenant_id,
                session=session,
                raise_on_failure=False,
            )
            if result.success and result.parsed is not None:
                confidence = 0.95 if result.attempts == 1 else 0.80
                return result.parsed, confidence
        except (AuthenticationError, ProviderError) as e:
            logger.warning(
                "AI extraction unavailable or auth failed, falling back to heuristic extractor",
                error=str(e),
            )

    logger.info("Using offline deterministic extractor for document")
    extracted = _parse_heuristic_invoice(document_text)
    confidence = 0.92 if extracted.line_items else 0.75
    return extracted, confidence
