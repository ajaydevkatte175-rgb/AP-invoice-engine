"""Financial text-to-SQL query agent.

NON-NEGOTIABLE ARCHITECTURAL RULES:
1. Rule 6: SQL guard with sqlglot (exactly one statement, SELECT only, table allowlist,
   tenant_id filter injected, LIMIT capped). Executes via invoice_ro read-only role
   with 5-second statement_timeout.
2. Rule 7: Postgres RLS enforcement. SET LOCAL app.current_tenant = '{tenant_id}' on connection.
3. Rule 8: TWO model calls — one to generate SQL, one to explain returned rows.
   Never one call predicting the answer.
4. Rule 13: Offline deterministic fallback so tests pass with NO live API key set.
"""

import json
import re
import uuid
from typing import Any

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ailayer.errors import AuthenticationError, ProviderError
from ailayer.prompts import PromptLoader
from ailayer.router import LLMRouter
from ailayer.types import LLMMessage, LLMRequest
from app.core.config import settings
from app.core.db import AsyncSessionLocal
from app.models.system import AuditLog, ChatMessage, ChatSession
from app.services.agent.sql_guard import (
    execute_guarded_query,
)

logger = structlog.get_logger(__name__)

prompt_loader = PromptLoader()


def extract_sql_from_text(raw_text: str) -> str:
    """Extract SQL query string from LLM output, handling markdown blocks."""
    match = re.search(r"```(?:sql)?\s*([\s\S]*?)\s*```", raw_text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return raw_text.strip()


def heuristic_sql_from_question(question: str) -> str:
    """Deterministic rule-based SQL generator for testing without a live API key (Rule 13)."""
    q = question.lower().strip()

    if "vendor" in q and ("spend" in q or "currency" in q or "total" in q):
        return (
            "SELECT vendor_name, currency, SUM(total_amount) AS total_spend, COUNT(*) AS invoice_count "
            "FROM invoices "
            "GROUP BY vendor_name, currency "
            "ORDER BY total_spend DESC"
        )
    if "top vendor" in q or "top vendors" in q:
        return (
            "SELECT vendor_name, SUM(total_amount) AS total_spend, COUNT(*) AS invoice_count "
            "FROM invoices "
            "GROUP BY vendor_name "
            "ORDER BY total_spend DESC LIMIT 5"
        )
    if "needs_review" in q or "flag" in q or "review" in q:
        return (
            "SELECT invoice_number, vendor_name, total_amount, currency, status "
            "FROM invoices "
            "WHERE status = 'needs_review' "
            "ORDER BY created_at DESC"
        )
    if "how many" in q or "count" in q or "status" in q:
        return (
            "SELECT status, COUNT(*) AS count, SUM(total_amount) AS total_amount "
            "FROM invoices "
            "GROUP BY status"
        )
    if "customer" in q or "issued" in q or "receivable" in q:
        return (
            "SELECT c.name AS customer_name, SUM(ii.total_amount) AS total_amount, COUNT(*) AS invoice_count "
            "FROM issued_invoices ii JOIN customers c ON ii.customer_id = c.id "
            "GROUP BY c.name "
            "ORDER BY total_amount DESC"
        )

    # General fallback
    return (
        "SELECT invoice_number, vendor_name, invoice_date, total_amount, currency, status "
        "FROM invoices "
        "ORDER BY created_at DESC LIMIT 10"
    )


def heuristic_explain_rows(question: str, sql: str, rows: list[dict[str, Any]]) -> str:
    """Deterministic natural language summary for testing without a live API key (Rule 13)."""
    if not rows:
        return f"No records were found matching your query: '{question}'."

    count = len(rows)

    # Spend by vendor and currency
    if "vendor_name" in rows[0] and "total_spend" in rows[0]:
        lines = []
        for r in rows[:5]:
            v = r.get("vendor_name", "Unknown")
            c = r.get("currency", "")
            amt = r.get("total_spend", 0)
            invs = r.get("invoice_count", "")
            if isinstance(amt, (int, float)):
                lines.append(f"- **{v}**: {amt:,.2f} {c} ({invs} invoice(s))")
            else:
                lines.append(f"- **{v}**: {amt} {c} ({invs} invoice(s))")
        return f"Total spend breakdown across {count} vendor/currency group(s):\n" + "\n".join(
            lines
        )

    # Invoices list
    if "vendor_name" in rows[0] and "total_amount" in rows[0]:
        lines = [
            f"- Invoice {r.get('invoice_number', 'N/A')} from {r.get('vendor_name', 'Unknown')}: {r.get('total_amount')} {r.get('currency', '')} (Status: {r.get('status', 'N/A')})"
            for r in rows[:5]
        ]
        return f"Found {count} invoice record(s):\n" + "\n".join(lines)

    # Status counts
    if "status" in rows[0] and "count" in rows[0]:
        lines = [f"- **{r.get('status')}**: {r.get('count')} invoices" for r in rows]
        return "Invoice breakdown by status:\n" + "\n".join(lines)

    # Customer issued amounts
    if "customer_name" in rows[0] and "total_amount" in rows[0]:
        lines = [
            f"- **{r.get('customer_name')}**: {r.get('total_amount')} ({r.get('invoice_count', 0)} invoices)"
            for r in rows[:5]
        ]
        return f"Issued invoices summary across {count} customer(s):\n" + "\n".join(lines)

    return f"Returned {count} record(s) answering '{question}': " + ", ".join(
        f"{k}={v}" for k, v in rows[0].items()
    )


class QueryAgent:
    """Two-step grounded text-to-SQL financial agent.

    Workflow:
    1. Model Call 1: Generate SQL from question & database schema.
    2. SQL Guard: Validate & sanitize with sqlglot (reject mutations, table allowlist, tenant injection, limit).
    3. Read-Only Execution: Execute with PostgreSQL RLS tenant context (invoice_ro role, statement_timeout=5s).
    4. Model Call 2: Explain returned rows strictly based on data actually retrieved.
    5. Persistence: Save session, messages, and audit log.
    """

    def __init__(self, router: LLMRouter | None = None) -> None:
        self.router = router or LLMRouter()

    def _has_live_api_key(self) -> bool:
        """Check if a valid, non-dummy Anthropic API key is configured."""
        key = settings.ANTHROPIC_API_KEY
        if not key:
            return False
        return not (key.startswith(("your_actual", "dummy")) or len(key) < 10)

    async def _generate_sql(
        self,
        question: str,
        tenant_id: uuid.UUID,
        session: AsyncSession | None = None,
    ) -> str:
        """Step 1: Generate SQL query from natural language question."""
        if not self._has_live_api_key():
            logger.info("Using offline heuristic SQL generator (Non-Negotiable Rule 13)")
            return heuristic_sql_from_question(question)

        try:
            prompt_tpl = prompt_loader.load("text_to_sql", version=1)
            system_prompt = prompt_tpl.render_system()
            user_prompt = prompt_tpl.render_user(question=question)

            req = LLMRequest(
                messages=[LLMMessage(role="user", content=user_prompt)],
                system=system_prompt,
                call_type="agent_sql_generate",
                tenant_id=tenant_id,
            )

            res = await self.router.execute(req, session=session)
            return extract_sql_from_text(res.content)
        except (AuthenticationError, ProviderError, Exception) as exc:
            logger.warning(
                "Model call 1 (SQL generation) failed, falling back to heuristic",
                error=str(exc),
            )
            return heuristic_sql_from_question(question)

    async def _explain_results(
        self,
        question: str,
        sql: str,
        rows: list[dict[str, Any]],
        tenant_id: uuid.UUID,
        session: AsyncSession | None = None,
    ) -> str:
        """Step 2: Synthesize grounded natural language explanation strictly from returned rows."""
        if not self._has_live_api_key():
            logger.info("Using offline heuristic explanation synthesizer (Non-Negotiable Rule 13)")
            return heuristic_explain_rows(question, sql, rows)

        try:
            prompt_tpl = prompt_loader.load("explain_sql", version=1)
            system_prompt = prompt_tpl.render_system()
            user_prompt = prompt_tpl.render_user(
                question=question,
                sql=sql,
                rows=json.dumps(rows, default=str),
            )

            req = LLMRequest(
                messages=[LLMMessage(role="user", content=user_prompt)],
                system=system_prompt,
                call_type="agent_sql_explain",
                tenant_id=tenant_id,
            )

            res = await self.router.execute(req, session=session)
            return res.content.strip()
        except (AuthenticationError, ProviderError, Exception) as exc:
            logger.warning(
                "Model call 2 (Explain SQL) failed, falling back to heuristic",
                error=str(exc),
            )
            return heuristic_explain_rows(question, sql, rows)

    async def ask(
        self,
        question: str,
        tenant_id: uuid.UUID,
        session_id: uuid.UUID | None = None,
        db_session: AsyncSession | None = None,
    ) -> dict[str, Any]:
        """Execute end-to-end two-step Text-to-SQL financial query pipeline.

        Returns:
            dict containing session_id, question, generated_sql, query_result, answer, row_count
        """
        logger.info(
            "Processing user financial question", question=question, tenant_id=str(tenant_id)
        )

        # 1. Model Call 1: Generate SQL
        raw_sql = await self._generate_sql(question, tenant_id=tenant_id, session=db_session)

        # 2 & 3. Guard & Execute on Read-Only Engine with RLS context (Rules 6 & 7)
        sanitized_sql, rows = await execute_guarded_query(
            sql=raw_sql,
            tenant_id=tenant_id,
            max_rows=settings.AGENT_MAX_ROWS,
        )

        # 4. Model Call 2: Explain rows returned (Rule 8)
        answer = await self._explain_results(
            question=question,
            sql=sanitized_sql,
            rows=rows,
            tenant_id=tenant_id,
            session=db_session,
        )

        # 5. Persist session, messages, and audit log
        chat_sess_id = session_id or uuid.uuid4()

        async def _persist(s: AsyncSession) -> None:
            # Enforce RLS tenant context
            await s.execute(text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))

            # Retrieve or create chat session
            result = await s.execute(
                select(ChatSession).where(
                    ChatSession.id == chat_sess_id,
                    ChatSession.tenant_id == tenant_id,
                )
            )
            chat_sess = result.scalar_one_or_none()
            if not chat_sess:
                chat_sess = ChatSession(
                    id=chat_sess_id,
                    tenant_id=tenant_id,
                    title=question[:100],
                )
                s.add(chat_sess)

            # Record user message
            user_msg = ChatMessage(
                tenant_id=tenant_id,
                session_id=chat_sess.id,
                role="user",
                content=question,
            )
            s.add(user_msg)

            # Record assistant message with generated SQL and query results
            assistant_msg = ChatMessage(
                tenant_id=tenant_id,
                session_id=chat_sess.id,
                role="assistant",
                content=answer,
                generated_sql=sanitized_sql,
                query_result=rows,
            )
            s.add(assistant_msg)

            # Record immutable audit log
            audit = AuditLog(
                tenant_id=tenant_id,
                action="CHAT_QUERY",
                actor_type="AI_AGENT",
                actor_id="query_agent",
                resource_type="chat_session",
                resource_id=chat_sess.id,
                metadata_json={
                    "question": question,
                    "generated_sql": sanitized_sql,
                    "row_count": len(rows),
                },
            )
            s.add(audit)
            await s.commit()

        if db_session is not None:
            await _persist(db_session)
        else:
            async with AsyncSessionLocal() as s:
                await _persist(s)

        return {
            "session_id": chat_sess_id,
            "question": question,
            "generated_sql": sanitized_sql,
            "query_result": rows,
            "answer": answer,
            "row_count": len(rows),
        }
