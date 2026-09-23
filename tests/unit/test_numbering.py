"""Unit tests for gapless, concurrency-safe invoice numbering per tenant.

NON-NEGOTIABLE RULE 5:
Invoice numbers must be gapless and unique per tenant.
Use a counter row with SELECT ... FOR UPDATE. Never COUNT(*) + 1.
"""

import asyncio
import uuid

import pytest
from sqlalchemy import text

from app.core.db import AsyncSessionLocal
from app.services.issuing.numbering import next_invoice_number


@pytest.mark.asyncio
async def test_sequential_invoice_numbering():
    """Verify that multiple consecutive calls generate sequential, gapless numbers."""
    tenant_id = uuid.uuid4()

    async with AsyncSessionLocal() as session:
        # Enforce RLS tenant context
        await session.execute(text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))

        num1 = await next_invoice_number(session, tenant_id=tenant_id, prefix="INV-")
        assert num1 == "INV-00001"

        num2 = await next_invoice_number(session, tenant_id=tenant_id, prefix="INV-")
        assert num2 == "INV-00002"

        num3 = await next_invoice_number(session, tenant_id=tenant_id, prefix="INV-")
        assert num3 == "INV-00003"

        await session.commit()


@pytest.mark.asyncio
async def test_tenant_isolation_numbering():
    """Verify that separate tenants have completely independent counter sequences."""
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()

    async with AsyncSessionLocal() as session_a:
        await session_a.execute(text(f"SET LOCAL app.current_tenant = '{tenant_a}'"))
        num_a1 = await next_invoice_number(session_a, tenant_id=tenant_a, prefix="A-")
        assert num_a1 == "A-00001"
        await session_a.commit()

    async with AsyncSessionLocal() as session_b:
        await session_b.execute(text(f"SET LOCAL app.current_tenant = '{tenant_b}'"))
        num_b1 = await next_invoice_number(session_b, tenant_id=tenant_b, prefix="B-")
        assert num_b1 == "B-00001"
        await session_b.commit()


@pytest.mark.asyncio
async def test_concurrent_invoice_numbering_gapless():
    """Verify that concurrent transactions serialize via SELECT ... FOR UPDATE with no duplicates or gaps."""
    tenant_id = uuid.uuid4()
    concurrency_count = 10

    async def _generate_one() -> str:
        async with AsyncSessionLocal() as session:
            await session.execute(text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))
            num = await next_invoice_number(session, tenant_id=tenant_id, prefix="INV-")
            await session.commit()
            return num

    # Run 10 concurrent numbering requests
    results = await asyncio.gather(*[_generate_one() for _ in range(concurrency_count)])

    # All generated invoice numbers must be unique
    assert len(results) == concurrency_count
    assert len(set(results)) == concurrency_count

    # Numbers must be strictly gapless from INV-00001 to INV-00010
    expected = [f"INV-{i:05d}" for i in range(1, concurrency_count + 1)]
    assert sorted(results) == expected

