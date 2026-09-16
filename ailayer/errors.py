class AIError(Exception):
    """Base exception for all AI layer operations."""

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class BudgetExceededError(AIError):
    """Raised when monthly LLM budget ceiling has been reached or would be exceeded."""

    pass


class ProviderError(AIError):
    """Base exception for provider communication or API errors."""

    def __init__(
        self,
        message: str,
        provider: str | None = None,
        status_code: int | None = None,
        details: dict | None = None,
    ) -> None:
        super().__init__(message, details)
        self.provider = provider
        self.status_code = status_code


class RateLimitError(ProviderError):
    """Provider rate limit or quota reached."""

    pass


class AuthenticationError(ProviderError):
    """API key invalid, missing, or unauthorized."""

    pass


class ModelUnavailableError(ProviderError):
    """Requested model is overloaded, down, or not found."""

    pass


class StructuredOutputError(AIError):
    """Failed to extract or parse structured data matching the target schema."""

    def __init__(
        self,
        message: str,
        raw_output: str | None = None,
        validation_errors: list[dict] | None = None,
        details: dict | None = None,
    ) -> None:
        super().__init__(message, details)
        self.raw_output = raw_output
        self.validation_errors = validation_errors or []


class MaxRepairAttemptsExceeded(StructuredOutputError):
    """Maximum bounded repair attempts (2) reached without successfully validating against schema."""

    def __init__(
        self,
        message: str,
        attempts: int = 2,
        raw_output: str | None = None,
        validation_errors: list[dict] | None = None,
        repair_history: list[str] | None = None,
    ) -> None:
        super().__init__(message, raw_output, validation_errors)
        self.attempts = attempts
        self.repair_history = repair_history or []


class PromptNotFoundError(AIError):
    """Prompt template file or version not found."""

    pass

