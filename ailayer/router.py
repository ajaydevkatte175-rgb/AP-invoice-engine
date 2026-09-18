import asyncio
import random
import time

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from ailayer.budget import BudgetManager
from ailayer.config import AIConfig, load_ai_config
from ailayer.errors import (
    ModelUnavailableError,
    ProviderError,
    RateLimitError,
)
from ailayer.ledger import LLMLedger
from ailayer.providers.anthropic import AnthropicProvider
from ailayer.providers.base import BaseLLMProvider
from ailayer.types import LLMRequest, LLMResponse

logger = structlog.get_logger(__name__)


class LLMRouter:
    """Intelligent router managing model failover chains and jittered exponential backoff retries.

    NON-NEGOTIABLE ARCHITECTURAL RULES:
    1. The router owns retries (SDK clients use max_retries=0).
    2. Enforces hard monthly budget ceiling before each invocation.
    3. Writes one llm_calls row per call attempt to the ledger (including failures).
    4. Automatically fails over across configured models in the fallback chain.
    """

    def __init__(
        self,
        config: AIConfig | None = None,
        providers: dict[str, BaseLLMProvider] | None = None,
        budget_manager: BudgetManager | None = None,
        ledger: LLMLedger | None = None,
    ) -> None:
        self.config = config or load_ai_config()
        self.providers = providers or {"anthropic": AnthropicProvider(config=self.config)}
        self.budget_manager = budget_manager or BudgetManager(config=self.config)
        self.ledger = ledger or LLMLedger()

    def _get_provider_for_model(self, model_name: str) -> BaseLLMProvider:
        spec = self.config.get_model_spec(model_name)
        provider = self.providers.get(spec.provider)
        if not provider:
            raise ProviderError(
                f"No provider registered for provider '{spec.provider}' (model: {model_name})",
                provider=spec.provider,
            )
        return provider

    def _get_fallback_chain(self, requested_model: str | None = None) -> list[str]:
        """Build the ordered list of models to try."""
        chain = list(self.config.fallback_chain)
        if requested_model and requested_model in chain:
            # Reorder to put requested model first
            chain.remove(requested_model)
            return [requested_model] + chain
        elif requested_model:
            return [requested_model] + chain
        return chain

    def _is_transient_error(self, exc: Exception) -> bool:
        """Determine if an error is eligible for retry."""
        if isinstance(exc, (RateLimitError, ModelUnavailableError)):
            return True
        if isinstance(exc, ProviderError):
            if exc.status_code in (429, 500, 502, 503, 504):
                return True
            if "connection" in exc.message.lower() or "timeout" in exc.message.lower():
                return True
        return False

    def _calculate_backoff(self, attempt: int) -> float:
        """Calculate jittered exponential backoff delay in seconds."""
        settings = self.config.retry
        delay_ms = settings.initial_delay_ms * (settings.backoff_multiplier**attempt)
        delay_ms = min(delay_ms, settings.max_delay_ms)

        if settings.jitter:
            # Full jitter: random factor between 0.5 and 1.5
            jitter_factor = 0.5 + random.random()
            delay_ms = delay_ms * jitter_factor

        return max(0.01, delay_ms / 1000.0)

    async def execute(
        self,
        request: LLMRequest,
        session: AsyncSession | None = None,
    ) -> LLMResponse:
        """Execute request with pre-call budget verification, retry backoff, and model failover."""
        # 1. Hard monthly ceiling check executed BEFORE each call
        await self.budget_manager.check_budget_or_raise(session=session)

        fallback_models = self._get_fallback_chain(request.model)
        last_error: Exception | None = None

        for model_idx, model_name in enumerate(fallback_models):
            provider = self._get_provider_for_model(model_name)
            max_retries = self.config.retry.max_retries

            logger.info(
                "Attempting model execution",
                model=model_name,
                model_index=model_idx,
                total_models=len(fallback_models),
            )

            for attempt in range(max_retries + 1):
                start_time = time.perf_counter()
                try:
                    # Point request to currently attempted model in chain
                    request.model = model_name
                    response = await provider.complete(request)

                    duration_ms = int((time.perf_counter() - start_time) * 1000)
                    response.duration_ms = duration_ms

                    # Record successful call in ledger
                    await self.ledger.record_success(
                        request=request,
                        response=response,
                        session=session,
                    )

                    return response

                except Exception as exc:
                    duration_ms = int((time.perf_counter() - start_time) * 1000)
                    last_error = exc

                    # Record failed call attempt in ledger
                    await self.ledger.record_failure(
                        request=request,
                        model_name=model_name,
                        error_message=str(exc),
                        duration_ms=duration_ms,
                        session=session,
                    )

                    is_transient = self._is_transient_error(exc)
                    logger.warning(
                        "Model execution attempt failed",
                        model=model_name,
                        attempt=attempt + 1,
                        max_retries=max_retries,
                        is_transient=is_transient,
                        error=str(exc),
                    )

                    if not is_transient:
                        # Non-retryable error on this model (e.g. invalid auth), failover immediately
                        break

                    if attempt < max_retries:
                        backoff_sec = self._calculate_backoff(attempt)
                        logger.info("Applying jittered retry backoff", backoff_sec=backoff_sec)
                        await asyncio.sleep(backoff_sec)
                    else:
                        logger.warning(
                            "Exhausted retries for model, failing over to next in chain",
                            failed_model=model_name,
                        )

        # If all models in the fallback chain were exhausted
        if isinstance(last_error, ProviderError):
            raise last_error
        raise ProviderError(
            message=f"All models in failover chain exhausted. Last error: {last_error}",
            details={"last_error": str(last_error)},
        )
