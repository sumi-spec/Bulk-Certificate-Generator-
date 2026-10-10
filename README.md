# Bulk Certificate Generator Backend API

[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688.svg)](https://fastapi.tiangolo.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-336791.svg)](https://www.postgresql.org/)
[![SQLAlchemy 2.0](https://img.shields.io/badge/SQLAlchemy-2.0-red.svg)](https://www.sqlalchemy.org/)
[![Celery](https://img.shields.io/badge/Celery-5.4%2B-37814A.svg)](https://docs.celeryq.dev/)
[![Redis](https://img.shields.io/badge/Redis-7-DC382D.svg)](https://redis.io/)
[![ReportLab](https://img.shields.io/badge/ReportLab-4.2%2B-orange.svg)](https://www.reportlab.com/)
[![Test Coverage](https://img.shields.io/badge/Coverage-99.88%25-brightgreen.svg)]()
[![Code Style: Ruff](https://img.shields.io/badge/Code%20Style-Ruff-black.svg)](https://github.com/astral-sh/ruff)

A robust, enterprise-grade, asynchronous bulk certificate generation backend API built with **FastAPI**, **PostgreSQL**, **SQLAlchemy 2.0**, **Celery**, **Redis**, and **ReportLab**.

The system enables organizations to submit bulk certificate generation requests for hundreds or thousands of participants in a single API call, validates recipient data, isolates item-level failures (*partial success tolerance*), renders vector PDF certificates with dynamic typography, tracks durable progress in the database, and provides secure binary downloads with SHA256 checksum integrity.

---

## Table of Contents

- [Key Features](#key-features)
- [Architecture & Data Flow](#architecture--data-flow)
- [Technology Stack](#technology-stack)
- [Prerequisites](#prerequisites)
- [Environment Configuration](#environment-configuration)
- [Local Setup & Installation](#local-setup--installation)
  - [Linux / macOS](#linux--macos)
  - [Windows PowerShell](#windows-powershell)
- [Database Migrations (Alembic)](#database-migrations-alembic)
- [Running the Application](#running-the-application)
- [Running with Docker Compose](#running-with-docker-compose)
- [API Reference & Interactive Documentation](#api-reference--interactive-documentation)
- [Step-by-Step API Usage Examples](#step-by-step-api-usage-examples)
  - [1. System Health Probe](#1-system-health-probe)
  - [2. Create Bulk Certificate Job](#2-create-bulk-certificate-job)
  - [3. Poll Job Execution Status & Progress](#3-poll-job-execution-status--progress)
  - [4. Retrieve Paginated Recipient Outcomes](#4-retrieve-paginated-recipient-outcomes)
  - [5. Download Issued PDF Certificate](#5-download-issued-pdf-certificate)
  - [6. Retrieve Certificate Metadata & SHA256](#6-retrieve-certificate-metadata--sha256)
- [Automated Testing & Code Quality](#automated-testing--code-quality)
- [Architectural & Design Decisions](#architectural--design-decisions)
- [Troubleshooting](#troubleshooting)
- [Limitations & Scaling Considerations](#limitations--scaling-considerations)

---

## Key Features

- **Asynchronous Chunked Processing**: Bulk requests return `202 Accepted` immediately with a unique `job_id`. Background workers process recipients in configurable database chunks (default: 50 items/batch).
- **Fault-Tolerant Partial Success**: Individual invalid recipients fail independently without blocking valid recipients. The batch finalizes with clear itemized error diagnostics and a composite status (`COMPLETED_WITH_ERRORS`).
- **High-Fidelity Vector PDF Generation**: Predefined ReportLab landscape canvas featuring dynamic font auto-scaling for long names, Unicode accent normalization (NFKD), dual decorative borders, vector gold medallion seals, and layout isolation.
- **Idempotency & Re-entrancy**: Safe job retries and client deduplication via `Idempotency-Key` HTTP headers. Terminal jobs are protected against duplicate processing.
- **Worker Crash Resumption**: Interrupted jobs resume seamlessly after worker restarts by querying only `PENDING` recipients and skipping already issued certificates.
- **Security & Defense-in-Depth**:
  - Configurable API Key authentication (`X-API-Key`).
  - Sliding-window in-memory IP rate limiter (120 req/min).
  - Path-traversal defense ensuring certificates can only be stored/retrieved within sandboxed boundaries.
  - Strict 10MB request payload limit middleware (`413 Payload Too Large`).
  - PII masking for email addresses in application logs (`j****e@domain.com`).
  - Security response headers (`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `X-XSS-Protection`).
- **Cryptographic Auditability**: Every generated certificate stores its exact file size and SHA256 digest in the database and HTTP headers for tamper verification.

---

## Architecture & Data Flow

### Layered Architecture

```
┌────────────────────────────────────────────────────────┐
│                   Client Applications                  │
│       (Web Frontend / Admin Portal / Script / CLI)     │
└───────────────────────────┬────────────────────────────┘
                            │ HTTP REST / JSON
┌───────────────────────────▼────────────────────────────┐
│                    API Gateway / FastAPI               │
│  - Security Headers & Payload Size Guard (10MB)        │
│  - Sliding-Window IP Rate Limiter                      │
│  - Optional API Key Authentication (X-API-Key)         │
│  - Pydantic v2 Request Validation                      │
└─────────────┬───────────────────────────┬──────────────┘
              │                           │
              │ Read Queries              │ Create Bulk Job
┌─────────────▼─────────────┐ ┌───────────▼──────────────┐
│       Job Service         │ │      Celery Task Queue   │
│ - Status Polling          │ │ (Redis Broker: db=0)     │
│ - Paginated Results       │ └───────────┬──────────────┘
│ - Certificate Streaming   │             │
└─────────────┬─────────────┘ ┌───────────▼──────────────┐
              │               │   Celery Background      │
              │               │       Worker(s)          │
              │               │ - Chunk Stream (50/batch)│
              │               │ - Recipient Isolation    │
              │               │ - ReportLab PDF Engine   │
              │               │ - Local/Object Storage   │
              │               └───────────┬──────────────┘
┌─────────────▼───────────────────────────▼──────────────┐
│          PostgreSQL Database (SQLAlchemy 2.0)          │
│    Jobs Table  ──<  Recipients Table  ──<  Certs Table │
└────────────────────────────────────────────────────────┘
```

### End-to-End Data Lifecycle

```mermaid
sequenceDiagram
    autonumber
    actor Client
    participant API as FastAPI Gateway
    participant DB as PostgreSQL DB
    participant Queue as Redis Queue
    participant Worker as Celery Worker
    participant Storage as File Storage

    Client->>API: POST /api/v1/certificate-jobs (Recipients, Metadata)
    API->>API: Validate envelope schema & rate limit
    API->>DB: INSERT Job (PENDING) & Recipients (PENDING)
    API->>Queue: Enqueue task process_bulk_certificate_job(job_id)
    API-->>Client: 202 Accepted (job_id, status: PENDING)

    Worker->>Queue: Consume job_id
    Worker->>DB: UPDATE Job status = PROCESSING
    loop Per Chunk of 50 Recipients
        Worker->>DB: SELECT PENDING Recipients FOR UPDATE SKIP LOCKED
        alt Valid Recipient
            Worker->>Worker: Render PDF with dynamic scaling & gold seal
            Worker->>Storage: Save binary & compute SHA256
            Worker->>DB: INSERT Certificate & UPDATE Recipient (SUCCESS)
        else Invalid Recipient
            Worker->>DB: UPDATE Recipient (FAILED, error_message)
        end
        Worker->>DB: Increment durable counters (success, failure, processed)
    end
    Worker->>DB: UPDATE Job status = COMPLETED or COMPLETED_WITH_ERRORS

    Client->>API: GET /api/v1/certificate-jobs/{job_id}
    API->>DB: SELECT Job status and progress counters
    API-->>Client: 200 OK (status, progress: 100%, counts)

    Client->>API: GET /api/v1/certificates/{certificate_id}
    API->>DB: Verify certificate record
    API->>Storage: Stream PDF binary with Content-Disposition
    API-->>Client: 200 OK (application/pdf, SHA256 header)
```

---

## Technology Stack

| Component | Technology | Version | Purpose & Rationale |
| :--- | :--- | :--- | :--- |
| **Language** | Python | 3.12 / 3.14 | Modern typing, high performance, robust ecosystem |
| **Framework** | FastAPI | 0.115+ | High-throughput asynchronous ASGI web framework with OpenAPI generation |
| **Server** | Uvicorn | 0.34+ | High-performance ASGI production server |
| **Database** | PostgreSQL | 16 | Relational ACID store with native UUID, indexing, and row-level locking |
| **ORM** | SQLAlchemy | 2.0.38+ | Declarative typed ORM with transactional boundaries |
| **Migrations** | Alembic | 1.14+ | Schema version control and reversible database migrations |
| **Validation** | Pydantic | 2.10+ | Fast data validation and serialization |
| **Task Queue** | Celery | 5.4+ | Distributed asynchronous task execution with retries and task persistence |
| **Message Broker** | Redis | 7.0+ | Low-latency in-memory broker and results backend |
| **PDF Generation** | ReportLab | 4.2+ | Vector graphics canvas engine producing precise PDF certificates without heavy headless browsers |
| **Testing** | Pytest + HTTPX | 8.3+ | Unit, API integration, and mock-isolated worker test suite |
| **Linter / Formatter** | Ruff | 0.8+ | Fast code analysis, formatting, and linting |
| **Containers** | Docker Compose | 2.x | Multi-container local and production deployment orchestration |

---

## Prerequisites

1. **Python**: Python 3.12+ installed ([python.org](https://www.python.org/downloads/)).
2. **Git**: Version control ([git-scm.com](https://git-scm.com/)).
3. **Docker & Docker Compose** (Optional, recommended for production): [Docker Desktop](https://www.docker.com/products/docker-desktop/).
4. **Redis**: (If running Celery worker locally without Docker) Redis installed and running on port 6379.

---

## Environment Configuration

Copy [.env.example](file:///d:/Downloads/Bulk%20Certificate%20Generator/.env.example) to `.env`:

```bash
cp .env.example .env
```

### Configuration Variables Reference

| Variable | Default Value | Description |
| :--- | :--- | :--- |
| `APP_NAME` | `Bulk Certificate Generator` | Project display name |
| `APP_ENV` | `development` | Environment mode (`development`, `staging`, `production`) |
| `DEBUG` | `true` | Enables verbose debug logging |
| `API_V1_STR` | `/api/v1` | URL prefix for API v1 routes |
| `DATABASE_URL` | `postgresql+psycopg2://postgres:postgres@localhost:5432/certificate_db` | PostgreSQL connection URI. Set to `sqlite:///./cert_dev.db` for zero-install dev mode. |
| `CELERY_BROKER_URL` | `redis://localhost:6379/0` | Redis queue connection URL |
| `CELERY_RESULT_BACKEND` | `redis://localhost:6379/1` | Redis task results backend |
| `STORAGE_DIR` | `./storage/certificates` | Local filesystem storage path for generated PDF files |
| `MAX_RECIPIENTS_PER_JOB`| `5000` | Maximum allowed participants per single bulk request |
| `CHUNK_SIZE` | `50` | Database transaction chunk size for background worker |
| `MAX_REQUEST_BODY_BYTES`| `10485760` | Request body size limit in bytes (10MB) |
| `RATE_LIMIT_PER_MINUTE` | `120` | Sliding-window request rate limit per IP address |
| `API_KEY` | *(empty)* | Optional API key protection. When set, clients must supply `X-API-Key`. |
| `ALLOWED_ORIGINS` | `*` | Comma-delimited list of CORS allowed origins |

---

## Local Setup & Installation

### Linux / macOS

```bash
# 1. Clone the repository
git clone https://github.com/your-org/bulk-certificate-generator.git
cd bulk-certificate-generator

# 2. Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install production and development dependencies
pip install --upgrade pip
pip install -r requirements.txt -r requirements-dev.txt

# 4. Configure environment
cp .env.example .env

# 5. Run database migrations
alembic upgrade head

# 6. Start the API server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

In a separate terminal, start the Celery background worker:

```bash
source venv/bin/activate
celery -A app.core.celery_app.celery_app worker --loglevel=info --concurrency=4 -P solo
```

---

### Windows PowerShell

```powershell
# 1. Clone and navigate to project directory
git clone https://github.com/your-org/bulk-certificate-generator.git
Set-Location "Bulk Certificate Generator"

# 2. Create and activate virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1

# 3. Install dependencies
python -m pip install --upgrade pip
pip install -r requirements.txt -r requirements-dev.txt

# 4. Configure environment
Copy-Item .env.example .env

# 5. Run database migrations
alembic upgrade head

# 6. Start the FastAPI API server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

In a second PowerShell window, start the Celery worker:

```powershell
.\venv\Scripts\Activate.ps1
celery -A app.core.celery_app.celery_app worker --loglevel=info --concurrency=4 -P solo
```

> **Windows Tip**: On Windows systems, Celery requires the `-P solo` or `-P threads` execution pool to avoid POSIX `fork()` emulation issues.

---

## Database Migrations (Alembic)

Database schema evolution is managed through Alembic. Migrations reside in the [alembic/versions/](file:///d:/Downloads/Bulk%20Certificate%20Generator/alembic/versions) directory.

```bash
# Apply all pending migrations to the latest revision
alembic upgrade head

# Inspect current migration version
alembic current

# Rollback one migration step
alembic downgrade -1

# Generate a new migration revision based on model changes
alembic revision --autogenerate -m "describe_schema_change"
```

---

## Running with Docker Compose

Docker Compose provisions the complete production topology: FastAPI API server, Celery worker, PostgreSQL 16 database, Redis 7 broker, and persisted named volumes.

```bash
# Build images and start all services in the background
docker-compose up -d --build

# Inspect container status
docker-compose ps

# Tail unified service logs
docker-compose logs -f

# Tail only Celery worker logs
docker-compose logs -f worker

# Run database migrations inside the running API container
docker-compose exec api alembic upgrade head

# Stop and remove all containers and networks
docker-compose down
```

### Services Overview

- **`api`**: FastAPI application running on `http://localhost:8000`
- **`worker`**: Celery background processing worker
- **`db`**: PostgreSQL 16 database on port `5432`
- **`redis`**: Redis 7 message broker on port `6379`
- **`storage_volume`**: Docker volume mapped to `/app/storage/certificates` for shared PDF persistence between API and worker

---

## API Reference & Interactive Documentation

Once the application is running, access interactive OpenAPI documentation:

- **Swagger UI (Interactive API Explorer)**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc (Technical Documentation)**: [http://localhost:8000/redoc](http://localhost:8000/redoc)
- **Raw OpenAPI JSON Spec**: [http://localhost:8000/openapi.json](http://localhost:8000/openapi.json) or [openapi.json](file:///d:/Downloads/Bulk%20Certificate%20Generator/openapi.json)
- **Postman Collection**: [postman_collection.json](file:///d:/Downloads/Bulk%20Certificate%20Generator/postman_collection.json)

---

## Step-by-Step API Usage Examples

### 1. System Health Probe

Checks API readiness, database connectivity, and Redis availability.

#### cURL
```bash
curl -X GET "http://localhost:8000/health" -H "Accept: application/json"
```

#### Windows PowerShell
```powershell
Invoke-RestMethod -Uri "http://localhost:8000/health" -Method Get | ConvertTo-Json
```

#### Response (`200 OK`)
```json
{
  "status": "healthy",
  "app_name": "Bulk Certificate Generator",
  "environment": "development",
  "database": "connected",
  "redis": "connected",
  "timestamp": "2026-10-10T14:45:00.000000+00:00"
}
```

---

### 2. Create Bulk Certificate Job

Submits a bulk certificate request. Responds immediately with `202 Accepted` while generation runs asynchronously.

#### cURL
```bash
curl -X POST "http://localhost:8000/api/v1/certificate-jobs" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: summit-2026-batch-01" \
  -d '{
    "event_name": "Cloud Native Architecture Bootcamp 2026",
    "issuer_name": "Cloud Native Computing Foundation",
    "issue_date": "2026-10-10",
    "recipients": [
      {
        "name": "Alice Cooper",
        "email": "alice@example.com",
        "custom_identifier": "ID-001"
      },
      {
        "name": "Bob Marley",
        "email": "bob@example.com",
        "custom_identifier": "ID-002"
      },
      {
        "name": "François Müller-Schütz",
        "email": "francois@example.org"
      }
    ]
  }'
```

#### Windows PowerShell
```powershell
$headers = @{
    "Content-Type"    = "application/json"
    "Idempotency-Key" = "summit-2026-batch-01"
}

$body = @{
    event_name  = "Cloud Native Architecture Bootcamp 2026"
    issuer_name = "Cloud Native Computing Foundation"
    issue_date  = "2026-10-10"
    recipients  = @(
        @{ name = "Alice Cooper"; email = "alice@example.com"; custom_identifier = "ID-001" },
        @{ name = "Bob Marley"; email = "bob@example.com"; custom_identifier = "ID-002" },
        @{ name = "François Müller-Schütz"; email = "francois@example.org" }
    )
} | ConvertTo-Json -Depth 5

$response = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/certificate-jobs" `
    -Method Post `
    -Headers $headers `
    -Body $body

$response | ConvertTo-Json
```

#### Response (`202 Accepted`)
```json
{
  "job_id": "7f8b92d1-4e2a-493b-85d7-83c9281a4b12",
  "status": "PENDING",
  "total_recipients": 3,
  "created_at": "2026-10-10T14:45:01.123456+00:00"
}
```

---

### 3. Poll Job Execution Status & Progress

Poll this endpoint to observe real-time progress counters and state transitions.

#### cURL
```bash
curl -X GET "http://localhost:8000/api/v1/certificate-jobs/7f8b92d1-4e2a-493b-85d7-83c9281a4b12"
```

#### Windows PowerShell
```powershell
$jobId = "7f8b92d1-4e2a-493b-85d7-83c9281a4b12"
Invoke-RestMethod -Uri "http://localhost:8000/api/v1/certificate-jobs/$jobId" -Method Get | ConvertTo-Json
```

#### Response (`200 OK`)
```json
{
  "job_id": "7f8b92d1-4e2a-493b-85d7-83c9281a4b12",
  "event_name": "Cloud Native Architecture Bootcamp 2026",
  "issuer_name": "Cloud Native Computing Foundation",
  "issue_date": "2026-10-10",
  "status": "COMPLETED",
  "total_recipients": 3,
  "processed_count": 3,
  "success_count": 3,
  "failure_count": 0,
  "created_at": "2026-10-10T14:45:01.123456+00:00",
  "started_at": "2026-10-10T14:45:01.350000+00:00",
  "completed_at": "2026-10-10T14:45:02.120000+00:00"
}
```

---

### 4. Retrieve Paginated Recipient Outcomes

Returns itemized results, including certificate IDs for successful recipients and error descriptions for failed items.

#### cURL
```bash
# Get Page 1 of Results
curl -X GET "http://localhost:8000/api/v1/certificate-jobs/7f8b92d1-4e2a-493b-85d7-83c9281a4b12/results?page=1&page_size=10"

# Filter only FAILED recipients
curl -X GET "http://localhost:8000/api/v1/certificate-jobs/7f8b92d1-4e2a-493b-85d7-83c9281a4b12/results?status=FAILED"
```

#### Windows PowerShell
```powershell
$jobId = "7f8b92d1-4e2a-493b-85d7-83c9281a4b12"
$results = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/certificate-jobs/$jobId/results?page=1&page_size=10" -Method Get
$results | ConvertTo-Json -Depth 5
```

#### Response (`200 OK`)
```json
{
  "job_id": "7f8b92d1-4e2a-493b-85d7-83c9281a4b12",
  "page": 1,
  "page_size": 10,
  "total_records": 3,
  "total_pages": 1,
  "results": [
    {
      "id": "e4a1b023-5e72-4d1a-8c90-b18420e6a392",
      "name": "Alice Cooper",
      "email": "alice@example.com",
      "custom_identifier": "ID-001",
      "status": "SUCCESS",
      "certificate_id": "bed24f82-a392-41f2-9842-7c8a19d2011b",
      "error_message": null,
      "created_at": "2026-10-10T14:45:01.123456+00:00",
      "processed_at": "2026-10-10T14:45:01.620000+00:00"
    },
    {
      "id": "c8b1a992-6d14-4a2b-9e12-d27419e5b120",
      "name": "Bob Marley",
      "email": "bob@example.com",
      "custom_identifier": "ID-002",
      "status": "SUCCESS",
      "certificate_id": "9acfbcce-c812-42da-8910-b98a21e4210a",
      "error_message": null,
      "created_at": "2026-10-10T14:45:01.123456+00:00",
      "processed_at": "2026-10-10T14:45:01.890000+00:00"
    }
  ]
}
```

---

### 5. Download Issued PDF Certificate

Direct binary stream download of the generated vector PDF certificate.

#### cURL
```bash
curl -X GET "http://localhost:8000/api/v1/certificates/bed24f82-a392-41f2-9842-7c8a19d2011b" \
  --output certificate_Alice_Cooper.pdf
```

#### Windows PowerShell
```powershell
$certId = "bed24f82-a392-41f2-9842-7c8a19d2011b"
Invoke-WebRequest -Uri "http://localhost:8000/api/v1/certificates/$certId" `
    -OutFile "certificate_Alice_Cooper.pdf"

Write-Host "Certificate downloaded successfully: certificate_Alice_Cooper.pdf"
```

---

### 6. Retrieve Certificate Metadata & SHA256

Retrieves audit and verification metadata along with the cryptographic checksum.

#### cURL
```bash
curl -X GET "http://localhost:8000/api/v1/certificates/bed24f82-a392-41f2-9842-7c8a19d2011b/metadata"
```

#### Windows PowerShell
```powershell
$certId = "bed24f82-a392-41f2-9842-7c8a19d2011b"
Invoke-RestMethod -Uri "http://localhost:8000/api/v1/certificates/$certId/metadata" -Method Get | ConvertTo-Json
```

#### Response (`200 OK`)
```json
{
  "certificate_id": "bed24f82-a392-41f2-9842-7c8a19d2011b",
  "job_id": "7f8b92d1-4e2a-493b-85d7-83c9281a4b12",
  "recipient_id": "e4a1b023-5e72-4d1a-8c90-b18420e6a392",
  "recipient_name": "Alice Cooper",
  "recipient_email": "alice@example.com",
  "event_name": "Cloud Native Architecture Bootcamp 2026",
  "issuer_name": "Cloud Native Computing Foundation",
  "issue_date": "2026-10-10",
  "file_name": "certificate_Alice_Cooper_bed24f82.pdf",
  "file_size_bytes": 2881,
  "sha256_checksum": "a7c2e391b8f04123891048bce981273901bca819283719283019283019283019",
  "issued_at": "2026-10-10T14:45:01.620000+00:00"
}
```

---

## Automated Testing & Code Quality

The test suite achieves **99.88% code coverage** (55 tests passing, 0 warnings/failures).

### Running Tests

```bash
# Execute entire test suite with line-by-line coverage
python -m pytest -v --cov=app --cov-report=term-missing

# Run only API integration tests
python -m pytest tests/test_api_endpoints.py -v

# Run only Celery worker and failure isolation tests
python -m pytest tests/test_background_workers.py -v

# Run only Security and rate limiting tests
python -m pytest tests/test_security.py -v
```

### Code Formatting & Static Analysis

```bash
# Check code style and linting with Ruff
python -m ruff check .

# Automatically apply safe fixes
python -m ruff check --fix .
```

---

## Architectural & Design Decisions

### 1. Vector PDF Generation (ReportLab) vs Headless Chrome (Puppeteer/WeasyPrint)
- **Decision**: Implemented native vector PDF rendering via ReportLab Canvas.
- **Rationale**: Headless browsers consume ~300MB RAM per rendering instance, introduce startup latency, and can suffer from memory leaks under high concurrency. ReportLab is a pure-Python C-extension rendering vector PDFs in under 15ms with less than 5MB memory overhead per worker thread.

### 2. Filesystem / Object Storage vs Database BLOBs
- **Decision**: Stored binary PDFs on the filesystem under an abstraction layer (`StorageService`) and persisted only file metadata, relative paths, and SHA256 hashes in PostgreSQL.
- **Rationale**: Relational databases suffer severe performance degradation, buffer pool cache eviction, and backup bloat when storing binary files. Storing files on disk (or S3 in production) keeps PostgreSQL lean, cache-friendly, and performant.

### 3. Partial Success vs All-or-Nothing Transactions
- **Decision**: Implemented individual recipient failure isolation. Invalid recipient data in a bulk request records an itemized `FAILED` status and failure reason without blocking valid recipients.
- **Rationale**: In bulk workloads of 1,000+ recipients, rejecting the entire batch because a single recipient has an invalid email format results in poor user experience and repeated processing overhead.

### 4. Deterministic Idempotency Keys
- **Decision**: Supported the standard `Idempotency-Key` header with database persistence.
- **Rationale**: Network timeouts during bulk ingestion can lead clients to retry requests. Persisting the key ensures that identical retried requests return the previously created `job_id` instead of generating duplicate jobs.

---

## Troubleshooting

### 1. Redis Connection Refused (`Error 10061` / `ECONNREFUSED`)
- **Cause**: Redis server is not running on `localhost:6379`.
- **Solution**: Start Redis using Docker: `docker run -d -p 6379:6379 redis:7-alpine` or use `docker-compose up -d`.

### 2. Celery Worker Hangs on Windows
- **Cause**: The default `prefork` pool in Celery does not support Windows process spawning.
- **Solution**: Always start Celery with the `-P solo` pool on Windows:
  `celery -A app.core.celery_app.celery_app worker -l info -P solo`

### 3. `413 Payload Too Large`
- **Cause**: The request body exceeds the configured `MAX_REQUEST_BODY_BYTES` (default: 10MB).
- **Solution**: Adjust `MAX_REQUEST_BODY_BYTES` in your `.env` file or split bulk batches into chunks of up to 5,000 recipients.

### 4. `401 Unauthorized`
- **Cause**: An `API_KEY` is configured in `.env`, but the client did not send the `X-API-Key` header or supplied an invalid value.
- **Solution**: Supply the matching `X-API-Key: your-configured-key` header in the request.

---

## Limitations & Scaling Considerations

1. **Local Storage**: The current implementation stores PDFs in `./storage/certificates`. In a distributed multi-node production deployment, replace `StorageService` with an S3 / Google Cloud Storage adapter so all worker nodes write to a shared bucket.
2. **Worker Scaling**: For workloads exceeding 50,000 certificates per hour, scale Celery worker containers horizontally (`docker-compose up --scale worker=8`) and utilize dedicated Redis / RabbitMQ clusters.
3. **Email Delivery**: The current service generates and stores certificates. Email dispatch (SMTP / SendGrid) can be attached as a secondary asynchronous Celery task triggered upon job completion.

---

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.
