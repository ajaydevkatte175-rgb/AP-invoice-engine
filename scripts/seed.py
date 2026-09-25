"""Seed database with realistic AP/AR invoice data for local testing and UI demonstration."""

import argparse
import asyncio
import hashlib
import random
import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings
from app.core.db import async_session_factory
from app.core.logging import get_logger, setup_logging
from app.models.inbound import Document, Invoice, LineItem, ReviewItem, ValidationFlag
from app.models.outbound import (
    Customer,
    InvoiceCounter,
    IssuedInvoice,
    IssuedLineItem,
    TenantProfile,
)

logger = get_logger(__name__)

DEFAULT_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


async def seed_data(invoice_count: int = 25) -> None:
    """Seed tenant, customers, outbound invoices, and inbound invoices with line items and flags."""
    setup_logging()
    logger.info("Starting database seed", count=invoice_count)

    uploads_dir = Path(settings.STORAGE_PATH)
    uploads_dir.mkdir(parents=True, exist_ok=True)

    async with async_session_factory() as session:  # type: AsyncSession
        # 1. Tenant Profile
        tenant_res = await session.execute(
            select(TenantProfile).where(TenantProfile.tenant_id == DEFAULT_TENANT_ID)
        )
        tenant = tenant_res.scalar_one_or_none()
        if not tenant:
            tenant = TenantProfile(
                tenant_id=DEFAULT_TENANT_ID,
                company_name="Acme Global Operations",
                tax_id="US-123456789",
                default_currency="USD",
                invoice_prefix="INV-",
            )
            session.add(tenant)
            await session.flush()
            logger.info("Created primary tenant profile", tenant_id=str(DEFAULT_TENANT_ID))

        # 2. Invoice Counter
        counter_res = await session.execute(
            select(InvoiceCounter).where(InvoiceCounter.tenant_id == DEFAULT_TENANT_ID)
        )
        counter = counter_res.scalar_one_or_none()
        if not counter:
            counter = InvoiceCounter(
                tenant_id=DEFAULT_TENANT_ID,
                series="DEFAULT",
                current_value=5,
            )
            session.add(counter)
            await session.flush()

        # 3. Customers
        customer_names = [
            ("Globex Corporation", "billing@globex.com", "100 Power Plant Rd, Springfield"),
            ("Initech Software", "ap@initech.com", "4120 Freemont Ave, Austin, TX"),
            ("Umbrella Labs", "finance@umbrella.corp", "500 Raccoon St, Chicago, IL"),
            ("Stark Enterprises", "accounts@stark.com", "10880 Wilshire Blvd, Los Angeles, CA"),
            ("Cyberdyne Systems", "billing@cyberdyne.net", "18144 El Camino Real, Sunnyvale, CA"),
        ]
        created_customers = []
        for name, email, addr in customer_names:
            c_res = await session.execute(
                select(Customer).where(
                    Customer.tenant_id == DEFAULT_TENANT_ID, Customer.name == name
                )
            )
            cust = c_res.scalar_one_or_none()
            if not cust:
                cust = Customer(
                    tenant_id=DEFAULT_TENANT_ID,
                    name=name,
                    email=email,
                    address=addr,
                    tax_id=f"TAX-{random.randint(100000, 999999)}",
                    is_active=True,
                )
                session.add(cust)
                await session.flush()
            created_customers.append(cust)

        # 4. Outbound Issued Invoices
        outbound_specs = [
            ("INV-000001", "paid", Decimal("1500.00"), Decimal("150.00"), Decimal("1650.00")),
            ("INV-000002", "issued", Decimal("3200.00"), Decimal("320.00"), Decimal("3520.00")),
            ("INV-000003", "draft", Decimal("800.00"), Decimal("80.00"), Decimal("880.00")),
            ("INV-000004", "paid", Decimal("4500.00"), Decimal("450.00"), Decimal("4950.00")),
            ("INV-000005", "issued", Decimal("2100.00"), Decimal("210.00"), Decimal("2310.00")),
        ]
        today = date.today()
        for idx, (inv_num, status, subtotal, tax, total) in enumerate(outbound_specs):
            ex_res = await session.execute(
                select(IssuedInvoice).where(
                    IssuedInvoice.tenant_id == DEFAULT_TENANT_ID,
                    IssuedInvoice.invoice_number == inv_num,
                )
            )
            if not ex_res.scalar_one_or_none():
                cust = created_customers[idx % len(created_customers)]
                issued_inv = IssuedInvoice(
                    tenant_id=DEFAULT_TENANT_ID,
                    customer_id=cust.id,
                    invoice_number=inv_num,
                    invoice_date=today - timedelta(days=idx * 7),
                    due_date=today + timedelta(days=30 - idx * 7),
                    subtotal=subtotal,
                    tax_rate=Decimal("0.1000"),
                    tax_amount=tax,
                    total_amount=total,
                    currency="USD",
                    status=status,
                    notes=f"Professional consulting services for {cust.name}",
                )
                session.add(issued_inv)
                await session.flush()

                line = IssuedLineItem(
                    tenant_id=DEFAULT_TENANT_ID,
                    issued_invoice_id=issued_inv.id,
                    line_number=1,
                    description="Professional Engineering & Architectural Advisory",
                    quantity=Decimal("10.0000"),
                    unit_price=subtotal / Decimal("10.0000"),
                    tax_rate=Decimal("0.1000"),
                    tax_amount=tax,
                    total_amount=total,
                )
                session.add(line)

        # 5. Inbound AP Invoices
        vendors = [
            ("Amazon Web Services", "USD", Decimal("0.00")),
            ("Google Cloud Platform", "USD", Decimal("0.00")),
            ("Twilio Inc", "USD", Decimal("0.0825")),
            ("Slack Technologies", "USD", Decimal("0.1000")),
            ("Datadog Monitoring", "USD", Decimal("0.00")),
            ("GitHub Enterprise", "USD", Decimal("0.00")),
            ("Vercel Inc", "USD", Decimal("0.00")),
            ("Office Supplies Direct", "USD", Decimal("0.0800")),
            ("SAP Europe", "EUR", Decimal("0.2000")),
            ("British Telecom", "GBP", Decimal("0.2000")),
        ]

        item_catalogue = [
            ("EC2 Cloud Compute Instances", Decimal("120.00")),
            ("S3 Object Storage GB-Mo", Decimal("25.50")),
            ("SMS Notification API Gateway", Decimal("15.00")),
            ("Developer SaaS Monthly Seat", Decimal("25.00")),  # will test drift: 20, 25, 30
            ("APM Server Monitoring Agent", Decimal("45.00")),
            ("Repository Enterprise License", Decimal("21.00")),
            ("Ergonomic Mesh Chair", Decimal("280.00")),
            ("USB-C Dual 4K Docking Station", Decimal("165.00")),
        ]

        # Generate N invoices
        for i in range(invoice_count):
            vendor_name, currency, tax_rate = vendors[i % len(vendors)]
            inv_num = f"INV-AP-{202400 + i}"
            inv_date = today - timedelta(days=random.randint(2, 85))
            due_date = inv_date + timedelta(days=30)

            # Injected conditions
            # Every 6th invoice has an arithmetic error to test review queue
            has_math_error = i % 6 == 0
            # Item with price drift: Developer SaaS Monthly Seat
            drift_price = (
                Decimal("20.00") if i < 8 else (Decimal("25.00") if i < 16 else Decimal("32.50"))
            )

            # Build line items
            num_items = random.randint(1, 4)
            line_items_list = []
            subtotal = Decimal("0.00")

            for line_idx in range(1, num_items + 1):
                if line_idx == 1 and (i % 3 == 0):
                    desc = "Developer SaaS Monthly Seat"
                    u_price = drift_price
                else:
                    item_desc, item_price = item_catalogue[(i + line_idx) % len(item_catalogue)]
                    desc = item_desc
                    u_price = item_price

                qty = Decimal(str(random.randint(1, 10)))
                line_tot = (qty * u_price).quantize(Decimal("0.01"))
                subtotal += line_tot
                line_items_list.append((line_idx, desc, qty, u_price, line_tot))

            tax_amount = (subtotal * tax_rate).quantize(Decimal("0.01"))
            if has_math_error:
                # Deliberate 50.00 mismatch
                total_amount = subtotal + tax_amount + Decimal("50.00")
                inv_status = "needs_review"
            else:
                total_amount = subtotal + tax_amount
                inv_status = "completed"

            # Check if invoice already exists
            dup_check = await session.execute(
                select(Invoice).where(
                    Invoice.tenant_id == DEFAULT_TENANT_ID,
                    Invoice.vendor_name == vendor_name,
                    Invoice.invoice_number == inv_num,
                )
            )
            if dup_check.scalar_one_or_none():
                continue

            # Create synthetic document
            doc_content = f"%PDF-1.4 Mock Invoice {inv_num} for {vendor_name} Total: {total_amount} {currency}".encode()
            sha256_hash = hashlib.sha256(doc_content).hexdigest()
            doc_file = uploads_dir / f"{DEFAULT_TENANT_ID}_{sha256_hash[:8]}_{inv_num}.pdf"
            doc_file.write_bytes(doc_content)

            doc = Document(
                tenant_id=DEFAULT_TENANT_ID,
                filename=f"{inv_num}.pdf",
                storage_path=str(doc_file),
                mime_type="application/pdf",
                file_size_bytes=len(doc_content),
                sha256_hash=sha256_hash,
                status=inv_status,
            )
            session.add(doc)
            await session.flush()

            conf_score = (
                Decimal("0.7850") if has_math_error else Decimal(f"0.{random.randint(9200, 9950)}")
            )
            inv = Invoice(
                tenant_id=DEFAULT_TENANT_ID,
                document_id=doc.id,
                invoice_number=inv_num,
                invoice_date=inv_date,
                due_date=due_date,
                vendor_name=vendor_name,
                currency=currency,
                subtotal=subtotal,
                tax_amount=tax_amount,
                total_amount=total_amount,
                status=inv_status,
                extraction_confidence=conf_score,
                tax_breakdown={"rate": str(tax_rate), "amount": str(tax_amount)},
            )
            session.add(inv)
            await session.flush()

            for l_idx, desc, qty, u_price, l_tot in line_items_list:
                li = LineItem(
                    tenant_id=DEFAULT_TENANT_ID,
                    invoice_id=inv.id,
                    line_number=l_idx,
                    description=desc,
                    quantity=qty.quantize(Decimal("0.0001")),
                    unit_price=u_price,
                    total_amount=l_tot,
                    confidence=Decimal("0.9600"),
                )
                session.add(li)

            # Add validation flags and review item if math error
            if has_math_error:
                vf = ValidationFlag(
                    tenant_id=DEFAULT_TENANT_ID,
                    invoice_id=inv.id,
                    rule_name="ARITHMETIC_MISMATCH",
                    severity="ERROR",
                    field_name="total_amount",
                    message=f"Subtotal ({subtotal}) + Tax ({tax_amount}) = {subtotal + tax_amount}, but printed total is {total_amount}",
                )
                session.add(vf)

                rev = ReviewItem(
                    tenant_id=DEFAULT_TENANT_ID,
                    invoice_id=inv.id,
                    assigned_to=None,
                    status="pending",
                    review_reason="Routed to human review: Arithmetic total mismatch detected.",
                )
                session.add(rev)

        # 6. Inject one deliberate logical duplicate cluster for Insights testing
        dup_inv_num = "INV-DUP-999"
        for d_idx in range(2):
            dup_content = f"%PDF-1.4 Duplicate Invoice {dup_inv_num} #{d_idx}".encode()
            d_sha = hashlib.sha256(dup_content).hexdigest()
            d_file = uploads_dir / f"{DEFAULT_TENANT_ID}_{d_sha[:8]}_{dup_inv_num}_{d_idx}.pdf"
            d_file.write_bytes(dup_content)

            d_doc = Document(
                tenant_id=DEFAULT_TENANT_ID,
                filename=f"{dup_inv_num}_{d_idx}.pdf",
                storage_path=str(d_file),
                mime_type="application/pdf",
                file_size_bytes=len(dup_content),
                sha256_hash=d_sha,
                status="needs_review",
            )
            session.add(d_doc)
            await session.flush()

            d_inv = Invoice(
                tenant_id=DEFAULT_TENANT_ID,
                document_id=d_doc.id,
                invoice_number=dup_inv_num,
                invoice_date=today - timedelta(days=10),
                due_date=today + timedelta(days=20),
                vendor_name="Duplicate Vendor LLC",
                currency="USD",
                subtotal=Decimal("1200.00"),
                tax_amount=Decimal("120.00"),
                total_amount=Decimal("1320.00"),
                status="needs_review",
                extraction_confidence=Decimal("0.9500"),
            )
            session.add(d_inv)
            await session.flush()

            session.add(
                ValidationFlag(
                    tenant_id=DEFAULT_TENANT_ID,
                    invoice_id=d_inv.id,
                    rule_name="DUPLICATE_WARNING",
                    severity="WARNING",
                    field_name="invoice_number",
                    message="Logical duplicate detected: matching vendor_name and invoice_number",
                )
            )

        await session.commit()
        logger.info("Database seeding successfully completed!", seeded_invoices=invoice_count)


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed database with invoices and customers.")
    parser.add_argument(
        "--invoices", type=int, default=25, help="Number of inbound invoices to seed"
    )
    args = parser.parse_args()
    asyncio.run(seed_data(invoice_count=args.invoices))


if __name__ == "__main__":
    main()
