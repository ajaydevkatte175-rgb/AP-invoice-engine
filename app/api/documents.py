import uuid
from pathlib import Path

import structlog
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import verify_api_key
from app.core.config import settings
from app.core.db import get_db
from app.core.middleware import tenant_dependency
from app.models.inbound import Document, Invoice
from app.services.preprocess import compute_sha256
from app.workers.worker import get_arq_redis

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/documents", tags=["Documents"])


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(tenant_dependency),
    _api_key: str = Depends(verify_api_key),
):
    """Upload document for ingestion.

    Performs SHA-256 duplicate detection. If duplicate file exists for tenant,
    returns HTTP 200 with existing document info. If new, saves to storage, creates
    Document record in PENDING state, queues ARQ background pipeline, and returns 202.
    """
    content = await file.read()
    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds maximum upload size of {settings.MAX_UPLOAD_MB}MB",
        )

    sha256_hash = compute_sha256(content)

    # Check for SHA-256 file duplicate within tenant
    dup_stmt = select(Document).where(
        Document.tenant_id == tenant_id,
        Document.sha256_hash == sha256_hash,
    )
    dup_res = await db.execute(dup_stmt)
    existing_doc = dup_res.scalar_one_or_none()

    if existing_doc:
        logger.info(
            "Duplicate file detected via SHA256",
            sha256=sha256_hash,
            existing_doc_id=str(existing_doc.id),
        )
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "message": "Document already uploaded (SHA-256 duplicate)",
                "document_id": str(existing_doc.id),
                "status": existing_doc.status,
                "sha256_hash": existing_doc.sha256_hash,
                "is_duplicate": True,
            },
        )

    # Save to storage directory
    storage_dir = Path(settings.STORAGE_PATH)
    storage_dir.mkdir(parents=True, exist_ok=True)
    safe_filename = f"{tenant_id}_{uuid.uuid4().hex[:8]}_{file.filename}"
    saved_file_path = storage_dir / safe_filename
    saved_file_path.write_bytes(content)

    # Create Document record
    document = Document(
        tenant_id=tenant_id,
        filename=file.filename or "unknown.pdf",
        storage_path=str(saved_file_path),
        mime_type=file.content_type or "application/pdf",
        file_size_bytes=len(content),
        sha256_hash=sha256_hash,
        status="PENDING",
    )
    db.add(document)
    await db.flush()

    correlation_id = str(uuid.uuid4())

    # Queue ARQ background task
    try:
        redis = await get_arq_redis()
        await redis.enqueue_job(
            "process_document_pipeline",
            document_id=str(document.id),
            tenant_id=str(tenant_id),
            correlation_id=correlation_id,
        )
        logger.info(
            "Document queued for processing",
            doc_id=str(document.id),
            correlation_id=correlation_id,
        )
    except Exception as e:
        logger.error("Failed to enqueue ARQ background task", error=str(e))
        # Even if Redis queueing fails, document record exists in DB

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "message": "Document uploaded and queued for processing",
            "document_id": str(document.id),
            "status": "PENDING",
            "correlation_id": correlation_id,
            "sha256_hash": sha256_hash,
            "is_duplicate": False,
        },
    )


@router.get("/latest")
async def get_latest_document(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(tenant_dependency),
    _api_key: str = Depends(verify_api_key),
):
    """Retrieve the most recently uploaded document and its extracted invoice, line items, and flags."""
    stmt = (
        select(Document)
        .where(Document.tenant_id == tenant_id)
        .order_by(Document.created_at.desc())
        .limit(1)
    )
    res = await db.execute(stmt)
    document = res.scalar_one_or_none()

    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No documents found for tenant",
        )

    # Load associated invoice with line items and validation flags
    inv_stmt = (
        select(Invoice)
        .where(
            Invoice.tenant_id == tenant_id,
            Invoice.document_id == document.id,
        )
        .options(
            selectinload(Invoice.line_items),
            selectinload(Invoice.validation_flags),
        )
    )
    inv_res = await db.execute(inv_stmt)
    invoice = inv_res.scalar_one_or_none()

    doc_data = {
        "id": str(document.id),
        "filename": document.filename,
        "status": document.status,
        "sha256_hash": document.sha256_hash,
        "created_at": document.created_at.isoformat() if document.created_at else None,
    }

    if not invoice:
        return {
            "document": doc_data,
            "invoice": None,
            "line_items": [],
            "validation_flags": [],
        }

    return {
        "document": doc_data,
        "invoice": {
            "id": str(invoice.id),
            "vendor_name": invoice.vendor_name,
            "invoice_number": invoice.invoice_number,
            "invoice_date": invoice.invoice_date.isoformat() if invoice.invoice_date else None,
            "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
            "currency": invoice.currency,
            "subtotal": str(invoice.subtotal) if invoice.subtotal is not None else None,
            "tax_amount": str(invoice.tax_amount) if invoice.tax_amount is not None else None,
            "total_amount": str(invoice.total_amount),
            "status": invoice.status,
            "confidence_score": float(invoice.extraction_confidence)
            if invoice.extraction_confidence is not None
            else None,
        },
        "line_items": [
            {
                "line_number": li.line_number,
                "description": li.description,
                "quantity": str(li.quantity),
                "unit_price": str(li.unit_price),
                "line_total": str(li.total_amount),
                "confidence": float(li.confidence) if li.confidence is not None else None,
            }
            for li in invoice.line_items
        ],
        "validation_flags": [
            {
                "flag_type": vf.rule_name,
                "severity": vf.severity,
                "field_name": vf.field_name,
                "message": vf.message,
            }
            for vf in invoice.validation_flags
        ],
    }


@router.get("/{document_id}")
async def get_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(tenant_dependency),
    _api_key: str = Depends(verify_api_key),
):
    """Retrieve document status and details by ID."""
    stmt = select(Document).where(
        Document.id == document_id,
        Document.tenant_id == tenant_id,
    )
    res = await db.execute(stmt)
    document = res.scalar_one_or_none()

    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document {document_id} not found",
        )

    return {
        "id": str(document.id),
        "filename": document.filename,
        "status": document.status,
        "sha256_hash": document.sha256_hash,
        "created_at": document.created_at.isoformat() if document.created_at else None,
    }


@router.get("/{document_id}/file")
async def get_document_file(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(tenant_dependency),
    _api_key: str = Depends(verify_api_key),
):
    """Retrieve raw document file content (PDF or image)."""
    stmt = select(Document).where(
        Document.id == document_id,
        Document.tenant_id == tenant_id,
    )
    res = await db.execute(stmt)
    document = res.scalar_one_or_none()

    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document {document_id} not found",
        )

    file_path = Path(document.storage_path)
    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document file not found on disk",
        )

    content = file_path.read_bytes()
    return Response(
        content=content,
        media_type=document.mime_type or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{document.filename}"'},
    )
