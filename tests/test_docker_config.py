"""Automated verification tests for Docker and Docker Compose configuration."""

from pathlib import Path

import yaml


def test_docker_compose_syntax_and_topology():
    """Verify docker-compose.yml syntax, required services, networks, and persistent volumes."""
    compose_path = Path("docker-compose.yml")
    assert compose_path.exists(), "docker-compose.yml must exist"

    with open(compose_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    services = config.get("services", {})
    assert "db" in services, "PostgreSQL db service missing from docker-compose.yml"
    assert "redis" in services, "Redis service missing from docker-compose.yml"
    assert "api" in services, "FastAPI api service missing from docker-compose.yml"
    assert "worker" in services, "Celery worker service missing from docker-compose.yml"

    # Verify persistent volumes
    volumes = config.get("volumes", {})
    assert "postgres_data" in volumes
    assert "redis_data" in volumes
    assert "cert_storage" in volumes

    # Verify service dependencies
    api_deps = services["api"].get("depends_on", {})
    assert "db" in api_deps
    assert api_deps["db"]["condition"] == "service_healthy"
    assert "redis" in api_deps
    assert api_deps["redis"]["condition"] == "service_healthy"

    worker_deps = services["worker"].get("depends_on", {})
    assert "db" in worker_deps
    assert "redis" in worker_deps
    assert "api" in worker_deps

    # Verify health checks
    assert "healthcheck" in services["db"]
    assert "healthcheck" in services["redis"]
    assert "healthcheck" in services["api"]

    # Verify restart policies
    for name, s in services.items():
        assert s.get("restart") == "unless-stopped", (
            f"Service {name} must have restart: unless-stopped"
        )


def test_dockerfile_security_and_multi_stage():
    """Verify Dockerfile contains multi-stage build, non-root user, and health check."""
    dockerfile_path = Path("Dockerfile")
    assert dockerfile_path.exists(), "Dockerfile must exist"

    content = dockerfile_path.read_text(encoding="utf-8")
    # Multi-stage check
    assert "AS builder" in content, "Dockerfile must use a builder stage"
    assert "AS runner" in content, "Dockerfile must use a runner stage"

    # Non-root security check
    assert "groupadd -g 1000 appgroup" in content
    assert "useradd -u 1000 -g appgroup" in content
    assert "USER appuser" in content, "Dockerfile must enforce non-root user"

    # Healthcheck check
    assert "HEALTHCHECK" in content
    assert "curl -f http://localhost:8000/health" in content

    # Entrypoint check
    assert 'ENTRYPOINT ["/app/docker-entrypoint.sh"]' in content


def test_docker_entrypoint_script_contents():
    """Verify docker-entrypoint.sh handles Alembic migrations and executes commands."""
    entrypoint_path = Path("docker-entrypoint.sh")
    assert entrypoint_path.exists(), "docker-entrypoint.sh must exist"

    content = entrypoint_path.read_text(encoding="utf-8")
    assert "alembic upgrade head" in content, "Entrypoint must apply database migrations"
    assert 'exec "$@"' in content, 'Entrypoint must exec "$@" for PID 1 signal propagation'


def test_dockerignore_coverage():
    """Verify .dockerignore excludes virtualenvs, test caches, and secrets."""
    dockerignore_path = Path(".dockerignore")
    assert dockerignore_path.exists(), ".dockerignore must exist"

    content = dockerignore_path.read_text(encoding="utf-8")
    assert ".venv/" in content or "venv/" in content
    assert ".git/" in content
    assert ".env" in content
    assert "__pycache__/" in content


def test_github_actions_ci_workflow_validity():
    """Verify that .github/workflows/ci.yml exists, is valid YAML, and contains all required stages."""
    workflow_path = Path(".github/workflows/ci.yml")
    assert workflow_path.exists(), "GitHub Actions workflow ci.yml must exist"

    with open(workflow_path, encoding="utf-8") as f:
        workflow = yaml.safe_load(f)

    jobs = workflow.get("jobs", {})
    assert "code-quality" in jobs, "CI workflow must have code-quality stage"
    assert "test-and-coverage" in jobs, "CI workflow must have test-and-coverage stage"
    assert "docker-build-and-scan" in jobs, "CI workflow must have docker-build-and-scan stage"
    assert "deploy-gate" in jobs, "CI workflow must have deploy-gate stage"

    # Verify dependency chain ensures untested code never reaches deployment
    assert jobs["test-and-coverage"]["needs"] == "code-quality"
    assert jobs["docker-build-and-scan"]["needs"] == "test-and-coverage"
    assert "test-and-coverage" in jobs["deploy-gate"]["needs"]
    assert "docker-build-and-scan" in jobs["deploy-gate"]["needs"]


def test_production_deployment_configuration():
    """Verify production deployment stack, Nginx TLS proxy, and zero-job-loss configurations."""
    # 1. Verify docker-compose.prod.yml
    prod_compose_path = Path("docker-compose.prod.yml")
    assert prod_compose_path.exists(), "docker-compose.prod.yml must exist"

    with open(prod_compose_path, encoding="utf-8") as f:
        prod_config = yaml.safe_load(f)

    services = prod_config.get("services", {})
    assert "nginx" in services, "Production compose must include Nginx reverse proxy"
    assert "api" in services, "Production compose must include api"
    assert "worker" in services, "Production compose must include worker"
    assert "redis" in services, "Production compose must include redis"

    # Zero job loss: worker must have stop_grace_period
    worker_service = services["worker"]
    assert worker_service.get("stop_grace_period") == "60s", (
        "Celery worker must have stop_grace_period: 60s for zero job loss during deployments"
    )

    # Redis persistence: AOF and noeviction
    redis_service = services["redis"]
    redis_cmd = redis_service.get("command", "")
    assert "--appendonly yes" in redis_cmd, "Production Redis must enable AOF persistence"
    assert "--maxmemory-policy noeviction" in redis_cmd, "Production Redis must use noeviction"

    # Volumes check
    prod_volumes = prod_config.get("volumes", {})
    assert "cert_storage" in prod_volumes
    assert "redis_data" in prod_volumes

    # 2. Verify .env.production.example
    env_prod_example = Path(".env.production.example")
    assert env_prod_example.exists()
    env_content = env_prod_example.read_text(encoding="utf-8")
    assert "APP_ENV=production" in env_content
    assert "DATABASE_URL=" in env_content
    assert "CELERY_BROKER_URL=" in env_content
    assert "API_KEY=" in env_content

    # 3. Verify Nginx configurations
    nginx_conf = Path("nginx/nginx.conf")
    api_conf = Path("nginx/conf.d/api.conf")
    assert nginx_conf.exists()
    assert api_conf.exists()

    api_conf_text = api_conf.read_text(encoding="utf-8")
    assert "ssl_protocols TLSv1.2 TLSv1.3;" in api_conf_text
    assert "Strict-Transport-Security" in api_conf_text
    assert "proxy_pass http://fastapi_backend;" in api_conf_text

    # 4. Verify deployment verification script
    verify_script = Path("scripts/verify_deployment.py")
    assert verify_script.exists()
    script_text = verify_script.read_text(encoding="utf-8")
    assert "def verify_deployment" in script_text
    assert "/health" in script_text
    assert "/api/v1/certificate-jobs" in script_text
    assert "%PDF" in script_text

    # 5. Verify documentation
    prod_doc = Path("docs/PRODUCTION_DEPLOYMENT.md")
    assert prod_doc.exists()
    doc_text = prod_doc.read_text(encoding="utf-8")
    assert "Deployment Target Evaluation" in doc_text
    assert "Zero-Downtime Database Migrations" in doc_text
    assert "Preventing Lost Jobs" in doc_text
