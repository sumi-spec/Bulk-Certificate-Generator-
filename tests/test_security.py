"""Security tests: path traversal defenses, API keys, rate limits, and PII masking."""

import pytest

from app.core.config import settings
from app.core.security import mask_email, sanitize_log_message
from app.services.storage import storage_service


def test_path_traversal_prevention_in_storage():
    """Verify that path traversal attempts raise ValueError in StorageService."""
    with pytest.raises(ValueError, match="Access denied"):
        storage_service.resolve_safe_path("../../windows/system32/cmd.exe")

    with pytest.raises(ValueError, match="Access denied"):
        storage_service.resolve_safe_path("../../../etc/shadow")


def test_path_traversal_rejected_at_api_boundary(client):
    """Verify that path traversal URLs are rejected at the HTTP route boundary."""
    # Attempting to inject directory traversal path in place of UUID
    response = client.get("/api/v1/certificates/..%2F..%2Fetc%2Fpasswd")
    # Must be 404 (Not Found) or 422 (Unprocessable UUID), never 200 or 500
    assert response.status_code in (404, 422)


def test_security_headers_present_on_responses(client):
    """Verify that security headers are attached by middleware."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("x-frame-options") in ("DENY", "SAMEORIGIN")
    assert response.headers.get("x-xss-protection") == "1; mode=block"


def test_payload_too_large_rejection(client):
    """Verify that requests exceeding MAX_REQUEST_BODY_BYTES are rejected with 413."""
    oversized_headers = {
        "Content-Length": str(settings.MAX_REQUEST_BODY_BYTES + 5000),
        "Content-Type": "application/json",
    }
    response = client.post("/api/v1/certificate-jobs", content=b"{}", headers=oversized_headers)
    assert response.status_code == 413
    data = response.json()
    assert data["error"] == "Payload Too Large"


def test_api_key_authentication_enforcement(client):
    """Verify API Key authentication when configured."""
    original_api_key = settings.API_KEY
    try:
        # Enable API Key protection
        settings.API_KEY = "test-secret-key-xyz"

        payload = {
            "event_name": "Secured Event",
            "issuer_name": "Security Board",
            "issue_date": "2026-10-10",
            "recipients": [{"name": "User", "email": "user@sec.com"}],
        }

        # 1. Missing API Key -> 401
        res_no_key = client.post("/api/v1/certificate-jobs", json=payload)
        assert res_no_key.status_code == 401

        # 2. Invalid API Key -> 401
        res_bad_key = client.post(
            "/api/v1/certificate-jobs", json=payload, headers={"X-API-Key": "wrong-key"}
        )
        assert res_bad_key.status_code == 401

        # 3. Valid API Key -> 202 Accepted
        res_valid = client.post(
            "/api/v1/certificate-jobs", json=payload, headers={"X-API-Key": "test-secret-key-xyz"}
        )
        assert res_valid.status_code == 202

    finally:
        # Restore configuration
        settings.API_KEY = original_api_key


def test_pii_email_masking():
    """Verify that email addresses are masked to protect PII in logs."""
    masked = mask_email("alexander.graham@example.com")
    assert masked.startswith("a*")
    assert masked.endswith("m@example.com")
    assert "graham" not in masked

    log_entry = "Worker generated certificate for user bob.builder@construct.org successfully."
    sanitized_log = sanitize_log_message(log_entry)
    assert "b*********r@construct.org" in sanitized_log
    assert "bob.builder@" not in sanitized_log


def test_pii_email_masking_edge_cases():
    """Verify masking edge cases: short username, missing @, empty string."""
    assert mask_email("") == "***"
    assert mask_email("invalid_string_no_at") == "***"
    # Short usernames (<= 2 chars)
    assert mask_email("a@test.com") == "a*@test.com"
    assert mask_email("ab@test.com") == "a*@test.com"


async def test_rate_limiter_exceeded():
    """Verify that requests exceeding RPM limit are rejected."""
    from unittest.mock import MagicMock

    from fastapi import HTTPException

    from app.core.security import InMemoryRateLimiter, enforce_rate_limit

    limiter = InMemoryRateLimiter(requests_per_minute=2)
    ip = "192.168.1.50"
    assert limiter.check_rate_limit(ip) is True
    assert limiter.check_rate_limit(ip) is True
    # 3rd request exceeds limit
    assert limiter.check_rate_limit(ip) is False

    # Test FastAPI dependency behavior
    mock_request = MagicMock()
    mock_request.client.host = "192.168.1.99"
    mock_request.url.path = "/api/v1/certificate-jobs"

    from app.core import security as sec_mod

    original_limiter = sec_mod.rate_limiter
    sec_mod.rate_limiter = InMemoryRateLimiter(requests_per_minute=1)
    try:
        # First request allowed
        await enforce_rate_limit(mock_request)
        # Second request raises 429
        with pytest.raises(HTTPException) as exc_info:
            await enforce_rate_limit(mock_request)
        assert exc_info.value.status_code == 429
        assert "Rate limit exceeded" in exc_info.value.detail
    finally:
        sec_mod.rate_limiter = original_limiter
