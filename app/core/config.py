from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"
    SECRET_KEY: str = "dev_secret_key_change_in_production_32bytes"
    API_KEY: str = "dev_api_key_for_testing_123456"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:devpassword@localhost:5432/invoicedb"
    DATABASE_URL_RO: str = "postgresql+asyncpg://invoice_ro:readonlypass@localhost:5432/invoicedb"

    # Redis & Queue
    REDIS_URL: str = "redis://127.0.0.1:6379/0"

    # LLM & AI
    ANTHROPIC_API_KEY: str = ""
    LLM_MONTHLY_BUDGET_USD: float = 25.0
    CONFIDENCE_THRESHOLD: float = 0.85

    # File Upload & Preprocessing
    MAX_UPLOAD_MB: int = 20
    RENDER_DPI: int = 200
    STORAGE_PATH: str = "./data/uploads"

    # SQL Agent Guardrails
    AGENT_MAX_ROWS: int = 200
    AGENT_QUERY_TIMEOUT_MS: int = 5000

    # API
    API_URL: str = "http://localhost:8000"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
