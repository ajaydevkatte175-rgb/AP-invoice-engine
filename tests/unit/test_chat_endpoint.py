"""Unit tests for Chat API endpoint (POST /chat/ask) and QueryAgent text-to-SQL pipeline.

Verifies:
1. API key authentication enforcement (401 on missing or invalid key).
2. End-to-end question processing returning question, generated_sql, query_result, and answer.
3. Conversational session continuity with session_id.
4. SQL guard rejection returning 400 Bad Request.
5. Persistent chat messages and audit logging.
6. Execution without live Anthropic API key (Non-Negotiable Rule 13).
"""

import uuid
from unittest.mock import patch
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.system import AuditLog, ChatMessage, ChatSession
from app.services.agent.query_agent import QueryAgent
from app.services.agent.sql_guard import MutationNotAllowedError


@pytest.mark.asyncio
async def test_chat_ask_authentication():
    """Verify POST /chat/ask requires valid API key authentication."""
    transport = ASGITransport(app=app)
    tenant = str(uuid.uuid4())

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Missing API key returns 401
        res_missing = await client.post(
            "/chat/ask",
            headers={"X-Tenant-ID": tenant},
            json={"question": "What is total spend by vendor and currency?"},
        )
        assert res_missing.status_code == 401

        # 2. Invalid API key returns 401
        res_invalid = await client.post(
            "/chat/ask",
            headers={"X-Tenant-ID": tenant, "X-API-Key": "wrong_key_xyz"},
            json={"question": "What is total spend by vendor and currency?"},
        )
        assert res_invalid.status_code == 401


@pytest.mark.asyncio
async def test_chat_ask_endpoint_success():
    """Verify POST /chat/ask processes question and returns SQL + answer (Rule 8 & 13)."""
    transport = ASGITransport(app=app)
    tenant = str(uuid.uuid4())

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/chat/ask",
            headers={"X-Tenant-ID": tenant, "X-API-Key": settings.API_KEY},
            json={"question": "What is total spend by vendor and currency?"},
        )

        assert res.status_code == 200
        data = res.json()

        assert "session_id" in data
        assert data["question"] == "What is total spend by vendor and currency?"
        assert "generated_sql" in data
        assert "SELECT" in data["generated_sql"].upper()
        assert "invoices" in data["generated_sql"].lower()
        assert "query_result" in data
        assert isinstance(data["query_result"], list)
        assert "answer" in data
        assert len(data["answer"]) > 0
        assert "row_count" in data


@pytest.mark.asyncio
async def test_chat_ask_session_continuity():
    """Verify conversational session continuity across multiple turns."""
    transport = ASGITransport(app=app)
    tenant = str(uuid.uuid4())

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Turn 1: Initial question
        res1 = await client.post(
            "/chat/ask",
            headers={"X-Tenant-ID": tenant, "X-API-Key": settings.API_KEY},
            json={"question": "What is total spend by vendor and currency?"},
        )
        assert res1.status_code == 200
        session_id = res1.json()["session_id"]

        # Turn 2: Follow-up question referencing session_id
        res2 = await client.post(
            "/chat/ask",
            headers={"X-Tenant-ID": tenant, "X-API-Key": settings.API_KEY},
            json={
                "question": "How many invoices are in needs_review status?",
                "session_id": session_id,
            },
        )
        assert res2.status_code == 200
        data2 = res2.json()
        assert data2["session_id"] == session_id

    # Verify session and messages in database
    async with AsyncSessionLocal() as session:
        sess_uuid = uuid.UUID(session_id)
        chat_sess = await session.get(ChatSession, sess_uuid)
        assert chat_sess is not None

        stmt = select(ChatMessage).where(ChatMessage.session_id == sess_uuid).order_by(ChatMessage.created_at)
        result = await session.execute(stmt)
        messages = result.scalars().all()
        # 2 user messages + 2 assistant messages = 4 messages
        assert len(messages) >= 4


@pytest.mark.asyncio
async def test_chat_ask_guard_rejection_returns_400():
    """Verify that if generated SQL violates guard, the API returns HTTP 400."""
    transport = ASGITransport(app=app)
    tenant = str(uuid.uuid4())

    with patch(
        "app.services.agent.query_agent.QueryAgent._generate_sql",
        return_value="DROP TABLE invoices",
    ):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post(
                "/chat/ask",
                headers={"X-Tenant-ID": tenant, "X-API-Key": settings.API_KEY},
                json={"question": "Delete everything"},
            )
            assert res.status_code == 400
            assert "Security guard rejected" in res.json()["detail"]


@pytest.mark.asyncio
async def test_query_agent_direct_ask_and_audit_log():
    """Verify QueryAgent creates audit log and handles various query types."""
    agent = QueryAgent()
    tenant_id = uuid.uuid4()

    result = await agent.ask(
        question="What are our top vendors by spend?",
        tenant_id=tenant_id,
    )

    assert result["question"] == "What are our top vendors by spend?"
    assert "LIMIT" in result["generated_sql"]
    assert "answer" in result

    # Verify audit log created
    async with AsyncSessionLocal() as session:
        stmt = select(AuditLog).where(
            AuditLog.tenant_id == tenant_id,
            AuditLog.action == "CHAT_QUERY",
        )
        log_res = await session.execute(stmt)
        audit_entry = log_res.scalar_one_or_none()
        assert audit_entry is not None
        assert audit_entry.actor_type == "AI_AGENT"
        assert audit_entry.resource_type == "chat_session"

