# Observability and Operations Guide: Bulk Certificate Generator

This document defines the observability architecture, real-time metrics, health probes, alerting thresholds, backup runbooks, retention policies, and operational troubleshooting procedures for the **Bulk Certificate Generator Backend**.

---

## 1. Observability Pillars

```
                     ┌─────────────────────────────────────────────────────────┐
                     │          ENTERPRISE OBSERVABILITY STACK                 │
                     └────────────────────────────┬────────────────────────────┘
                                                  │
          ┌─────────────────────────┬─────────────┴───────────┬─────────────────────────┐
          ▼                         ▼                         ▼                         ▼
   STRUCTURED LOGS              METRICS & TELEMETRY       HEALTH PROBES             ALERTS & RUNBOOKS
• Correlation IDs (Request/Job) • Prometheus /metrics     • /health (DB + Redis)    • Alertmanager Rules
• JSON Formatting (ELK/Loki)    • PDF Rendering Latency   • /health/storage (Disk)  • PagerDuty / Slack
• Sensitive Data Masking        • Queue Backlog & Workers • Worker Heartbeats       • Step-by-Step Runbooks
```

---

## 2. Structured Logs & Distributed Correlation IDs

### Correlation ID Flow
Every request is assigned a unique correlation ID that traverses API handlers and asynchronous background workers:
1. **API Ingress (`X-Request-ID`)**:
   - The [`correlation_and_metrics_middleware`](file:///d:/Downloads/Bulk%20Certificate%20Generator/app/main.py#L84-L107) inspects incoming headers for `X-Request-ID`. If absent, a new UUID4 hex string is generated.
   - The ID is stored in Python's async context (`ContextVar` via [`set_request_id`](file:///d:/Downloads/Bulk%20Certificate%20Generator/app/core/logging.py#L18)) and echoed on the HTTP response header.
2. **Background Tasks (`job_id`)**:
   - When Celery executes [`process_bulk_certificate_job`](file:///d:/Downloads/Bulk%20Certificate%20Generator/app/tasks/certificate_tasks.py), the `job_id` is set into [`set_job_id`](file:///d:/Downloads/Bulk%20Certificate%20Generator/app/core/logging.py#L28).
   - All log records emitted during recipient validation, PDF rendering, storage, and database commits automatically inherit `request_id` and `job_id`.

### JSON Structured Logging Format
When `LOG_FORMAT=json` or `APP_ENV=production`, [`JsonLogFormatter`](file:///d:/Downloads/Bulk%20Certificate%20Generator/app/core/logging.py#L42-L63) outputs single-line JSON records:

```json
{
  "timestamp": "2026-10-10T09:40:15.123456Z",
  "level": "INFO",
  "logger": "bulk_certificate_generator",
  "message": "Job 550e8400-e29b-41d4-a716-446655440000 transitioned to PROCESSING. Total recipients: 2500",
  "file": "certificate_tasks.py",
  "line": 52,
  "request_id": "a1b2c3d4e5f64789",
  "job_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

*Querying in Loki / Datadog:*
```logql
{app="bulk-certificate-generator"} | json | job_id="550e8400-e29b-41d4-a716-446655440000"
```

---

## 3. The 6 Most Important Metrics for Processing Thousands of Certificates

When scaling bulk certificate workloads to thousands or millions of recipients, monitoring standard CPU and memory alone is insufficient. Operators must monitor these **six core domain metrics**:

```
 1. QUEUE LATENCY          2. PDF THROUGHPUT         3. QUEUE DEPTH
 ┌───────────────────┐     ┌───────────────────┐     ┌───────────────────┐
 │ Wait time between │     │ Certificates      │     │ Backlog of jobs   │
 │ submit and pickup │     │ rendered / sec    │     │ pending in Redis  │
 └───────────────────┘     └───────────────────┘     └───────────────────┘
 4. FAILURE RATIO          5. WORKER RSS MEMORY      6. STORAGE I/O & DISK
 ┌───────────────────┐     ┌───────────────────┐     ┌───────────────────┐
 │ % invalid input   │     │ Memory bloat from │     │ Disk write speed  │
 │ vs infra errors   │     │ fonts & images    │     │ and capacity %    │
 └───────────────────┘     └───────────────────┘     └───────────────────┘
```

### Metric 1: Queue Latency (Time-in-Queue)
- **Definition**: The elapsed time between `job.created_at` (API submission) and `job.started_at` (worker pickup).
- **Why It Matters**: If queue latency exceeds 15 seconds, workers are starved or backlogged. It directly signals when to scale out additional Celery worker replicas.
- **Healthy Target**: `< 5 seconds`. **Alert Threshold**: `> 60 seconds`.

### Metric 2: PDF Generation Throughput (Certificates / Second per Worker)
- **Definition**: Number of individual vector PDF certificates generated and saved per second across active workers.
- **Why It Matters**: ReportLab canvas drawing, font rendering, and SHA256 hashing are CPU-intensive. Measuring throughput (typically 40–80 certs/sec per CPU core) reveals CPU throttling, font cache degradation, or slow disk I/O.
- **Healthy Target**: `> 50 certs/sec/worker`. **Alert Threshold**: `< 15 certs/sec/worker`.

### Metric 3: Queue Depth (Pending Task Backlog)
- **Definition**: Total count of pending tasks in Redis waiting to be consumed (`LLEN celery`).
- **Why It Matters**: The primary metric for horizontal pod/container autoscaling (HPA via KEDA or AWS Target Tracking). When queue depth increases faster than worker throughput, automated scaling must spin up additional worker instances.
- **Healthy Target**: `< 50 tasks`. **Alert Threshold**: `> 500 tasks`.

### Metric 4: Recipient Failure & Error Ratio
- **Definition**: Ratio of failed recipients to total processed recipients (`failure_count / processed_count`).
- **Why It Matters**: Distinguishes between payload errors (e.g. malformed email addresses) and catastrophic system failures (e.g. database disconnection, storage volume out of inodes, corrupt font files).
- **Healthy Target**: `< 1%`. **Alert Threshold**: `> 5%` over a 5-minute rolling window.

### Metric 5: Worker Resident Set Size (RSS Memory)
- **Definition**: Physical RAM consumed by each Celery worker process.
- **Why It Matters**: ReportLab image embedding and PDF streams can retain memory buffers if not carefully managed. Monitoring worker RSS prevents Linux kernel Out-Of-Memory (OOM) killer terminations.
- **Healthy Target**: `< 500 MB per worker process`. **Alert Threshold**: `> 1.2 GB per worker process`.

### Metric 6: Storage I/O & Capacity Utilization
- **Definition**: Persistent volume disk usage percentage and storage write latency.
- **Why It Matters**: Generating 50,000 certificates at ~35 KB each creates ~1.75 GB of disk writes. Running out of disk space halts all active workers immediately.
- **Healthy Target**: `< 75% capacity`. **Alert Threshold**: `> 85% capacity`.

---

## 4. Prometheus Telemetry & Scraping

The API exposes real-time telemetry at `/metrics` compliant with standard Prometheus exposition format:

### Exposed Metrics Catalog

| Metric Name | Type | Description |
| :--- | :--- | :--- |
| `certificate_jobs_total{state="submitted|completed|failed"}` | Counter | Cumulative bulk certificate jobs by terminal state. |
| `certificates_generated_total{outcome="success|failure"}` | Counter | Cumulative individual PDF certificates generated. |
| `certificate_pdf_render_duration_seconds` | Summary | PDF rendering and saving latency summary. |
| `bulk_job_duration_seconds` | Summary | Total bulk generation job duration from start to finish. |
| `http_requests_total{method, path, status}` | Counter | Ingress HTTP request count by method, route, and status. |

### Prometheus Scrape Configuration
Add to your `prometheus.yml`:
```yaml
scrape_configs:
  - job_name: "bulk_certificate_api"
    scrape_interval: 15s
    scrape_timeout: 5s
    metrics_path: "/metrics"
    static_configs:
      - targets: ["api:8000"]
```

---

## 5. Health Probes & Deep Diagnostic Endpoints

### 1. `/health` (Readiness & Liveness Probe)
Probes core dependencies (PostgreSQL and Redis):
```bash
curl -i http://localhost:8000/health
```
```json
{
  "status": "healthy",
  "app_name": "Bulk Certificate Generator",
  "environment": "production",
  "database": "connected",
  "redis": "connected",
  "timestamp": "2026-10-10T09:40:00.000000Z"
}
```
*HTTP 200 = Ready for traffic. HTTP 503 = Degraded (Database or Redis connection failed).*

### 2. `/health/storage` (Storage Volume Probe)
Probes persistent volume accessibility, file counts, and disk usage:
```bash
curl -i http://localhost:8000/health/storage
```
```json
{
  "status": "healthy",
  "storage_dir": "/app/storage/certificates",
  "total_files": 12450,
  "total_size_bytes": 435750000,
  "total_size_mb": 415.56
}
```

---

## 6. Failure Alerts & Alertmanager Rules

Deploy these production alerting rules into Prometheus Alertmanager (`alerts.yml`):

```yaml
groups:
  - name: bulk_certificate_alerts
    rules:
      # Alert 1: Queue Depth Backlog
      - alert: BulkCertificateQueueBacklogHigh
        expr: celery_queue_depth > 500
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Celery queue backlog exceeds 500 tasks"
          description: "Queue depth has remained above 500 for 5 minutes. Scale additional Celery workers."

      # Alert 2: Worker Outage
      - alert: BulkCertificateWorkersDown
        expr: celery_active_workers == 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "No active Celery workers detected"
          description: "Zero Celery workers are processing tasks. Certificates cannot be generated."

      # Alert 3: High Failure Rate
      - alert: BulkCertificateHighFailureRate
        expr: rate(certificates_generated_total{outcome="failure"}[5m]) / rate(certificates_generated_total[5m]) > 0.05
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "Certificate generation failure rate exceeds 5%"
          description: "Over 5% of certificate rendering operations are failing. Check worker logs."

      # Alert 4: Storage Capacity Exhaustion
      - alert: CertificateStorageDiskSpaceLow
        expr: (node_filesystem_free_bytes{mountpoint="/app/storage"} / node_filesystem_size_bytes{mountpoint="/app/storage"}) < 0.15
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "Certificate storage disk has less than 15% space remaining"
          description: "Expand storage volume or run cleanup_retention.py immediately."
```

---

## 7. Backups and Disaster Recovery

### PostgreSQL Automated Backup Runbook
1. **Daily Logical Dump**:
   ```bash
   pg_dump -Fc -h db-host -U cert_prod_user -d cert_prod_db > /backups/db_$(date +%Y%m%d_%H%M%S).dump
   ```
2. **Restore Procedure**:
   ```bash
   # Restore to fresh database instance
   pg_restore -h db-host -U cert_prod_user -d cert_prod_db --clean --if-exists /backups/db_target.dump
   ```

### Storage Volume Backup (rsync & S3)
Synchronize persistent PDF certificates to off-site cloud storage:
```bash
aws s3 sync /app/storage/certificates s3://my-org-certificate-backups/certificates --delete
```

---

## 8. Retention Policy and Automated Cleanup

In accordance with compliance and data governance policies, generated certificates and terminal jobs should be pruned after their retention lifecycle (default: **90 days**).

### Administrative Cleanup Script
Use [`scripts/cleanup_retention.py`](file:///d:/Downloads/Bulk%20Certificate%20Generator/scripts/cleanup_retention.py) to audit and prune expired data:

```bash
# 1. Preview deletions (Dry Run)
python scripts/cleanup_retention.py --days 90

# 2. Execute live purge and reclaim disk space
python scripts/cleanup_retention.py --days 90 --force
```

### Automated Cron Job
Schedule automated retention enforcement weekly on the host:
```bash
0 2 * * 0 docker compose -f docker-compose.prod.yml exec -T api python scripts/cleanup_retention.py --days 90 --force >> /var/log/retention_cleanup.log 2>&1
```

---

## 9. Operational Troubleshooting Runbook

### Incident 1: Celery Queue Depth Continuously Climbing
- **Symptoms**: Jobs stay in `PENDING` status; Redis `LLEN celery` is increasing.
- **Diagnostic Commands**:
  ```bash
  # Check active workers
  docker compose -f docker-compose.prod.yml exec worker celery -A app.core.celery_app inspect ping
  # Check tasks currently being processed
  docker compose -f docker-compose.prod.yml exec worker celery -A app.core.celery_app inspect active
  ```
- **Remediation**:
  1. If workers are responsive but saturated, scale workers horizontally:
     ```bash
     docker compose -f docker-compose.prod.yml up -d --scale worker=3
     ```
  2. If workers are stuck, check for database deadlocks or network hangs.

### Incident 2: Celery Worker Exits with OOM (Exit Code 137)
- **Symptoms**: Worker container restarts repeatedly; syslog displays `Out of memory: Killed process`.
- **Root Cause**: Large batches of high-resolution images or unbounded memory leaks.
- **Remediation**:
  1. Increase container memory limit in [`docker-compose.prod.yml`](file:///d:/Downloads/Bulk%20Certificate%20Generator/docker-compose.prod.yml#L151) from `2048M` to `4096M`.
  2. Reduce chunk processing size from `CHUNK_SIZE=50` to `CHUNK_SIZE=25` in `.env.production`.

### Incident 3: High Rate of Individual Certificate Failures
- **Symptoms**: Jobs finish with status `COMPLETED_WITH_ERRORS`; `failure_count > 0`.
- **Diagnostic Commands**:
  ```bash
  # Query failed recipient error messages
  curl -s http://localhost:8000/api/v1/certificate-jobs/{job_id}/results | jq '.recipients[] | select(.status=="FAILED")'
  ```
- **Remediation**:
  1. If error is `"Email must contain a valid domain"`, it is a caller input validation error; no system action required.
  2. If error is `"Font not found"` or `"Disk full"`, inspect container filesystem permissions.

### Incident 4: Database Connection Pool Exhaustion
- **Symptoms**: API returns HTTP 500; logs show `TimeoutError: QueuePool limit of size 20 overflow 10 reached`.
- **Remediation**:
  1. Check for long-running uncommitted transactions:
     ```sql
     SELECT pid, now() - xact_start AS duration, query FROM pg_stat_activity WHERE state = 'active' ORDER BY duration DESC;
     ```
  2. Increase connection pool size or deploy PgBouncer in front of PostgreSQL.

### Incident 5: Storage Volume Reaches 100% Capacity
- **Symptoms**: Worker logs show `OSError: [Errno 28] No space left on device`.
- **Remediation**:
  1. Run retention cleanup immediately:
     ```bash
     python scripts/cleanup_retention.py --days 30 --force
     ```
  2. Expand the underlying EBS / cloud volume and resize the filesystem (`resize2fs`).
