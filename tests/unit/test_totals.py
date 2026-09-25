"""Unit tests for deterministic invoice totals calculations.

NON-NEGOTIABLE RULES:
1. Rule 1: Money is Decimal in Python and Numeric(14,2) in Postgres. NEVER float.
   Quantities are Numeric(14,4).
2. Rule 4: Invoice generation totals are computed in Python, never by the LLM.
"""

from decimal import Decimal

from app.services.issuing.totals import (
    compute_totals,
    to_decimal_money,
    to_decimal_qty,
    to_decimal_rate,
)


class TestDecimalConversions:
    """Verify precision quantization helper functions."""

    def test_to_decimal_money(self):
        assert to_decimal_money("12.345") == Decimal("12.35")
        assert to_decimal_money(10) == Decimal("10.00")
        assert to_decimal_money(Decimal("99.99")) == Decimal("99.99")
        assert to_decimal_money(None) == Decimal("0.00")

    def test_to_decimal_qty(self):
        assert to_decimal_qty("2.5") == Decimal("2.5000")
        assert to_decimal_qty(1) == Decimal("1.0000")
        assert to_decimal_qty(Decimal("3.14159")) == Decimal("3.1416")
        assert to_decimal_qty(None) == Decimal("1.0000")

    def test_to_decimal_rate(self):
        assert to_decimal_rate("0.0825") == Decimal("0.0825")
        assert to_decimal_rate(0) == Decimal("0.0000")
        assert to_decimal_rate(None) == Decimal("0.0000")


class TestComputeTotals:
    """Verify complete invoice totals computation with Decimal precision."""

    def test_basic_line_items_without_tax(self):
        items = [
            {
                "description": "Item 1",
                "quantity": Decimal("2.0000"),
                "unit_price": Decimal("50.00"),
            },
            {
                "description": "Item 2",
                "quantity": Decimal("3.0000"),
                "unit_price": Decimal("25.00"),
            },
        ]
        res = compute_totals(items)

        assert isinstance(res.subtotal, Decimal)
        assert res.subtotal == Decimal("175.00")
        assert res.tax_amount == Decimal("0.00")
        assert res.total_amount == Decimal("175.00")
        assert len(res.line_items) == 2
        assert res.line_items[0].total_amount == Decimal("100.00")
        assert res.line_items[1].total_amount == Decimal("75.00")

    def test_invoice_with_tax_rate(self):
        items = [
            {"description": "Consulting", "quantity": "10.0000", "unit_price": "150.00"},
            {"description": "Software License", "quantity": "1.0000", "unit_price": "500.00"},
        ]
        # 10% tax rate
        res = compute_totals(items, default_tax_rate=Decimal("0.1000"))

        assert res.subtotal == Decimal("2000.00")
        assert res.tax_rate == Decimal("0.1000")
        assert res.tax_amount == Decimal("200.00")
        assert res.total_amount == Decimal("2200.00")

    def test_half_cent_commercial_rounding(self):
        """Test commercial half-up rounding on fractional quantities (1.5 * 1.05 = 1.575 -> 1.58)."""
        items = [
            {
                "description": "Fractional qty item",
                "quantity": Decimal("1.5000"),
                "unit_price": Decimal("1.05"),
            },
        ]
        res = compute_totals(items)
        assert res.line_items[0].total_amount == Decimal("1.58")
        assert res.subtotal == Decimal("1.58")
        assert res.total_amount == Decimal("1.58")

    def test_float_drift_immunity(self):
        """Ensure Decimal prevents typical IEEE 754 float drift (0.1 + 0.2 != 0.3)."""
        items = [
            {"description": "A", "quantity": "1", "unit_price": "0.10"},
            {"description": "B", "quantity": "1", "unit_price": "0.20"},
        ]
        res = compute_totals(items)
        assert res.subtotal == Decimal("0.30")
        assert str(res.subtotal) == "0.30"

    def test_per_line_tax_rates(self):
        items = [
            {
                "description": "Taxable item",
                "quantity": 1,
                "unit_price": 100,
                "tax_rate": Decimal("0.2000"),
            },
            {
                "description": "Exempt item",
                "quantity": 1,
                "unit_price": 50,
                "tax_rate": Decimal("0.0000"),
            },
        ]
        res = compute_totals(items)
        assert res.subtotal == Decimal("150.00")
        assert res.line_items[0].tax_amount == Decimal("20.00")
        assert res.line_items[1].tax_amount == Decimal("0.00")
        assert res.tax_amount == Decimal("20.00")
        assert res.total_amount == Decimal("170.00")

    def test_empty_line_items(self):
        res = compute_totals([])
        assert res.subtotal == Decimal("0.00")
        assert res.tax_amount == Decimal("0.00")
        assert res.total_amount == Decimal("0.00")
        assert res.line_items == []
