from contextlib import asynccontextmanager

from fastapi import FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware

from app.api.chat import router as chat_router
from app.api.documents import router as documents_router
from app.api.v1 import api_v1_router
from app.core.config import settings
from app.core.db import check_db_health, engine, engine_ro
from app.core.logging import get_logger, setup_logging
from app.core.middleware import TenantMiddleware
from app.workers.worker import close_arq_redis

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    logger.info("Application starting up", env=settings.APP_ENV)
    yield
    logger.info("Application shutting down")
    await close_arq_redis()
    await engine.dispose()
    await engine_ro.dispose()


app = FastAPI(
    title="AP Invoice Engine",
    description="Automated AP invoice extraction, deterministic validation, and AR issuance platform",
    version="0.1.0",
    lifespan=lifespan,
)

# Middleware
app.add_middleware(TenantMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(documents_router)
app.include_router(chat_router)
app.include_router(api_v1_router)


@app.get("/health", tags=["Health"])
async def health(response: Response):
    """Health check endpoint that verifies database connectivity."""
    db_connected = await check_db_health()
    if not db_connected:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "degraded",
            "database": "disconnected",
            "environment": settings.APP_ENV,
        }
    return {
        "status": "ok",
        "database": "connected",
        "environment": settings.APP_ENV,
    }


@app.get("/", tags=["Root"])
async def root():
    return {
        "name": "AP Invoice Engine API",
        "version": "0.1.0",
        "status": "running",
        "docs_url": "/docs",
    }
