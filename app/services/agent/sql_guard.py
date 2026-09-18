"""SQL Guard for text-to-SQL financial agent queries.

NON-NEGOTIABLE ARCHITECTURAL RULES (Rules 6 & 7):
1. The AI agent generates SQL that is validated before execution by a guard using sqlglot:
   - Exactly one statement
   - SELECT only (strictly reject mutations like DELETE, DROP, UPDATE, INSERT, TRUNCATE, ALTER)
   - Table allowlist enforced
   - tenant_id filter injected if absent
   - LIMIT capped (default max 200 rows)
2. Executes as a READ-ONLY Postgres role (invoice_ro) with statement_timeout = '5s'.
3. Database Tenant Safety Net (Postgres RLS): In addition to sqlglot, Postgres Row-Level
   Security (RLS) is enabled. Every execution MUST force:
   `SET LOCAL app.current_tenant = 'tenant-uuid'`
   so cross-tenant leakage is impossible at the database kernel level.
"""

import uuid
from typing import Any
from decimal import Decimal
from datetime import date, datetime
import sqlglot
from sqlglot import exp
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from app.core.config import settings
from app.core.db import AsyncSessionLocalRO

logger = structlog.get_logger(__name__)

# Allowed tables for agent text-to-SQL queries
ALLOWED_TABLES: set[str] = {
    "documents",
    "invoices",
    "line_items",
    "validation_flags",
    "review_items",
    "tenant_profile",
    "invoice_counters",
    "customers",
    "issued_invoices",
    "issued_line_items",
    "audit_log",
    "chat_sessions",
    "chat_messages",
}

# Tables that have tenant_id column for tenant filter injection
TENANT_FILTERABLE_TABLES: set[str] = {
    "documents",
    "invoices",
    "line_items",
    "validation_flags",
    "review_items",
    "tenant_profile",
    "invoice_counters",
    "customers",
    "issued_invoices",
    "issued_line_items",
    "audit_log",
    "chat_sessions",
    "chat_messages",
}

# AST expression types strictly forbidden in queries
DISALLOWED_EXPRESSION_TYPES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Alter,
    exp.Create,
    exp.Command,
    exp.TruncateTable,
    exp.Grant,
    exp.Revoke,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Set,
)


class SQLGuardError(Exception):
    """Base exception for all SQL guard validation failures."""


class InvalidQueryError(SQLGuardError):
    """Raised when the query is empty or syntactically unparseable."""


class StackedStatementsError(SQLGuardError):
    """Raised when multiple SQL statements are detected."""


class MutationNotAllowedError(SQLGuardError):
    """Raised when any mutation or DDL/DML operation is detected."""


class DisallowedTableError(SQLGuardError):
    """Raised when a table outside the allowed list is queried."""


class TenantSecurityError(SQLGuardError):
    """Raised when cross-tenant access is attempted."""


def validate_and_sanitize_sql(
    raw_sql: str,
    tenant_id: str | uuid.UUID,
    max_rows: int = 200,
) -> str:
    """Validate and sanitize a generated SQL query using sqlglot AST analysis.

    Enforces:
    1. Exactly one statement.
    2. Read-only query expression (Select, Union). Rejects all mutations.
    3. All queried tables reside in the allowlist.
    4. Tenant isolation filter injection (and verification).
    5. LIMIT injection / capping to max_rows.

    Returns the sanitized SQL string.
    """
    if not raw_sql or not raw_sql.strip():
        raise InvalidQueryError("Query cannot be empty.")

    tenant_uuid_str = str(tenant_id).strip()

    # Parse statements
    try:
        parsed = [s for s in sqlglot.parse(raw_sql, read="postgres") if s is not None]
    except Exception as exc:
        raise InvalidQueryError(f"Failed to parse SQL query: {exc}") from exc

    if len(parsed) == 0:
        raise InvalidQueryError("No valid SQL statement found.")

    if len(parsed) > 1:
        raise StackedStatementsError(
            f"Multiple SQL statements detected ({len(parsed)}). Stacked queries are strictly forbidden."
        )

    ast = parsed[0]

    # Rule: Must be a Query (Select or Union)
    if not isinstance(ast, exp.Query):
        raise MutationNotAllowedError(
            f"Only SELECT queries are allowed. Root statement is {type(ast).__name__}."
        )

    # Rule: Scan AST for ANY prohibited mutation or DDL/DML expressions
    for disallowed_type in DISALLOWED_EXPRESSION_TYPES:
        found_nodes = list(ast.find_all(disallowed_type))
        if found_nodes:
            raise MutationNotAllowedError(
                f"Prohibited mutation or management expression detected: {disallowed_type.__name__}."
            )

    # Collect CTE aliases so we do not treat them as physical tables
    cte_aliases = {cte.alias.lower() for cte in ast.find_all(exp.CTE) if cte.alias}

    # Rule: Check all physical tables against ALLOWED_TABLES
    referenced_tables: list[exp.Table] = []
    for tbl_node in ast.find_all(exp.Table):
        tbl_name = tbl_node.name.lower()
        if tbl_name in cte_aliases:
            continue
        referenced_tables.append(tbl_node)
        if tbl_name not in ALLOWED_TABLES:
            raise DisallowedTableError(
                f"Query references disallowed table: '{tbl_name}'. Allowed tables: {sorted(ALLOWED_TABLES)}"
            )

    # Rule: Tenant filter verification and injection
    # Traverse all Select nodes in AST (including in CTEs, Unions, Subqueries)
    for select_node in ast.find_all(exp.Select):
        from_node = select_node.args.get("from_")
        if not from_node:
            continue

        local_tables = [
            t for t in from_node.find_all(exp.Table)
            if t.name.lower() not in cte_aliases
        ]
        if not local_tables:
            continue

        where_node = select_node.args.get("where")
        has_tenant = False

        if where_node:
            for col in where_node.find_all(exp.Column):
                if col.name.lower() == "tenant_id":
                    has_tenant = True
                    # Check for explicit literal comparison to other tenants
                    parent = col.parent
                    if isinstance(parent, exp.EQ):
                        other_side = parent.expression if parent.this == col else parent.this
                        if isinstance(other_side, exp.Literal) and str(other_side.this) != tenant_uuid_str:
                            raise TenantSecurityError(
                                f"Cross-tenant access attempted: filter {other_side.this} does not match authorized tenant {tenant_uuid_str}."
                            )

        # Inject tenant_id filter if absent on physical tenant tables
        if not has_tenant:
            target_table = None
            for tbl in local_tables:
                if tbl.name.lower() in TENANT_FILTERABLE_TABLES:
                    target_table = tbl
                    break

            if target_table:
                qualifier = target_table.alias or target_table.name
                tenant_condition = exp.EQ(
                    this=exp.column("tenant_id", table=qualifier),
                    expression=exp.Literal.string(tenant_uuid_str),
                )
                select_node.where(tenant_condition, copy=False)

    # Rule: LIMIT capping / injection
    limit_node = ast.args.get("limit")
    if limit_node:
        try:
            current_limit = int(limit_node.expression.this)
            if current_limit > max_rows or current_limit <= 0:
                ast.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))
        except (ValueError, TypeError, AttributeError):
            ast.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))
    else:
        ast.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))

    return ast.sql("postgres")


def _serialize_row_value(val: Any) -> Any:
    """Serialize database column value for JSON compatibility."""
    if isinstance(val, Decimal):
        return float(val)
    if isinstance(val, (datetime, date)):
        return val.isoformat()
    if isinstance(val, uuid.UUID):
        return str(val)
    return val


async def execute_guarded_query(
    sql: str,
    tenant_id: str | uuid.UUID,
    session: AsyncSession | None = None,
    max_rows: int = 200,
) -> tuple[str, list[dict[str, Any]]]:
    """Validate, sanitize, and execute a SQL query with PostgreSQL RLS tenant context.

    Enforces:
    1. SQL guard validation (SELECT only, single statement, table allowlist, tenant injection, limit).
    2. Enforces `SET LOCAL app.current_tenant = '{tenant_id}'` on the connection.
    3. Executes query via read-only session (invoice_ro role).
    4. Returns sanitized SQL and serialized result rows.
    """
    tenant_uuid_str = str(tenant_id).strip()
    sanitized_sql = validate_and_sanitize_sql(sql, tenant_uuid_str, max_rows=max_rows)

    logger.info(
        "Executing guarded SQL query",
        tenant_id=tenant_uuid_str,
        sanitized_sql=sanitized_sql,
    )

    if session is not None:
        # Enforce RLS tenant context
        await session.execute(text(f"SET LOCAL app.current_tenant = '{tenant_uuid_str}'"))
        result = await session.execute(text(sanitized_sql))
        raw_rows = result.mappings().all()
        rows = [
            {k: _serialize_row_value(v) for k, v in dict(row).items()}
            for row in raw_rows
        ]
        return sanitized_sql, rows

    async with AsyncSessionLocalRO() as ro_session:
        # Enforce RLS tenant context on read-only session
        await ro_session.execute(text(f"SET LOCAL app.current_tenant = '{tenant_uuid_str}'"))
        result = await ro_session.execute(text(sanitized_sql))
        raw_rows = result.mappings().all()
        rows = [
            {k: _serialize_row_value(v) for k, v in dict(row).items()}
            for row in raw_rows
        ]
        return sanitized_sql, rows
