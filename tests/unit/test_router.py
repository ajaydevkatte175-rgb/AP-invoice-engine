from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from ailayer.budget import BudgetManager
from ailayer.config import AIConfig, ModelSpec, RetrySettings, calculate_cost
from ailayer.errors import (
    BudgetExceededError,
    ModelUnavailableError,
    RateLimitError,
)
from ailayer.providers.anthropic import AnthropicProvider
from ailayer.providers.base import BaseLLMProvider
from ailayer.router import LLMRouter
from ailayer.types import LLMMessage, LLMRequest, LLMResponse, TokenUsage


class MockProvider(BaseLLMProvider):
    """Test double simulating LLM provider behavior without live API calls."""

    def __init__(self, name: str = "anthropic") -> None:
        self._name = name
        self.call_history: list[LLMRequest] = []
        self.responses: list[LLMResponse | Exception] = []
        self.call_index = 0

    @property
    def name(self) -> str:
        return self._name

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.call_history.append(request)
        if self.call_index < len(self.responses):
            result = self.responses[self.call_index]
            self.call_index += 1
            if isinstance(result, Exception):
                raise result
            return result

        return LLMResponse(
            text="Mock success response",
            model=request.model or "claude-3-5-sonnet-20241022",
            usage=TokenUsage(prompt_tokens=100, completion_tokens=50),
            cost_usd=Decimal("0.001050"),
            duration_ms=120,
        )


@pytest.fixture
def mock_config() -> AIConfig:
    return AIConfig(
        default_model="claude-3-5-sonnet-20241022",
        fallback_chain=[
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
        ],
        models={
            "claude-3-5-sonnet-20241022": ModelSpec(
                provider="anthropic",
                input_price_per_million=Decimal("3.00"),
                output_price_per_million=Decimal("15.00"),
            ),
            "claude-3-5-haiku-20241022": ModelSpec(
                provider="anthropic",
                input_price_per_million=Decimal("0.80"),
                output_price_per_million=Decimal("4.00"),
            ),
        },
        retry=RetrySettings(
            max_retries=2,
            initial_delay_ms=10,
            max_delay_ms=50,
            jitter=False,
        ),
    )


@pytest.mark.asyncio
async def test_anthropic_client_has_zero_max_retries():
    """NON-NEGOTIABLE RULE: SDK client must have max_retries=0 because the router owns retries."""
    provider = AnthropicProvider(api_key="mock-key-for-test")
    assert provider._client.max_retries == 0


@pytest.mark.asyncio
async def test_cost_calculation_uses_decimal():
    """NON-NEGOTIABLE RULE: Money is Decimal in Python, NEVER float."""
    cost = calculate_cost("claude-3-5-sonnet-20241022", prompt_tokens=1000, completion_tokens=500)
    assert isinstance(cost, Decimal)
    # (1000/1M * 3.00) + (500/1M * 15.00) = 0.003 + 0.0075 = 0.010500
    assert cost == Decimal("0.010500")


@pytest.mark.asyncio
async def test_router_successful_execution(mock_config: AIConfig):
    mock_provider = MockProvider()
    ledger_mock = AsyncMock()
    budget_mock = AsyncMock()
    budget_mock.check_budget_or_raise = AsyncMock()

    router = LLMRouter(
        config=mock_config,
        providers={"anthropic": mock_provider},
        budget_manager=budget_mock,
        ledger=ledger_mock,
    )

    request = LLMRequest(
        messages=[LLMMessage(role="user", content="Hello")],
        call_type="test",
    )

    response = await router.execute(request)

    assert response.text == "Mock success response"
    assert len(mock_provider.call_history) == 1
    budget_mock.check_budget_or_raise.assert_called_once()
    ledger_mock.record_success.assert_called_once()


@pytest.mark.asyncio
async def test_router_retries_transient_error(mock_config: AIConfig):
    mock_provider = MockProvider()
    mock_provider.responses = [
        RateLimitError(message="Rate limit exceeded", status_code=429),
        LLMResponse(
            text="Recovered response",
            model="claude-3-5-sonnet-20241022",
            usage=TokenUsage(prompt_tokens=50, completion_tokens=20),
            cost_usd=Decimal("0.000450"),
        ),
    ]

    ledger_mock = AsyncMock()
    budget_mock = AsyncMock()

    router = LLMRouter(
        config=mock_config,
        providers={"anthropic": mock_provider},
        budget_manager=budget_mock,
        ledger=ledger_mock,
    )

    request = LLMRequest(messages=[LLMMessage(role="user", content="Test retry")])
    response = await router.execute(request)

    assert response.text == "Recovered response"
    assert len(mock_provider.call_history) == 2
    # 1 failure recorded, 1 success recorded
    assert ledger_mock.record_failure.call_count == 1
    assert ledger_mock.record_success.call_count == 1


@pytest.mark.asyncio
async def test_router_failover_chain(mock_config: AIConfig):
    mock_provider = MockProvider()
    # Primary model fails max_retries (3 attempts total)
    mock_provider.responses = [
        ModelUnavailableError("Sonnet down", status_code=500),
        ModelUnavailableError("Sonnet down again", status_code=500),
        ModelUnavailableError("Sonnet down 3rd", status_code=500),
        LLMResponse(
            text="Haiku fallback succeeded",
            model="claude-3-5-haiku-20241022",
            usage=TokenUsage(prompt_tokens=50, completion_tokens=20),
            cost_usd=Decimal("0.000120"),
        ),
    ]

    ledger_mock = AsyncMock()
    budget_mock = AsyncMock()

    router = LLMRouter(
        config=mock_config,
        providers={"anthropic": mock_provider},
        budget_manager=budget_mock,
        ledger=ledger_mock,
    )

    request = LLMRequest(messages=[LLMMessage(role="user", content="Failover test")])
    response = await router.execute(request)

    assert response.text == "Haiku fallback succeeded"
    assert response.model == "claude-3-5-haiku-20241022"
    # 3 failures on Sonnet, 1 success on Haiku
    assert ledger_mock.record_failure.call_count == 3
    assert ledger_mock.record_success.call_count == 1


@pytest.mark.asyncio
async def test_router_hard_monthly_budget_ceiling_blocks_call(mock_config: AIConfig):
    mock_provider = MockProvider()
    ledger_mock = AsyncMock()

    # Create budget manager simulating spend >= ceiling
    async def mock_spend():
        return Decimal("25.01")

    budget_manager = BudgetManager(
        monthly_ceiling_usd=Decimal("25.00"),
        spend_override_fn=mock_spend,
    )

    router = LLMRouter(
        config=mock_config,
        providers={"anthropic": mock_provider},
        budget_manager=budget_manager,
        ledger=ledger_mock,
    )

    request = LLMRequest(messages=[LLMMessage(role="user", content="Should be blocked")])

    with pytest.raises(BudgetExceededError) as excinfo:
        await router.execute(request)

    assert "Monthly LLM budget ceiling" in str(excinfo.value)
    # Ensure provider was never called
    assert len(mock_provider.call_history) == 0
