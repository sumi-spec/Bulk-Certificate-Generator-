"""Application Configuration Module."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    APP_NAME: str = "Bulk Certificate Generator"
    APP_ENV: str = "development"
    DEBUG: bool = True
    PORT: int = 8000
    HOST: str = "0.0.0.0"

    # Database
    DATABASE_URL: str = Field(
        default="postgresql://cert_user:cert_password@localhost:5432/cert_db",
        description="Relational database connection string",
    )

    # Celery & Redis
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/0"

    # Storage
    STORAGE_DIR: str = "./storage/certificates"

    # API Routing
    API_V1_STR: str = "/api/v1"
    ALLOWED_ORIGINS: str = "*"

    # Security & API Keys
    API_KEY: str | None = None
    MAX_REQUEST_BODY_BYTES: int = 10 * 1024 * 1024  # 10MB limit

    # Observability & Retention
    LOG_FORMAT: str = "text"  # "text" or "json"
    RETENTION_DAYS: int = 90

    # Limits
    MAX_RECIPIENTS_PER_JOB: int = 5000
    CHUNK_SIZE: int = 50

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")

    @property
    def resolved_storage_path(self) -> Path:
        path = Path(self.STORAGE_DIR).resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path


settings = Settings()
