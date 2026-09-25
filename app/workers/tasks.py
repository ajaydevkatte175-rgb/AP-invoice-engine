import uuid
from decimal import Decimal
from pathlib import Path

import structlog
from sqlalchemy import select, text

from app.core.config import settings
from app.core.db import AsyncSessionLocal
from app.models.inbound import Document, Invoice, LineItem, ReviewItem, ValidationFlag
from app.models.system import AuditLog
from app.services.extract import extract_invoice_from_document
from app.services.preprocess import preprocess_document
from app.services.validate import (
    InvoiceValidationData,
    LineItemValidationData,
    validate_invoice_data,
)

logger = structlog.get_logger(__name__)


async def process_document_pipeline(
    ctx: dict,
    document_id: str,
    tenant_id: str,
    correlation_id: str | None = None,
) -> dict:
    """Full asynchronous document processing pipeline.

    PIPELINE SEQUENCE:
    1. Preprocess: Enforce SHA256 and 5-page budget limit (Rule 11).
    2. Extract: Structured extraction with bounded repair loop (Rule 10).
    3. Merge & Confidence: Score field confidence.
    4. Validate: Deterministic arithmetic & currency check in pure Python (Rule 2).
    5. Logical Duplicate Check: Check (vendor_name, invoice_number) (Rule 12).
    6. Persist: Save Invoice, LineItems, ValidationFlags.
    7. Route: Set status to COMPLETE or NEEDS_REVIEW + create ReviewItem.
    8. Audit Log: Append immutable record.
    Thread correlation ID through everything.
    """
    corr_id = correlation_id or str(uuid.uuid4())
    doc_uuid = uuid.UUID(document_id)
    tenant_uuid = uuid.UUID(tenant_id)

    structlog.contextvars.bind_contextvars(
        correlation_id=corr_id,
        document_id=document_id,
        tenant_id=tenant_id,
    )

    logger.info("Initiating document processing pipeline", step="START")

    async with AsyncSessionLocal() as session:
        # Enforce PostgreSQL Row-Level Security tenant context
        await session.execute(text(f"SET LOCAL app.current_tenant = '{tenant_uuid}'"))

        # Retrieve Document
        doc_stmt = select(Document).where(
            Document.id == doc_uuid,
            Document.tenant_id == tenant_uuid,
        )
        res = await session.execute(doc_stmt)
        document = res.scalar_one_or_none()

        if not document:
            logger.error("Document not found for processing", doc_id=document_id)
            return {"status": "error", "message": "Document not found"}

        document.status = "PROCESSING"
        await session.commit()

    # STEP 1: Preprocess (outside long-held DB transaction)
    file_path = Path(document.storage_path)
    if not file_path.exists():
        logger.error("Document file missing on disk", file_path=str(file_path))
        async with AsyncSessionLocal() as session:
            await session.execute(text(f"SET LOCAL app.current_tenant = '{tenant_uuid}'"))
            doc_to_fail = await session.get(Document, doc_uuid)
            if doc_to_fail:
                doc_to_fail.status = "FAILED"
                await session.commit()
        return {"status": "error", "message": f"File not found: {file_path}"}

    file_bytes = file_path.read_bytes()
    preprocessed = preprocess_document(
        file_bytes=file_bytes,
        file_path=file_path,
        mime_type=document.mime_type,
    )

    logger.info(
        "Document preprocessed",
        page_count=preprocessed.page_count,
        pages_processed=preprocessed.pages_processed,
        truncated=preprocessed.truncated,
    )

    # STEP 2: Extract
    extracted, confidence = await extract_invoice_from_document(
        document_text=preprocessed.text_content,
        tenant_id=tenant_uuid,
    )

    logger.info(
        "Invoice data extracted",
        vendor_name=extracted.vendor_name,
        invoice_number=extracted.invoice_number,
        total_amount=str(extracted.total_amount),
        confidence=confidence,
    )

    # STEP 3 & 4: Deterministic Validation in pure Python (NON-NEGOTIABLE RULE 2)
    val_input = InvoiceValidationData(
        vendor_name=extracted.vendor_name,
        invoice_number=extracted.invoice_number,
        invoice_date=extracted.invoice_date,
        due_date=extracted.due_date,
        currency=extracted.currency,
        subtotal=extracted.subtotal,
        tax_amount=extracted.tax_amount,
        total_amount=extracted.total_amount,
        line_items=[
            LineItemValidationData(
                line_number=item.line_number or idx,
                description=item.description,
                quantity=item.quantity,
                unit_price=item.unit_price,
                line_total=item.line_total,
            )
            for idx, item in enumerate(extracted.line_items, start=1)
        ],
    )
    val_result = validate_invoice_data(val_input)

    # If document was truncated due to budget guard, record an INFO flag
    if preprocessed.truncated:
        from app.services.validate import ValidationFlagData

        val_result.flags.append(
            ValidationFlagData(
                flag_type="PAGE_BUDGET_GUARD_TRUNCATED",
                severity="INFO",
                field_name="pages",
                message=f"Document exceeded 5 pages ({preprocessed.page_count} total). Processed pages {preprocessed.pages_processed}.",
                details={
                    "total_pages": preprocessed.page_count,
                    "processed": preprocessed.pages_processed,
                },
            )
        )

    # STEP 5: Logical Duplicate Check (NON-NEGOTIABLE RULE 12)
    is_logical_duplicate = False

    async with AsyncSessionLocal() as session:
        await session.execute(text(f"SET LOCAL app.current_tenant = '{tenant_uuid}'"))

        dup_stmt = select(Invoice).where(
            Invoice.tenant_id == tenant_uuid,
            Invoice.vendor_name == extracted.vendor_name,
            Invoice.invoice_number == extracted.invoice_number,
        )
        dup_res = await session.execute(dup_stmt)
        existing_dup = dup_res.scalar_one_or_none()

        if existing_dup:
            is_logical_duplicate = True
            logger.warning(
                "Logical duplicate detected via (vendor_name, invoice_number)",
                vendor_name=extracted.vendor_name,
                invoice_number=extracted.invoice_number,
                existing_invoice_id=str(existing_dup.id),
            )

            from app.services.validate import ValidationFlagData

            val_result.flags.append(
                ValidationFlagData(
                    flag_type="DUPLICATE_INVOICE",
                    severity="WARNING",
                    field_name="invoice_number",
                    message=(
                        f"Logical duplicate: Invoice '{extracted.invoice_number}' from "
                        f"vendor '{extracted.vendor_name}' already exists "
                        f"(ID: {existing_dup.id}, Date: {existing_dup.invoice_date})."
                    ),
                    details={
                        "existing_invoice_id": str(existing_dup.id),
                        "existing_invoice_date": str(existing_dup.invoice_date),
                    },
                )
            )

            # Log DUPLICATE_DETECTED to audit_log
            audit_dup = AuditLog(
                tenant_id=tenant_uuid,
                action="DUPLICATE_DETECTED",
                actor_type="SYSTEM",
                resource_type="invoices",
                resource_id=existing_dup.id,
                changes={
                    "duplicate_invoice_number": extracted.invoice_number,
                    "vendor_name": extracted.vendor_name,
                },
                metadata_json={
                    "correlation_id": corr_id,
                    "document_id": str(document.id),
                },
            )
            session.add(audit_dup)
            await session.commit()

        # STEP 6 & 7: Routing & Persistence
        # Rule 12: Never overwrite existing invoice.
        # Route to review if errors, low confidence, or logical duplicate
        needs_review = (
            val_result.has_errors
            or is_logical_duplicate
            or confidence < settings.CONFIDENCE_THRESHOLD
        )
        invoice_status = "NEEDS_REVIEW" if needs_review else "COMPLETE"

        # Create Invoice
        invoice = Invoice(
            tenant_id=tenant_uuid,
            document_id=doc_uuid,
            vendor_name=extracted.vendor_name,
            vendor_tax_id=extracted.vendor_tax_id,
            vendor_address=extracted.vendor_address,
            invoice_number=extracted.invoice_number,
            invoice_date=extracted.invoice_date,
            due_date=extracted.due_date,
            currency=extracted.currency,
            subtotal=extracted.subtotal,
            tax_amount=extracted.tax_amount,
            total_amount=extracted.total_amount,
            tax_breakdown=extracted.tax_breakdown,
            status=invoice_status,
            extraction_confidence=Decimal(str(round(confidence, 4))),
        )
        session.add(invoice)
        await session.flush()

        # Create Line Items
        for idx, item in enumerate(extracted.line_items, start=1):
            line_item = LineItem(
                tenant_id=tenant_uuid,
                invoice_id=invoice.id,
                line_number=item.line_number or idx,
                description=item.description,
                quantity=item.quantity,
                unit_price=item.unit_price,
                total_amount=item.line_total,
                confidence=Decimal(str(round(item.confidence, 4))) if item.confidence else None,
            )
            session.add(line_item)

        # Create Validation Flags
        for flag in val_result.flags:
            db_flag = ValidationFlag(
                tenant_id=tenant_uuid,
                invoice_id=invoice.id,
                rule_name=flag.flag_type,
                severity=flag.severity,
                field_name=flag.field_name,
                message=flag.message,
            )
            session.add(db_flag)

        # Create Review Item if routed to review queue
        if needs_review:
            if is_logical_duplicate:
                review_reason = "DUPLICATE_INVOICE"
            elif val_result.has_errors:
                review_reason = "ARITHMETIC_MISMATCH"
            else:
                review_reason = "LOW_CONFIDENCE"

            review_item = ReviewItem(
                tenant_id=tenant_uuid,
                invoice_id=invoice.id,
                review_reason=review_reason,
                priority="HIGH" if is_logical_duplicate or val_result.has_errors else "MEDIUM",
                status="PENDING",
            )
            session.add(review_item)

        # Update Document status
        doc = await session.get(Document, doc_uuid)
        if doc:
            doc.status = "PROCESSED"
            doc.page_count = preprocessed.page_count

        # STEP 8: Audit Log
        audit_log = AuditLog(
            tenant_id=tenant_uuid,
            action="INVOICE_PROCESSED",
            actor_type="SYSTEM",
            resource_type="invoices",
            resource_id=invoice.id,
            changes={
                "status": invoice_status,
                "confidence_score": confidence,
            },
            metadata_json={
                "correlation_id": corr_id,
                "document_id": str(doc_uuid),
                "is_logical_duplicate": is_logical_duplicate,
                "flags_count": len(val_result.flags),
            },
        )
        session.add(audit_log)

        await session.commit()

        logger.info(
            "Document processing pipeline completed",
            invoice_id=str(invoice.id),
            status=invoice_status,
            flags_count=len(val_result.flags),
            correlation_id=corr_id,
        )

        return {
            "status": "success",
            "document_id": str(doc_uuid),
            "invoice_id": str(invoice.id),
            "invoice_status": invoice_status,
            "correlation_id": corr_id,
            "flags": [f.model_dump() for f in val_result.flags],
        }
