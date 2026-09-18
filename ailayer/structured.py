import json
import re
import uuid
from typing import Any, TypeVar

import structlog
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from ailayer.config import AIConfig, load_ai_config
from ailayer.errors import MaxRepairAttemptsExceeded, StructuredOutputError
from ailayer.prompts import PromptLoader
from ailayer.router import LLMRouter
from ailayer.types import (
    ImageContent,
    LLMMessage,
    LLMRequest,
    StructuredExtractionResult,
)

logger = structlog.get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


class StructuredExtractor[T: BaseModel]:
    """Enforces strict schema validation using a bounded repair loop (strictly max 2 attempts).

    NON-NEGOTIABLE RULE:
    Bounded repair loop: maximum 2 repair attempts, then route to human review.
    Never unbounded.
    """

    def __init__(
        self,
        router: LLMRouter | None = None,
        prompt_loader: PromptLoader | None = None,
        config: AIConfig | None = None,
    ) -> None:
        self.config = config or load_ai_config()
        self.router = router or LLMRouter(config=self.config)
        self.prompt_loader = prompt_loader or PromptLoader()
        self.max_repair_attempts = self.config.structured.max_repair_attempts

    @staticmethod
    def _extract_json_substring(text: str) -> str:
        """Strip markdown code fence blocks (```json ... ```) or extract outer JSON object/array."""
        text = text.strip()
        # Match markdown ```json ... ``` or ``` ... ```
        fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL | re.IGNORECASE)
        if fence_match:
            return fence_match.group(1).strip()

        # Match outermost curly braces or square brackets
        first_brace = text.find("{")
        last_brace = text.rfind("}")
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            return text[first_brace : last_brace + 1].strip()

        return text

    def _parse_and_validate(self, text: str, schema: type[T]) -> T:
        """Parse JSON text and validate with target Pydantic schema."""
        cleaned = self._extract_json_substring(text)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise StructuredOutputError(
                message=f"JSON decoding error: {e}",
                raw_output=text,
                details={"json_error": str(e)},
            ) from e

        try:
            return schema.model_validate(data)
        except ValidationError as e:
            raise StructuredOutputError(
                message="Schema validation error",
                raw_output=text,
                validation_errors=e.errors(),
                details={"validation_errors": e.errors()},
            ) from e

    async def extract(
        self,
        schema: type[T],
        prompt_name: str = "extract_invoice",
        version: int = 1,
        prompt_kwargs: dict[str, Any] | None = None,
        images: list[ImageContent] | None = None,
        tenant_id: uuid.UUID | None = None,
        session: AsyncSession | None = None,
        raise_on_failure: bool = True,
    ) -> StructuredExtractionResult[T]:
        """Extract structured data conforming to `schema` with up to 2 bounded repair attempts."""
        kwargs = dict(prompt_kwargs or {})
        kwargs["schema"] = json.dumps(schema.model_json_schema(), indent=2)

        template = self.prompt_loader.load_prompt(prompt_name, version)
        system_prompt = template.render_system(**kwargs)
        user_prompt = template.render_user(**kwargs)

        request = LLMRequest(
            messages=[
                LLMMessage(
                    role="user",
                    content=user_prompt,
                    images=images or [],
                )
            ],
            system=system_prompt,
            model=template.model,
            temperature=template.temperature,
            max_tokens=template.max_tokens,
            call_type=f"{prompt_name}_extract",
            tenant_id=tenant_id,
        )

        response = await self.router.execute(request, session=session)
        current_text = response.text
        repair_history: list[str] = []

        # Attempt 1: Parse and validate initial output
        try:
            validated = self._parse_and_validate(current_text, schema)
            return StructuredExtractionResult(
                parsed=validated,
                raw_text=current_text,
                attempts=1,
                repair_history=[],
                success=True,
                error=None,
            )
        except StructuredOutputError as e:
            logger.warning(
                "Initial schema validation failed, starting bounded repair loop",
                error=str(e),
                validation_errors=e.validation_errors,
            )
            repair_history.append(f"Attempt 1 failed: {e.message}")
            last_error = repair_history[-1]
            last_validation_errors = e.validation_errors

        # Bounded repair loop: strictly max 2 attempts

        for repair_attempt in range(1, self.max_repair_attempts + 1):
            logger.info(
                "Executing repair attempt",
                repair_attempt=repair_attempt,
                max_repairs=self.max_repair_attempts,
            )

            repair_template = self.prompt_loader.load_prompt("repair", 1)
            repair_kwargs = {
                "original_output": current_text,
                "error_details": json.dumps(
                    last_validation_errors or [{"error": last_error}], indent=2
                ),
                "schema": kwargs["schema"],
            }
            repair_system = repair_template.render_system(**repair_kwargs)
            repair_user = repair_template.render_user(**repair_kwargs)

            repair_req = LLMRequest(
                messages=[LLMMessage(role="user", content=repair_user)],
                system=repair_system,
                model=repair_template.model,
                temperature=repair_template.temperature,
                max_tokens=repair_template.max_tokens,
                call_type="schema_repair",
                tenant_id=tenant_id,
            )

            repair_resp = await self.router.execute(repair_req, session=session)
            current_text = repair_resp.text

            try:
                validated = self._parse_and_validate(current_text, schema)
                logger.info(
                    "Schema repair successful",
                    successful_attempt=repair_attempt + 1,
                )
                return StructuredExtractionResult(
                    parsed=validated,
                    raw_text=current_text,
                    attempts=repair_attempt + 1,
                    repair_history=repair_history,
                    success=True,
                    error=None,
                )
            except StructuredOutputError as err:
                logger.warning(
                    "Repair attempt failed",
                    repair_attempt=repair_attempt,
                    error=str(err),
                )
                repair_history.append(f"Repair attempt {repair_attempt} failed: {err.message}")
                last_error = err.message
                last_validation_errors = err.validation_errors

        # Both repair attempts exhausted (total 3 tries: initial + 2 repairs)
        total_attempts = 1 + self.max_repair_attempts
        logger.error(
            "Max bounded repair attempts exceeded. Routing to human review.",
            total_attempts=total_attempts,
            history=repair_history,
        )

        if raise_on_failure:
            raise MaxRepairAttemptsExceeded(
                message=f"Failed to validate output after {total_attempts} attempts. "
                "Output routed to human review.",
                attempts=total_attempts,
                raw_output=current_text,
                validation_errors=last_validation_errors,
                repair_history=repair_history,
            )

        return StructuredExtractionResult(
            parsed=None,
            raw_text=current_text,
            attempts=total_attempts,
            repair_history=repair_history,
            success=False,
            error=last_error,
        )
