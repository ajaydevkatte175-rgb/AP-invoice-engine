from abc import ABC, abstractmethod

from ailayer.types import LLMRequest, LLMResponse


class BaseLLMProvider(ABC):
    """Abstract base provider interface for language model execution."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Name of the provider (e.g. 'anthropic')."""

    @abstractmethod
    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Execute a completion request and return a standardized response."""
