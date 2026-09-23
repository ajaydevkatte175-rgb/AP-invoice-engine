"""Deterministic SQL Insights API endpoints.

Surfaces core business intelligence and AP/AR financial metrics:
- /insights/summary: Headline KPI metrics (spend, open items, review queue).
- /insights/vendors: Spend grouped by vendor AND currency.
- /insights/trend: Period spend trends over time.
- /insights/price-drift: Line item unit price changes across 3+ invoices.
- /insights/duplicates: Logical duplicates and duplicate risk clusters.
- /insights/aging: Overdue payment aging buckets.
- /insights/vendor-quality: Deterministic validation error rate per vendor.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import verify_api_key
from app.core.db import get_db
from app.core.logging import get_logger
from app.core.middleware import tenant_dependency

logger = get_logger(__name__)

router = APIRouter(prefix="/insights", tags=["Financial Insights & Analytics"])


def _serialize_row_val(val: Any) -> Any:
    """Safely convert database row values to JSON-serializable primitives."""
    if isinstance(val, Decimal):
        return float(val)
    if isinstance(val, date):
        return val.isoformat()
    if isinstance(val, uuid.UUID):
        return str(val)
    return val


@router.get(
    "/summary",
    summary="Headline AP/AR platform summary metrics",
)
async def get_insights_summary(
    _api_key: str = Depends(verify_api_key),
    tenant_id: uuid.UUID = Depends(tenant_dependency),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Retrieve top-level summary metrics across inbound and outbound pipelines."""
    await db.execute(text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))

    # Inbound AP stats
    ap_stmt = text(
        """
        SELECT
            COUNT(*) AS total_invoices,
            COALESCE(SUM(total_amount), 0) AS total_spend,
            COUNT(CASE WHEN status = 'needs_review' THEN 1 END) AS review_count,
            COUNT(CASE WHEN status = 'completed' THEN 1 END) AS completed_count,
            COALESCE(AVG(extraction_confidence), 0.0) AS avg_confidence
        FROM invoices
        WHERE tenant_id = :tenant_id AND status != 'void'
        """
    )
    ap_res = await db.execute(ap_stmt, {"tenant_id": tenant_id})
    ap_row = ap_res.mappings().one()

    # Outbound AR stats
    ar_stmt = text(
        """
        SELECT
            COUNT(*) AS total_issued_invoices,
            COALESCE(SUM(total_amount), 0) AS total_issued_amount,
            COUNT(CASE WHEN status = 'paid' THEN 1 END) AS paid_count
        FROM issued_invoices
        WHERE tenant_id = :tenant_id AND status != 'cancelled'
        """
    )
    ar_res = await db.execute(ar_stmt, {"tenant_id": tenant_id})
    ar_row = ar_res.mappings().one()

    # Unique vendor count
    vendor_stmt = text(
        """
        SELECT COUNT(DISTINCT vendor_name) AS vendor_count
        FROM invoices
        WHERE tenant_id = :tenant_id AND vendor_name IS NOT NULL
        """
    )
    vendor_res = await db.execute(vendor_stmt, {"tenant_id": tenant_id})
    vendor_count = vendor_res.scalar() or 0

    return {
        "ap": {
            "total_invoices": ap_row["total_invoices"],
            "total_spend": _serialize_row_val(ap_row["total_spend"]),
            "review_queue_count": ap_row["review_count"],
            "completed_count": ap_row["completed_count"],
            "avg_extraction_confidence": _serialize_row_val(ap_row["avg_confidence"]),
            "vendor_count": vendor_count,
        },
        "ar": {
            "total_issued_invoices": ar_row["total_issued_invoices"],
            "total_issued_amount": _serialize_row_val(ar_row["total_issued_amount"]),
            "paid_count": ar_row["paid_count"],
        },
    }


@router.get(
    "/vendors",
    summary="Spend breakdown grouped by vendor and currency",
)
async def get_insights_vendors(
    _api_key: str = Depends(verify_api_key),
    tenant_id: uuid.UUID = Depends(tenant_dependency),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Spend metrics strictly grouped by vendor AND currency (Rule from AGENTS.md)."""
    await db.execute(text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))

    stmt = text(
        """
        SELECT
            vendor_name,
            currency,
            COUNT(*) AS invoice_count,
            COALESCE(SUM(total_amount), 0) AS total_spend,
            COALESCE(AVG(total_amount), 0) AS avg_invoice_amount,
            MIN(invoice_date) AS first_invoice_date,
            MAX(invoice_date) AS latest_invoice_date
        FROM invoices
        WHERE tenant_id = :tenant_id AND vendor_name IS NOT NULL AND status != 'void'
        GROUP BY vendor_name, currency
        ORDER BY total_spend DESC, invoice_count DESC
        """
    )
    res = await db.execute(stmt, {"tenant_id": tenant_id})
    rows = res.mappings().all()

    items = [
        {k: _serialize_row_val(v) for k, v in dict(row).items()}
        for row in rows
    ]
    return {
        "items": items,
        "total": len(items),
    }


@router.get(
    "/trend",
    summary="Monthly/period spend trend over time",
)
async def get_insights_trend(
    _api_key: str = Depends(verify_api_key),
    tenant_id: uuid.UUID = Depends(tenant_dependency),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Retrieve historical spend trends grouped by year-month period and currency."""
    await db.execute(text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))

    stmt = text(
        """
        SELECT
            TO_CHAR(COALESCE(invoice_date, created_at::date), 'YYYY-MM') AS period,
            currency,
            COUNT(*) AS invoice_count,
            COALESCE(SUM(total_amount), 0) AS total_spend
        FROM invoices
        WHERE tenant_id = :tenant_id AND status != 'void'
        GROUP BY period, currency
        ORDER BY period ASC
        """
    )
    res = await db.execute(stmt, {"tenant_id": tenant_id})
    rows = res.mappings().all()

    items = [
        {k: _serialize_row_val(v) for k, v in dict(row).items()}
        for row in rows
    ]
    return {
        "items": items,
        "total": len(items),
    }


@router.get(
    "/price-drift",
    summary="Unit price drift tracking across 3+ invoices",
)
async def get_insights_price_drift(
    min_invoices: int = Query(default=3, ge=1, description="Minimum number of invoices required to compute price drift"),
    _api_key: str = Depends(verify_api_key),
    tenant_id: uuid.UUID = Depends(tenant_dependency),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Detect significant unit price drift on line items across 3 or more invoices."""
    await db.execute(text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))

    stmt = text(
        """
        SELECT
            i.vendor_name,
            li.description,
            i.currency,
            COUNT(DISTINCT i.id) AS invoice_count,
            MIN(li.unit_price) AS min_unit_price,
            MAX(li.unit_price) AS max_unit_price,
            AVG(li.unit_price) AS avg_unit_price,
            (MAX(li.unit_price) - MIN(li.unit_price)) AS price_spread,
            CASE
                WHEN MIN(li.unit_price) > 0 THEN
                    ROUND(((MAX(li.unit_price) - MIN(li.unit_price)) / MIN(li.unit_price) * 100)::numeric, 2)
                ELSE 0.00
            END AS percentage_drift
        FROM line_items li
        JOIN invoices i ON li.invoice_id = i.id
        WHERE i.tenant_id = :tenant_id AND i.vendor_name IS NOT NULL
        GROUP BY i.vendor_name, li.description, i.currency
        HAVING COUNT(DISTINCT i.id) >= :min_invoices
        ORDER BY percentage_drift DESC, invoice_count DESC
        """
    )
    res = await db.execute(stmt, {"tenant_id": tenant_id, "min_invoices": min_invoices})
    rows = res.mappings().all()

    items = [
        {k: _serialize_row_val(v) for k, v in dict(row).items()}
        for row in rows
    ]
    return {
        "items": items,
        "min_invoices_threshold": min_invoices,
        "total": len(items),
    }


@router.get(
    "/duplicates",
    summary="Duplicate invoice risk detection",
)
async def get_insights_duplicates(
    _api_key: str = Depends(verify_api_key),
    tenant_id: uuid.UUID = Depends(tenant_dependency),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Surface logical duplicates and potential duplicate risk clusters."""
    await db.execute(text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))

    # 1. Exact match clusters on (vendor_name, invoice_number)
    exact_stmt = text(
        """
        SELECT
            vendor_name,
            invoice_number,
            COUNT(*) AS duplicate_count,
            COALESCE(MAX(total_amount), 0) AS amount,
            currency,
            ARRAY_AGG(id::text) AS invoice_ids
        FROM invoices
        WHERE tenant_id = :tenant_id
          AND vendor_name IS NOT NULL
          AND invoice_number IS NOT NULL
          AND status != 'void'
        GROUP BY vendor_name, invoice_number, currency
        HAVING COUNT(*) > 1
        ORDER BY duplicate_count DESC
        """
    )
    exact_res = await db.execute(exact_stmt, {"tenant_id": tenant_id})
    exact_rows = exact_res.mappings().all()

    # 2. Invoices with duplicate validation flags
    flag_stmt = text(
        """
        SELECT DISTINCT
            i.id,
            i.invoice_number,
            i.vendor_name,
            i.total_amount,
            i.currency,
            i.invoice_date,
            vf.rule_name,
            vf.message
        FROM invoices i
        JOIN validation_flags vf ON i.id = vf.invoice_id
        WHERE i.tenant_id = :tenant_id
          AND vf.rule_name ILIKE '%duplicate%'
        """
    )
    flag_res = await db.execute(flag_stmt, {"tenant_id": tenant_id})
    flag_rows = flag_res.mappings().all()

    exact_duplicates = [
        {k: _serialize_row_val(v) for k, v in dict(row).items()}
        for row in exact_rows
    ]
    flagged_duplicates = [
        {k: _serialize_row_val(v) for k, v in dict(row).items()}
        for row in flag_rows
    ]

    return {
        "exact_duplicates": exact_duplicates,
        "flagged_duplicates": flagged_duplicates,
        "total_risk_count": len(exact_duplicates) + len(flagged_duplicates),
    }


@router.get(
    "/aging",
    summary="Accounts payable payment aging breakdown",
)
async def get_insights_aging(
    _api_key: str = Depends(verify_api_key),
    tenant_id: uuid.UUID = Depends(tenant_dependency),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Calculate payment aging buckets (Current, 1-30, 31-60, 61-90, 90+ days overdue)."""
    await db.execute(text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))

    stmt = text(
        """
        WITH categorized AS (
            SELECT
                CASE
                    WHEN due_date IS NULL OR due_date >= CURRENT_DATE THEN 'Current'
                    WHEN CURRENT_DATE - due_date BETWEEN 1 AND 30 THEN '1-30 Days'
                    WHEN CURRENT_DATE - due_date BETWEEN 31 AND 60 THEN '31-60 Days'
                    WHEN CURRENT_DATE - due_date BETWEEN 61 AND 90 THEN '61-90 Days'
                    ELSE '90+ Days'
                END AS aging_bucket,
                currency,
                total_amount
            FROM invoices
            WHERE tenant_id = :tenant_id AND status != 'void'
        )
        SELECT
            aging_bucket,
            currency,
            COUNT(*) AS invoice_count,
            COALESCE(SUM(total_amount), 0) AS total_amount
        FROM categorized
        GROUP BY aging_bucket, currency
        ORDER BY
            CASE aging_bucket
                WHEN 'Current' THEN 1
                WHEN '1-30 Days' THEN 2
                WHEN '31-60 Days' THEN 3
                WHEN '61-90 Days' THEN 4
                ELSE 5
            END
        """
    )
    res = await db.execute(stmt, {"tenant_id": tenant_id})
    rows = res.mappings().all()

    buckets = [
        {k: _serialize_row_val(v) for k, v in dict(row).items()}
        for row in rows
    ]
    return {
        "buckets": buckets,
        "as_of_date": date.today().isoformat(),
    }


@router.get(
    "/vendor-quality",
    summary="Vendor error rate and quality metrics",
)
async def get_insights_vendor_quality(
    _api_key: str = Depends(verify_api_key),
    tenant_id: uuid.UUID = Depends(tenant_dependency),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Calculate validation error rate and review frequency per supplier."""
    await db.execute(text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))

    stmt = text(
        """
        SELECT
            i.vendor_name,
            COUNT(DISTINCT i.id) AS total_invoices,
            COUNT(DISTINCT vf.id) AS total_flags,
            COUNT(DISTINCT CASE WHEN i.status = 'needs_review' THEN i.id END) AS review_count,
            COALESCE(
                ROUND(
                    (COUNT(DISTINCT CASE WHEN i.status = 'needs_review' THEN i.id END)::numeric /
                    NULLIF(COUNT(DISTINCT i.id), 0) * 100), 2
                ), 0.00
            ) AS error_rate_percent,
            COALESCE(AVG(i.extraction_confidence), 0.0) AS avg_confidence
        FROM invoices i
        LEFT JOIN validation_flags vf ON i.id = vf.invoice_id
        WHERE i.tenant_id = :tenant_id AND i.vendor_name IS NOT NULL
        GROUP BY i.vendor_name
        ORDER BY error_rate_percent DESC, total_invoices DESC
        """
    )
    res = await db.execute(stmt, {"tenant_id": tenant_id})
    rows = res.mappings().all()

    items = [
        {k: _serialize_row_val(v) for k, v in dict(row).items()}
        for row in rows
    ]
    return {
        "items": items,
        "total": len(items),
    }

