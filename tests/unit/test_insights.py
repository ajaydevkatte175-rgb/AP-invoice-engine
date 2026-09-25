"""Unit tests for deterministic Insights API endpoints.

Covers:
- /insights/summary
- /insights/vendors (grouped by vendor AND currency)
- /insights/trend
- /insights/price-drift
- /insights/duplicates
- /insights/aging
- /insights/vendor-quality
- Authentication via X-API-Key
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.config import settings
from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.inbound import Invoice, LineItem, ValidationFlag


@pytest.fixture
async def seeded_insights_tenant():
    """Seed sample invoices and line items for insights testing."""
    tenant_id = uuid.uuid4()
    today = date.today()

    async with AsyncSessionLocal() as session:
        await session.execute(text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))

        # Seed 3 invoices from "Alpha Corp" in USD (with price drift on "Standard Widget")
        prices = [Decimal("10.00"), Decimal("12.50"), Decimal("15.00")]
        for i, price in enumerate(prices, start=1):
            inv = Invoice(
                tenant_id=tenant_id,
                invoice_number=f"ALPHA-{i:03d}",
                invoice_date=today - timedelta(days=30 * (4 - i)),
                due_date=today - timedelta(days=10 * (4 - i)),
                vendor_name="Alpha Corp",
                currency="USD",
                subtotal=price * Decimal(2),
                tax_amount=Decimal("0.00"),
                total_amount=price * Decimal(2),
                status="completed",
                extraction_confidence=Decimal("0.9500"),
            )
            session.add(inv)
            await session.flush()

            li = LineItem(
                tenant_id=tenant_id,
                invoice_id=inv.id,
                line_number=1,
                description="Standard Widget",
                quantity=Decimal("2.0000"),
                unit_price=price,
                total_amount=price * Decimal(2),
            )
            session.add(li)

        # Seed an invoice from "Alpha Corp" in EUR (different currency)
        inv_eur = Invoice(
            tenant_id=tenant_id,
            invoice_number="ALPHA-EUR-01",
            invoice_date=today - timedelta(days=15),
            due_date=today + timedelta(days=15),
            vendor_name="Alpha Corp",
            currency="EUR",
            subtotal=Decimal("100.00"),
            total_amount=Decimal("100.00"),
            status="completed",
            extraction_confidence=Decimal("0.9000"),
        )
        session.add(inv_eur)

        # Seed an invoice with needs_review and a duplicate flag
        inv_review = Invoice(
            tenant_id=tenant_id,
            invoice_number="BETA-001",
            invoice_date=today - timedelta(days=45),
            due_date=today - timedelta(days=15),  # Overdue
            vendor_name="Beta Logistics",
            currency="USD",
            subtotal=Decimal("250.00"),
            total_amount=Decimal("250.00"),
            status="needs_review",
            extraction_confidence=Decimal("0.7500"),
        )
        session.add(inv_review)
        await session.flush()

        flag = ValidationFlag(
            tenant_id=tenant_id,
            invoice_id=inv_review.id,
            rule_name="DUPLICATE_WARNING",
            severity="WARNING",
            message="Possible duplicate of invoice BETA-001",
        )
        session.add(flag)

        await session.commit()

    return tenant_id


@pytest.mark.asyncio
async def test_insights_auth():
    """Verify that insights endpoints require valid X-API-Key."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Missing API key -> 401
        res = await client.get("/insights/summary")
        assert res.status_code == 401

        # Invalid API key -> 401
        res = await client.get(
            "/insights/summary",
            headers={"X-API-Key": "invalid_key_xyz"},
        )
        assert res.status_code == 401


@pytest.mark.asyncio
async def test_insights_summary(seeded_insights_tenant):
    """Verify /insights/summary returns headline AP and AR metrics."""
    tenant_id = str(seeded_insights_tenant)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get(
            "/insights/summary",
            headers={"X-API-Key": settings.API_KEY, "X-Tenant-ID": tenant_id},
        )
        assert res.status_code == 200
        data = res.json()

        assert "ap" in data
        assert "ar" in data
        assert data["ap"]["total_invoices"] >= 5
        assert data["ap"]["vendor_count"] >= 2
        assert data["ap"]["review_queue_count"] >= 1
        assert data["ap"]["total_spend"] > 0


@pytest.mark.asyncio
async def test_insights_vendors_grouped_by_vendor_and_currency(seeded_insights_tenant):
    """Verify /insights/vendors groups by BOTH vendor AND currency."""
    tenant_id = str(seeded_insights_tenant)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get(
            "/insights/vendors",
            headers={"X-API-Key": settings.API_KEY, "X-Tenant-ID": tenant_id},
        )
        assert res.status_code == 200
        data = res.json()
        assert "items" in data
        items = data["items"]

        # Alpha Corp should appear twice: once for USD, once for EUR
        alpha_items = [i for i in items if i["vendor_name"] == "Alpha Corp"]
        assert len(alpha_items) == 2
        currencies = {i["currency"] for i in alpha_items}
        assert currencies == {"USD", "EUR"}


@pytest.mark.asyncio
async def test_insights_price_drift(seeded_insights_tenant):
    """Verify /insights/price-drift detects unit price change over 3+ invoices."""
    tenant_id = str(seeded_insights_tenant)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get(
            "/insights/price-drift?min_invoices=3",
            headers={"X-API-Key": settings.API_KEY, "X-Tenant-ID": tenant_id},
        )
        assert res.status_code == 200
        data = res.json()
        assert "items" in data
        items = data["items"]

        # Standard Widget has 3 invoices with prices 10.00, 12.50, 15.00
        widget_drift = next((i for i in items if i["description"] == "Standard Widget"), None)
        assert widget_drift is not None
        assert widget_drift["invoice_count"] == 3
        assert float(widget_drift["min_unit_price"]) == 10.0
        assert float(widget_drift["max_unit_price"]) == 15.0
        # percentage drift = (15 - 10) / 10 * 100 = 50.0%
        assert float(widget_drift["percentage_drift"]) == 50.0


@pytest.mark.asyncio
async def test_insights_duplicates(seeded_insights_tenant):
    """Verify /insights/duplicates returns flagged or duplicate invoices."""
    tenant_id = str(seeded_insights_tenant)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get(
            "/insights/duplicates",
            headers={"X-API-Key": settings.API_KEY, "X-Tenant-ID": tenant_id},
        )
        assert res.status_code == 200
        data = res.json()
        assert "flagged_duplicates" in data
        assert any(d["invoice_number"] == "BETA-001" for d in data["flagged_duplicates"])


@pytest.mark.asyncio
async def test_insights_aging(seeded_insights_tenant):
    """Verify /insights/aging returns overdue aging buckets."""
    tenant_id = str(seeded_insights_tenant)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get(
            "/insights/aging",
            headers={"X-API-Key": settings.API_KEY, "X-Tenant-ID": tenant_id},
        )
        assert res.status_code == 200
        data = res.json()
        assert "buckets" in data
        assert len(data["buckets"]) > 0


@pytest.mark.asyncio
async def test_insights_vendor_quality(seeded_insights_tenant):
    """Verify /insights/vendor-quality computes error and review rates per vendor."""
    tenant_id = str(seeded_insights_tenant)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get(
            "/insights/vendor-quality",
            headers={"X-API-Key": settings.API_KEY, "X-Tenant-ID": tenant_id},
        )
        assert res.status_code == 200
        data = res.json()
        assert "items" in data
        beta_quality = next(
            (i for i in data["items"] if i["vendor_name"] == "Beta Logistics"), None
        )
        assert beta_quality is not None
        assert beta_quality["review_count"] >= 1
        assert float(beta_quality["error_rate_percent"]) == 100.0
