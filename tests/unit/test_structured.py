import json
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from ailayer.errors import MaxRepairAttemptsExceeded
from ailayer.prompts import PromptLoader
from ailayer.router import LLMRouter
from ailayer.structured import StructuredExtractor
from ailayer.types import (
    ExtractedInvoice,
    LLMResponse,
    TokenUsage,
)


@pytest.mark.asyncio
async def test_prompt_explicitly_forbids_arithmetic():
    """NON-NEGOTIABLE CONSTRAINT:

    System prompt MUST explicitly state: 'Do NOT perform arithmetic. Report printed numbers exactly as shown.'
    """
    loader = PromptLoader()

    extract_prompt = loader.load_prompt("extract_invoice", 1)
    assert (
        "Do NOT perform arithmetic. Report printed numbers exactly as shown."
        in extract_prompt.system_prompt
    )

    repair_prompt = loader.load_prompt("repair", 1)
    assert (
        "Do NOT perform arithmetic. Report printed numbers exactly as shown."
        in repair_prompt.system_prompt
    )


@pytest.mark.asyncio
async def test_structured_extraction_success_first_attempt():
    router_mock = AsyncMock(spec=LLMRouter)
    valid_invoice_payload = {
        "vendor_name": "Acme Supplies Ltd",
        "vendor_tax_id": "GB123456789",
        "vendor_address": "123 High Street, London",
        "invoice_number": "INV-2026-001",
        "invoice_date": "2026-09-01",
        "due_date": "2026-09-30",
        "currency": "USD",
        "subtotal": "100.00",
        "tax_amount": "20.00",
        "total_amount": "120.00",
        "line_items": [
            {
                "line_number": 1,
                "description": "Server Hosting September",
                "quantity": "1.0000",
                "unit_price": "100.00",
                "line_total": "100.00",
            }
        ],
    }

    router_mock.execute.return_value = LLMResponse(
        text=f"```json\n{json.dumps(valid_invoice_payload)}\n```",
        model="claude-3-5-sonnet-20241022",
        usage=TokenUsage(prompt_tokens=200, completion_tokens=80),
        cost_usd=Decimal("0.001800"),
    )

    extractor = StructuredExtractor(router=router_mock)
    result = await extractor.extract(
        schema=ExtractedInvoice,
        prompt_kwargs={"document_text": "Acme Supplies invoice text..."},
    )

    assert result.success is True
    assert result.attempts == 1
    assert result.repair_history == []
    assert isinstance(result.parsed, ExtractedInvoice)
    assert result.parsed.vendor_name == "Acme Supplies Ltd"
    assert result.parsed.total_amount == Decimal("120.00")
    assert isinstance(result.parsed.total_amount, Decimal)
    assert len(result.parsed.line_items) == 1
    assert result.parsed.line_items[0].quantity == Decimal("1.0000")
    assert isinstance(result.parsed.line_items[0].quantity, Decimal)
    assert router_mock.execute.call_count == 1


@pytest.mark.asyncio
async def test_bounded_repair_loop_succeeds_on_second_attempt():
    router_mock = AsyncMock(spec=LLMRouter)

    # 1st attempt: malformed JSON (unterminated string)
    # 2nd attempt: valid JSON
    valid_payload = {
        "vendor_name": "Beta Logistics",
        "invoice_number": "INV-999",
        "total_amount": "500.00",
        "currency": "USD",
        "line_items": [],
    }

    router_mock.execute.side_effect = [
        LLMResponse(
            text='{"vendor_name": "Beta Logistics", "invoice_number": "INV-999", "total_amount": ',
            model="claude-3-5-sonnet-20241022",
            usage=TokenUsage(prompt_tokens=150, completion_tokens=30),
            cost_usd=Decimal("0.000900"),
        ),
        LLMResponse(
            text=json.dumps(valid_payload),
            model="claude-3-5-sonnet-20241022",
            usage=TokenUsage(prompt_tokens=180, completion_tokens=40),
            cost_usd=Decimal("0.001140"),
        ),
    ]

    extractor = StructuredExtractor(router=router_mock)
    result = await extractor.extract(
        schema=ExtractedInvoice,
        prompt_kwargs={"document_text": "Beta Logistics invoice content..."},
    )

    assert result.success is True
    assert result.attempts == 2
    assert len(result.repair_history) == 1
    assert "Attempt 1 failed" in result.repair_history[0]
    assert result.parsed is not None
    assert result.parsed.vendor_name == "Beta Logistics"
    assert router_mock.execute.call_count == 2


@pytest.mark.asyncio
async def test_bounded_repair_loop_strictly_bounded_at_max_2_repairs():
    """NON-NEGOTIABLE RULE:

    Bounded repair loop: maximum 2 repair attempts, then route to human review.
    Never unbounded.
    """
    router_mock = AsyncMock(spec=LLMRouter)

    # 3 consecutive invalid outputs (Initial attempt + 2 repair attempts)
    router_mock.execute.return_value = LLMResponse(
        text="Sorry, I could not parse this invoice properly.",
        model="claude-3-5-sonnet-20241022",
        usage=TokenUsage(prompt_tokens=100, completion_tokens=20),
        cost_usd=Decimal("0.000600"),
    )

    extractor = StructuredExtractor(router=router_mock)

    with pytest.raises(MaxRepairAttemptsExceeded) as excinfo:
        await extractor.extract(
            schema=ExtractedInvoice,
            prompt_kwargs={"document_text": "Unreadable invoice"},
            raise_on_failure=True,
        )

    # Total 3 attempts: 1 initial + 2 bounded repairs
    assert excinfo.value.attempts == 3
    assert len(excinfo.value.repair_history) == 3
    assert "human review" in str(excinfo.value)
    # Crucially, router was called exactly 3 times and stopped!
    assert router_mock.execute.call_count == 3
