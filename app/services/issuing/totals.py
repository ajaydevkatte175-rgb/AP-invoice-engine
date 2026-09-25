"""Deterministic arithmetic calculation for outbound and inbound invoices.

NON-NEGOTIABLE RULES:
1. Rule 1: Money is Decimal in Python and Numeric(14,2) in Postgres. NEVER float.
   Quantities are Numeric(14,4).
2. Rule 4: Invoice generation totals are computed in Python, never by the LLM.
   If a model proposes a total, discard it and recompute.
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any


def to_decimal_money(val: Any) -> Decimal:
    """Convert any input value to Decimal with 2 decimal places (Numeric(14,2))."""
    if val is None:
        return Decimal("0.00")
    if isinstance(val, Decimal):
        return val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return Decimal(str(val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def to_decimal_qty(val: Any) -> Decimal:
    """Convert any input value to Decimal with 4 decimal places (Numeric(14,4))."""
    if val is None:
        return Decimal("1.0000")
    if isinstance(val, Decimal):
        return val.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    return Decimal(str(val)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def to_decimal_rate(val: Any) -> Decimal:
    """Convert tax rate to Decimal with 4 decimal places (e.g. 0.1000 for 10%)."""
    if val is None:
        return Decimal("0.0000")
    if isinstance(val, Decimal):
        return val.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    return Decimal(str(val)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


@dataclass
class ComputedLineItem:
    """Line item with verified deterministic calculations."""

    line_number: int
    description: str
    quantity: Decimal
    unit_price: Decimal
    total_amount: Decimal
    tax_rate: Decimal
    tax_amount: Decimal

    def to_dict(self) -> dict[str, Any]:
        return {
            "line_number": self.line_number,
            "description": self.description,
            "quantity": self.quantity,
            "unit_price": self.unit_price,
            "total_amount": self.total_amount,
            "tax_rate": self.tax_rate,
            "tax_amount": self.tax_amount,
        }


@dataclass
class ComputedTotals:
    """Complete invoice totals computed deterministically."""

    subtotal: Decimal
    tax_rate: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    line_items: list[ComputedLineItem]

    def to_dict(self) -> dict[str, Any]:
        return {
            "subtotal": self.subtotal,
            "tax_rate": self.tax_rate,
            "tax_amount": self.tax_amount,
            "total_amount": self.total_amount,
            "line_items": [item.to_dict() for item in self.line_items],
        }


def compute_totals(
    line_items: list[dict[str, Any] | Any],
    default_tax_rate: Decimal | str | float | None = None,
) -> ComputedTotals:
    """Compute invoice totals using pure Decimal arithmetic.

    Guarantees:
    - Never uses floating point arithmetic.
    - Each line item total = quantity * unit_price (rounded to 2 decimal places with ROUND_HALF_UP).
    - Subtotal = sum of line totals.
    - Tax amount computed per line (or fallback to invoice default tax rate).
    - Total amount = subtotal + tax_amount.
    """
    invoice_tax_rate = to_decimal_rate(default_tax_rate)
    computed_items: list[ComputedLineItem] = []

    running_subtotal = Decimal("0.00")
    running_tax = Decimal("0.00")

    for idx, item in enumerate(line_items, start=1):
        # Extract fields whether item is dict or object
        if isinstance(item, dict):
            desc = str(item.get("description", f"Line Item {idx}"))
            raw_qty = item.get("quantity", 1)
            raw_price = item.get("unit_price", 0)
            raw_rate = item.get("tax_rate", invoice_tax_rate)
            line_num = int(item.get("line_number", idx))
        else:
            desc = str(getattr(item, "description", f"Line Item {idx}"))
            raw_qty = getattr(item, "quantity", 1)
            raw_price = getattr(item, "unit_price", 0)
            raw_rate = getattr(item, "tax_rate", invoice_tax_rate)
            line_num = int(getattr(item, "line_number", idx))

        qty = to_decimal_qty(raw_qty)
        price = to_decimal_money(raw_price)
        rate = to_decimal_rate(raw_rate if raw_rate is not None else invoice_tax_rate)

        # Pure Decimal arithmetic with standard commercial rounding
        line_total = (qty * price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        line_tax = (line_total * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        running_subtotal += line_total
        running_tax += line_tax

        computed_items.append(
            ComputedLineItem(
                line_number=line_num,
                description=desc,
                quantity=qty,
                unit_price=price,
                total_amount=line_total,
                tax_rate=rate,
                tax_amount=line_tax,
            )
        )

    subtotal = running_subtotal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    tax_amount = running_tax.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    total_amount = (subtotal + tax_amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    return ComputedTotals(
        subtotal=subtotal,
        tax_rate=invoice_tax_rate,
        tax_amount=tax_amount,
        total_amount=total_amount,
        line_items=computed_items,
    )

