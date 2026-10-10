# Production Deployment Guide: Bulk Certificate Generator

This document provides a production-grade operations manual for deploying, running, and maintaining the **Bulk Certificate Generator API** and its distributed Celery workers in high-reliability environments.

---

## 1. Deployment Target Evaluation & Recommendation

Selecting the appropriate production target requires balancing **financial cost**, **system reliability**, and **operational complexity**:

| Evaluation Criteria | Option A: Cloud VM + Docker Compose (Recommended Default) | Option B: Managed Containers (AWS ECS Fargate / GCP Cloud Run) | Option C: Kubernetes (AWS EKS / GKE) |
| :--- | :--- | :--- | :--- |
| **Target Architecture** | Hardened Linux VPS (Ubuntu 24.04 LTS) running Nginx + Docker Compose with Managed DB/Redis. | Serverless container tasks orchestrated by AWS ECS with Application Load Balancer. | EKS cluster running Helm chart, Ingress Controller, cert-manager, KEDA worker autoscaler. |
| **Estimated Monthly Cost** | **$20 – $45 / month**<br>(4GB RAM VPS + Managed DB/Redis) | **$60 – $180 / month**<br>(ALB + Fargate vCPU/GB runtime + NAT Gateway) | **$120 – $350+ / month**<br>($75 base cluster control plane + worker EC2 nodes) |
| **Operational Complexity** | **Low**: 1:1 parity with local Compose; standard Linux tools; single-command deployment. | **Medium**: Requires IAM policies, CloudWatch log groups, Task Definitions, and ALB rules. | **High**: Requires Kubernetes manifests, RBAC, Ingress routing, storage classes, and cluster upgrades. |
| **Reliability & Uptime** | **High**: Systemd auto-restart, Docker health checks, independent managed DB/Redis. | **Very High**: Native multi-AZ redundancy; automated container health replacement. | **Maximum**: Multi-AZ node groups, self-healing pods, automated rolling deployments. |
| **Ideal For** | Early-stage, internal enterprise, and mid-scale systems (< 50,000 certificates/day). | Workloads with sharp traffic spikes requiring automated horizontal scaling. | Large organizations with dedicated DevOps teams already running Kubernetes. |

### Recommended Choice
For cost-conscious production readiness with minimal friction, **Option A (Hardened Linux VPS with Docker Compose + Managed PostgreSQL & Redis)** is the recommended deployment strategy. The architecture provided in [`docker-compose.prod.yml`](file:///d:/Downloads/Bulk%20Certificate%20Generator/docker-compose.prod.yml) offers direct container parity while isolating public traffic through a hardened Nginx reverse proxy.

---

## 2. Infrastructure Setup & Network Topology

```
             INTERNET (HTTPS Clients / Webhooks)
                            │
                     Port 443 (TLS 1.3)
                            ▼
      ┌───────────────────────────────────────────────┐
      │  PUBLIC DMZ / REVERSE PROXY TIER              │
      │  • Nginx Reverse Proxy (SSL Termination)      │
      │  • Rate Limiting (30 req/s, Burst 50)         │
      │  • Security Headers & Max Body 15MB           │
      └──────────────────────┬────────────────────────┘
                             │ Forward via internal_net
                             ▼
      ┌───────────────────────────────────────────────┐
      │  APPLICATION & WORKER TIER (Private Network)  │
      │  • FastAPI ASGI App (Port 8000 internal)      │
      │  • Celery Workers (Concurrency 4, Graceful)   │
      │  • Persistent PDF Storage Volume              │
      └──────────────┬──────────────────┬─────────────┘
                     │                  │
                     ▼                  ▼
      ┌────────────────────────┐  ┌───────────────────────────────────┐
      │ MANAGED POSTGRESQL 16  │  │ MANAGED REDIS BROKER              │
      │ • Multi-AZ Standby     │  │ • AOF Persistence (appendonly)    │
      │ • Automated Backups    │  │ • Password Protected Auth         │
      │ • Port 5432 (Internal) │  │ • Port 6379 (Internal)            │
      └────────────────────────┘  └───────────────────────────────────┘
```

### Firewall & Port Rules (Security Group)
- **Port 80 (HTTP)**: Ingress open to `0.0.0.0/0` (strictly used for ACME challenge & 301 HTTPS redirect).
- **Port 443 (HTTPS)**: Ingress open to `0.0.0.0/0` (TLS termination).
- **Port 22 (SSH)**: Ingress restricted to admin static IP or bastion host.
- **Ports 8000, 5432, 6379**: **BLOCKED from external internet**. Only accessible internally via the Docker bridge network (`internal_net`).

---

## 3. Secrets Management & Environment Isolation

### Separation of Environments
- **Local Development**: Uses `.env` (SQLite or local PostgreSQL, public mock keys).
- **Staging / Testing**: Uses dedicated test databases in GitHub Actions (`coverage-report`).
- **Production**: Uses `.env.production` (or AWS Secrets Manager / Doppler / Vault). The production file has permissions set to `chmod 600` and is owned by the deployment user.

### Generating Cryptographic Secrets
Always generate cryptographically random keys for production:
```bash
# Generate 64-character API Key
openssl rand -hex 32

# Generate strong database password
openssl rand -base64 24
```

Copy and configure the production environment file:
```bash
cp .env.production.example .env.production
chmod 600 .env.production
nano .env.production
```

---

## 4. PostgreSQL and Redis: Persistence & Reliability

### Managed PostgreSQL Configuration
1. **Connection Pooling**: Use PgBouncer or configure SQLAlchemy pool settings (`pool_size=20`, `max_overflow=10`).
2. **Automated Backups**: Enable 7-day automated backups with Point-In-Time Recovery (PITR).
3. **SSL Enforcement**: Ensure connection strings use `sslmode=require` or `sslmode=verify-full`:
   ```text
   DATABASE_URL=postgresql://user:password@db-host:5432/cert_prod_db?sslmode=require
   ```

### Redis Configuration: Zero Queue Loss
By default, Redis stores data in RAM. If Redis restarts abruptly, unpersisted messages in the queue can be lost. To prevent this, the production configuration in [`docker-compose.prod.yml`](file:///d:/Downloads/Bulk%20Certificate%20Generator/docker-compose.prod.yml) enforces:
- **Append-Only File (AOF) Persistence**: `--appendonly yes` writes every queue push/pop to disk.
- **Fsync Policy**: `--appendfsync everysec` guarantees that at most 1 second of queue updates could ever be lost during catastrophic host power failure.
- **RDB Snapshots**: `--save 900 1 --save 300 10` provides periodic compact snapshots.
- **Eviction Policy**: `--maxmemory-policy noeviction` ensures Redis **never silently discards** unread queue items if memory fills up; instead, it raises an error that triggers worker backpressure.

---

## 5. Certificate Storage Architecture

### Transitioning from Local Disk to Object Storage
In standard containerized environments, container filesystems are ephemeral. Storing generated PDFs inside a container will cause data loss on container restart.

1. **Phase 1: Persistent Mounted Volume (Single-Node / Compose)**
   - Mount a named Docker volume (`cert_prod_storage`) mapped to host persistent storage (e.g. AWS EBS or DigitalOcean Block Storage).
   - In [`docker-compose.prod.yml`](file:///d:/Downloads/Bulk%20Certificate%20Generator/docker-compose.prod.yml), both the `api` and `worker` mount the same volume:
     ```yaml
     volumes:
       - cert_storage:/app/storage/certificates
     ```

2. **Phase 2: S3-Compatible Object Storage (Multi-Node / Cloud Native)**
   - For multi-node or serverless deployments, transition `storage_service` to write directly to an S3 bucket (or Cloudflare R2 / MinIO).
   - Enable **S3 Versioning** and **Cross-Region Replication (CRR)**.
   - Serve certificates via short-lived (e.g. 15-minute) pre-signed S3 download URLs generated by the API, offloading PDF bandwidth from the application servers.

---

## 6. Container Image Publishing

Images are automatically built and verified in CI. In a production pipeline:
1. **Build and Tag**:
   ```bash
   # Build image tagged with Git commit SHA and latest
   COMMIT_SHA=$(git rev-parse --short HEAD)
   docker build -t ghcr.io/organization/bulk-certificate-generator:${COMMIT_SHA} .
   docker tag ghcr.io/organization/bulk-certificate-generator:${COMMIT_SHA} ghcr.io/organization/bulk-certificate-generator:latest
   ```
2. **Scan with Trivy**:
   ```bash
   trivy image --severity HIGH,CRITICAL ghcr.io/organization/bulk-certificate-generator:${COMMIT_SHA}
   ```
3. **Publish to Registry**:
   ```bash
   docker push ghcr.io/organization/bulk-certificate-generator:${COMMIT_SHA}
   docker push ghcr.io/organization/bulk-certificate-generator:latest
   ```

*Rule: Never deploy `:latest` blindly in production. Always deploy specific immutable commit SHAs (`:${COMMIT_SHA}`).*

---

## 7. Zero-Downtime Database Migrations

Applying database migrations carelessly can lock tables and cause downtime. To ensure safe migrations:

### Expand-Contract Migration Pattern
1. **Expand (Pre-Deployment)**: Add new columns as **nullable** or with safe defaults. Add new tables. Existing code continues running without failure.
2. **Deploy Code**: Deploy new API and worker containers that read/write the new columns.
3. **Backfill**: Run asynchronous scripts to backfill historical data into new columns if necessary.
4. **Contract (Post-Deployment)**: In a subsequent release, apply constraints (e.g. `NOT NULL`) or drop deprecated columns.

### Execution Command
Migrations must be executed **before** launching updated application containers:
```bash
# Run migrations using the new image against production database
docker run --rm \
  --env-file .env.production \
  --network cert_prod_internal_net \
  ghcr.io/organization/bulk-certificate-generator:${COMMIT_SHA} \
  alembic upgrade head
```

---

## 8. API & Worker Deployment: Preventing Lost Jobs

### 1. How to Avoid Losing Queued Jobs
Deployments must never drop jobs waiting in the queue or jobs currently being processed:
- **`task_acks_late=True`**: Configured in [`app/core/celery_app.py`](file:///d:/Downloads/Bulk%20Certificate%20Generator/app/core/celery_app.py). Celery workers only acknowledge and remove a task from Redis **after** the task has successfully executed and committed to PostgreSQL. If a worker process terminates mid-execution, Redis automatically re-queues the task for another worker.
- **`worker_prefetch_multiplier=1`**: Prevents workers from greedily prefetching batches of jobs into memory where they could be stranded during a shutdown.
- **Graceful Worker Shutdown (`stop_grace_period: 60s`)**:
  When deploying updated workers, Docker sends `SIGTERM` to the Celery process. Celery enters **warm shutdown**:
  - It immediately stops accepting new tasks from the queue.
  - It allows currently executing certificate chunks up to 60 seconds to finish rendering and committing to disk.
  - Only after existing tasks complete does the worker process exit cleanly.

### 2. How to Avoid Losing Generated Certificates
- The PDF generator writes the certificate to the persistent volume `cert_storage`.
- In [`app/tasks/certificate_tasks.py`](file:///d:/Downloads/Bulk%20Certificate%20Generator/app/tasks/certificate_tasks.py), the database record with file metadata (`file_path`, `file_size_bytes`, `sha256_checksum`) is committed in the same unit of work as the file write.
- If a deployment occurs between recipients, the worker restarts from the last recorded state without overwriting or duplicating existing PDFs.

### 3. Rolling Update Sequence
```bash
# 1. Pull the new immutable image
export IMAGE_TAG=ghcr.io/organization/bulk-certificate-generator:${COMMIT_SHA}
docker compose -f docker-compose.prod.yml pull api worker

# 2. Run database migrations
docker run --rm --env-file .env.production ${IMAGE_TAG} alembic upgrade head

# 3. Update API container (Zero-downtime rolling restart)
docker compose -f docker-compose.prod.yml up -d --no-deps api

# 4. Wait for API health check to pass
docker compose -f docker-compose.prod.yml exec api curl -f http://localhost:8000/health

# 5. Gracefully restart Celery workers
docker compose -f docker-compose.prod.yml up -d --no-deps worker

# 6. Verify Nginx configuration and reload without dropping connections
docker compose -f docker-compose.prod.yml exec nginx nginx -s reload
```

---

## 9. TLS & Domain Setup (Nginx + Let's Encrypt)

The Nginx configuration files are organized under [`nginx/`](file:///d:/Downloads/Bulk%20Certificate%20Generator/nginx/):
- [`nginx/nginx.conf`](file:///d:/Downloads/Bulk%20Certificate%20Generator/nginx/nginx.conf): Global settings, logging format, rate-limiting zones, and gzip compression.
- [`nginx/conf.d/api.conf`](file:///d:/Downloads/Bulk%20Certificate%20Generator/nginx/conf.d/api.conf): Upstream proxy configuration, HTTP-to-HTTPS 301 redirects, security headers, and TLS settings.

### Initial SSL Certificate Acquisition (Certbot)
Before starting Nginx with SSL enabled:
```bash
# Run standalone Certbot to obtain certificates for your domain
docker run -it --rm \
  -v cert_prod_certbot_certs:/etc/letsencrypt \
  -p 80:80 \
  certbot/certbot certonly --standalone \
  -d certs.yourdomain.com \
  --email admin@yourdomain.com \
  --agree-tos --no-eff-email
```

### Automated SSL Renewal Cron
Add a daily cron job on the host machine to renew certificates and reload Nginx:
```bash
0 3 * * * docker run --rm -v cert_prod_certbot_certs:/etc/letsencrypt -v cert_prod_certbot_challenge:/var/www/certbot certbot/certbot renew --quiet && docker exec cert_prod_nginx nginx -s reload
```

---

## 10. Automated Workflow Verification (Smoke Test)

Following every deployment, run the automated verification script [`scripts/verify_deployment.py`](file:///d:/Downloads/Bulk%20Certificate%20Generator/scripts/verify_deployment.py) to test the live production endpoints:

```bash
# Verify deployed instance
python scripts/verify_deployment.py \
  --base-url https://certs.yourdomain.com \
  --api-key prod_live_sec_your_strong_key \
  --timeout 60
```

The script automatically executes and verifies:
1. `/health` responds with `healthy` status, confirming PostgreSQL and Redis connectivity.
2. Authenticated access via `X-API-Key` is enforced.
3. Submits a test bulk certificate job (`POST /api/v1/certificate-jobs`).
4. Polls job progress until status is `COMPLETED`.
5. Confirms individual recipient results and certificate IDs.
6. Downloads a generated PDF and validates the `%PDF` header and file size.

---

## 11. Rollback & Disaster Recovery Procedures

### 1. Application Rollback (Fastest)
If an application error occurs post-deployment:
```bash
# Revert to the previous known good commit SHA
export IMAGE_TAG=ghcr.io/organization/bulk-certificate-generator:previous-known-sha
docker compose -f docker-compose.prod.yml up -d --no-deps api worker
```

### 2. Database Downgrade (If Safe)
If the migration was additive and includes an explicit downgrade path:
```bash
docker run --rm --env-file .env.production ${IMAGE_TAG} alembic downgrade -1
```
*Note: If data has already been written to new structures, prefer a forward-fix migration over a destructive rollback.*

### 3. Disaster Recovery Objectives
- **Recovery Time Objective (RTO)**: **< 15 minutes** (Spin up fresh host, clone repo, inject `.env.production`, launch `docker-compose.prod.yml`).
- **Recovery Point Objective (RPO)**: **< 1 minute** (PostgreSQL automated WAL archiving + Redis AOF persistence).
