import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.middleware import tenant_dependency
from app.schemas.common import PaginatedResponse
from app.schemas.outbound import (
    IssuedInvoiceCreate,
    IssuedInvoiceDetailResponse,
    IssuedInvoiceResponse,
    IssuedInvoiceStatusUpdate,
    IssuedInvoiceUpdate,
)
from app.services.outbound import (
    create_issued_invoice,
    get_issued_invoice,
    list_issued_invoices,
    update_issued_invoice,
    update_issued_invoice_status,
)

router = APIRouter(prefix="/issued-invoices", tags=["Issued Invoices (AR)"])


@router.post("", response_model=IssuedInvoiceResponse, status_code=status.HTTP_201_CREATED)
async def create_new_issued_invoice(
    data: IssuedInvoiceCreate,
    tenant_id: uuid.UUID = Depends(tenant_dependency),
    db: AsyncSession = Depends(get_db),
):
    """Create and issue an outbound invoice with live calculated totals and gapless numbering."""
    from sqlalchemy.exc import IntegrityError

    try:
        return await create_issued_invoice(db, tenant_id, data)
    except (ValueError, IntegrityError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to create issued invoice: {exc!s}",
        )


@router.get("", response_model=PaginatedResponse[IssuedInvoiceResponse])
async def get_all_issued_invoices(
    status: str | None = Query(default=None),
    customer_id: uuid.UUID | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    tenant_id: uuid.UUID = Depends(tenant_dependency),
    db: AsyncSession = Depends(get_db),
):
    """List issued invoices with status and customer filters."""
    items, total = await list_issued_invoices(
        db,
        tenant_id,
        status=status,
        customer_id=customer_id,
        page=page,
        page_size=page_size,
    )
    total_pages = (total + page_size - 1) // page_size if total > 0 else 0
    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get("/{invoice_id}", response_model=IssuedInvoiceDetailResponse)
async def get_issued_invoice_by_id(
    invoice_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(tenant_dependency),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve complete outbound issued invoice with customer details and line items."""
    invoice = await get_issued_invoice(db, tenant_id, invoice_id, include_relations=True)
    if not invoice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Issued invoice '{invoice_id}' not found",
        )
    return invoice


@router.patch("/{invoice_id}", response_model=IssuedInvoiceResponse)
async def update_issued_invoice_fields(
    invoice_id: uuid.UUID,
    data: IssuedInvoiceUpdate,
    tenant_id: uuid.UUID = Depends(tenant_dependency),
    db: AsyncSession = Depends(get_db),
):
    """Update fields on an issued invoice."""
    invoice = await update_issued_invoice(db, tenant_id, invoice_id, data)
    if not invoice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Issued invoice '{invoice_id}' not found",
        )
    return invoice


@router.patch("/{invoice_id}/status", response_model=IssuedInvoiceResponse)
async def update_issued_invoice_state(
    invoice_id: uuid.UUID,
    data: IssuedInvoiceStatusUpdate,
    tenant_id: uuid.UUID = Depends(tenant_dependency),
    db: AsyncSession = Depends(get_db),
):
    """Update lifecycle status of an issued invoice (draft, issued, paid, cancelled)."""
    invoice = await update_issued_invoice_status(db, tenant_id, invoice_id, data.status)
    if not invoice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Issued invoice '{invoice_id}' not found",
        )
    return invoice
