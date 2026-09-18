import io
import uuid
from decimal import Decimal
from pathlib import Path

import pypdf
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.inbound import ReviewItem
from app.models.system import AuditLog
from app.services.preprocess import preprocess_document
from app.services.validate import (
    InvoiceValidationData,
    LineItemValidationData,
    validate_invoice_data,
)
from app.workers.tasks import process_document_pipeline


@pytest.mark.asyncio
async def test_api_key_authentication():
    transport = ASGITransport(app=app)
    tenant = uuid.uuid4()
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Missing API key returns 401
        res_missing = await client.post(
            "/documents",
            headers={"X-Tenant-ID": str(tenant)},
            files={"file": ("test.pdf", b"%PDF-1.4 dummy", "application/pdf")},
        )
        assert res_missing.status_code == 401

        # 2. Invalid API key returns 401
        res_invalid = await client.post(
            "/documents",
            headers={"X-Tenant-ID": str(tenant), "X-API-Key": "wrong_key_123"},
            files={"file": ("test.pdf", b"%PDF-1.4 dummy", "application/pdf")},
        )
        assert res_invalid.status_code == 401

        # 3. Valid API key passes auth
        unique_bytes = f"%PDF-1.4 auth dummy {uuid.uuid4()}".encode()
        res_valid = await client.post(
            "/documents",
            headers={"X-Tenant-ID": str(tenant), "X-API-Key": settings.API_KEY},
            files={"file": ("test_auth.pdf", unique_bytes, "application/pdf")},
        )
        assert res_valid.status_code == 202
        data = res_valid.json()
        assert data["is_duplicate"] is False
        assert "document_id" in data


@pytest.mark.asyncio
async def test_sha256_file_duplicate_detection():
    transport = ASGITransport(app=app)
    tenant = uuid.uuid4()
    unique_content = f"%PDF-1.4 unique content {uuid.uuid4()}".encode()

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"X-Tenant-ID": str(tenant), "X-API-Key": settings.API_KEY}

        # First upload: 202 Accepted
        res1 = await client.post(
            "/documents",
            headers=headers,
            files={"file": ("invoice_sample.pdf", unique_content, "application/pdf")},
        )
        assert res1.status_code == 202
        doc_id1 = res1.json()["document_id"]
        assert res1.json()["is_duplicate"] is False

        # Second upload with identical bytes: 200 OK (duplicate detected)
        res2 = await client.post(
            "/documents",
            headers=headers,
            files={"file": ("invoice_sample_copy.pdf", unique_content, "application/pdf")},
        )
        assert res2.status_code == 200
        doc_id2 = res2.json()["document_id"]
        assert res2.json()["is_duplicate"] is True
        assert doc_id1 == doc_id2


@pytest.mark.asyncio
async def test_page_budget_guard_truncation():
    """NON-NEGOTIABLE RULE 11:

    If a PDF exceeds 5 pages, preprocess and extract ONLY the first 3 pages and the last 2 pages.
    """
    # Build an in-memory 7-page PDF
    writer = pypdf.PdfWriter()
    for i in range(7):
        writer.add_blank_page(width=200, height=200)

    pdf_stream = io.BytesIO()
    writer.write(pdf_stream)
    seven_page_bytes = pdf_stream.getvalue()

    result = preprocess_document(
        file_bytes=seven_page_bytes,
        file_path="multipage_test.pdf",
        mime_type="application/pdf",
    )

    assert result.page_count == 7
    assert result.truncated is True
    # First 3 pages (1, 2, 3) and last 2 pages (6, 7)
    assert result.pages_processed == [1, 2, 3, 6, 7]


@pytest.mark.asyncio
async def test_deterministic_validation_arithmetic():
    """NON-NEGOTIABLE RULE 2:

    All arithmetic, date, and currency checks are deterministic Python. Zero AI layer imports.
    """
    # 1. Clean valid invoice
    valid_data = InvoiceValidationData(
        vendor_name="Acme Corp",
        invoice_number="INV-100",
        currency="USD",
        subtotal=Decimal("100.00"),
        tax_amount=Decimal("20.00"),
        total_amount=Decimal("120.00"),
        line_items=[
            LineItemValidationData(
                line_number=1,
                description="Item A",
                quantity=Decimal("2.0000"),
                unit_price=Decimal("50.00"),
                line_total=Decimal("100.00"),
            )
        ],
    )
    res_clean = validate_invoice_data(valid_data)
    assert res_clean.is_valid is True
    assert res_clean.has_errors is False

    # 2. Arithmetic line item mismatch
    bad_item_data = InvoiceValidationData(
        vendor_name="Acme Corp",
        invoice_number="INV-101",
        currency="USD",
        subtotal=Decimal("90.00"),
        tax_amount=Decimal("18.00"),
        total_amount=Decimal("108.00"),
        line_items=[
            LineItemValidationData(
                line_number=1,
                description="Item Bad",
                quantity=Decimal("2.0000"),
                unit_price=Decimal("50.00"),
                line_total=Decimal("90.00"),  # Expected 100.00!
            )
        ],
    )
    res_bad_item = validate_invoice_data(bad_item_data)
    assert res_bad_item.has_errors is True
    assert any(f.flag_type == "LINE_ITEM_MATH_MISMATCH" for f in res_bad_item.flags)

    # 3. Subtotal mismatch
    bad_subtotal_data = InvoiceValidationData(
        vendor_name="Acme Corp",
        invoice_number="INV-102",
        currency="USD",
        subtotal=Decimal("150.00"),  # Printed says 150.00, line totals sum to 100.00
        tax_amount=Decimal("20.00"),
        total_amount=Decimal("170.00"),
        line_items=[
            LineItemValidationData(
                line_number=1,
                description="Item A",
                quantity=Decimal("2.0000"),
                unit_price=Decimal("50.00"),
                line_total=Decimal("100.00"),
            )
        ],
    )
    res_subtotal = validate_invoice_data(bad_subtotal_data)
    assert res_subtotal.has_errors is True
    assert any(f.flag_type == "SUBTOTAL_MISMATCH" for f in res_subtotal.flags)


@pytest.mark.asyncio
async def test_logical_duplicate_detection_pipeline():
    """NON-NEGOTIABLE RULE 12:

    Post-extraction, check (vendor_name, invoice_number).
    Flag as DUPLICATE_INVOICE, route to review, create ReviewItem, do NOT overwrite.
    """
    transport = ASGITransport(app=app)
    pdf_path = Path("data/eval/pdfs/invoice_0000.pdf")
    if not pdf_path.exists():
        pytest.skip("invoice_0000.pdf not found")

    pdf_bytes = pdf_path.read_bytes()
    tenant = uuid.uuid4()

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"X-Tenant-ID": str(tenant), "X-API-Key": settings.API_KEY}

        # Upload document 1 with unique PDF comment
        pdf_bytes_1 = pdf_bytes + f"\n% salt 1 {uuid.uuid4()}".encode()
        res1 = await client.post(
            "/documents",
            headers=headers,
            files={"file": ("test_inv_1.pdf", pdf_bytes_1, "application/pdf")},
        )
        assert res1.status_code == 202
        doc_id1 = res1.json()["document_id"]

        # Process pipeline for document 1 directly
        out1 = await process_document_pipeline({}, document_id=doc_id1, tenant_id=str(tenant))
        assert out1["status"] == "success"
        inv_id1 = out1["invoice_id"]

        # Upload document 2 with different bytes (passes SHA256) but identical invoice number & vendor
        pdf_bytes_2 = pdf_bytes + f"\n% salt 2 {uuid.uuid4()}".encode()
        res2 = await client.post(
            "/documents",
            headers=headers,
            files={"file": ("test_inv_2.pdf", pdf_bytes_2, "application/pdf")},
        )
        assert res2.status_code == 202
        doc_id2 = res2.json()["document_id"]

        # Process pipeline for document 2
        out2 = await process_document_pipeline({}, document_id=doc_id2, tenant_id=str(tenant))
        assert out2["status"] == "success"
        inv_id2 = out2["invoice_id"]

        # Ensure separate invoices exist (Rule 12: Do NOT overwrite!)
        assert inv_id1 != inv_id2

        # Verify second invoice was marked NEEDS_REVIEW due to logical duplicate
        assert out2["invoice_status"] == "NEEDS_REVIEW"
        assert any(f["flag_type"] == "DUPLICATE_INVOICE" for f in out2["flags"])

        # Check ReviewItem and AuditLog in database
        async with AsyncSessionLocal() as session:
            rev_res = await session.execute(
                select(ReviewItem).where(ReviewItem.invoice_id == uuid.UUID(inv_id2))
            )
            review_item = rev_res.scalar_one_or_none()
            assert review_item is not None
            assert review_item.review_reason == "DUPLICATE_INVOICE"

            audit_res = await session.execute(
                select(AuditLog).where(
                    AuditLog.action == "DUPLICATE_DETECTED",
                    AuditLog.resource_id == uuid.UUID(inv_id1),
                )
            )
            audit_entry = audit_res.scalar_one_or_none()
            assert audit_entry is not None


@pytest.mark.asyncio
async def test_get_latest_document_endpoint():
    transport = ASGITransport(app=app)
    pdf_path = Path("data/eval/pdfs/invoice_0001.pdf")
    if not pdf_path.exists():
        pytest.skip("invoice_0001.pdf not found")

    pdf_bytes = pdf_path.read_bytes() + f"\n% salt {uuid.uuid4()}".encode()
    tenant = uuid.uuid4()

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"X-Tenant-ID": str(tenant), "X-API-Key": settings.API_KEY}

        # Upload and process
        res_up = await client.post(
            "/documents",
            headers=headers,
            files={"file": ("test_latest.pdf", pdf_bytes, "application/pdf")},
        )
        assert res_up.status_code == 202
        doc_id = res_up.json()["document_id"]

        await process_document_pipeline({}, document_id=doc_id, tenant_id=str(tenant))

        # Query latest document endpoint
        res = await client.get("/documents/latest", headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert "document" in data
        assert "invoice" in data
        assert "line_items" in data
        assert "validation_flags" in data
        assert data["invoice"] is not None
        assert "vendor_name" in data["invoice"]
        assert "total_amount" in data["invoice"]
        assert len(data["line_items"]) > 0

