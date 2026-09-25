"""API endpoints for outbound accounts receivable invoice issuance and management."""

import uuid
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import verify_api_key
from app.core.db import get_db
from app.core.logging import get_logger
from app.core.middleware import tenant_dependency
from app.models.outbound import IssuedInvoice, IssuedLineItem, TenantProfile
from app.schemas.outbound import (
    IssuedInvoiceCreate,
    IssuedInvoiceDetailResponse,
    IssuedInvoiceResponse,
    IssuedInvoiceStatusUpdate,
)
from app.services.issuing import (
    DraftInvoiceService,
    compute_totals,
    generate_invoice_pdf,
    next_invoice_number,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/issuing", tags=["Outbound Invoice Issuance"])
draft_service = DraftInvoiceService()


class DraftFromTextRequest(BaseModel):
    """Request payload to parse natural language into draft invoice fields."""

    prompt: str = Field(
        ...,
        min_length=3,
        description="Natural language description of the invoice to draft",
        examples=[
            "Bill Acme Corp for 5 hours of consulting at $150/hr and 1 server setup for $500"
        ],
    )


class DraftLineItemResponse(BaseModel):
    line_number: int
    description: str
    quantity: Decimal
    unit_price: Decimal
    total_amount: Decimal
    tax_rate: Decimal
    tax_amount: Decimal


class DraftInvoiceResponse(BaseModel):
    customer_name: str | None = None
    currency: str = "USD"
    invoice_date: str
    due_date: str
    payment_terms: str | None = "Net 30"
    notes: str | None = None
    subtotal: Decimal
    tax_rate: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    line_items: list[DraftLineItemResponse] = Field(default_factory=list)


@router.post(
    "/draft-from-text",
    response_model=DraftInvoiceResponse,
    status_code=status.HTTP_200_OK,
    summary="Draft invoice from natural language text",
    description="Parses unstructured text to draft invoice fields, then recomputes all totals strictly in Python with pure Decimal arithmetic.",
)
async def draft_from_text(
    req: DraftFromTextRequest,
    _api_key: str = Depends(verify_api_key),
    tenant_id: uuid.UUID = Depends(tenant_dependency),
    db: AsyncSession = Depends(get_db),
) -> DraftInvoiceResponse:
    """Parse text into draft invoice fields with python-computed totals."""
    try:
        draft = await draft_service.parse_draft_invoice(
            text=req.prompt,
            tenant_id=tenant_id,
            session=db,
        )
        return DraftInvoiceResponse(**draft)
    except Exception as exc:
        logger.error("Failed to parse draft from text", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to parse draft from text: {exc}",
        )


@router.post(
    "/invoices",
    response_model=IssuedInvoiceDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new outbound issued invoice",
    description="Calculates deterministic totals, assigns a gapless invoice number via SELECT ... FOR UPDATE, and stores line items.",
)
async def create_issued_invoice(
    payload: IssuedInvoiceCreate,
    _api_key: str = Depends(verify_api_key),
    tenant_id: uuid.UUID = Depends(tenant_dependency),
    db: AsyncSession = Depends(get_db),
) -> IssuedInvoiceDetailResponse:
    """Create a new outbound invoice with gapless numbering and python-calculated totals."""
    # 1. Deterministic totals calculation in Python (Rule 4)
    computed = compute_totals(
        [item.model_dump() for item in payload.line_items],
        default_tax_rate=payload.tax_rate,
    )

    # 2. Gapless invoice numbering (Rule 5)
    invoice_num = payload.invoice_number
    if not invoice_num:
        invoice_num = await next_invoice_number(db, tenant_id=tenant_id)

    # 3. Create IssuedInvoice record
    issued_inv = IssuedInvoice(
        tenant_id=tenant_id,
        customer_id=payload.customer_id,
        invoice_number=invoice_num,
        invoice_date=payload.invoice_date,
        due_date=payload.due_date,
        currency=payload.currency.upper(),
        subtotal=computed.subtotal,
        tax_rate=computed.tax_rate,
        tax_amount=computed.tax_amount,
        total_amount=computed.total_amount,
        notes=payload.notes,
        payment_terms=payload.payment_terms,
        status="draft",
    )
    db.add(issued_inv)
    await db.flush()

    # 4. Create line items
    for item in computed.line_items:
        db_line_item = IssuedLineItem(
            tenant_id=tenant_id,
            issued_invoice_id=issued_inv.id,
            line_number=item.line_number,
            description=item.description,
            quantity=item.quantity,
            unit_price=item.unit_price,
            total_amount=item.total_amount,
            tax_rate=item.tax_rate,
            tax_amount=item.tax_amount,
        )
        db.add(db_line_item)

    await db.commit()

    # Reload with relationships
    stmt = (
        select(IssuedInvoice)
        .options(selectinload(IssuedInvoice.line_items), selectinload(IssuedInvoice.customer))
        .where(IssuedInvoice.id == issued_inv.id, IssuedInvoice.tenant_id == tenant_id)
    )
    res = await db.execute(stmt)
    full_inv = res.scalar_one()

    return IssuedInvoiceDetailResponse.model_validate(full_inv)


@router.get(
    "/invoices",
    response_model=dict[str, Any],
    summary="List outbound issued invoices",
)
async def list_issued_invoices(
    status_filter: str | None = Query(default=None, alias="status"),
    customer_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _api_key: str = Depends(verify_api_key),
    tenant_id: uuid.UUID = Depends(tenant_dependency),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List issued invoices with filtering and pagination."""
    stmt = (
        select(IssuedInvoice)
        .options(selectinload(IssuedInvoice.customer))
        .where(IssuedInvoice.tenant_id == tenant_id)
        .order_by(desc(IssuedInvoice.created_at))
    )
    if status_filter:
        stmt = stmt.where(IssuedInvoice.status == status_filter)
    if customer_id:
        stmt = stmt.where(IssuedInvoice.customer_id == customer_id)

    # Count total
    count_stmt = select(IssuedInvoice.id).where(IssuedInvoice.tenant_id == tenant_id)
    if status_filter:
        count_stmt = count_stmt.where(IssuedInvoice.status == status_filter)
    if customer_id:
        count_stmt = count_stmt.where(IssuedInvoice.customer_id == customer_id)
    count_res = await db.execute(count_stmt)
    total_count = len(count_res.scalars().all())

    stmt = stmt.limit(limit).offset(offset)
    result = await db.execute(stmt)
    invoices = result.scalars().all()

    return {
        "items": [IssuedInvoiceResponse.model_validate(inv) for inv in invoices],
        "total": total_count,
        "limit": limit,
        "offset": offset,
    }


@router.get(
    "/invoices/{invoice_id}",
    response_model=IssuedInvoiceDetailResponse,
    summary="Get single issued invoice with line items",
)
async def get_issued_invoice(
    invoice_id: uuid.UUID,
    _api_key: str = Depends(verify_api_key),
    tenant_id: uuid.UUID = Depends(tenant_dependency),
    db: AsyncSession = Depends(get_db),
) -> IssuedInvoiceDetailResponse:
    """Retrieve full details of an issued invoice."""
    stmt = (
        select(IssuedInvoice)
        .options(selectinload(IssuedInvoice.line_items), selectinload(IssuedInvoice.customer))
        .where(IssuedInvoice.id == invoice_id, IssuedInvoice.tenant_id == tenant_id)
    )
    res = await db.execute(stmt)
    inv = res.scalar_one_or_none()
    if not inv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Issued invoice {invoice_id} not found.",
        )
    return IssuedInvoiceDetailResponse.model_validate(inv)


@router.patch(
    "/invoices/{invoice_id}/status",
    response_model=IssuedInvoiceResponse,
    summary="Update status of an issued invoice",
)
async def update_issued_invoice_status(
    invoice_id: uuid.UUID,
    status_update: IssuedInvoiceStatusUpdate,
    _api_key: str = Depends(verify_api_key),
    tenant_id: uuid.UUID = Depends(tenant_dependency),
    db: AsyncSession = Depends(get_db),
) -> IssuedInvoiceResponse:
    """Update status of an issued invoice (e.g. draft, issued, paid, cancelled)."""
    stmt = select(IssuedInvoice).where(
        IssuedInvoice.id == invoice_id, IssuedInvoice.tenant_id == tenant_id
    )
    res = await db.execute(stmt)
    inv = res.scalar_one_or_none()
    if not inv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Issued invoice {invoice_id} not found.",
        )

    valid_statuses = {"draft", "issued", "paid", "cancelled"}
    new_status = status_update.status.lower()
    if new_status not in valid_statuses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid status '{new_status}'. Allowed: {sorted(valid_statuses)}",
        )

    inv.status = new_status
    await db.commit()
    await db.refresh(inv)
    return IssuedInvoiceResponse.model_validate(inv)


@router.get(
    "/invoices/{invoice_id}/pdf",
    summary="Download or stream A4 PDF for issued invoice",
)
async def get_invoice_pdf(
    invoice_id: uuid.UUID,
    _api_key: str = Depends(verify_api_key),
    tenant_id: uuid.UUID = Depends(tenant_dependency),
    db: AsyncSession = Depends(get_db),
):
    """Generate and stream an A4 PDF document for an issued invoice."""
    stmt = (
        select(IssuedInvoice)
        .options(selectinload(IssuedInvoice.line_items), selectinload(IssuedInvoice.customer))
        .where(IssuedInvoice.id == invoice_id, IssuedInvoice.tenant_id == tenant_id)
    )
    res = await db.execute(stmt)
    inv = res.scalar_one_or_none()
    if not inv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Issued invoice {invoice_id} not found.",
        )

    # Fetch tenant profile
    prof_stmt = select(TenantProfile).where(TenantProfile.tenant_id == tenant_id)
    prof_res = await db.execute(prof_stmt)
    profile = prof_res.scalar_one_or_none()
    company_dict = (
        {
            "company_name": profile.company_name,
            "address": profile.address,
            "tax_id": profile.tax_id,
            "email": profile.email,
            "phone": profile.phone,
            "bank_details": profile.bank_details,
        }
        if profile
        else {}
    )

    customer_dict = (
        {
            "name": inv.customer.name,
            "address": inv.customer.address,
            "tax_id": inv.customer.tax_id,
            "email": inv.customer.email,
            "phone": inv.customer.phone,
        }
        if inv.customer
        else {}
    )

    invoice_dict = {
        "invoice_number": inv.invoice_number,
        "invoice_date": inv.invoice_date,
        "due_date": inv.due_date,
        "currency": inv.currency,
        "subtotal": inv.subtotal,
        "tax_rate": inv.tax_rate,
        "tax_amount": inv.tax_amount,
        "total_amount": inv.total_amount,
        "status": inv.status,
        "notes": inv.notes,
        "payment_terms": inv.payment_terms,
        "line_items": [
            {
                "line_number": li.line_number,
                "description": li.description,
                "quantity": li.quantity,
                "unit_price": li.unit_price,
                "total_amount": li.total_amount,
            }
            for li in inv.line_items
        ],
    }

    pdf_bytes = generate_invoice_pdf(
        invoice_data=invoice_dict,
        company_profile=company_dict,
        customer_data=customer_dict,
    )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{inv.invoice_number}.pdf"',
        },
    )
