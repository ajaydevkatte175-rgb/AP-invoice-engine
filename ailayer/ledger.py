import uuid
from decimal import Decimal
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from ailayer.types import LLMRequest, LLMResponse
from app.core.db import async_session_factory
from app.models.system import LLMCall

logger = structlog.get_logger(__name__)


class LLMLedger:
    """Persistence layer writing an immutable record to `llm_calls` for every invocation."""

    @staticmethod
    async def record_success(
        request: LLMRequest,
        response: LLMResponse,
        session: AsyncSession | None = None,
    ) -> LLMCall:
        """Record a successful model completion into `llm_calls`."""
        record = LLMCall(
            tenant_id=request.tenant_id,
            call_type=request.call_type,
            model=response.model,
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
            cost_usd=response.cost_usd,
            duration_ms=response.duration_ms,
            success=True,
            error_message=None,
        )
        await LLMLedger._persist(record, session)
        return record

    @staticmethod
    async def record_failure(
        request: LLMRequest,
        model_name: str,
        error_message: str,
        duration_ms: int = 0,
        session: AsyncSession | None = None,
    ) -> LLMCall:
        """Record a failed model invocation into `llm_calls`."""
        record = LLMCall(
            tenant_id=request.tenant_id,
            call_type=request.call_type,
            model=model_name,
            prompt_tokens=0,
            completion_tokens=0,
            cost_usd=Decimal("0.000000"),
            duration_ms=duration_ms,
            success=False,
            error_message=error_message,
        )
        await LLMLedger._persist(record, session)
        return record

    @staticmethod
    async def _persist(record: LLMCall, session: AsyncSession | None = None) -> None:
        """Persist the LLMCall record to PostgreSQL."""
        try:
            if session is not None:
                session.add(record)
                await session.flush()
            else:
                async with async_session_factory() as new_session:
                    new_session.add(record)
                    await new_session.commit()
        except Exception as e:
            # We log the persistence failure without crashing the pipeline,
            # as logging shouldn't break the user request if the DB connection blips
            logger.error("Failed to write llm_calls ledger record", error=str(e))

