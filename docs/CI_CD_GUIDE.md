# CI/CD Pipeline & Deployment Security Guide

## 1. Overview & Pipeline Philosophy

This project enforces a strict **Zero Untested Code in Production** policy. Every code change submitted to the repository must pass automated quality, security, and integration gates before it can be containerized or considered for deployment.

### Quality & Security Gates Flow

```mermaid
graph TD
    A[Code Push / Pull Request] --> B[Stage 1: Code Quality & Linting]
    B -->|Ruff Check & Format| C[Stage 2: Unit & Integration Tests]
    C -->|Pytest >= 90% Coverage| D[Stage 3: Docker Build & Security Scan]
    D -->|Trivy Vulnerability Scan| E{Branch == main?}
    E -->|Yes| F[Stage 4: Production Deployment Gate]
    E -->|No / PR| G[Pass / Merge Approved]
    F -->|Protected Environment & Approvals| H[Production Deployment]
    
    style B fill:#e1f5fe,stroke:#03a9f4
    style C fill:#e8f5e9,stroke:#4caf50
    style D fill:#fff3e0,stroke:#ff9800
    style F fill:#fce4ec,stroke:#e91e63
    style H fill:#e8eaf6,stroke:#3f51b5
```

---

## 2. Pipeline Stages in Detail

### Stage 1: Code Quality & Formatting (`code-quality`)
- **Trigger**: Every push and pull request.
- **Tools**: [Ruff](https://github.com/astral-sh/ruff)
- **Checks**:
  1. `ruff check .` — Static code analysis, import sorting, bug pattern detection.
  2. `ruff format --check .` — Enforces consistent black-compatible code formatting.

### Stage 2: Automated Testing & Coverage (`test-and-coverage`)
- **Trigger**: Runs only if Stage 1 succeeds.
- **Tools**: [Pytest](https://pytest.org/), [pytest-cov](https://pytest-cov.readthedocs.io/)
- **Enforcement Rules**:
  - All 59 tests must pass with 0 failures.
  - Code coverage threshold must be `>= 90%` (current actual: `99.88%`).
  - Generates Cobertura XML coverage reports uploaded as workflow artifacts (`coverage-report-py3.12`).

### Stage 3: Docker Build & Container Security Scan (`docker-build-and-scan`)
- **Trigger**: Runs only after tests and coverage pass.
- **Tools**: Docker Buildx, [Trivy](https://github.com/aquasecurity/trivy-action)
- **Checks**:
  1. `docker compose config` — Validates compose syntax, volume mappings, and service dependencies.
  2. `docker/build-push-action@v6` — Builds the multi-stage non-root container image with GitHub Actions caching (`type=gha`).
  3. `aquasecurity/trivy-action` — Scans the built container image for known OS (`libpq5`, `debian-slim`) and Python package vulnerabilities (CVEs), flagging `CRITICAL` or `HIGH` risks.

### Stage 4: Production Deployment Gate (`deploy-gate`)
- **Trigger**: Only runs on direct pushes/merges to the `main` branch after Stages 1, 2, and 3 pass.
- **Protection**: Bound to GitHub Environment `production` requiring reviewer sign-off.
- **Guarantee**: Untested, failing, or unvetted pull requests are strictly blocked from triggering deployments.

---

## 3. Repository Secrets & Credential Management

Production credentials and secrets must **NEVER** be committed to Git. Instead, they are managed via **GitHub Repository Secrets** (`Settings -> Secrets and variables -> Actions`).

### Required Production Secrets Reference

| Secret Name | Purpose | Example / Format |
| :--- | :--- | :--- |
| `DOCKERHUB_USERNAME` | Registry username for pushing release container images | `myorg` |
| `DOCKERHUB_TOKEN` | Registry personal access token (PAT) with push privileges | `dckr_pat_...` |
| `PROD_DATABASE_URL` | Production PostgreSQL connection string | `postgresql+psycopg2://usr:pwd@db.internal:5432/cert_prod` |
| `PROD_REDIS_URL` | Production Redis cluster connection string | `redis://:pwd@redis.internal:6379/0` |
| `PROD_API_KEY` | Master API Key for service-to-service authentication | Secure 64-char random token |
| `DEPLOY_HOST` | Production server IP or hostname for SSH / Ansible / K8s | `api.certificates.example.com` |
| `DEPLOY_USER` | Deployment user with limited container restart permissions | `deployer` |
| `DEPLOY_SSH_KEY` | Ed25519 private SSH key for secure remote deployment | `-----BEGIN OPENSSH PRIVATE KEY-----...` |

---

## 4. Branch Protection Best Practices

To safeguard the `main` branch, configure the following rules in GitHub under **Settings -> Branches -> Add branch ruleset**:

1. **Require a pull request before merging**:
   - Require at least 1 peer review approval.
   - Dismiss stale pull request approvals when new commits are pushed.
2. **Require status checks to pass before merging**:
   - `Code Quality & Formatting`
   - `Pytest & Coverage (Python 3.12)`
   - `Docker Build & Security Scan`
3. **Require branches to be up to date before merging**:
   - Prevents merging code tested against stale base branches.
4. **Do not allow bypassing the above settings**:
   - Enforce even for repository administrators.

---

## 5. Local CI Pre-flight Verification Commands

Developers can run the exact same checks locally before pushing to Git:

```bash
# 1. Check code quality and formatting
python -m ruff check .
python -m ruff format --check .

# 2. Run test suite with 90% coverage enforcement
python -m pytest -v --cov=app --cov-report=term-missing --cov-fail-under=90

# 3. Validate Docker Compose topology
docker compose config
```
