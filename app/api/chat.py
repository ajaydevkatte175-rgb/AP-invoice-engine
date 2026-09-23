"""Chat API endpoints for the financial text-to-SQL AI agent.

Provides:
- POST /chat/ask: Natural language financial query endpoint with SQL guard,
  RLS tenant enforcement, two-step model calls, and grounded explanations.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import verify_api_key
from app.core.db import get_db
from app.core.logging import get_logger
from app.core.middleware import tenant_dependency
from app.services.agent.query_agent import QueryAgent
from app.services.agent.sql_guard import SQLGuardError

logger = get_logger(__name__)

router = APIRouter(tags=["Chat & Financial Agent"])
query_agent = QueryAgent()


class ChatAskRequest(BaseModel):
    """Request payload for financial text-to-SQL query."""

    question: str = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="Natural language financial question",
        examples=["What is total spend by vendor and currency?"],
    )
    session_id: uuid.UUID | None = Field(
        default=None,
        description="Optional ID of an existing conversational session",
    )


class ChatAskResponse(BaseModel):
    """Response payload containing generated SQL, retrieved rows, and grounded answer."""

    session_id: uuid.UUID
    question: str
    generated_sql: str
    query_result: list[dict[str, Any]]
    answer: str
    row_count: int


@router.post(
    "/chat/ask",
    response_model=ChatAskResponse,
    status_code=status.HTTP_200_OK,
    summary="Ask financial questions via Text-to-SQL AI Agent",
    description=(
        "Executes a two-step text-to-SQL pipeline: "
        "1) Generates SQL from question, "
        "2) Validates via sqlglot guard and executes with PostgreSQL RLS tenant context, "
        "3) Synthesizes grounded answer explaining returned rows."
    ),
)
async def ask_chat(
    req: ChatAskRequest,
    _api_key: str = Depends(verify_api_key),
    tenant_id: uuid.UUID = Depends(tenant_dependency),
    db: AsyncSession = Depends(get_db),
) -> ChatAskResponse:
    """Submit a natural language financial question to the text-to-SQL agent."""
    try:
        result = await query_agent.ask(
            question=req.question,
            tenant_id=tenant_id,
            session_id=req.session_id,
            db_session=db,
        )
        return ChatAskResponse(**result)
    except SQLGuardError as exc:
        logger.warning(
            "SQL guard rejected query",
            question=req.question,
            tenant_id=str(tenant_id),
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Security guard rejected generated SQL: {exc}",
        )
    except Exception as exc:
        logger.error(
            "Failed to execute financial chat query",
            question=req.question,
            tenant_id=str(tenant_id),
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process financial query: {exc}",
        )

