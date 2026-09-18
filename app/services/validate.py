from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# NON-NEGOTIABLE RULE 2:
# app/services/validate.py must NOT import anything from the AI layer.
# All arithmetic, date, and currency checks are deterministic Python.
# Money is Decimal. Never float.
# ---------------------------------------------------------------------------

COMMON_ISO_CURRENCIES = {
    "USD", "EUR", "GBP", "CAD", "AUD", "JPY", "CHF", "CNY", "INR", "NZD",
    "SGD", "HKD", "SEK", "NOK", "DKK", "ZAR", "MXN", "BRL", "PLN",
}

ROUNDING_TOLERANCE = Decimal("0.02")
QUANTIZE_TWO_PLACES = Decimal("0.01")


class LineItemValidationData(BaseModel):
    """Line item data passed to the deterministic validation engine."""

    line_number: int | None = None
    description: str
    quantity: Decimal = Field(default=Decimal("1.0000"))
    unit_price: Decimal = Field(default=Decimal("0.00"))
    line_total: Decimal = Field(default=Decimal("0.00"))


class InvoiceValidationData(BaseModel):
    """Invoice header and items passed to the deterministic validation engine."""

    vendor_name: str
    invoice_number: str
    invoice_date: date | None = None
    due_date: date | None = None
    currency: str = "USD"
    subtotal: Decimal | None = None
    tax_amount: Decimal | None = None
    total_amount: Decimal
    line_items: list[LineItemValidationData] = Field(default_factory=list)


class ValidationFlagData(BaseModel):
    """A discrete validation issue or informational finding."""

    flag_type: str
    severity: str  # INFO, WARNING, ERROR
    field_name: str | None = None
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ValidationResult(BaseModel):
    """Aggregate result of deterministic validation checks."""

    is_valid: bool
    has_errors: bool
    has_warnings: bool
    flags: list[ValidationFlagData] = Field(default_factory=list)


def validate_invoice_data(data: InvoiceValidationData) -> ValidationResult:
    """Execute pure Python deterministic validation checks across invoice amounts, dates, and currency.

    Strictly adheres to:
    - Non-Negotiable Rule 1: Money is Decimal, never float.
    - Non-Negotiable Rule 2: Zero imports from ailayer.
    """
    flags: list[ValidationFlagData] = []

    # 1. Vendor & Invoice Number existence
    if not data.vendor_name or not data.vendor_name.strip():
        flags.append(
            ValidationFlagData(
                flag_type="MISSING_VENDOR_NAME",
                severity="ERROR",
                field_name="vendor_name",
                message="Vendor name is missing or empty.",
            )
        )

    if not data.invoice_number or not data.invoice_number.strip():
        flags.append(
            ValidationFlagData(
                flag_type="MISSING_INVOICE_NUMBER",
                severity="ERROR",
                field_name="invoice_number",
                message="Invoice number is missing or empty.",
            )
        )

    # 2. Currency Validation
    curr = (data.currency or "").strip().upper()
    if not curr or len(curr) != 3 or curr not in COMMON_ISO_CURRENCIES:
        flags.append(
            ValidationFlagData(
                flag_type="INVALID_CURRENCY",
                severity="WARNING" if len(curr) == 3 else "ERROR",
                field_name="currency",
                message=f"Currency '{data.currency}' is unrecognized or non-standard ISO code.",
                details={"currency": data.currency},
            )
        )

    # 3. Line Items Arithmetic Validation
    computed_subtotal = Decimal("0.00")
    for idx, item in enumerate(data.line_items, start=1):
        expected_line_total = (item.quantity * item.unit_price).quantize(
            QUANTIZE_TWO_PLACES, rounding=ROUND_HALF_UP
        )
        diff = abs(expected_line_total - item.line_total)
        computed_subtotal += item.line_total

        if diff > ROUNDING_TOLERANCE:
            flags.append(
                ValidationFlagData(
                    flag_type="LINE_ITEM_MATH_MISMATCH",
                    severity="ERROR",
                    field_name=f"line_items[{idx}].line_total",
                    message=(
                        f"Line {idx} '{item.description[:30]}': quantity ({item.quantity}) * "
                        f"unit_price ({item.unit_price}) = {expected_line_total}, "
                        f"but printed line_total is {item.line_total} (diff: {diff})."
                    ),
                    details={
                        "line_number": idx,
                        "quantity": str(item.quantity),
                        "unit_price": str(item.unit_price),
                        "expected_line_total": str(expected_line_total),
                        "printed_line_total": str(item.line_total),
                    },
                )
            )

    # 4. Subtotal Validation
    if data.subtotal is not None and data.line_items:
        subtotal_diff = abs(computed_subtotal - data.subtotal)
        if subtotal_diff > ROUNDING_TOLERANCE:
            flags.append(
                ValidationFlagData(
                    flag_type="SUBTOTAL_MISMATCH",
                    severity="ERROR",
                    field_name="subtotal",
                    message=(
                        f"Sum of line totals ({computed_subtotal}) does not equal printed "
                        f"subtotal ({data.subtotal}) (diff: {subtotal_diff})."
                    ),
                    details={
                        "computed_subtotal": str(computed_subtotal),
                        "printed_subtotal": str(data.subtotal),
                        "difference": str(subtotal_diff),
                    },
                )
            )

    # 5. Total Amount Validation
    base_subtotal = data.subtotal if data.subtotal is not None else computed_subtotal
    tax = data.tax_amount if data.tax_amount is not None else Decimal("0.00")
    expected_total = (base_subtotal + tax).quantize(QUANTIZE_TWO_PLACES, rounding=ROUND_HALF_UP)
    total_diff = abs(expected_total - data.total_amount)

    if total_diff > ROUNDING_TOLERANCE and (base_subtotal > 0 or tax > 0):
        flags.append(
            ValidationFlagData(
                flag_type="TOTAL_MATH_MISMATCH",
                severity="ERROR",
                field_name="total_amount",
                message=(
                    f"Subtotal ({base_subtotal}) + tax ({tax}) = {expected_total}, "
                    f"does not match printed total ({data.total_amount}) (diff: {total_diff})."
                ),
                details={
                    "subtotal": str(base_subtotal),
                    "tax": str(tax),
                    "expected_total": str(expected_total),
                    "printed_total": str(data.total_amount),
                    "difference": str(total_diff),
                },
            )
        )

    # 6. Date Sanity Validations
    today = date.today()
    if data.invoice_date:
        if data.invoice_date > today + timedelta(days=1):
            flags.append(
                ValidationFlagData(
                    flag_type="FUTURE_INVOICE_DATE",
                    severity="WARNING",
                    field_name="invoice_date",
                    message=f"Invoice date {data.invoice_date} is in the future.",
                    details={"invoice_date": str(data.invoice_date)},
                )
            )
        if data.invoice_date < today - timedelta(days=365 * 3):
            flags.append(
                ValidationFlagData(
                    flag_type="OLD_INVOICE_DATE",
                    severity="WARNING",
                    field_name="invoice_date",
                    message=f"Invoice date {data.invoice_date} is more than 3 years old.",
                    details={"invoice_date": str(data.invoice_date)},
                )
            )

    if data.invoice_date and data.due_date:
        if data.due_date < data.invoice_date:
            flags.append(
                ValidationFlagData(
                    flag_type="DUE_DATE_BEFORE_INVOICE_DATE",
                    severity="ERROR",
                    field_name="due_date",
                    message=f"Due date ({data.due_date}) occurs before invoice date ({data.invoice_date}).",
                    details={
                        "invoice_date": str(data.invoice_date),
                        "due_date": str(data.due_date),
                    },
                )
            )

    has_errors = any(f.severity == "ERROR" for f in flags)
    has_warnings = any(f.severity == "WARNING" for f in flags)

    return ValidationResult(
        is_valid=not has_errors,
        has_errors=has_errors,
        has_warnings=has_warnings,
        flags=flags,
    )

