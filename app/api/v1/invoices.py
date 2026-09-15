import hashlib
import os
import uuid
from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db
from app.core.middleware import tenant_dependency
from app.schemas.common import PaginatedResponse
from app.schemas.inbound import (
    DocumentCreate,
    DocumentResponse,
    InvoiceCreate,
    InvoiceDetailResponse,
    InvoiceResponse,
    InvoiceStatusUpdate,
    InvoiceUpdate,
    LineItemResponse,
    ReviewItemResponse,
    ValidationFlagResponse,
)
from app.services.inbound import (
    create_document,
    create_invoice,
    get_invoice,
    get_invoice_line_items,
    get_invoice_review_items,
    get_invoice_validation_flags,
    list_invoices,
    update_invoice,
    update_invoice_status,
)

router = APIRouter(prefix="/invoices", tags=["Inbound Invoices"])


@router.post("/upload", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def upload_invoice_document(
    file: UploadFile = File(...),
    tenant_id: uuid.UUID = Depends(tenant_dependency),
    db: AsyncSession = Depends(get_db),
):
    """Upload an inbound invoice file (PDF, PNG, JPG, TIFF) and store metadata."""
    content = await file.read()
    file_size = len(content)

    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    if file_size > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds maximum allowed size of {settings.MAX_UPLOAD_MB}MB",
        )

    # Compute SHA-256 hash (Rule 12: Catch duplicate files)
    sha256_hash = hashlib.sha256(content).hexdigest()

    # Ensure storage directory exists
    os.makedirs(settings.STORAGE_PATH, exist_ok=True)
    saved_filename = f"{tenant_id}_{sha256_hash}_{file.filename}"
    file_path = os.path.join(settings.STORAGE_PATH, saved_filename)

    import anyio

    def _write_file() -> None:
        with open(file_path, "wb") as f:
            f.write(content)

    await anyio.to_thread.run_sync(_write_file)

    doc_data = DocumentCreate(
        filename=file.filename or "invoice.pdf",
        storage_path=file_path,
        mime_type=file.content_type or "application/pdf",
        file_size_bytes=file_size,
        sha256_hash=sha256_hash,
        page_count=None,
        status="uploaded",
    )
    return await create_document(db, tenant_id, doc_data)


@router.post("", response_model=InvoiceResponse, status_code=status.HTTP_201_CREATED)
async def create_inbound_invoice(
    data: InvoiceCreate,
    tenant_id: uuid.UUID = Depends(tenant_dependency),
    db: AsyncSession = Depends(get_db),
):
    """Create a structured inbound invoice with line items."""
    return await create_invoice(db, tenant_id, data)


@router.get("", response_model=PaginatedResponse[InvoiceResponse])
async def get_invoices(
    status: str | None = Query(default=None),
    vendor_name: str | None = Query(default=None),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    tenant_id: uuid.UUID = Depends(tenant_dependency),
    db: AsyncSession = Depends(get_db),
):
    """List inbound invoices with optional status, vendor, and date range filters."""
    items, total = await list_invoices(
        db,
        tenant_id,
        status=status,
        vendor_name=vendor_name,
        start_date=start_date,
        end_date=end_date,
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


@router.get("/{invoice_id}", response_model=InvoiceDetailResponse)
async def get_invoice_by_id(
    invoice_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(tenant_dependency),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve full details of an invoice including line items, flags, and review status."""
    invoice = await get_invoice(db, tenant_id, invoice_id, include_relations=True)
    if not invoice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Invoice '{invoice_id}' not found",
        )
    return invoice


@router.patch("/{invoice_id}", response_model=InvoiceResponse)
async def update_invoice_fields(
    invoice_id: uuid.UUID,
    data: InvoiceUpdate,
    tenant_id: uuid.UUID = Depends(tenant_dependency),
    db: AsyncSession = Depends(get_db),
):
    """Update fields on an inbound invoice."""
    invoice = await update_invoice(db, tenant_id, invoice_id, data)
    if not invoice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Invoice '{invoice_id}' not found",
        )
    return invoice


@router.patch("/{invoice_id}/status", response_model=InvoiceResponse)
async def update_invoice_state(
    invoice_id: uuid.UUID,
    data: InvoiceStatusUpdate,
    tenant_id: uuid.UUID = Depends(tenant_dependency),
    db: AsyncSession = Depends(get_db),
):
    """Update processing status of an inbound invoice."""
    invoice = await update_invoice_status(db, tenant_id, invoice_id, data.status)
    if not invoice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Invoice '{invoice_id}' not found",
        )
    return invoice


@router.get("/{invoice_id}/line-items", response_model=list[LineItemResponse])
async def get_line_items(
    invoice_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(tenant_dependency),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve line items associated with an inbound invoice."""
    return await get_invoice_line_items(db, tenant_id, invoice_id)


@router.get("/{invoice_id}/validation-flags", response_model=list[ValidationFlagResponse])
async def get_validation_flags(
    invoice_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(tenant_dependency),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve deterministic validation flags raised on an invoice."""
    return await get_invoice_validation_flags(db, tenant_id, invoice_id)


@router.get("/{invoice_id}/review-items", response_model=list[ReviewItemResponse])
async def get_review_items(
    invoice_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(tenant_dependency),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve human review queue items for an invoice."""
    return await get_invoice_review_items(db, tenant_id, invoice_id)
