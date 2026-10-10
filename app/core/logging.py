"""Centralized Structured Logging Configuration."""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from app.core.config import settings

# Context Variables for Distributed Tracing & Correlation
request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)
job_id_ctx: ContextVar[str | None] = ContextVar("job_id", default=None)


def set_request_id(request_id: str | None) -> None:
    """Set the active request ID in async context."""
    request_id_ctx.set(request_id)


def get_request_id() -> str | None:
    """Retrieve active request ID from context."""
    return request_id_ctx.get()


def set_job_id(job_id: str | None) -> None:
    """Set the active job ID in async/task context."""
    job_id_ctx.set(job_id)


def get_job_id() -> str | None:
    """Retrieve active job ID from context."""
    return job_id_ctx.get()


class CorrelationIdFilter(logging.Filter):
    """Injects contextual correlation IDs (request_id, job_id) into all log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id() or "-"
        record.job_id = get_job_id() or "-"
        return True


class JsonLogFormatter(logging.Formatter):
    """
    JSON formatter emitting machine-readable structured logs.
    Compatible with Datadog, ELK, CloudWatch, and Grafana Loki.
    """

    def format(self, record: logging.LogRecord) -> str:
        log_payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "file": record.filename,
            "line": record.lineno,
            "request_id": getattr(record, "request_id", "-"),
            "job_id": getattr(record, "job_id", "-"),
        }

        if record.exc_info:
            log_payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_payload, default=str)


def setup_logging() -> logging.Logger:
    log_level = logging.DEBUG if settings.DEBUG else logging.INFO

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(log_level)
    handler.addFilter(CorrelationIdFilter())

    if settings.LOG_FORMAT.lower() == "json" or settings.APP_ENV == "production":
        handler.setFormatter(JsonLogFormatter())
    else:
        text_formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | [%(request_id)s] %(name)s:%(lineno)d - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(text_formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Avoid duplicate handlers on reload
    root_logger.handlers = [handler]

    # Silence overly chatty third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    logger = logging.getLogger("bulk_certificate_generator")
    logger.info(
        "Logging configured. Environment: %s, Format: %s, Debug: %s",
        settings.APP_ENV,
        settings.LOG_FORMAT,
        settings.DEBUG,
    )
    return logger


logger = setup_logging()
