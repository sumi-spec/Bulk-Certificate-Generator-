# Bulk Certificate Generator — Fresher Interview Handover & Technical Defense Guide

This guide is designed to help a junior backend engineer/fresher thoroughly understand, articulate, and defend the **Bulk Certificate Generator Backend API** in high-stakes engineering interviews.

---

## 1. The Two-Minute Introduction (The Pitch)

> **Interviewer**: *"Tell me about this project."*

### 🎙️ The Two-Minute Script:
> "I built the **Bulk Certificate Generator Backend API**, which is an asynchronous, high-throughput system designed to generate, validate, and securely distribute personalized PDF certificates at scale—for events, bootcamps, and universities.
>
> In typical web applications, generating PDFs synchronously in the request-response cycle causes HTTP timeouts and exhausts server threads because PDF rendering is CPU-bound. To solve this, I designed an **asynchronous event-driven architecture**:
> 1. When a client submits a bulk request of up to 10,000 recipients, FastAPI validates the payload and accepts it immediately with an **HTTP 202 Accepted** response and a tracking `job_id`.
> 2. The job is queued into **Redis** and processed asynchronously by **Celery background workers**.
> 3. Instead of holding all recipients in memory, workers process records in **chunked database batches of 50**, using **ReportLab** to generate vector PDFs in **2.7 milliseconds** per certificate.
> 4. The system implements **partial success tolerance**: if 5 out of 500 recipients have invalid emails or corrupt data, those 5 fail independently with itemized error diagnostics, while the other 495 certificates are successfully issued.
> 5. It features **crash-recovery**: if a worker process dies mid-batch, the next worker resumes seamlessly without duplicating already generated certificates.
> 6. On the security side, it enforces **sliding-window IP rate limiting**, **sandboxed path-traversal protection**, **constant-time API key verification**, and **SHA-256 cryptographic checksums** for tamper detection.
>
> The entire backend is covered by **71 automated unit and integration tests** achieving **99.5% test coverage**, containerized with **Docker Compose**, and equipped with Prometheus metrics and structured JSON logging."

---

## 2. High-Level Architecture & Database Design

### Layered Architecture Diagram

```
┌────────────────────────────────────────────────────────┐
│                   Client Applications                  │
│       (Frontend / Admin Portal / PowerShell / cURL)    │
└───────────────────────────┬────────────────────────────┘
                            │ HTTP REST / JSON
┌───────────────────────────▼────────────────────────────┐
│                    FastAPI Gateway                     │
│  - Security Headers & 10MB Payload Size Guard          │
│  - Sliding-Window In-Memory IP Rate Limiter (120/min)  │
│  - Constant-time API Key Auth (`X-API-Key`)            │
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
              │               │ - Sandboxed File Storage │
              │               └───────────┬──────────────┘
┌─────────────▼───────────────────────────▼──────────────┐
│          PostgreSQL Database (SQLAlchemy 2.0)          │
│    Jobs Table  ──<  Recipients Table  ──<  Certs Table │
└────────────────────────────────────────────────────────┘
```

### Database Schema (3 Normalized Tables)

1. **`jobs` Table**:
   - `id`: `UUID` (Primary Key, random v4).
   - `event_name`: `VARCHAR(255)` (Title of the event).
   - `issuer_name`: `VARCHAR(255)` (Organization issuing the certificate).
   - `issue_date`: `DATE` (Date printed on the certificate).
   - `status`: `VARCHAR(32)` (`PENDING`, `PROCESSING`, `COMPLETED`, `COMPLETED_WITH_ERRORS`, `FAILED`).
   - `total_recipients`: `INTEGER` (Expected number of recipients).
   - `processed_count`: `INTEGER` (Durable progress tracker).
   - `success_count`: `INTEGER` (Successfully issued certificates).
   - `failure_count`: `INTEGER` (Failed recipient records).
   - `created_at`, `updated_at`, `started_at`, `completed_at`: `TIMESTAMPTZ`.

2. **`recipients` Table**:
   - `id`: `UUID` (Primary Key).
   - `job_id`: `UUID` (Foreign Key referencing `jobs.id` with `ON DELETE CASCADE`).
   - `name`: `VARCHAR(150)` (Participant full name).
   - `email`: `VARCHAR(255)` (Participant email address).
   - `custom_identifier`: `VARCHAR(50)` (Optional Roll No/Student ID).
   - `status`: `VARCHAR(32)` (`PENDING`, `SUCCESS`, `FAILED`).
   - `error_message`: `TEXT` (Diagnostic reason if failed, e.g., "Invalid email format").
   - **Composite Index**: `ix_recipients_job_status (job_id, status)` — *enables 2.4ms lookups for workers querying only PENDING recipients*.

3. **`certificates` Table**:
   - `id`: `UUID` (Primary Key, unique certificate identifier).
   - `job_id`: `UUID` (Foreign Key referencing `jobs.id`).
   - `recipient_id`: `UUID` (Unique Foreign Key referencing `recipients.id`, 1-to-1).
   - `file_name`: `VARCHAR(255)` (Sanitized filename).
   - `file_path`: `VARCHAR(512)` (Sandboxed relative path).
   - `file_size_bytes`: `INTEGER` (Exact size of generated PDF).
   - `sha256_checksum`: `VARCHAR(64)` (Cryptographic digest for tamper verification).

#### 💡 Why UUIDs instead of auto-incrementing Integers?
- **Enumeration Attack Prevention**: With integers (`/certificates/1`, `/certificates/2`), an attacker can scrape every certificate issued by the organization. UUIDv4 (`e30cf2b6-7a71-4ce8-...`) is cryptographically random and unguessable.
- **Distributed Generation**: UUIDs can be generated on worker nodes before inserting into the database without primary key collision or centralized lock contention.

---

## 3. End-to-End API Lifecycle

```
Client POST /api/v1/certificate-jobs
  │
  ├── 1. Middleware: Checks payload <= 10MB; checks IP rate limit (120 req/min).
  ├── 2. Pydantic v2: Validates JSON schema (3-255 char event, RFC-5322 email syntax).
  ├── 3. DB Transaction: Inserts 1 `Job` (PENDING) and N `Recipients` (PENDING).
  ├── 4. Celery Queue: Sends task `process_bulk_certificate_job.delay(job_id)`.
  └── 5. Returns HTTP 202 Accepted: `{ job_id: "...", status: "PENDING" }` in ~100ms.
         │
         ▼ (Asynchronous Background Execution)
Celery Worker receives `job_id`
  │
  ├── 1. Updates `Job.status = PROCESSING` and sets `started_at = now()`.
  ├── 2. Queries chunks of 50 recipients: `WHERE job_id = :id AND status = 'PENDING'`.
  │      Uses `FOR UPDATE SKIP LOCKED` for row-level concurrency safety.
  ├── 3. For each recipient:
  │      ├── Validates recipient specific data.
  │      ├── If valid:
  │      │    - Renders PDF in memory using ReportLab (2.7ms).
  │      │    - Calculates SHA-256 hash & writes to sandboxed file storage.
  │      │    - Inserts `Certificate` row & marks `Recipient.status = SUCCESS`.
  │      └── If invalid / exception:
  │           - Marks `Recipient.status = FAILED`, records `error_message`.
  ├── 4. Commits chunk to PostgreSQL and increments durable counters.
  └── 5. Finalizes `Job.status`:
         - All succeeded -> `COMPLETED`
         - Some failed -> `COMPLETED_WITH_ERRORS`
         - All failed -> `FAILED`
         │
         ▼
Client polls GET /api/v1/certificate-jobs/{job_id} -> Observes progress
Client downloads GET /api/v1/certificates/{cert_id} -> Streams PDF with SHA256 header
```

---

## 4. Key Technology Choices & Justifications

| Choice | Alternatives Considered | Why This Technology Was Chosen |
| :--- | :--- | :--- |
| **FastAPI** | Flask, Django | FastAPI provides native async ASGI support, high concurrency, automatic OpenAPI documentation, and strict runtime type enforcement with Pydantic v2. |
| **PostgreSQL** | MySQL, MongoDB | Strong ACID compliance, reliable `FOR UPDATE SKIP LOCKED` row-level locking, rich indexing (composite B-Trees), and strict schema constraints. |
| **Celery + Redis** | BackgroundTasks, RQ, RabbitMQ | FastAPI's built-in `BackgroundTasks` run in the same memory space as the web server, risking web server crashes on CPU spikes and losing jobs on restart. Celery provides true out-of-process distributed workers, task retry semantics, and Redis offers sub-millisecond queuing latency. |
| **ReportLab** | WeasyPrint, Puppeteer / Playwright | Headless browser solutions take **500–1,500 ms** per page and consume **150+ MB RAM** per instance. ReportLab renders vector PDFs in pure Python in **2.7 ms** with **< 3 KB** file size and zero external browser dependencies. |
| **SQLAlchemy 2.0** | Raw SQL, Tortoise ORM | Modern SQLAlchemy 2.0 syntax (`select()`) provides full static typing support, connection pooling, and parameterized queries that completely eliminate SQL injection risks. |

---

## 5. Critical Engineering Concepts Explained Simply

### 1. Partial Failure Isolation
- **The Problem**: In a bulk batch of 1,000 recipients, if recipient #499 has a typo in their email or an invalid character, a naive transaction rolls back all 1,000 certificates.
- **The Solution**: Process in **chunks of 50**, handling each recipient inside a `try/except` block. If an individual fails, its recipient status is set to `FAILED` with an explanatory message (`"Invalid email domain"`). The loop continues, ensuring the other 999 recipients are successfully generated.
- **Job Status**: The job completes as `COMPLETED_WITH_ERRORS`, allowing the admin to inspect only the failed records without re-submitting the valid ones.

### 2. Retry Idempotency & Crash Recovery
- **The Problem**: A worker server gets restarted (e.g., spot instance termination, OOM) after generating 200 out of 500 certificates. If the task restarts from recipient 0, it creates 200 duplicate files and database entries.
- **The Solution**:
  1. The worker query filters strictly:
     ```python
     select(RecipientModel).where(
         RecipientModel.job_id == job_id,
         RecipientModel.status == RecipientStatus.PENDING,
     )
     ```
  2. The 200 already completed records have `status = 'SUCCESS'`. The resumed worker queries only the remaining 300 `PENDING` records.
  3. No duplicate files are created, and already generated certificates remain intact.
  4. If a job is already in a terminal state (`COMPLETED`), the worker immediately exits.

### 3. In-Memory vs. Durable Progress Calculation
- **The Problem**: If a worker maintains progress in a Python variable `progress = 45%` or a Redis key without DB persistence, a worker crash resets the progress to 0%, or the API reports misleading numbers.
- **The Solution**: Progress is stored directly in PostgreSQL columns (`processed_count`, `success_count`, `failure_count`) and committed at the end of every 50-item chunk. When the client calls `GET /api/v1/certificate-jobs/{job_id}`, the counts are read from the database or computed dynamically using `count(recipients.id)` if counts need synchronization.

### 4. File Security & Path Traversal Prevention
- **The Problem**: If a user submits a custom filename like `../../../../etc/passwd.pdf`, the server could overwrite or read system files.
- **The Solution**:
  1. Files are assigned deterministic names derived from UUIDs: `certificate_{uuid}.pdf`.
  2. In `app/services/storage.py`, `resolve()` is called on paths and checked against the storage root:
     ```python
     target_path = (self.storage_dir / clean_path).resolve()
     if not target_path.is_relative_to(self.storage_dir.resolve()):
         raise ValueError("Path traversal attempt detected")
     ```
  3. Downloads are served via streamed file generators with sanitized headers (`Content-Disposition`).

---

## 6. Scaling & Production Deployment Strategy

```
                          Internet / Clients
                                 │
                          [Cloudflare / WAF]
                                 │ TLS Termination
                     [Nginx / AWS ALB Ingress]
                                 │
            ┌────────────────────┴────────────────────┐
            │                                         │
    [FastAPI Web Pod 1]                       [FastAPI Web Pod 2]
    (uvicorn, 4 workers)                      (uvicorn, 4 workers)
            │                                         │
            └────────────────────┬────────────────────┘
                                 │
                 ┌───────────────┴───────────────┐
                 │                               │
         [PostgreSQL Primary]             [Redis 7 Cluster]
         (RDS / Managed Cloud)                   │
                                         ┌───────┴───────┐
                                         │               │
                                 [Celery Worker 1] [Celery Worker 2]
                                 (4 CPU threads)   (4 CPU threads)
                                         │               │
                                         └───────┬───────┘
                                                 │
                                         [AWS S3 / Cloud Storage]
```

### How to Scale for 100,000 Certificates:
1. **Horizontal Worker Auto-scaling**: Celery workers run as stateless Docker containers on Kubernetes (KEDA) or AWS ECS, auto-scaling based on the Redis queue depth (`LLEN certificate_queue`).
2. **Chunk Size Optimization**: Keep `CHUNK_SIZE=50` to balance database transaction lock duration (< 80 ms) with bulk insert efficiency.
3. **Storage Offloading**: Store certificates in **AWS S3** or **Google Cloud Storage** rather than local disk. Clients download via pre-signed S3 URLs, offloading all binary egress traffic from the API servers.
4. **Read Replica Database**: Point `GET /status` and `GET /results` queries to a PostgreSQL read replica, keeping the primary database dedicated to worker writes.

---

## 7. Trade-offs & Limitations

1. **Local Filesystem vs. Object Storage**:
   - *Current Implementation*: Uses local sandboxed volume storage (`data/certificates`).
   - *Trade-off*: Zero external cloud dependency for local Docker deployment, but multi-node worker clusters require a shared NFS volume or migration to an S3/MinIO bucket.
2. **Single-Worker Sequential Chunking per Job**:
   - *Current Implementation*: One Celery task processes the entire job in sequential chunks of 50.
   - *Trade-off*: Simpler state tracking and zero race conditions for job counters. However, for a massive 50,000-recipient job, splitting the job across multiple sub-tasks (using Celery `chord` or `group`) would allow 10 workers to process parts in parallel.
3. **In-Memory Rate Limiting**:
   - *Current Implementation*: Sliding-window rate limiter stored in application memory.
   - *Trade-off*: Extremely fast (zero network hop), but each API instance maintains its own counters. In multi-pod deployments, this should transition to Redis-backed rate limiting (`redis.evalsha` token bucket).

---

## 8. Top 10 Technical Interview Q&As

### Q1: Why did you return HTTP 202 Accepted instead of HTTP 200 OK for the bulk job endpoint?
> **Answer**: "Because bulk certificate generation is an asynchronous, long-running operation. Returning `200 OK` would imply that all certificates are already generated and ready. `202 Accepted` is the standard HTTP status code indicating that the request has been validated and queued for background processing, but execution has not yet concluded. The response includes a `job_id` so the client can poll the status."

### Q2: What is `FOR UPDATE SKIP LOCKED` and why did you use it?
> **Answer**: "In PostgreSQL, `FOR UPDATE` places an exclusive row-level lock on selected records so other transactions cannot modify them. Adding `SKIP LOCKED` instructs the query to ignore any rows already locked by other worker transactions rather than blocking. This allows multiple concurrent Celery workers to consume chunks of recipients simultaneously without deadlocks or race conditions."

### Q3: What happens if the database connection drops while a worker is rendering a certificate?
> **Answer**: "The rendering itself happens in Python memory using ReportLab. When the worker tries to commit the transaction, SQLAlchemy raises an `OperationalError`. Because the chunk is wrapped in an atomic database transaction, the uncommitted changes roll back, leaving those recipients in `PENDING` status. When database connectivity resumes, the worker or a retried task re-fetches those `PENDING` recipients and completes the job."

### Q4: How do you prevent SQL injection in your application?
> **Answer**: "We use SQLAlchemy 2.0 ORM with parameterized query building (`select()`, `where()`, `filter_by()`). At no point are user inputs concatenated into raw SQL strings. All inputs are automatically escaped and passed as bound parameters by the database driver."

### Q5: Why did you choose ReportLab over HTML-to-PDF converters like Puppeteer or WeasyPrint?
> **Answer**: "Performance and resource consumption. In our benchmarks, ReportLab generates a vector PDF in **2.71 milliseconds** with an output size of ~2.9 KB. Headless browsers like Puppeteer launch a Chromium process that consumes 150 MB of memory and takes 500 to 1,500 ms per certificate. For a batch of 1,000 certificates, ReportLab takes ~2.7 seconds on a single thread, whereas Puppeteer would take 15+ minutes and risk server crashes."

### Q6: How does the system handle Unicode names with accents or non-Latin characters in certificates?
> **Answer**: "ReportLab's default core fonts (Helvetica) only support Latin-1 characters. In our `pdf_generator.py`, we apply **Unicode Normalization (NFKD)** using Python's `unicodedata` library. This decomposes accented characters (e.g., 'é' becomes 'e' + combining accent) and cleans unrenderable glyphs, ensuring certificates render cleanly without raising encoding exceptions."

### Q7: How do you prevent long recipient names from overflowing the certificate canvas?
> **Answer**: "We implemented a dynamic font auto-scaling algorithm in `pdf_generator.py`. We define the maximum allowed printable width (e.g., 680 points). Using ReportLab's `stringWidth()`, we calculate the exact rendered width of the name at the default 36 pt font. If the width exceeds 680 pt, we dynamically calculate a scale factor and reduce the font size proportionally down to a safe minimum (18 pt)."

### Q8: What is the difference between unit tests and integration tests in your project?
> **Answer**: "Our **unit tests** test components in isolation with mocks—for example, validating that Pydantic rejects invalid emails or that ReportLab correctly calculates border coordinates. Our **integration tests** verify the complete system: spinning up a FastAPI test client with an isolated SQLite/Postgres session, creating a real job via the API, executing the worker task, verifying database row transitions, downloading the resulting PDF binary, and validating that the first 4 bytes are `%PDF`."

### Q9: How is the progress percentage calculated when the client polls for status?
> **Answer**: "The job table tracks `processed_count` and `total_recipients`. In the API schema, progress is calculated as `(processed_count / total_recipients) * 100`. Furthermore, to safeguard against edge cases where counters drift during unexpected restarts, our `JobService` can perform a dynamic `select(count(id))` query on the `recipients` table grouped by status."

### Q10: How did you implement defense-in-depth against malicious file downloads?
> **Answer**: "First, certificate IDs are random UUIDv4, making ID enumeration impossible. Second, `storage.py` resolves the requested file path and verifies that `is_relative_to(STORAGE_DIR)` is True, blocking path-traversal attacks like `../../`. Third, every certificate has an immutable SHA-256 hash stored in the DB upon creation, which is returned in the `X-Certificate-Checksum` header to verify file integrity."

---

## 9. Practical Live-Coding Exercises (With Solutions)

### Exercise 1: Add a Certificate Expiry Date
**Scenario**: The interviewer asks: *"Some certificates expire after 1 year. How would you add an optional `expiry_date` to certificates?"*

#### Solution Steps:
1. **Model Change** ([`app/models/certificate.py`](file:///d:/Downloads/Bulk%20Certificate%20Generator/app/models/certificate.py)):
   ```python
   # Add column to CertificateModel:
   expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
   ```
2. **Schema Change** ([`app/schemas/certificate.py`](file:///d:/Downloads/Bulk%20Certificate%20Generator/app/schemas/certificate.py)):
   ```python
   class CertificateMetadataResponse(BaseModel):
       # ...
       expiry_date: date | None = None
   ```
3. **PDF Generator** ([`app/services/pdf_generator.py`](file:///d:/Downloads/Bulk%20Certificate%20Generator/app/services/pdf_generator.py)):
   ```python
   # In render(), draw expiry if provided:
   if expiry_date:
       canvas.setFont("Helvetica", 10)
       canvas.drawCentredString(
           PAGE_WIDTH / 2.0, 100, f"Valid Until: {expiry_date.strftime('%B %d, %Y')}"
       )
   ```
4. **Alembic Migration**:
   ```bash
   python -m alembic revision --autogenerate -m "add_expiry_date_to_certificates"
   python -m alembic upgrade head
   ```

---

### Exercise 2: Implement a Job Cancellation Endpoint
**Scenario**: *"Add an endpoint allowing an admin to cancel a job that is still PENDING."*

#### Solution Steps:
1. **Endpoint** ([`app/api/v1/endpoints/jobs.py`](file:///d:/Downloads/Bulk%20Certificate%20Generator/app/api/v1/endpoints/jobs.py)):
   ```python
   @router.post("/{job_id}/cancel", status_code=status.HTTP_200_OK)
   def cancel_job(job_id: uuid.UUID, db: Session = Depends(get_db)):
       job = job_service.get_job_by_id(db, job_id)
       if not job:
           raise HTTPException(status_code=404, detail="Job not found")
       if job.status != JobStatus.PENDING:
           raise HTTPException(
               status_code=400, detail="Only PENDING jobs can be cancelled"
           )

       job.status = JobStatus.FAILED
       job.completed_at = datetime.now(timezone.utc)
       db.commit()
       return {"message": "Job successfully cancelled", "job_id": str(job.id)}
   ```
2. **Worker Check** ([`app/tasks/certificate_tasks.py`](file:///d:/Downloads/Bulk%20Certificate%20Generator/app/tasks/certificate_tasks.py)):
   ```python
   # At beginning of execute_bulk_job_logic:
   if job.status == JobStatus.FAILED:
       return {"status": "CANCELLED", "message": "Job was cancelled before processing"}
   ```

---

### Exercise 3: Add a Webhook Notification on Job Completion
**Scenario**: *"When a job finishes, notify a client webhook URL with the job results."*

#### Solution Steps:
1. **Add `webhook_url` to `JobCreateRequest`** in [`app/schemas/job.py`](file:///d:/Downloads/Bulk%20Certificate%20Generator/app/schemas/job.py).
2. **Store `webhook_url` in `JobModel`** in [`app/models/job.py`](file:///d:/Downloads/Bulk%20Certificate%20Generator/app/models/job.py).
3. **Dispatch Webhook in Celery Task** ([`app/tasks/certificate_tasks.py`](file:///d:/Downloads/Bulk%20Certificate%20Generator/app/tasks/certificate_tasks.py)):
   ```python
   if job.webhook_url:
       import httpx

       try:
           payload = {
               "job_id": str(job.id),
               "status": job.status.value,
               "total": job.total_recipients,
               "success": job.success_count,
               "failure": job.failure_count,
           }
           httpx.post(job.webhook_url, json=payload, timeout=5.0)
       except Exception as exc:
           logger.warning(
               "Failed to deliver webhook for job %s: %s", job.id, exc
           )
   ```

---

## 10. Real-World Debugging Scenarios

### Scenario 1: "A user says their job has been stuck on `PROCESSING` for 2 hours."
- **Root Cause Analysis**:
  1. Check Celery worker logs: Did the worker process get killed by the OS due to an Out of Memory (OOM) killer?
  2. Check Redis queue depth: `redis-cli LLEN certificate_queue`. If depth > 0, the task is still in queue waiting for available workers.
  3. Check the database:
     ```sql
     SELECT status, processed_count, total_recipients, started_at FROM jobs WHERE id = '...';
     SELECT status, count(*) FROM recipients WHERE job_id = '...' GROUP BY status;
     ```
- **Remediation**:
  If the worker died, re-queue the job: `process_bulk_certificate_job.delay(job_id)`. The crash-recovery mechanism will pick up only the remaining `PENDING` records without duplicating already completed ones.

---

### Scenario 2: "The worker throws `OperationalError: SSL SYSCALL error: EOF detected` on large batches."
- **Root Cause Analysis**:
  A single database connection was kept open too long while rendering a large batch, causing the database or network firewall to terminate the idle connection.
- **Remediation**:
  Our chunking implementation prevents this! By processing in batches of 50 (`CHUNK_SIZE = 50`) and committing after each batch, the connection is refreshed every ~140 milliseconds, preventing connection timeouts.

---

### Scenario 3: "A recipient complains they received a certificate with another person's name."
- **Root Cause Analysis**:
  1. Inspect the certificate metadata:
     `GET /api/v1/certificates/{certificate_id}/metadata`
  2. Join `certificates` to `recipients` and `jobs`:
     ```sql
     SELECT c.id, c.file_name, r.name, r.email, j.event_name
     FROM certificates c
     JOIN recipients r ON c.recipient_id = r.id
     JOIN jobs j ON c.job_id = j.id
     WHERE c.id = '...';
     ```
  3. Validate file generation logic: In `execute_bulk_job_logic`, `pdf_generator.render()` receives `recipient_name=recipient.name`.
  4. Ensure threads do not share mutable canvas state: `ReportLab Canvas` is instantiated inside `render()` on a fresh `io.BytesIO()` buffer per recipient, guaranteeing zero cross-thread state pollution.

---

## 🏁 Summary Checklist Before Your Interview

- [x] Read Section 1 aloud twice until your 2-minute elevator pitch flows naturally.
- [x] Be able to draw the 3 database tables (`jobs`, `recipients`, `certificates`) on a whiteboard.
- [x] Memorize the key benchmark numbers: **2.7 ms per PDF**, **367 certs/sec**, **99.5% test coverage**.
- [x] Explain **partial failure isolation** and **worker crash recovery** without looking at notes.
- [x] Know why **ReportLab** is better than headless browsers for this use case.
