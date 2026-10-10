"""Health and Readiness Check Endpoint."""

from datetime import UTC, datetime

import redis
from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.logging import logger
from app.core.metrics import metrics_registry

router = APIRouter(tags=["Health"])


@router.get("/health", summary="System Health & Readiness Probe")
def health_check(db: Session = Depends(get_db)):
    """
    Checks operational health of API service, database connection, and redis broker.
    Returns 200 OK when services are reachable, or 503 Service Unavailable if degraded.
    """
    db_status = "connected"
    redis_status = "connected"
    overall_status = "healthy"
    status_code = status.HTTP_200_OK

    # 1. Check Database
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        logger.error("Health check database failure: %s", exc)
        db_status = f"disconnected ({type(exc).__name__})"
        overall_status = "degraded"
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    # 2. Check Redis
    try:
        r = redis.from_url(settings.CELERY_BROKER_URL, socket_timeout=2)
        r.ping()
    except Exception as exc:
        logger.warning("Health check redis failure: %s", exc)
        redis_status = f"disconnected ({type(exc).__name__})"
        overall_status = "degraded"
        # Redis being unavailable degrades async worker queuing

    response_payload = {
        "status": overall_status,
        "app_name": settings.APP_NAME,
        "environment": settings.APP_ENV,
        "database": db_status,
        "redis": redis_status,
        "timestamp": datetime.now(UTC).isoformat(),
    }

    return JSONResponse(status_code=status_code, content=response_payload)


@router.get("/health/storage", summary="Storage Volume Health & Capacity Probe")
def storage_health_check():
    """
    Inspects persistent storage directory accessibility, file count, and byte volume.
    """
    storage_path = settings.resolved_storage_path
    accessible = storage_path.exists() and storage_path.is_dir()

    total_files = 0
    total_bytes = 0

    if accessible:
        try:
            for entry in storage_path.iterdir():
                if entry.is_file():
                    total_files += 1
                    total_bytes += entry.stat().st_size
        except Exception as exc:
            logger.error("Error inspecting storage volume: %s", exc)
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"status": "error", "message": f"Storage read error: {exc}"},
            )

    return {
        "status": "healthy" if accessible else "unhealthy",
        "storage_dir": str(storage_path),
        "total_files": total_files,
        "total_size_bytes": total_bytes,
        "total_size_mb": round(total_bytes / (1024 * 1024), 2),
    }


@router.get(
    "/metrics",
    summary="Prometheus Telemetry & Metrics",
    response_class=PlainTextResponse,
)
def get_prometheus_metrics(request: Request, format: str | None = None):
    """
    Exports application telemetry in standard Prometheus text format or JSON.
    Used by Prometheus scrapers, Datadog agents, or cloud health monitors.
    """
    accept_header = request.headers.get("accept", "")
    if format == "json" or "application/json" in accept_header:
        return JSONResponse(content=metrics_registry.to_dict())

    return PlainTextResponse(
        content=metrics_registry.to_prometheus_text(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
