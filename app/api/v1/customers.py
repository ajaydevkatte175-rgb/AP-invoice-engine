import uuid
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.middleware import tenant_dependency
from app.schemas.common import PaginatedResponse
from app.schemas.outbound import CustomerCreate, CustomerResponse, CustomerUpdate
from app.services.outbound import (
    create_customer,
    delete_customer,
    get_customer,
    list_customers,
    update_customer,
)

router = APIRouter(prefix="/customers", tags=["Customers"])


@router.post("", response_model=CustomerResponse, status_code=status.HTTP_201_CREATED)
async def create_new_customer(
    data: CustomerCreate,
    tenant_id: uuid.UUID = Depends(tenant_dependency),
    db: AsyncSession = Depends(get_db),
):
    """Create a new accounts receivable customer record."""
    return await create_customer(db, tenant_id, data)


@router.get("", response_model=PaginatedResponse[CustomerResponse])
async def get_all_customers(
    is_active: bool | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    tenant_id: uuid.UUID = Depends(tenant_dependency),
    db: AsyncSession = Depends(get_db),
):
    """List customers with optional active status filter and pagination."""
    items, total = await list_customers(
        db,
        tenant_id,
        is_active=is_active,
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


@router.get("/{customer_id}", response_model=CustomerResponse)
async def get_customer_by_id(
    customer_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(tenant_dependency),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve customer details by ID."""
    customer = await get_customer(db, tenant_id, customer_id)
    if not customer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Customer '{customer_id}' not found",
        )
    return customer


@router.patch("/{customer_id}", response_model=CustomerResponse)
async def update_customer_by_id(
    customer_id: uuid.UUID,
    data: CustomerUpdate,
    tenant_id: uuid.UUID = Depends(tenant_dependency),
    db: AsyncSession = Depends(get_db),
):
    """Update customer details."""
    customer = await update_customer(db, tenant_id, customer_id, data)
    if not customer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Customer '{customer_id}' not found",
        )
    return customer


@router.delete("/{customer_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_customer_by_id(
    customer_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(tenant_dependency),
    db: AsyncSession = Depends(get_db),
):
    """Delete a customer record."""
    deleted = await delete_customer(db, tenant_id, customer_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Customer '{customer_id}' not found",
        )

