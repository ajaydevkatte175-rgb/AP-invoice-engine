import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.inbound import Document, Invoice, LineItem, ReviewItem, ValidationFlag
from app.schemas.inbound import DocumentCreate, InvoiceCreate, InvoiceUpdate

# ---------------------------------------------------------------------------
# Document Service
# ---------------------------------------------------------------------------


async def create_document(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    data: DocumentCreate,
) -> Document:
    doc = Document(
        tenant_id=tenant_id,
        filename=data.filename,
        storage_path=data.storage_path,
        mime_type=data.mime_type,
        file_size_bytes=data.file_size_bytes,
        sha256_hash=data.sha256_hash,
        page_count=data.page_count,
        status=data.status,
    )
    db.add(doc)
    await db.flush()
    await db.refresh(doc)
    return doc


async def get_document(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    document_id: uuid.UUID,
) -> Document | None:
    stmt = select(Document).where(
        Document.tenant_id == tenant_id,
        Document.id == document_id,
    )
    res = await db.execute(stmt)
    return res.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Inbound Invoice Service
# ---------------------------------------------------------------------------


async def create_invoice(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    data: InvoiceCreate,
) -> Invoice:
    # Compute subtotal and totals deterministically if line items provided and values not specified
    computed_subtotal = Decimal("0.00")
    line_item_models = []

    for item in data.line_items:
        qty = Decimal(str(item.quantity))
        price = Decimal(str(item.unit_price))
        tot = (qty * price).quantize(Decimal("0.01"))
        computed_subtotal += tot

        line_item_models.append(
            LineItem(
                tenant_id=tenant_id,
                line_number=item.line_number,
                description=item.description,
                quantity=qty,
                unit_price=price,
                total_amount=tot,
                tax_rate=item.tax_rate,
                tax_amount=item.tax_amount,
                confidence=item.confidence,
                raw_data=item.raw_data,
            )
        )

    subtotal = data.subtotal if data.subtotal is not None else computed_subtotal
    tax_amt = data.tax_amount if data.tax_amount is not None else Decimal("0.00")
    total_amt = data.total_amount if data.total_amount is not None else (subtotal + tax_amt)
    bal_due = data.balance_due if data.balance_due is not None else (total_amt - data.amount_paid)

    invoice = Invoice(
        tenant_id=tenant_id,
        document_id=data.document_id,
        invoice_number=data.invoice_number,
        invoice_date=data.invoice_date,
        due_date=data.due_date,
        vendor_name=data.vendor_name,
        vendor_address=data.vendor_address,
        vendor_tax_id=data.vendor_tax_id,
        customer_name=data.customer_name,
        customer_address=data.customer_address,
        customer_tax_id=data.customer_tax_id,
        currency=data.currency,
        subtotal=subtotal,
        tax_amount=tax_amt,
        total_amount=total_amt,
        amount_paid=data.amount_paid,
        balance_due=bal_due,
        tax_breakdown=data.tax_breakdown,
        status=data.status,
        extraction_confidence=data.extraction_confidence,
        raw_extraction=data.raw_extraction,
        line_items=line_item_models,
    )

    db.add(invoice)
    await db.flush()
    await db.refresh(invoice)
    return invoice


async def get_invoice(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    invoice_id: uuid.UUID,
    include_relations: bool = False,
) -> Invoice | None:
    stmt = select(Invoice).where(
        Invoice.tenant_id == tenant_id,
        Invoice.id == invoice_id,
    )
    if include_relations:
        stmt = stmt.options(
            selectinload(Invoice.document),
            selectinload(Invoice.line_items),
            selectinload(Invoice.validation_flags),
            selectinload(Invoice.review_items),
        )
    res = await db.execute(stmt)
    return res.scalar_one_or_none()


async def list_invoices(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    status: str | None = None,
    vendor_name: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[Invoice], int]:
    filters = [Invoice.tenant_id == tenant_id]

    if status:
        filters.append(Invoice.status == status)
    if vendor_name:
        filters.append(Invoice.vendor_name.ilike(f"%{vendor_name}%"))
    if start_date:
        filters.append(Invoice.invoice_date >= start_date)
    if end_date:
        filters.append(Invoice.invoice_date <= end_date)

    # Count query
    count_stmt = select(func.count(Invoice.id)).where(*filters)
    total_count = (await db.execute(count_stmt)).scalar() or 0

    # Paged query
    offset = (page - 1) * page_size
    query_stmt = (
        select(Invoice)
        .where(*filters)
        .order_by(Invoice.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    items = (await db.execute(query_stmt)).scalars().all()
    return list(items), total_count


async def update_invoice(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    invoice_id: uuid.UUID,
    data: InvoiceUpdate,
) -> Invoice | None:
    invoice = await get_invoice(db, tenant_id, invoice_id)
    if not invoice:
        return None

    update_dict = data.model_dump(exclude_unset=True)
    for field, val in update_dict.items():
        setattr(invoice, field, val)

    await db.flush()
    await db.refresh(invoice)
    return invoice


async def update_invoice_status(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    invoice_id: uuid.UUID,
    new_status: str,
) -> Invoice | None:
    invoice = await get_invoice(db, tenant_id, invoice_id)
    if not invoice:
        return None

    invoice.status = new_status
    await db.flush()
    await db.refresh(invoice)
    return invoice


async def get_invoice_line_items(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    invoice_id: uuid.UUID,
) -> list[LineItem]:
    stmt = (
        select(LineItem)
        .where(
            LineItem.tenant_id == tenant_id,
            LineItem.invoice_id == invoice_id,
        )
        .order_by(LineItem.line_number.asc())
    )
    res = await db.execute(stmt)
    return list(res.scalars().all())


async def get_invoice_validation_flags(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    invoice_id: uuid.UUID,
) -> list[ValidationFlag]:
    stmt = (
        select(ValidationFlag)
        .where(
            ValidationFlag.tenant_id == tenant_id,
            ValidationFlag.invoice_id == invoice_id,
        )
        .order_by(ValidationFlag.created_at.asc())
    )
    res = await db.execute(stmt)
    return list(res.scalars().all())


async def get_invoice_review_items(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    invoice_id: uuid.UUID,
) -> list[ReviewItem]:
    stmt = (
        select(ReviewItem)
        .where(
            ReviewItem.tenant_id == tenant_id,
            ReviewItem.invoice_id == invoice_id,
        )
        .order_by(ReviewItem.created_at.asc())
    )
    res = await db.execute(stmt)
    return list(res.scalars().all())
