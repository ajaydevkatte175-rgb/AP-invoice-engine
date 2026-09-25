"""Unit tests for outbound invoice issuing API endpoints.

Covers:
- Authentication via X-API-Key
- POST /issuing/draft-from-text (deterministic Python totals)
- POST /issuing/invoices (creation, gapless numbering, deterministic totals)
- GET /issuing/invoices (listing)
- GET /issuing/invoices/{id} (retrieval)
- PATCH /issuing/invoices/{id}/status (status transitions)
- GET /issuing/invoices/{id}/pdf (ReportLab PDF generation and streaming)
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.main import app


@pytest.mark.asyncio
async def test_issuing_auth():
    """Verify that issuing endpoints require valid X-API-Key."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Missing API key -> 401
        res = await client.post(
            "/issuing/draft-from-text",
            json={"prompt": "Bill Acme Corp for consulting"},
        )
        assert res.status_code == 401

        # Invalid API key -> 401
        res = await client.post(
            "/issuing/draft-from-text",
            headers={"X-API-Key": "invalid_key_xyz"},
            json={"prompt": "Bill Acme Corp for consulting"},
        )
        assert res.status_code == 401


@pytest.mark.asyncio
async def test_draft_from_text_endpoint():
    """Verify POST /issuing/draft-from-text returns parsed draft with python totals."""
    tenant_id = str(uuid.uuid4())
    transport = ASGITransport(app=app)

    prompt = "Bill Globex Corporation for 5 hours of consulting at $150 and 2 widgets at $50"

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/issuing/draft-from-text",
            headers={"X-API-Key": settings.API_KEY, "X-Tenant-ID": tenant_id},
            json={"prompt": prompt},
        )
        assert res.status_code == 200
        data = res.json()

        assert "Globex" in data["customer_name"]
        assert len(data["line_items"]) >= 1
        assert Decimal(str(data["subtotal"])) > Decimal("0.00")
        assert Decimal(str(data["total_amount"])) == Decimal(str(data["subtotal"])) + Decimal(
            str(data["tax_amount"])
        )


@pytest.mark.asyncio
async def test_create_and_manage_issued_invoice():
    """Verify outbound invoice lifecycle: create -> list -> get -> update status -> stream PDF."""
    tenant_id = str(uuid.uuid4())
    transport = ASGITransport(app=app)
    today = date.today()

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create customer first via customer API
        cust_res = await client.post(
            "/api/v1/customers",
            headers={"X-Tenant-ID": tenant_id},
            json={
                "name": "Stark Industries",
                "email": "billing@stark.com",
                "address": "10880 Wilshire Blvd, Los Angeles, CA",
                "tax_id": "US-987654321",
            },
        )
        assert cust_res.status_code == 201
        customer_id = cust_res.json()["id"]

        # 2. Create issued invoice with line items
        create_payload = {
            "customer_id": customer_id,
            "invoice_date": today.isoformat(),
            "due_date": (today + timedelta(days=30)).isoformat(),
            "currency": "USD",
            "tax_rate": "0.1000",
            "notes": "Payment due within 30 days.",
            "payment_terms": "Net 30",
            "line_items": [
                {
                    "line_number": 1,
                    "description": "Arc Reactor Blueprint Licensing",
                    "quantity": "1.0000",
                    "unit_price": "5000.00",
                },
                {
                    "line_number": 2,
                    "description": "On-site Technical Consulting",
                    "quantity": "10.0000",
                    "unit_price": "250.00",
                },
            ],
        }

        create_res = await client.post(
            "/issuing/invoices",
            headers={"X-API-Key": settings.API_KEY, "X-Tenant-ID": tenant_id},
            json=create_payload,
        )
        assert create_res.status_code == 201
        inv_data = create_res.json()
        invoice_id = inv_data["id"]

        # Verify gapless invoice numbering
        assert inv_data["invoice_number"].startswith("INV-")
        assert inv_data["invoice_number"] == "INV-00001"

        # Verify deterministic arithmetic (5000 + 2500 = 7500 subtotal, 750 tax, 8250 total)
        assert Decimal(str(inv_data["subtotal"])) == Decimal("7500.00")
        assert Decimal(str(inv_data["tax_amount"])) == Decimal("750.00")
        assert Decimal(str(inv_data["total_amount"])) == Decimal("8250.00")
        assert inv_data["status"] == "draft"
        assert len(inv_data["line_items"]) == 2

        # 3. List issued invoices
        list_res = await client.get(
            "/issuing/invoices",
            headers={"X-API-Key": settings.API_KEY, "X-Tenant-ID": tenant_id},
        )
        assert list_res.status_code == 200
        list_data = list_res.json()
        assert list_data["total"] >= 1
        assert any(item["id"] == invoice_id for item in list_data["items"])

        # 4. Get single issued invoice
        get_res = await client.get(
            f"/issuing/invoices/{invoice_id}",
            headers={"X-API-Key": settings.API_KEY, "X-Tenant-ID": tenant_id},
        )
        assert get_res.status_code == 200
        get_data = get_res.json()
        assert get_data["id"] == invoice_id
        assert get_data["customer"]["name"] == "Stark Industries"

        # 5. Status update: draft -> issued -> paid
        status_res = await client.patch(
            f"/issuing/invoices/{invoice_id}/status",
            headers={"X-API-Key": settings.API_KEY, "X-Tenant-ID": tenant_id},
            json={"status": "issued"},
        )
        assert status_res.status_code == 200
        assert status_res.json()["status"] == "issued"

        # Invalid status should return 400
        bad_status_res = await client.patch(
            f"/issuing/invoices/{invoice_id}/status",
            headers={"X-API-Key": settings.API_KEY, "X-Tenant-ID": tenant_id},
            json={"status": "invalid_status"},
        )
        assert bad_status_res.status_code == 400

        # 6. Stream and download PDF
        pdf_res = await client.get(
            f"/issuing/invoices/{invoice_id}/pdf",
            headers={"X-API-Key": settings.API_KEY, "X-Tenant-ID": tenant_id},
        )
        assert pdf_res.status_code == 200
        assert pdf_res.headers["content-type"] == "application/pdf"
        assert pdf_res.content.startswith(b"%PDF")
        assert len(pdf_res.content) > 1000
