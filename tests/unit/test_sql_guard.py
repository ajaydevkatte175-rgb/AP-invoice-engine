"""Unit tests for SQL Guard and PostgreSQL RLS tenant enforcement.

Verifies:
1. Rejection of all mutation statements (DELETE, DROP, UPDATE, INSERT, TRUNCATE, ALTER, CREATE, GRANT).
2. Rejection of stacked SQL statements (multiple queries separated by semicolons).
3. Rejection of tables outside the allowlist (pg_*, information_schema, llm_calls, users).
4. Tenant filter injection and cross-tenant access rejection.
5. LIMIT capping and injection.
6. Enforcement of SET LOCAL app.current_tenant = '...' for RLS.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.agent.sql_guard import (
    ALLOWED_TABLES,
    DisallowedTableError,
    InvalidQueryError,
    MutationNotAllowedError,
    StackedStatementsError,
    TenantSecurityError,
    execute_guarded_query,
    validate_and_sanitize_sql,
)

TEST_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
OTHER_TENANT_ID = uuid.UUID("99999999-9999-9999-9999-999999999999")


class TestSQLGuardMutations:
    """Verify strictly SELECT-only queries; all mutations must be rejected."""

    @pytest.mark.parametrize(
        "mutation_sql",
        [
            "DELETE FROM invoices WHERE id = '123'",
            "DROP TABLE invoices",
            "DROP TABLE line_items CASCADE",
            "UPDATE invoices SET status = 'PAID' WHERE id = '123'",
            "INSERT INTO invoices (invoice_number) VALUES ('INV-001')",
            "TRUNCATE TABLE invoices",
            "ALTER TABLE invoices ADD COLUMN hacked text",
            "CREATE TABLE evil (id serial primary key)",
            "GRANT ALL PRIVILEGES ON invoices TO public",
            "REVOKE ALL ON invoices FROM invoice_ro",
            # CTE containing hidden mutations
            "WITH bad AS (DELETE FROM invoices RETURNING *) SELECT * FROM bad",
            "WITH bad AS (UPDATE invoices SET status = 'void' RETURNING *) SELECT * FROM bad",
            "WITH bad AS (INSERT INTO invoices (invoice_number) VALUES ('X') RETURNING *) SELECT * FROM bad",
        ],
    )
    def test_rejects_mutation_statements(self, mutation_sql: str):
        with pytest.raises(MutationNotAllowedError):
            validate_and_sanitize_sql(mutation_sql, tenant_id=TEST_TENANT_ID)


class TestSQLGuardStackedStatements:
    """Verify that stacked queries are strictly forbidden."""

    @pytest.mark.parametrize(
        "stacked_sql",
        [
            "SELECT * FROM invoices; DROP TABLE invoices;",
            "SELECT * FROM invoices; SELECT * FROM line_items;",
            "SELECT * FROM invoices; DELETE FROM line_items WHERE id = 1;",
            "SELECT 1; SELECT 2;",
        ],
    )
    def test_rejects_stacked_statements(self, stacked_sql: str):
        with pytest.raises(StackedStatementsError):
            validate_and_sanitize_sql(stacked_sql, tenant_id=TEST_TENANT_ID)


class TestSQLGuardTableAllowlist:
    """Verify that only tables in ALLOWED_TABLES can be queried."""

    @pytest.mark.parametrize(
        "disallowed_sql",
        [
            # System internal table containing token spend and API costs
            "SELECT * FROM llm_calls",
            # PostgreSQL catalog tables
            "SELECT * FROM pg_shadow",
            "SELECT * FROM pg_user",
            "SELECT * FROM pg_database",
            # Information schema
            "SELECT * FROM information_schema.tables",
            "SELECT * FROM information_schema.columns",
            # Non-existent or arbitrary tables
            "SELECT * FROM users",
            "SELECT * FROM passwords",
            # Join with disallowed table
            "SELECT * FROM invoices JOIN pg_shadow ON true",
            "SELECT * FROM invoices i, llm_calls l WHERE i.tenant_id = l.tenant_id",
        ],
    )
    def test_rejects_tables_outside_allowlist(self, disallowed_sql: str):
        with pytest.raises(DisallowedTableError):
            validate_and_sanitize_sql(disallowed_sql, tenant_id=TEST_TENANT_ID)

    @pytest.mark.parametrize("table", list(ALLOWED_TABLES))
    def test_allows_all_allowlisted_tables(self, table: str):
        sql = f"SELECT * FROM {table}"
        sanitized = validate_and_sanitize_sql(sql, tenant_id=TEST_TENANT_ID)
        assert table in sanitized.lower()

    def test_allows_cte_with_allowed_tables(self):
        sql = (
            "WITH monthly_spend AS ("
            "    SELECT vendor_name, SUM(total_amount) AS total "
            "    FROM invoices "
            "    GROUP BY vendor_name"
            ") "
            "SELECT * FROM monthly_spend WHERE total > 1000"
        )
        sanitized = validate_and_sanitize_sql(sql, tenant_id=TEST_TENANT_ID)
        assert "monthly_spend" in sanitized.lower()
        assert "invoices" in sanitized.lower()


class TestSQLGuardTenantIsolation:
    """Verify tenant filter injection and rejection of cross-tenant attempts."""

    def test_injects_tenant_filter_when_absent(self):
        sql = "SELECT * FROM invoices"
        sanitized = validate_and_sanitize_sql(sql, tenant_id=TEST_TENANT_ID)
        assert f"invoices.tenant_id = '{TEST_TENANT_ID}'" in sanitized

    def test_injects_tenant_filter_with_existing_where(self):
        sql = "SELECT * FROM invoices WHERE status = 'completed'"
        sanitized = validate_and_sanitize_sql(sql, tenant_id=TEST_TENANT_ID)
        assert "status = 'completed'" in sanitized
        assert f"invoices.tenant_id = '{TEST_TENANT_ID}'" in sanitized

    def test_injects_tenant_filter_with_table_alias(self):
        sql = "SELECT i.invoice_number, li.description FROM invoices AS i JOIN line_items AS li ON i.id = li.invoice_id"
        sanitized = validate_and_sanitize_sql(sql, tenant_id=TEST_TENANT_ID)
        assert f"i.tenant_id = '{TEST_TENANT_ID}'" in sanitized

    def test_injects_tenant_filter_into_both_union_arms(self):
        sql = "SELECT id, total_amount FROM invoices UNION SELECT id, total_amount FROM issued_invoices"
        sanitized = validate_and_sanitize_sql(sql, tenant_id=TEST_TENANT_ID)
        assert f"invoices.tenant_id = '{TEST_TENANT_ID}'" in sanitized
        assert f"issued_invoices.tenant_id = '{TEST_TENANT_ID}'" in sanitized

    def test_allows_matching_tenant_filter(self):
        sql = f"SELECT * FROM invoices WHERE tenant_id = '{TEST_TENANT_ID}'"
        sanitized = validate_and_sanitize_sql(sql, tenant_id=TEST_TENANT_ID)
        assert str(TEST_TENANT_ID) in sanitized

    def test_rejects_cross_tenant_filter(self):
        sql = f"SELECT * FROM invoices WHERE tenant_id = '{OTHER_TENANT_ID}'"
        with pytest.raises(TenantSecurityError):
            validate_and_sanitize_sql(sql, tenant_id=TEST_TENANT_ID)


class TestSQLGuardLimitCapping:
    """Verify that LIMIT is always capped or injected."""

    def test_injects_default_limit_when_missing(self):
        sql = "SELECT * FROM invoices"
        sanitized = validate_and_sanitize_sql(sql, tenant_id=TEST_TENANT_ID, max_rows=200)
        assert "LIMIT 200" in sanitized

    def test_caps_limit_when_exceeding_max(self):
        sql = "SELECT * FROM invoices LIMIT 5000"
        sanitized = validate_and_sanitize_sql(sql, tenant_id=TEST_TENANT_ID, max_rows=200)
        assert "LIMIT 200" in sanitized
        assert "5000" not in sanitized

    def test_preserves_lower_limit(self):
        sql = "SELECT * FROM invoices LIMIT 50"
        sanitized = validate_and_sanitize_sql(sql, tenant_id=TEST_TENANT_ID, max_rows=200)
        assert "LIMIT 50" in sanitized


class TestSQLGuardInputValidation:
    """Verify handling of invalid, empty, or unparseable input."""

    @pytest.mark.parametrize("invalid_sql", ["", "   ", "\n\t"])
    def test_rejects_empty_query(self, invalid_sql: str):
        with pytest.raises(InvalidQueryError):
            validate_and_sanitize_sql(invalid_sql, tenant_id=TEST_TENANT_ID)

    def test_rejects_malformed_syntax(self):
        with pytest.raises(InvalidQueryError):
            validate_and_sanitize_sql("SELECT FROM WHERE ;;;", tenant_id=TEST_TENANT_ID)


class TestRLSTenantContextEnforcement:
    """Verify that execution forces SET LOCAL app.current_tenant for RLS safety."""

    @pytest.mark.asyncio
    async def test_execute_guarded_query_forces_rls_tenant_context(self):
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.mappings.return_value.all.return_value = [
            {"vendor_name": "Acme Supplies Ltd", "total_amount": 1500.0}
        ]
        mock_session.execute.return_value = mock_result

        sql = "SELECT vendor_name, total_amount FROM invoices"
        _sanitized_sql, rows = await execute_guarded_query(
            sql=sql,
            tenant_id=TEST_TENANT_ID,
            session=mock_session,
        )

        assert len(rows) == 1
        assert rows[0]["vendor_name"] == "Acme Supplies Ltd"

        # Verify that SET LOCAL app.current_tenant was executed before query
        calls = mock_session.execute.call_args_list
        assert len(calls) == 2

        first_call_sql = str(calls[0][0][0])
        assert f"SET LOCAL app.current_tenant = '{TEST_TENANT_ID}'" in first_call_sql

        second_call_sql = str(calls[1][0][0])
        assert f"invoices.tenant_id = '{TEST_TENANT_ID}'" in second_call_sql

