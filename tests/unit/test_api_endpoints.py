import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_customer_crud_lifecycle():
    tenant_id = str(uuid.uuid4())
    headers = {"X-Tenant-ID": tenant_id}

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=headers,
    ) as client:
        # 1. Create customer
        create_payload = {
            "name": "Acme Corp",
            "email": "billing@acme.com",
            "address": "123 Market St, San Francisco, CA",
            "tax_id": "US-123456789",
            "phone": "+1-555-0100",
            "default_currency": "USD",
            "is_active": True,
        }
        res = await client.post("/api/v1/customers", json=create_payload)
        assert res.status_code == 201
        customer = res.json()
        customer_id = customer["id"]
        assert customer["name"] == "Acme Corp"
        assert customer["tenant_id"] == tenant_id

        # 2. Get customer
        res = await client.get(f"/api/v1/customers/{customer_id}")
        assert res.status_code == 200
        assert res.json()["id"] == customer_id

        # 3. List customers
        res = await client.get("/api/v1/customers")
        assert res.status_code == 200
        data = res.json()
        assert data["total"] >= 1
        assert any(c["id"] == customer_id for c in data["items"])

        # 4. Update customer
        res = await client.patch(
            f"/api/v1/customers/{customer_id}",
            json={"phone": "+1-555-9999"},
        )
        assert res.status_code == 200
        assert res.json()["phone"] == "+1-555-9999"

        # 5. Delete customer
        res = await client.delete(f"/api/v1/customers/{customer_id}")
        assert res.status_code == 204

        # 6. Verify deleted
        res = await client.get(f"/api/v1/customers/{customer_id}")
        assert res.status_code == 404


@pytest.mark.asyncio
async def test_inbound_invoice_lifecycle():
    tenant_id = str(uuid.uuid4())
    headers = {"X-Tenant-ID": tenant_id}

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=headers,
    ) as client:
        # 1. Create invoice with line items
        invoice_payload = {
            "invoice_number": "SUP-2026-001",
            "invoice_date": "2026-09-01",
            "due_date": "2026-09-30",
            "vendor_name": "Supplier One LLC",
            "vendor_address": "456 Industrial Blvd",
            "vendor_tax_id": "TAX-998877",
            "currency": "USD",
            "amount_paid": "0.00",
            "tax_breakdown": [{"rate": 0.10, "amount": 25.00, "name": "State VAT"}],
            "line_items": [
                {
                    "line_number": 1,
                    "description": "Consulting Services",
                    "quantity": "2.5000",
                    "unit_price": "100.00",
                    "total_amount": "250.00",
                }
            ],
        }
        res = await client.post("/api/v1/invoices", json=invoice_payload)
        assert res.status_code == 201
        invoice = res.json()
        invoice_id = invoice["id"]
        assert invoice["vendor_name"] == "Supplier One LLC"
        assert invoice["subtotal"] == "250.00"

        # 2. Get invoice detail with line items
        res = await client.get(f"/api/v1/invoices/{invoice_id}")
        assert res.status_code == 200
        detail = res.json()
        assert len(detail["line_items"]) == 1
        assert detail["line_items"][0]["description"] == "Consulting Services"
        assert detail["line_items"][0]["quantity"] == "2.5000"

        # 3. Get line items directly
        res = await client.get(f"/api/v1/invoices/{invoice_id}/line-items")
        assert res.status_code == 200
        items = res.json()
        assert len(items) == 1
        assert items[0]["unit_price"] == "100.00"

        # 4. Update status
        res = await client.patch(
            f"/api/v1/invoices/{invoice_id}/status",
            json={"status": "approved"},
        )
        assert res.status_code == 200
        assert res.json()["status"] == "approved"

        # 5. List invoices with filter
        res = await client.get("/api/v1/invoices", params={"vendor_name": "Supplier One"})
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 1
        assert data["items"][0]["id"] == invoice_id


@pytest.mark.asyncio
async def test_issued_invoice_gapless_numbering_and_recomputation():
    tenant_id = str(uuid.uuid4())
    headers = {"X-Tenant-ID": tenant_id}

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=headers,
    ) as client:
        # Create Customer first
        cust_res = await client.post(
            "/api/v1/customers",
            json={"name": "Big Enterprise Client"},
        )
        assert cust_res.status_code == 201
        customer_id = cust_res.json()["id"]

        # Create issued invoice 1 without explicit invoice_number (auto-generated gapless)
        inv1_payload = {
            "customer_id": customer_id,
            "invoice_date": "2026-09-15",
            "due_date": "2026-10-15",
            "currency": "USD",
            "tax_rate": "0.1000",
            "line_items": [
                {
                    "line_number": 1,
                    "description": "Platform Subscription",
                    "quantity": "2.0000",
                    "unit_price": "500.00",
                },
                {
                    "line_number": 2,
                    "description": "Onboarding Service",
                    "quantity": "1.0000",
                    "unit_price": "250.00",
                },
            ],
        }
        res1 = await client.post("/api/v1/issued-invoices", json=inv1_payload)
        assert res1.status_code == 201
        inv1 = res1.json()

        # Check Python-recomputed totals (Rule 4)
        # subtotal: (2 * 500) + (1 * 250) = 1250.00
        # tax: 1250.00 * 0.10 = 125.00
        # total: 1375.00
        assert inv1["subtotal"] == "1250.00"
        assert inv1["tax_amount"] == "125.00"
        assert inv1["total_amount"] == "1375.00"
        assert inv1["invoice_number"].endswith("00001")

        # Create issued invoice 2 (should be gapless 00002)
        inv2_payload = {
            "customer_id": customer_id,
            "invoice_date": "2026-09-15",
            "due_date": "2026-10-15",
            "currency": "USD",
            "tax_rate": "0.0500",
            "line_items": [
                {
                    "line_number": 1,
                    "description": "Additional user seat",
                    "quantity": "5.0000",
                    "unit_price": "20.00",
                }
            ],
        }
        res2 = await client.post("/api/v1/issued-invoices", json=inv2_payload)
        assert res2.status_code == 201
        inv2 = res2.json()
        assert inv2["invoice_number"].endswith("00002")
        assert inv2["subtotal"] == "100.00"
        assert inv2["tax_amount"] == "5.00"
        assert inv2["total_amount"] == "105.00"


@pytest.mark.asyncio
async def test_vendor_aggregation_and_tenant_isolation():
    tenant_a = str(uuid.uuid4())
    tenant_b = str(uuid.uuid4())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Create invoice under Tenant A
        await client.post(
            "/api/v1/invoices",
            headers={"X-Tenant-ID": tenant_a},
            json={
                "invoice_number": "VEND-A-1",
                "vendor_name": "Global Office Supplies",
                "currency": "USD",
                "line_items": [
                    {
                        "line_number": 1,
                        "description": "Desks",
                        "quantity": "2.0000",
                        "unit_price": "300.00",
                        "total_amount": "600.00",
                    }
                ],
            },
        )

        # Create invoice under Tenant B
        await client.post(
            "/api/v1/invoices",
            headers={"X-Tenant-ID": tenant_b},
            json={
                "invoice_number": "VEND-B-1",
                "vendor_name": "Cloud Hosting Direct",
                "currency": "USD",
                "line_items": [
                    {
                        "line_number": 1,
                        "description": "Server",
                        "quantity": "1.0000",
                        "unit_price": "150.00",
                        "total_amount": "150.00",
                    }
                ],
            },
        )

        # Tenant A asks for vendors
        res_a = await client.get("/api/v1/vendors", headers={"X-Tenant-ID": tenant_a})
        assert res_a.status_code == 200
        vendors_a = [v["vendor_name"] for v in res_a.json()["items"]]
        assert "Global Office Supplies" in vendors_a
        assert "Cloud Hosting Direct" not in vendors_a

        # Tenant B asks for vendors
        res_b = await client.get("/api/v1/vendors", headers={"X-Tenant-ID": tenant_b})
        assert res_b.status_code == 200
        vendors_b = [v["vendor_name"] for v in res_b.json()["items"]]
        assert "Cloud Hosting Direct" in vendors_b
        assert "Global Office Supplies" not in vendors_b
