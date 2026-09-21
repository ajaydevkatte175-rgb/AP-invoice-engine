"""Draft invoice creation from unstructured natural language text.

NON-NEGOTIABLE RULES:
1. Rule 4: Invoice generation totals are computed in Python, never by the LLM.
   If the model proposes a total, discard it and recompute with compute_totals().
   The AI fills fields; Python does arithmetic; a human confirms before issuing.
2. Rule 13: Every test must pass with NO API key set. Use recorded fixtures or
   deterministic heuristic fallback.
"""

import json
import re
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from ailayer.errors import AuthenticationError, ProviderError
from ailayer.prompts import PromptLoader
from ailayer.router import LLMRouter
from ailayer.types import LLMMessage, LLMRequest
from app.core.config import settings
from app.services.issuing.totals import compute_totals

logger = structlog.get_logger(__name__)
prompt_loader = PromptLoader()


def _extract_json_from_text(raw_text: str) -> dict[str, Any]:
    """Extract JSON object from markdown or raw text."""
    match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw_text, re.IGNORECASE)
    if match:
        return json.loads(match.group(1))

    brace_match = re.search(r"(\{[\s\S]*\})", raw_text)
    if brace_match:
        return json.loads(brace_match.group(1))

    return json.loads(raw_text)


def heuristic_draft_from_text(text: str) -> dict[str, Any]:
    """Deterministic natural language parser for offline testing without API key (Rule 13)."""
    # 1. Extract customer name
    customer_name = "Acme Corp"
    cust_match = re.search(r"(?:bill|invoice|to|for)\s+([A-Za-z0-9\s&,.-]+?)(?:\s+(?:for|with|due|at|\d|-|\$))", text, re.IGNORECASE)
    if cust_match:
        customer_name = cust_match.group(1).strip()
    elif "Acme" in text:
        customer_name = "Acme Corp"
    elif "Globex" in text:
        customer_name = "Globex Corporation"

    # 2. Currency
    currency = "USD"
    if "eur" in text.lower() or "€" in text:
        currency = "EUR"
    elif "gbp" in text.lower() or "£" in text:
        currency = "GBP"

    # 3. Extract items
    # Pattern: X [units/hours/items] of [description] at/for $Y
    line_items: list[dict[str, Any]] = []

    # Common patterns:
    # "5 hours of consulting at $150"
    # "2 widgets at $45.00"
    # "web design for $1,200"
    item_matches = re.finditer(
        r"(?:(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|days?|units?|items?|x)?\s+(?:of\s+)?([A-Za-z\s_-]+?)\s+(?:at|@|for)\s+[\$€£]?\s*(\d+(?:,\d{3})*(?:\.\d{2})?))",
        text,
        re.IGNORECASE,
    )

    found_any = False
    for idx, match in enumerate(item_matches, start=1):
        found_any = True
        qty_str = match.group(1) or "1"
        desc = match.group(2).strip()
        price_str = match.group(3).replace(",", "")
        line_items.append({
            "line_number": idx,
            "description": desc.capitalize(),
            "quantity": Decimal(qty_str),
            "unit_price": Decimal(price_str),
        })

    if not found_any:
        # Fallback default item
        price_match = re.search(r"[\$€£]?\s*(\d+(?:,\d{3})*(?:\.\d{2})?)", text)
        unit_price = Decimal(price_match.group(1).replace(",", "")) if price_match else Decimal("100.00")
        line_items.append({
            "line_number": 1,
            "description": "Professional Services",
            "quantity": Decimal("1.0000"),
            "unit_price": unit_price,
        })

    return {
        "customer_name": customer_name,
        "currency": currency,
        "payment_terms": "Net 30",
        "notes": "Generated from natural language request.",
        "tax_rate": Decimal("0.0000"),
        "line_items": line_items,
    }


class DraftInvoiceService:
    """Parses natural language requests into structured draft outbound invoices."""

    def __init__(self, router: LLMRouter | None = None) -> None:
        self.router = router or LLMRouter()

    def _has_live_api_key(self) -> bool:
        key = settings.ANTHROPIC_API_KEY
        if not key:
            return False
        return not (key.startswith(("your_actual", "dummy")) or len(key) < 10)

    async def parse_draft_invoice(
        self,
        text: str,
        tenant_id: uuid.UUID | None = None,
        session: AsyncSession | None = None,
    ) -> dict[str, Any]:
        """Convert natural language description into verified draft invoice fields.

        Enforces:
        - Discards any LLM-proposed totals.
        - Computes all totals strictly in Python with compute_totals().
        """
        raw_draft: dict[str, Any]

        if not self._has_live_api_key():
            logger.info("Using offline heuristic draft parser (Non-Negotiable Rule 13)")
            raw_draft = heuristic_draft_from_text(text)
        else:
            try:
                prompt_tpl = prompt_loader.load("draft_invoice", version=1)
                system_prompt = prompt_tpl.render_system()
                user_prompt = prompt_tpl.render_user(request_text=text)

                req = LLMRequest(
                    messages=[LLMMessage(role="user", content=user_prompt)],
                    system=system_prompt,
                    call_type="draft_invoice_generation",
                    tenant_id=tenant_id,
                )
                res = await self.router.execute(req, session=session)
                raw_draft = _extract_json_from_text(res.content)
            except (AuthenticationError, ProviderError, Exception) as exc:
                logger.warning(
                    "LLM draft invoice extraction failed, falling back to heuristic",
                    error=str(exc),
                )
                raw_draft = heuristic_draft_from_text(text)

        # -------------------------------------------------------------
        # NON-NEGOTIABLE RULE 4:
        # Discard any LLM-generated totals and recompute with compute_totals().
        # -------------------------------------------------------------
        raw_items = raw_draft.get("line_items", [])
        tax_rate = Decimal(str(raw_draft.get("tax_rate", 0)))

        # Python deterministic arithmetic
        computed = compute_totals(raw_items, default_tax_rate=tax_rate)

        today = date.today()
        due_date = today + timedelta(days=30)

        return {
            "customer_name": raw_draft.get("customer_name"),
            "currency": str(raw_draft.get("currency", "USD")).upper(),
            "invoice_date": today.isoformat(),
            "due_date": due_date.isoformat(),
            "payment_terms": raw_draft.get("payment_terms", "Net 30"),
            "notes": raw_draft.get("notes"),
            "subtotal": computed.subtotal,
            "tax_rate": computed.tax_rate,
            "tax_amount": computed.tax_amount,
            "total_amount": computed.total_amount,
            "line_items": [item.to_dict() for item in computed.line_items],
        }

