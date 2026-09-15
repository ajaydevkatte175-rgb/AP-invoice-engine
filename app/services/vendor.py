import uuid
from decimal import Decimal
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inbound import Invoice
from app.schemas.vendor import VendorDetail, VendorSummary


async def list_vendors(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[VendorSummary], int]:
    """Aggregate vendors from inbound invoices with invoice count, total spend, and currency."""
    base_query = (
        select(
            Invoice.vendor_name,
            Invoice.currency,
            func.count(Invoice.id).label("invoice_count"),
            func.coalesce(func.sum(Invoice.total_amount), Decimal("0.00")).label("total_spend"),
            func.max(Invoice.invoice_date).label("last_invoice_date"),
        )
        .where(
            Invoice.tenant_id == tenant_id,
            Invoice.vendor_name.isnot(None),
        )
        .group_by(Invoice.vendor_name, Invoice.currency)
    )

    # Total distinct vendors count
    count_subquery = (
        select(Invoice.vendor_name)
        .where(
            Invoice.tenant_id == tenant_id,
            Invoice.vendor_name.isnot(None),
        )
        .distinct()
        .subquery()
    )
    total_count = (await db.execute(select(func.count()).select_from(count_subquery))).scalar() or 0

    offset = (page - 1) * page_size
    query = base_query.order_by(desc("total_spend")).offset(offset).limit(page_size)
    rows = (await db.execute(query)).all()

    items = [
        VendorSummary(
            vendor_name=r.vendor_name,
            currency=r.currency,
            invoice_count=r.invoice_count,
            total_spend=Decimal(str(r.total_spend)),
            last_invoice_date=r.last_invoice_date,
        )
        for r in rows
    ]
    return items, total_count


async def get_vendor_detail(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    vendor_name: str,
) -> VendorDetail | None:
    """Retrieve vendor aggregate details and recent invoices."""
    stats_query = (
        select(
            func.count(Invoice.id).label("invoice_count"),
            func.coalesce(func.sum(Invoice.total_amount), Decimal("0.00")).label("total_spend"),
            func.max(Invoice.vendor_address).label("vendor_address"),
            func.max(Invoice.vendor_tax_id).label("vendor_tax_id"),
        )
        .where(
            Invoice.tenant_id == tenant_id,
            Invoice.vendor_name == vendor_name,
        )
    )
    stats = (await db.execute(stats_query)).one_or_none()
    if not stats or stats.invoice_count == 0:
        return None

    # Get currencies
    curr_stmt = (
        select(Invoice.currency)
        .where(
            Invoice.tenant_id == tenant_id,
            Invoice.vendor_name == vendor_name,
        )
        .distinct()
    )
    currencies = list((await db.execute(curr_stmt)).scalars().all())

    # Get recent 10 invoices
    invoices_stmt = (
        select(Invoice)
        .where(
            Invoice.tenant_id == tenant_id,
            Invoice.vendor_name == vendor_name,
        )
        .order_by(Invoice.invoice_date.desc().nullslast(), Invoice.created_at.desc())
        .limit(10)
    )
    recent_invoices = list((await db.execute(invoices_stmt)).scalars().all())

    return VendorDetail(
        vendor_name=vendor_name,
        vendor_address=stats.vendor_address,
        vendor_tax_id=stats.vendor_tax_id,
        invoice_count=stats.invoice_count,
        total_spend=Decimal(str(stats.total_spend)),
        currencies=currencies,
        recent_invoices=recent_invoices,
    )

