import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

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


INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Bulk Certificate Generator</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <style>
    :root {
      --primary: #1e3a8a;
      --primary-light: #3b82f6;
      --card-bg: rgba(30, 41, 59, 0.7);
      --border: rgba(255, 255, 255, 0.1);
      --text: #f8fafc;
      --text-muted: #94a3b8;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      background: radial-gradient(circle at 50% 0%, #1e293b 0%, #0f172a 100%);
      color: var(--text);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      padding: 2.5rem 1rem;
    }
    .container { max-width: 760px; width: 100%; }
    .header { text-align: center; margin-bottom: 2rem; }
    .header h1 {
      font-size: 2.25rem;
      font-weight: 800;
      background: linear-gradient(135deg, #60a5fa, #fbbf24);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      margin-bottom: 0.5rem;
    }
    .header p { color: var(--text-muted); font-size: 1rem; }
    .card {
      background: var(--card-bg);
      backdrop-filter: blur(16px);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 2rem;
      box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.5);
      margin-bottom: 2rem;
    }
    .form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1.25rem; margin-bottom: 1.25rem; }
    .form-group { display: flex; flex-direction: column; gap: 0.5rem; }
    .form-group.full-width { grid-column: span 2; }
    label { font-size: 0.875rem; font-weight: 600; color: #cbd5e1; }
    input {
      background: rgba(15, 23, 42, 0.6);
      border: 1px solid rgba(255, 255, 255, 0.15);
      color: #fff;
      padding: 0.75rem 1rem;
      border-radius: 8px;
      font-size: 0.95rem;
    }
    input:focus {
      outline: none;
      border-color: var(--primary-light);
      box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.25);
    }
    .btn {
      background: linear-gradient(135deg, #2563eb, #1d4ed8);
      color: #fff;
      border: none;
      padding: 0.875rem 1.75rem;
      font-size: 1rem;
      font-weight: 600;
      border-radius: 8px;
      cursor: pointer;
      width: 100%;
      display: flex;
      justify-content: center;
      align-items: center;
      gap: 0.5rem;
    }
    .btn:hover {
      background: linear-gradient(135deg, #3b82f6, #2563eb);
      transform: translateY(-1px);
    }
    .btn-download {
      background: linear-gradient(135deg, #059669, #10b981);
      margin-top: 1rem;
      text-decoration: none;
      text-align: center;
      display: block;
    }
    .btn-download:hover {
      background: linear-gradient(135deg, #10b981, #34d399);
    }
    .result-box {
      margin-top: 1.5rem;
      padding: 1.5rem;
      background: rgba(16, 185, 129, 0.1);
      border: 1px solid rgba(16, 185, 129, 0.3);
      border-radius: 12px;
      display: none;
    }
    .result-header { font-weight: 700; color: #34d399; margin-bottom: 0.75rem; }
    .footer-links { text-align: center; color: var(--text-muted); font-size: 0.9rem; }
    .footer-links a { color: #60a5fa; text-decoration: none; margin: 0 0.5rem; font-weight: 500; }
    .spinner {
      border: 3px solid rgba(255, 255, 255, 0.3);
      border-radius: 50%;
      border-top: 3px solid #fff;
      width: 18px;
      height: 18px;
      animation: spin 1s linear infinite;
      display: none;
    }
    @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <h1>Bulk Certificate Generator</h1>
      <p>Generate, render, and download vector PDF certificates instantly</p>
    </div>

    <div class="card">
      <form id="certForm">
        <div class="form-grid">
          <div class="form-group">
            <label for="eventName">Course / Event Name</label>
            <input type="text" id="eventName" value="Cloud Native Architecture Bootcamp" required>
          </div>
          <div class="form-group">
            <label for="issuerName">Issuing Organization</label>
            <input type="text" id="issuerName" value="Cloud Native Foundation" required>
          </div>
          <div class="form-group">
            <label for="recipientName">Recipient Name</label>
            <input type="text" id="recipientName" value="Alex Johnson" required>
          </div>
          <div class="form-group">
            <label for="recipientEmail">Recipient Email</label>
            <input type="email" id="recipientEmail" value="alex.johnson@example.com" required>
          </div>
          <div class="form-group full-width">
            <label for="issueDate">Issue Date</label>
            <input type="date" id="issueDate" value="2026-10-10" required>
          </div>
        </div>

        <button type="submit" id="submitBtn" class="btn">
          <span class="spinner" id="spinner"></span>
          <span id="btnText">✨ Generate Certificate PDF</span>
        </button>
      </form>

      <div class="result-box" id="resultBox">
        <div class="result-header">
          ✓ Certificate Generated Successfully!
        </div>
        <p style="color: #cbd5e1; font-size: 0.95rem; margin-bottom: 0.5rem;">
          Recipient: <strong id="resName" style="color:#fff;"></strong>
        </p>
        <p style="color: #94a3b8; font-size: 0.85rem; margin-bottom: 1rem;">
          Certificate ID: <code id="resCertId" style="color: #f1f5f9; background: rgba(0,0,0,0.3); padding: 2px 6px; border-radius: 4px;"></code>
        </p>
        <div style="display: flex; gap: 0.75rem; margin-top: 1rem; flex-wrap: wrap;">
          <a id="viewLink" href="#" class="btn" style="flex: 1; background: linear-gradient(135deg, #2563eb, #1d4ed8); text-decoration: none; text-align: center;" target="_blank">
            👁️ Open PDF in New Tab
          </a>
          <a id="downloadLink" href="#" class="btn btn-download" style="flex: 1; margin-top: 0;" target="_blank" download>
            📥 Download PDF File
          </a>
        </div>

        <iframe id="pdfIframe" src="" style="width: 100%; height: 500px; border: 1px solid rgba(255,255,255,0.2); border-radius: 8px; margin-top: 1.25rem; display: none; background: #fff;"></iframe>
      </div>
    </div>

    <div class="footer-links">
      <a href="/docs" target="_blank">📖 Swagger UI Documentation (/docs)</a> •
      <a href="/health" target="_blank">🩺 Health Probe (/health)</a> •
      <a href="/metrics" target="_blank">📊 Metrics (/metrics)</a>
    </div>
  </div>

  <script>
    const form = document.getElementById('certForm');
    const submitBtn = document.getElementById('submitBtn');
    const spinner = document.getElementById('spinner');
    const btnText = document.getElementById('btnText');
    const resultBox = document.getElementById('resultBox');
    const viewLink = document.getElementById('viewLink');
    const downloadLink = document.getElementById('downloadLink');
    const pdfIframe = document.getElementById('pdfIframe');
    const resName = document.getElementById('resName');
    const resCertId = document.getElementById('resCertId');

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      spinner.style.display = 'inline-block';
      btnText.innerText = 'Rendering PDF...';
      submitBtn.disabled = true;
      resultBox.style.display = 'none';
      pdfIframe.style.display = 'none';

      const payload = {
        event_name: document.getElementById('eventName').value,
        issuer_name: document.getElementById('issuerName').value,
        issue_date: document.getElementById('issueDate').value,
        recipients: [
          {
            name: document.getElementById('recipientName').value,
            email: document.getElementById('recipientEmail').value,
            custom_identifier: 'CERT-' + Math.floor(1000 + Math.random() * 9000)
          }
        ]
      };

      try {
        const postRes = await fetch('/api/v1/certificate-jobs', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const job = await postRes.json();

        await new Promise(r => setTimeout(r, 400));

        const resQuery = await fetch(`/api/v1/certificate-jobs/${job.job_id}/results`);
        const resData = await resQuery.json();
        const cert = resData.results[0];

        if (cert && cert.certificate_id) {
          resName.innerText = cert.name;
          resCertId.innerText = cert.certificate_id;
          const pdfUrl = `/api/v1/certificates/${cert.certificate_id}`;
          viewLink.href = pdfUrl + '?inline=true';
          downloadLink.href = pdfUrl;
          downloadLink.setAttribute('download', cert.name.replace(/\s+/g, '_') + '_Certificate.pdf');
          pdfIframe.src = pdfUrl + '?inline=true';
          pdfIframe.style.display = 'block';
          resultBox.style.display = 'block';
        } else {
          alert('Could not generate certificate: ' + (cert.error_message || 'Unknown error'));
        }
      } catch (err) {
        alert('Error: ' + err.message);
      } finally {
        spinner.style.display = 'none';
        btnText.innerText = '✨ Generate Certificate PDF';
        submitBtn.disabled = false;
      }
    });
  </script>
</body>
</html>
"""


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

    # Interactive Web Interface
    @application.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index():
        return HTMLResponse(content=INDEX_HTML)

    # Mount Routers
    application.include_router(health_router)
    application.include_router(api_v1_router, prefix=settings.API_V1_STR)

    return application


app = create_application()
