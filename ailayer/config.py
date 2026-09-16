import os
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any
import yaml
from pydantic import BaseModel, Field


class ModelSpec(BaseModel):
    """Specification and pricing for an LLM model."""

    provider: str = "anthropic"
    input_price_per_million: Decimal = Field(default=Decimal("3.00"))
    output_price_per_million: Decimal = Field(default=Decimal("15.00"))
    max_tokens: int = 4096
    supports_vision: bool = True


class RetrySettings(BaseModel):
    """Jittered exponential backoff retry configuration."""

    max_retries: int = 3
    initial_delay_ms: int = 500
    max_delay_ms: int = 5000
    backoff_multiplier: float = 2.0
    jitter: bool = True


class BudgetSettings(BaseModel):
    """Hard budget ceiling settings."""

    monthly_ceiling_usd: Decimal = Field(default=Decimal("25.00"))
    warning_threshold_usd: Decimal = Field(default=Decimal("20.00"))


class StructuredSettings(BaseModel):
    """Settings for structured outputs and repair loop."""

    max_repair_attempts: int = 2


class AIConfig(BaseModel):
    """Top-level configuration loaded from ai.yaml."""

    default_model: str = "claude-3-5-sonnet-20241022"
    fallback_chain: list[str] = Field(
        default_factory=lambda: [
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
            "claude-3-haiku-20240307",
        ]
    )
    models: dict[str, ModelSpec] = Field(default_factory=dict)
    retry: RetrySettings = Field(default_factory=RetrySettings)
    budget: BudgetSettings = Field(default_factory=BudgetSettings)
    structured: StructuredSettings = Field(default_factory=StructuredSettings)

    def get_model_spec(self, model_name: str) -> ModelSpec:
        if model_name in self.models:
            return self.models[model_name]
        # Return sensible default if not explicitly in config
        return ModelSpec(
            provider="anthropic",
            input_price_per_million=Decimal("3.00"),
            output_price_per_million=Decimal("15.00"),
        )


def find_ai_yaml_path() -> Path:
    """Locate the ai.yaml configuration file."""
    # Check environment variable
    if "AI_CONFIG_PATH" in os.environ:
        p = Path(os.environ["AI_CONFIG_PATH"])
        if p.exists():
            return p

    # Check project root and parent directories
    current = Path(__file__).resolve().parent
    for _ in range(4):
        candidate = current / "ai.yaml"
        if candidate.exists():
            return candidate
        current = current.parent

    # Default fallback to working directory
    return Path("ai.yaml")


@lru_cache(maxsize=1)
def load_ai_config(path: str | Path | None = None) -> AIConfig:
    """Load and cache the AIConfig from ai.yaml."""
    config_file = Path(path) if path else find_ai_yaml_path()

    if not config_file.exists():
        # Return defaults if file doesn't exist
        return AIConfig()

    with open(config_file, "r", encoding="utf-8") as f:
        raw_data = yaml.safe_load(f) or {}

    return AIConfig.model_validate(raw_data)


def calculate_cost(
    model_name: str,
    prompt_tokens: int,
    completion_tokens: int,
    config: AIConfig | None = None,
) -> Decimal:
    """Compute exact USD cost using Decimal arithmetic.

    Rule: Money is Decimal in Python. NEVER float.
    """
    if config is None:
        config = load_ai_config()

    spec = config.get_model_spec(model_name)
    prompt_tokens_dec = Decimal(prompt_tokens)
    completion_tokens_dec = Decimal(completion_tokens)
    one_million = Decimal("1000000")

    input_cost = (prompt_tokens_dec / one_million) * spec.input_price_per_million
    output_cost = (completion_tokens_dec / one_million) * spec.output_price_per_million

    # Quantize to 6 decimal places for token level micro-billing
    return (input_cost + output_cost).quantize(Decimal("0.000001"))

