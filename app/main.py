import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.health import router as health_router
from app.api.v1.router import api_v1_router
from app.core.config import settings
from app.core.logging import logger, set_request_id
from app.core.metrics import metrics_registry


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan event handler for application startup and shutdown."""
    logger.info("Starting up %s (env: %s)...", settings.APP_NAME, settings.APP_ENV)
    # Ensure storage directory exists
    storage_path = settings.resolved_storage_path
    logger.info("Storage directory verified at: %s", storage_path)
    yield
    logger.info("Shutting down %s...", settings.APP_NAME)


TAGS_METADATA = [
    {
        "name": "Health",
        "description": "System readiness, liveness, and component connectivity probes (Database, Redis broker).",
    },
    {
        "name": "Certificate Jobs",
        "description": "Asynchronous bulk generation job creation, real-time progress polling, and paginated recipient results.",
    },
    {
        "name": "Certificates",
        "description": "Authorized binary streaming of vector PDF certificates and cryptographic SHA256 integrity verification.",
    },
]


def create_application() -> FastAPI:
    """Application factory for FastAPI."""
    application = FastAPI(
        title=settings.APP_NAME,
        version="1.0.0",
        description=(
            "### Bulk Certificate Generator Backend API\n\n"
            "An enterprise-grade, asynchronous bulk certificate generation engine.\n\n"
            "**Key Features:**\n"
            "- Asynchronous bulk processing via Celery & Redis\n"
            "- Fault-tolerant partial success: invalid recipients fail independently\n"
            "- ReportLab vector PDF rendering with dynamic font scaling & decorative borders\n"
            "- Idempotent job submission with `Idempotency-Key` headers\n"
            "- Cryptographic SHA256 integrity checksums\n"
            "- Sliding-window rate limiting & path-traversal prevention\n"
        ),
        openapi_tags=TAGS_METADATA,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        contact={
            "name": "Engineering Team",
            "url": "https://github.com/org/bulk-certificate-generator",
        },
        license_info={
            "name": "MIT License",
            "url": "https://opensource.org/licenses/MIT",
        },
        lifespan=lifespan,
    )

    # CORS Configuration
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in settings.ALLOWED_ORIGINS.split(",")],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Observability: Correlation ID and Telemetry Middleware
    @application.middleware("http")
    async def correlation_and_metrics_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        set_request_id(request_id)
        request.state.request_id = request_id
        start_time = time.perf_counter()

        response = None
        try:
            response = await call_next(request)
            return response
        finally:
            duration = time.perf_counter() - start_time
            status_code = response.status_code if response else 500
            metrics_registry.record_http_request(
                method=request.method,
                path=request.url.path,
                status_code=status_code,
                duration_seconds=duration,
            )
            if response:
                response.headers["X-Request-ID"] = request_id
            set_request_id(None)

    # Security Headers and Payload Size Middleware
    @application.middleware("http")
    async def security_headers_and_limits_middleware(request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > settings.MAX_REQUEST_BODY_BYTES:
            return JSONResponse(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                content={
                    "error": "Payload Too Large",
                    "message": f"Request body exceeds maximum allowed size of {settings.MAX_REQUEST_BODY_BYTES} bytes.",
                },
            )

        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        return response

    # Global Exception Handlers
    @application.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        logger.warning("Validation error on %s: %s", request.url.path, exc.errors())
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": "Unprocessable Entity",
                "message": "The request payload failed validation checks.",
                "details": exc.errors(),
            },
        )

    @application.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        logger.error("Unhandled server exception on %s: %s", request.url.path, exc, exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "Internal Server Error",
                "message": "An unexpected error occurred while processing the request.",
            },
        )

    # Mount Routers
    application.include_router(health_router)
    application.include_router(api_v1_router, prefix=settings.API_V1_STR)

    return application


app = create_application()
