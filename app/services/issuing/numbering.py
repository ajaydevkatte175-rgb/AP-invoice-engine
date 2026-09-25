"""Gapless, concurrency-safe invoice numbering per tenant.

NON-NEGOTIABLE RULE 5:
Invoice numbers must be gapless and unique per tenant.
Use a counter row with SELECT ... FOR UPDATE. Never COUNT(*) + 1.
"""

import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from app.models.outbound import InvoiceCounter, TenantProfile

logger = structlog.get_logger(__name__)


async def next_invoice_number(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    series: str = "DEFAULT",
    prefix: str | None = None,
) -> str:
    """Generate the next sequential gapless invoice number for a tenant.

    Uses `SELECT ... FOR UPDATE` on `invoice_counters` to prevent race conditions
    under concurrent requests, ensuring strict monotonicity and no skipped numbers.
    """
    # 1. Acquire exclusive row lock on the tenant's counter
    stmt = (
        select(InvoiceCounter)
        .where(
            InvoiceCounter.tenant_id == tenant_id,
            InvoiceCounter.series == series,
        )
        .with_for_update()
    )
    result = await session.execute(stmt)
    counter = result.scalar_one_or_none()

    if counter is None:
        # Initialize counter row for this tenant & series
        counter = InvoiceCounter(
            tenant_id=tenant_id,
            series=series,
            current_value=1,
        )
        session.add(counter)
        await session.flush()
        next_val = 1
        logger.info(
            "Initialized new invoice counter",
            tenant_id=str(tenant_id),
            series=series,
            initial_value=next_val,
        )
    else:
        counter.current_value += 1
        await session.flush()
        next_val = counter.current_value
        logger.info(
            "Incremented invoice counter",
            tenant_id=str(tenant_id),
            series=series,
            new_value=next_val,
        )

    # 2. Determine invoice prefix
    if prefix is None:
        profile_stmt = select(TenantProfile.invoice_prefix).where(
            TenantProfile.tenant_id == tenant_id
        )
        profile_res = await session.execute(profile_stmt)
        db_prefix = profile_res.scalar_one_or_none()
        prefix = db_prefix if db_prefix else "INV-"

    # Format standard 5-digit zero-padded number (e.g. INV-00001)
    invoice_number = f"{prefix}{next_val:05d}"
    return invoice_number

