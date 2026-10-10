# Performance and Scalability: Benchmarks and Architectural Sizing

This document provides empirical benchmark results, architectural optimizations, and horizontal scaling models for the **Bulk Certificate Generator Backend**.

---

## 1. Executive Performance Summary

All measurements below represent **actual empirical benchmark data** gathered using [`scripts/benchmark_performance.py`](file:///d:/Downloads/Bulk%20Certificate%20Generator/scripts/benchmark_performance.py) on a standard multicore CPU execution environment. Theoretical projections for larger workloads are documented separately in [Section 4](#4-theoretical-projections-at-scale-estimates-only).

| Metric Category | Measured Empirical Value | Key Takeaway |
| :--- | :--- | :--- |
| **PDF Rendering Throughput** | **367.22 certificates / second** (single thread) | ReportLab vector rendering averages **2.71 ms per certificate**. |
| **PDF Payload Footprint** | **2.91 KB per certificate** | Vector primitives and embedded metadata yield an extremely compact storage footprint. |
| **Disk Storage IOPS** | **533.0 Write IOPS** / **470.3 Read IOPS** | Direct in-memory buffer streaming avoids temporary file thrashing. |
| **API Ingestion Speed** | **109.5 ms** for **500 recipients** | Pydantic payload validation and atomic batch insertion occur in < 110 ms. |
| **DB Paginated Query Latency** | **2.48 ms** (Filtered) / **11.28 ms** (Joined) | Composite B-Tree indexes enable sub-15ms queries across 1,000 records. |

---

## 2. Actual Empirical Benchmark Results

### Benchmark 1: ReportLab PDF Vector Rendering Latency & Throughput
Evaluates single-thread rendering across increasing batch sizes:

| Batch Size | Total Render Time (s) | Avg Latency (ms/cert) | Throughput (cert/s) | Avg Output Size (KB) |
| :---: | :---: | :---: | :---: | :---: |
| **10** | 0.029 s | 2.85 ms | 349.79 cert/s | 2.91 KB |
| **50** | 0.132 s | 2.64 ms | 378.07 cert/s | 2.91 KB |
| **100** | 0.281 s | 2.80 ms | 355.81 cert/s | 2.91 KB |
| **250** | 0.681 s | 2.71 ms | 367.22 cert/s | 2.91 KB |

*Finding*: PDF rendering scales linearly with $O(N)$ CPU complexity. Rendering is completely CPU-bound; ReportLab does not suffer from compounding memory leaks or cache thrashing across 250 consecutive certificates.

---

### Benchmark 2: File Storage Write & Read Throughput
Tests sequential filesystem I/O across 100 generated certificates (total volume ~0.28 MB):

| Operation | Total Time (s) | Effective Bandwidth | IOPS (Operations / Sec) |
| :--- | :---: | :---: | :---: |
| **Write (`storage_service.save_file`)** | 0.188 s | 1.49 MB/s | **533.0 IOPS** |
| **Read (`storage_service.read_file`)** | 0.213 s | 1.31 MB/s | **470.3 IOPS** |

*Finding*: Because PDFs average ~2.91 KB, the I/O bottleneck is inode/metadata operations (IOPS) rather than raw bandwidth (MB/s). In high-volume cloud deployments (e.g. AWS EBS), provisioned IOPS (gp3 with 3000 IOPS baseline) provides more than enough headroom.

---

### Benchmark 3: Chunk Size Tuning & Transaction Commit Efficiency
Evaluates processing a 200-recipient batch across varying `CHUNK_SIZE` thresholds:

| Chunk Size | Total Time (s) | Throughput (recip/s) | Commit Batches | Architectural Trade-off |
| :---: | :---: | :---: | :---: | :--- |
| **10** | 0.375 s | 534.04 recip/s | 20 commits | High transaction overhead; safest progress updates. |
| **25** | 0.149 s | 1343.44 recip/s | 8 commits | 2.5x speedup over size 10; reasonable balance. |
| **50** *(Default)* | **0.079 s** | **2538.28 recip/s** | **4 commits** | **4.7x speedup over size 10; optimal balance of throughput and progress resolution.** |
| **100** | 0.044 s | 4585.55 recip/s | 2 commits | Maximum database efficiency; larger batch re-run on worker crash. |

*Finding*: Increasing `CHUNK_SIZE` from 10 to 50 provides a **4.7x throughput increase** by reducing SQLAlchemy transaction commit round-trips from 20 to 4. Setting `CHUNK_SIZE=50` (or `CHUNK_SIZE=100` for jobs over 5,000) keeps database lock durations under 80 ms while guaranteeing that no more than 50 certificates ever need re-rendering if a worker abruptly dies.

---

### Benchmark 4: Database Query Latency & B-Tree Index Utilization
Measures database query latencies on a table populated with 1,000 recipient records:

| Query Pattern | Index Utilized | Measured Latency (ms) |
| :--- | :--- | :---: |
| **Primary Key Lookup (`get_job_by_id`)** | `jobs.id` (PK B-Tree) | **0.962 ms** |
| **Paginated Results (`Page 1, limit 50, joined cert`)** | `ix_recipients_job_id` | **11.284 ms** |
| **Status Filtered Query (`WHERE status='FAILED'`)** | `ix_recipients_job_status` (Composite) | **2.479 ms** |
| **Deep Page Query (`Page 15, offset 700`)** | `recipients.created_at` | **8.080 ms** |

*Finding*: The composite index `ix_recipients_job_status` on `(job_id, status)` allows filtered queries to execute in **2.48 ms**, avoiding sequential table scans.

---

### Benchmark 5: Ingestion API Submission & Payload Validation
Measures the time to validate Pydantic recipient models and persist the parent Job and child Recipient records:

| Ingested Recipients | Validation Time (ms) | DB Persistence Time (ms) | Total Ingest Time (ms) |
| :---: | :---: | :---: | :---: |
| **50** | 8.28 ms | 34.96 ms | **43.24 ms** |
| **200** | 41.47 ms | 30.23 ms | **71.70 ms** |
| **500** | 54.23 ms | 55.28 ms | **109.52 ms** |

*Finding*: The API accepts, validates, and persists a 500-recipient bulk job in **~109 ms**, promptly returning HTTP 202 Accepted and offloading execution to Celery.

---

## 3. Engineering Optimizations Applied Based on Measurements

1. **Database-Side Count Aggregate Optimization**:
   - *Previous Approach*: Fetched all recipient IDs into memory and ran `len(db.execute(count_stmt).all())`.
   - *Optimized Approach*: Changed to `select(func.count(RecipientModel.id))`.
   - *Impact*: Eliminates allocating thousands of UUID objects in Python memory, pushing count aggregation directly to PostgreSQL's B-Tree index in < 1 ms.
2. **Chunk Size Calibration (`CHUNK_SIZE=50`)**:
   - *Optimization*: Replaced per-record database commits with chunked transactions of 50 recipients.
   - *Impact*: Reduced transaction overhead by **78%** without sacrificing failure recovery granularity.
3. **Pure In-Memory Vector PDF Streaming**:
   - *Optimization*: Generated PDFs are rendered directly into `io.BytesIO` buffers and streamed to destination storage in a single write operation.
   - *Impact*: Eliminates temporary file disk writes (`tempfile`), cutting disk I/O operations by 50%.
4. **Eager Joinedload for Certificate Retrieval**:
   - *Optimization*: Applied `options(joinedload(RecipientModel.certificate))` to the paginated query.
   - *Impact*: Completely prevents $N+1$ query cascades when retrieving certificate download IDs.

---

## 4. Theoretical Projections at Scale (Estimates Only)

> [!NOTE]
> The figures below are **theoretical mathematical estimates** derived from our empirical measurements (2.71 ms render latency, 2.91 KB PDF size, 50-item chunks). Actual real-world performance will vary based on hardware, network latency, and disk IOPS.

| Total Recipients | Output Storage Volume (Est.) | Processing Time (1 Worker, Est.) | Processing Time (4 Workers, Est.) | Processing Time (8 Workers, Est.) |
| :---: | :---: | :---: | :---: | :---: |
| **1,000** | ~2.91 MB | ~2.7 seconds | **~0.7 seconds** | **~0.4 seconds** |
| **5,000** | ~14.55 MB | ~13.6 seconds | **~3.4 seconds** | **~1.7 seconds** |
| **10,000** | ~29.10 MB | ~27.2 seconds | **~6.8 seconds** | **~3.4 seconds** |
| **50,000** | ~145.50 MB | ~136.0 seconds | **~34.0 seconds** | **~17.0 seconds** |
| **100,000** | ~291.00 MB | ~272.0 seconds (~4.5 min) | **~68.0 seconds (~1.1 min)** | **~34.0 seconds** |

---

## 5. Horizontal Worker Concurrency & Sizing Guidelines

Because ReportLab vector rendering is **strictly CPU-bound**:

```
                       CPU & WORKER CONCURRENCY FORMULA
        Celery Worker Concurrency = Available CPU Cores × 1.0
```

1. **Worker Concurrency Settings**:
   - 4-core VPS: Run Celery with `--concurrency=4`.
   - 8-core Cloud VM: Run Celery with `--concurrency=8`.
   - Setting concurrency higher than available physical cores causes CPU context switching overhead without increasing throughput.
2. **Worker Prefetching & Queue Behavior**:
   - Enforce `worker_prefetch_multiplier = 1` in [`app/core/celery_app.py`](file:///d:/Downloads/Bulk%20Certificate%20Generator/app/core/celery_app.py#L24). This prevents a fast worker from prefetching batches into local memory while other idle workers sit empty.
   - Enforce `task_acks_late = True`. Tasks are acknowledged to Redis only after database commit, guaranteeing zero lost jobs on worker restarts.

---

## 6. Database Connection Pool Sizing

To prevent `QueuePool limit exceeded` under high concurrency, size the SQLAlchemy connection pool using this formula:

$$\text{Total Connections} = (N_{\text{API Replicas}} \times \text{Uvicorn Workers} \times 3) + (N_{\text{Celery Workers}} \times \text{Concurrency}) + 10$$

### Example Sizing for 2 API Replicas and 2 Celery Workers (Concurrency 4):
- API connections: $2 \times 4 \times 3 = 24$ connections
- Worker connections: $2 \times 4 = 8$ connections
- Administrative & Health check buffer: $10$ connections
- **Total PostgreSQL Pool Size**: **42 connections** (well within standard PostgreSQL `max_connections = 100`).

---

## 7. Storage Throughput & Cloud Object Storage Transition

- **Local / Volume Storage**: Capable of handling up to ~500 writes/sec (~1.5 MB/s). Sufficient for single-node deployments up to 100,000 certificates/day.
- **S3 / Cloudflare R2 Transition**: When scaling beyond 100,000 certificates/day, transition storage to Amazon S3 or Cloudflare R2:
  - Writes: Stream directly to S3 via `boto3.upload_fileobj`.
  - Reads: Generate pre-signed S3 download URLs (15-minute TTL), completely offloading PDF network egress from application servers.
