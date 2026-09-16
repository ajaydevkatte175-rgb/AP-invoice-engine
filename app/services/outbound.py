import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.outbound import (
    Customer,
    InvoiceCounter,
    IssuedInvoice,
    IssuedLineItem,
    TenantProfile,
)
from app.schemas.outbound import (
    CustomerCreate,
    CustomerUpdate,
    IssuedInvoiceCreate,
    IssuedInvoiceUpdate,
)

# ---------------------------------------------------------------------------
# Invoice Counter Service (Rule 5: Gapless numbering with SELECT FOR UPDATE)
# ---------------------------------------------------------------------------


async def get_next_invoice_number(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    series: str = "DEFAULT",
) -> str:
    """Acquire the next sequential, gapless invoice number using SELECT ... FOR UPDATE."""
    # Query tenant profile to fetch customized invoice prefix if configured
    profile_stmt = select(TenantProfile).where(TenantProfile.tenant_id == tenant_id)
    profile_res = await db.execute(profile_stmt)
    profile = profile_res.scalar_one_or_none()
    prefix = profile.invoice_prefix if profile else "INV-"

    # Row-level lock on the counter row
    counter_stmt = (
        select(InvoiceCounter)
        .where(
            InvoiceCounter.tenant_id == tenant_id,
            InvoiceCounter.series == series,
        )
        .with_for_update()
    )
    counter_res = await db.execute(counter_stmt)
    counter = counter_res.scalar_one_or_none()

    if counter is None:
        counter = InvoiceCounter(
            tenant_id=tenant_id,
            series=series,
            current_value=1,
        )
        db.add(counter)
        next_val = 1
    else:
        counter.current_value += 1
        next_val = counter.current_value

    await db.flush()
    return f"{prefix}{next_val:05d}"


# ---------------------------------------------------------------------------
# Customer Service
# ---------------------------------------------------------------------------


async def create_customer(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    data: CustomerCreate,
) -> Customer:
    customer = Customer(
        tenant_id=tenant_id,
        name=data.name,
        email=data.email,
        address=data.address,
        tax_id=data.tax_id,
        phone=data.phone,
        default_currency=data.default_currency,
        is_active=data.is_active,
    )
    db.add(customer)
    await db.flush()
    await db.refresh(customer)
    return customer


async def get_customer(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    customer_id: uuid.UUID,
) -> Customer | None:
    stmt = select(Customer).where(
        Customer.tenant_id == tenant_id,
        Customer.id == customer_id,
    )
    res = await db.execute(stmt)
    return res.scalar_one_or_none()


async def list_customers(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    is_active: bool | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[Customer], int]:
    filters = [Customer.tenant_id == tenant_id]
    if is_active is not None:
        filters.append(Customer.is_active == is_active)

    count_stmt = select(func.count(Customer.id)).where(*filters)
    total_count = (await db.execute(count_stmt)).scalar() or 0

    offset = (page - 1) * page_size
    query_stmt = (
        select(Customer)
        .where(*filters)
        .order_by(Customer.name.asc())
        .offset(offset)
        .limit(page_size)
    )
    items = (await db.execute(query_stmt)).scalars().all()
    return list(items), total_count


async def update_customer(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    customer_id: uuid.UUID,
    data: CustomerUpdate,
) -> Customer | None:
    customer = await get_customer(db, tenant_id, customer_id)
    if not customer:
        return None

    update_dict = data.model_dump(exclude_unset=True)
    for field, val in update_dict.items():
        setattr(customer, field, val)

    await db.flush()
    await db.refresh(customer)
    return customer


async def delete_customer(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    customer_id: uuid.UUID,
) -> bool:
    customer = await get_customer(db, tenant_id, customer_id)
    if not customer:
        return False

    await db.delete(customer)
    await db.flush()
    return True


# ---------------------------------------------------------------------------
# Issued Invoice Service (Rule 4: totals calculated in Python)
# ---------------------------------------------------------------------------


async def create_issued_invoice(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    data: IssuedInvoiceCreate,
) -> IssuedInvoice:
    # 1. Deterministically calculate line item totals in Python (Rule 4)
    computed_subtotal = Decimal("0.00")
    line_models = []

    for item in data.line_items:
        qty = Decimal(str(item.quantity))
        price = Decimal(str(item.unit_price))
        tot = (qty * price).quantize(Decimal("0.01"))
        computed_subtotal += tot

        line_models.append(
            IssuedLineItem(
                tenant_id=tenant_id,
                line_number=item.line_number,
                description=item.description,
                quantity=qty,
                unit_price=price,
                total_amount=tot,
                tax_rate=item.tax_rate,
                tax_amount=(tot * item.tax_rate).quantize(Decimal("0.01"))
                if item.tax_rate
                else Decimal("0.00"),
            )
        )

    # Recompute subtotal, tax_amount, total_amount deterministically in Python
    tax_rate = Decimal(str(data.tax_rate))
    computed_tax_amount = (computed_subtotal * tax_rate).quantize(Decimal("0.01"))
    computed_total_amount = computed_subtotal + computed_tax_amount

    # 2. Sequential gapless numbering if invoice_number not explicitly provided (Rule 5)
    invoice_num = data.invoice_number
    if not invoice_num:
        invoice_num = await get_next_invoice_number(db, tenant_id)

    issued_inv = IssuedInvoice(
        tenant_id=tenant_id,
        customer_id=data.customer_id,
        invoice_number=invoice_num,
        invoice_date=data.invoice_date,
        due_date=data.due_date,
        currency=data.currency,
        subtotal=computed_subtotal,
        tax_rate=tax_rate,
        tax_amount=computed_tax_amount,
        total_amount=computed_total_amount,
        notes=data.notes,
        payment_terms=data.payment_terms,
        status="draft",
        line_items=line_models,
    )

    db.add(issued_inv)
    await db.flush()
    await db.refresh(issued_inv)
    return issued_inv


async def get_issued_invoice(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    invoice_id: uuid.UUID,
    include_relations: bool = False,
) -> IssuedInvoice | None:
    stmt = select(IssuedInvoice).where(
        IssuedInvoice.tenant_id == tenant_id,
        IssuedInvoice.id == invoice_id,
    )
    if include_relations:
        stmt = stmt.options(
            selectinload(IssuedInvoice.customer),
            selectinload(IssuedInvoice.line_items),
        )
    res = await db.execute(stmt)
    return res.scalar_one_or_none()


async def list_issued_invoices(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    status: str | None = None,
    customer_id: uuid.UUID | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[IssuedInvoice], int]:
    filters = [IssuedInvoice.tenant_id == tenant_id]
    if status:
        filters.append(IssuedInvoice.status == status)
    if customer_id:
        filters.append(IssuedInvoice.customer_id == customer_id)

    count_stmt = select(func.count(IssuedInvoice.id)).where(*filters)
    total_count = (await db.execute(count_stmt)).scalar() or 0

    offset = (page - 1) * page_size
    query_stmt = (
        select(IssuedInvoice)
        .where(*filters)
        .order_by(IssuedInvoice.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    items = (await db.execute(query_stmt)).scalars().all()
    return list(items), total_count


async def update_issued_invoice(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    invoice_id: uuid.UUID,
    data: IssuedInvoiceUpdate,
) -> IssuedInvoice | None:
    invoice = await get_issued_invoice(db, tenant_id, invoice_id)
    if not invoice:
        return None

    update_dict = data.model_dump(exclude_unset=True)
    for field, val in update_dict.items():
        setattr(invoice, field, val)

    await db.flush()
    await db.refresh(invoice)
    return invoice


async def update_issued_invoice_status(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    invoice_id: uuid.UUID,
    new_status: str,
) -> IssuedInvoice | None:
    invoice = await get_issued_invoice(db, tenant_id, invoice_id)
    if not invoice:
        return None

    invoice.status = new_status
    await db.flush()
    await db.refresh(invoice)
    return invoice
