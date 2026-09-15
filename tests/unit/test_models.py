from sqlalchemy import Numeric
from sqlalchemy.dialects.postgresql import JSONB

from app.models import (
    Base,
    ChatMessage,
    ChatSession,
    Customer,
    Document,
    Invoice,
    InvoiceCounter,
    IssuedInvoice,
    IssuedLineItem,
    LineItem,
    ReviewItem,
    TenantProfile,
    ValidationFlag,
)


def test_table_count():
    expected_tables = {
        "audit_log",
        "chat_messages",
        "chat_sessions",
        "customers",
        "documents",
        "invoice_counters",
        "invoices",
        "issued_invoices",
        "issued_line_items",
        "line_items",
        "llm_calls",
        "review_items",
        "tenant_profile",
        "validation_flags",
    }
    actual_tables = set(Base.metadata.tables.keys())
    assert expected_tables == actual_tables
    assert len(actual_tables) == 14


def test_money_and_quantity_columns():
    # Verify invoice money columns are Numeric(14, 2)
    invoices_table = Base.metadata.tables["invoices"]
    for col_name in ["subtotal", "tax_amount", "total_amount", "amount_paid", "balance_due"]:
        col = invoices_table.c[col_name]
        assert isinstance(col.type, Numeric), f"{col_name} is not Numeric"
        assert col.type.precision == 14, f"{col_name} precision is not 14"
        assert col.type.scale == 2, f"{col_name} scale is not 2"

    # Verify tax_breakdown is JSONB
    assert isinstance(invoices_table.c["tax_breakdown"].type, JSONB)

    # Verify line_items money and quantity
    line_items_table = Base.metadata.tables["line_items"]
    qty_col = line_items_table.c["quantity"]
    assert isinstance(qty_col.type, Numeric)
    assert qty_col.type.precision == 14
    assert qty_col.type.scale == 4

    for col_name in ["unit_price", "total_amount"]:
        col = line_items_table.c[col_name]
        assert isinstance(col.type, Numeric)
        assert col.type.precision == 14
        assert col.type.scale == 2

    # Verify issued_invoices money
    issued_table = Base.metadata.tables["issued_invoices"]
    for col_name in ["subtotal", "tax_amount", "total_amount"]:
        col = issued_table.c[col_name]
        assert isinstance(col.type, Numeric)
        assert col.type.precision == 14
        assert col.type.scale == 2

    # Verify issued_line_items money and quantity
    issued_lines_table = Base.metadata.tables["issued_line_items"]
    qty_col = issued_lines_table.c["quantity"]
    assert isinstance(qty_col.type, Numeric)
    assert qty_col.type.precision == 14
    assert qty_col.type.scale == 4

    for col_name in ["unit_price", "total_amount"]:
        col = issued_lines_table.c[col_name]
        assert isinstance(col.type, Numeric)
        assert col.type.precision == 14
        assert col.type.scale == 2


def test_issued_invoices_unique_constraint():
    issued_table = Base.metadata.tables["issued_invoices"]
    constraints = [c for c in issued_table.constraints if getattr(c, "columns", None)]
    found = False
    for c in constraints:
        cols = {col.name for col in c.columns}
        if cols == {"tenant_id", "invoice_number"}:
            found = True
            break
    assert found, "Missing unique constraint on (tenant_id, invoice_number) in issued_invoices"


def test_mixins_present_on_models():
    # TenantMixin presence
    tenant_models = [
        Document,
        Invoice,
        LineItem,
        ValidationFlag,
        ReviewItem,
        TenantProfile,
        InvoiceCounter,
        Customer,
        IssuedInvoice,
        IssuedLineItem,
        ChatSession,
        ChatMessage,
    ]
    for model in tenant_models:
        assert hasattr(model, "tenant_id"), f"{model.__name__} missing tenant_id"
        assert hasattr(model, "id"), f"{model.__name__} missing id"
        assert hasattr(model, "created_at"), f"{model.__name__} missing created_at"
