import time
from typing import Any

import anthropic

from ailayer.config import AIConfig, calculate_cost, load_ai_config
from ailayer.errors import (
    AuthenticationError,
    ModelUnavailableError,
    ProviderError,
    RateLimitError,
)
from ailayer.providers.base import BaseLLMProvider
from ailayer.types import LLMMessage, LLMRequest, LLMResponse, TokenUsage
from app.core.config import settings


class AnthropicProvider(BaseLLMProvider):
    """Anthropic Claude provider adapter.

    NON-NEGOTIABLE RULE:
    max_retries=0 on the SDK client (the router owns retries).
    Token costs must be computed accurately using Decimal.
    """

    def __init__(
        self,
        api_key: str | None = None,
        config: AIConfig | None = None,
        client: anthropic.AsyncAnthropic | None = None,
    ) -> None:
        self.config = config or load_ai_config()
        self._api_key = api_key or settings.ANTHROPIC_API_KEY
        # Set max_retries=0 on the SDK client as required by specification
        self._client = client or anthropic.AsyncAnthropic(
            api_key=self._api_key or "sk-ant-dummy-key-for-init",
            max_retries=0,
        )

    @property
    def name(self) -> str:
        return "anthropic"

    def _format_messages(self, messages: list[LLMMessage]) -> list[dict[str, Any]]:
        """Format unified messages into Anthropic messages API structure."""
        formatted = []
        for msg in messages:
            if msg.role == "system":
                # System messages are handled via the top-level 'system' parameter in Claude
                continue

            if not msg.images:
                formatted.append({"role": msg.role, "content": msg.content})
            else:
                content_blocks: list[dict[str, Any]] = []
                for img in msg.images:
                    content_blocks.append(
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": img.media_type,
                                "data": img.data_base64,
                            },
                        }
                    )
                if msg.content:
                    content_blocks.append({"type": "text", "text": msg.content})
                formatted.append({"role": msg.role, "content": content_blocks})
        return formatted

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Execute a completion via the Anthropic Claude API."""
        model_name = request.model or self.config.default_model
        formatted_messages = self._format_messages(request.messages)

        start_time = time.perf_counter()
        try:
            kwargs: dict[str, Any] = {
                "model": model_name,
                "messages": formatted_messages,
                "max_tokens": request.max_tokens,
                "temperature": request.temperature,
            }
            if request.system:
                kwargs["system"] = request.system

            response = await self._client.messages.create(**kwargs)

            duration_ms = int((time.perf_counter() - start_time) * 1000)

            # Extract generated text from content blocks
            extracted_text = ""
            for block in response.content:
                if getattr(block, "type", None) == "text":
                    extracted_text += getattr(block, "text", "")

            # Track tokens
            prompt_tokens = getattr(response.usage, "input_tokens", 0)
            completion_tokens = getattr(response.usage, "output_tokens", 0)
            usage = TokenUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )

            # Compute execution cost deterministically using Decimal
            cost_usd = calculate_cost(
                model_name=model_name,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                config=self.config,
            )

            return LLMResponse(
                text=extracted_text,
                model=model_name,
                usage=usage,
                cost_usd=cost_usd,
                duration_ms=duration_ms,
                raw_response=response,
            )

        except anthropic.AuthenticationError as e:
            raise AuthenticationError(
                message=str(e),
                provider=self.name,
                status_code=401,
            ) from e
        except anthropic.RateLimitError as e:
            raise RateLimitError(
                message=str(e),
                provider=self.name,
                status_code=429,
            ) from e
        except (anthropic.InternalServerError, anthropic.APITimeoutError) as e:
            raise ModelUnavailableError(
                message=str(e),
                provider=self.name,
                status_code=500,
            ) from e
        except anthropic.APIConnectionError as e:
            raise ProviderError(
                message=f"Anthropic connection error: {e}",
                provider=self.name,
            ) from e
        except anthropic.APIStatusError as e:
            raise ProviderError(
                message=f"Anthropic API status error {e.status_code}: {e.message}",
                provider=self.name,
                status_code=e.status_code,
            ) from e
        except Exception as e:
            raise ProviderError(
                message=f"Unexpected error calling Anthropic: {e}",
                provider=self.name,
            ) from e
