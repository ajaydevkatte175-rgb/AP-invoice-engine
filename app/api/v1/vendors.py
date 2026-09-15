import uuid
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.middleware import tenant_dependency
from app.schemas.common import PaginatedResponse
from app.schemas.vendor import VendorDetail, VendorSummary
from app.services.vendor import get_vendor_detail, list_vendors

router = APIRouter(prefix="/vendors", tags=["Vendors"])


@router.get("", response_model=PaginatedResponse[VendorSummary])
async def get_vendors(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    tenant_id: uuid.UUID = Depends(tenant_dependency),
    db: AsyncSession = Depends(get_db),
):
    """List vendors with invoice counts, spend summaries, and last invoice dates."""
    items, total = await list_vendors(db, tenant_id, page=page, page_size=page_size)
    total_pages = (total + page_size - 1) // page_size if total > 0 else 0
    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get("/{vendor_name}", response_model=VendorDetail)
async def get_vendor(
    vendor_name: str,
    tenant_id: uuid.UUID = Depends(tenant_dependency),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve detailed vendor spend information and recent invoices."""
    detail = await get_vendor_detail(db, tenant_id, vendor_name)
    if not detail:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Vendor '{vendor_name}' not found for tenant",
        )
    return detail

