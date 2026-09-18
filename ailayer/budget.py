from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ailayer.config import AIConfig, load_ai_config
from ailayer.errors import BudgetExceededError
from app.core.config import settings
from app.core.db import async_session_factory
from app.models.system import LLMCall

logger = structlog.get_logger(__name__)


class BudgetManager:
    """Manages and enforces hard monthly ceiling on LLM spend before any model call."""

    def __init__(
        self,
        config: AIConfig | None = None,
        monthly_ceiling_usd: Decimal | None = None,
        spend_override_fn: Callable[[], Awaitable[Decimal]] | None = None,
    ) -> None:
        self.config = config or load_ai_config()
        if monthly_ceiling_usd is not None:
            self.monthly_ceiling_usd = monthly_ceiling_usd
        elif self.config.budget.monthly_ceiling_usd is not None:
            self.monthly_ceiling_usd = self.config.budget.monthly_ceiling_usd
        else:
            self.monthly_ceiling_usd = Decimal(str(settings.LLM_MONTHLY_BUDGET_USD))

        self._spend_override_fn = spend_override_fn

    async def get_current_monthly_spend(
        self,
        session: AsyncSession | None = None,
    ) -> Decimal:
        """Query PostgreSQL for total USD cost incurred by LLM calls in current month."""
        if self._spend_override_fn is not None:
            return await self._spend_override_fn()

        now = datetime.now(UTC)
        first_of_month = datetime(now.year, now.month, 1, tzinfo=UTC)

        stmt = select(func.coalesce(func.sum(LLMCall.cost_usd), Decimal("0.000000"))).where(
            LLMCall.created_at >= first_of_month
        )

        try:
            if session is not None:
                res = await session.execute(stmt)
                spend = res.scalar_one_or_none()
                return Decimal(str(spend or "0.000000"))
            else:
                async with async_session_factory() as new_session:
                    res = await new_session.execute(stmt)
                    spend = res.scalar_one_or_none()
                    return Decimal(str(spend or "0.000000"))
        except Exception as e:
            logger.warning(
                "Could not query DB for monthly LLM spend, defaulting to 0", error=str(e)
            )
            return Decimal("0.000000")

    async def check_budget_or_raise(
        self,
        estimated_cost: Decimal = Decimal("0.000000"),
        session: AsyncSession | None = None,
    ) -> None:
        """Pre-call budget check: verify current spend has not reached or exceeded the ceiling."""
        current_spend = await self.get_current_monthly_spend(session=session)
        projected = current_spend + estimated_cost

        if projected >= self.monthly_ceiling_usd:
            logger.error(
                "Monthly LLM budget ceiling exceeded",
                current_spend=str(current_spend),
                ceiling=str(self.monthly_ceiling_usd),
            )
            raise BudgetExceededError(
                f"Monthly LLM budget ceiling of ${self.monthly_ceiling_usd:.2f} exceeded. "
                f"Current spend is ${current_spend:.4f} USD."
            )
