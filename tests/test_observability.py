"""Automated verification tests for observability, metrics, correlation IDs, and retention."""

import json
import logging

from app.core.logging import (
    CorrelationIdFilter,
    JsonLogFormatter,
    set_job_id,
    set_request_id,
)
from app.core.metrics import metrics_registry
from scripts.cleanup_retention import cleanup_expired_records


def test_correlation_id_middleware_generates_id(client):
    """Verify that incoming requests without X-Request-ID receive an auto-generated ID."""
    response = client.get("/health")
    assert response.status_code == 200
    assert "x-request-id" in response.headers
    request_id = response.headers["x-request-id"]
    assert len(request_id) >= 16


def test_correlation_id_middleware_propagates_existing_id(client):
    """Verify that incoming requests with X-Request-ID echo back the same ID."""
    custom_trace_id = "trace-client-custom-987654"
    response = client.get("/health", headers={"X-Request-ID": custom_trace_id})
    assert response.status_code == 200
    assert response.headers.get("x-request-id") == custom_trace_id


def test_metrics_prometheus_exposition_endpoint(client):
    """Verify that /metrics outputs valid Prometheus plain-text exposition."""
    # Record sample telemetry to ensure counters populate
    metrics_registry.record_job_submitted()
    metrics_registry.record_certificate_rendered(0.025, success=True)

    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers.get("content-type", "")

    content = response.text
    assert "# HELP certificate_jobs_total" in content
    assert "# TYPE certificate_jobs_total counter" in content
    assert "# HELP certificates_generated_total" in content
    assert 'certificate_jobs_total{state="submitted"}' in content
    assert 'certificates_generated_total{outcome="success"}' in content
    assert "http_requests_total" in content


def test_metrics_json_export_endpoint(client):
    """Verify that /metrics exports JSON when format=json or Accept: application/json."""
    # 1. Query parameter format=json
    resp_param = client.get("/metrics?format=json")
    assert resp_param.status_code == 200
    assert "application/json" in resp_param.headers.get("content-type", "")
    data = resp_param.json()
    assert "jobs" in data
    assert "certificates" in data
    assert "http_requests_total" in data

    # 2. Header Accept: application/json
    resp_header = client.get("/metrics", headers={"Accept": "application/json"})
    assert resp_header.status_code == 200
    assert "jobs" in resp_header.json()


def test_storage_health_probe_endpoint(client):
    """Verify that /health/storage returns persistent volume capacity diagnostics."""
    response = client.get("/health/storage")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "storage_dir" in data
    assert isinstance(data["total_files"], int)
    assert isinstance(data["total_size_bytes"], int)
    assert isinstance(data["total_size_mb"], (int, float))


def test_json_log_formatter_and_correlation_filter():
    """Verify JsonLogFormatter formats records with correlation IDs and timestamps."""
    set_request_id("req-test-abc")
    set_job_id("job-test-xyz")
    try:
        formatter = JsonLogFormatter()
        log_filter = CorrelationIdFilter()

        record = logging.LogRecord(
            name="test_logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=42,
            msg="Processing certificate batch",
            args=(),
            exc_info=None,
        )

        assert log_filter.filter(record) is True
        formatted_json_str = formatter.format(record)
        log_obj = json.loads(formatted_json_str)

        assert log_obj["level"] == "INFO"
        assert log_obj["logger"] == "test_logger"
        assert log_obj["message"] == "Processing certificate batch"
        assert log_obj["request_id"] == "req-test-abc"
        assert log_obj["job_id"] == "job-test-xyz"
        assert "timestamp" in log_obj
    finally:
        set_request_id(None)
        set_job_id(None)


def test_retention_cleanup_script_dry_run():
    """Verify that cleanup_expired_records executes safely in dry-run mode."""
    stats = cleanup_expired_records(days=90, dry_run=True)
    assert isinstance(stats, dict)
    assert "expired_jobs_count" in stats
    assert "certificates_deleted" in stats
    assert "bytes_reclaimed" in stats
    assert "orphaned_files_cleaned" in stats
