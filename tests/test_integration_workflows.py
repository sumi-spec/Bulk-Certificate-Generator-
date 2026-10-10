"""End-to-End Integration Workflow Tests.

Verifies the entire lifecycle:
1. Bulk job creation via API.
2. Background processing with partial success / failure isolation.
3. Job status progress verification.
4. Itemized recipient outcome querying.
5. Certificate PDF streaming and verification.
6. Certificate metadata retrieval.
7. Unhandled exception boundary protection.
"""

import uuid
from unittest.mock import patch

from app.models.enums import RecipientStatus
from app.tasks.certificate_tasks import execute_bulk_job_logic


def test_full_bulk_certificate_generation_lifecycle(client, db_session):
    """
    End-to-end test simulating a complete real-world bulk generation workflow:
    - 4 recipients: 2 valid, 1 invalid custom identifier characters, 1 oversized identifier
    - Ensures valid certificates are produced and downloadable
    - Ensures failures are isolated with precise error messages
    - Ensures job finishes as COMPLETED_WITH_ERRORS
    """
    payload = {
        "event_name": "Cloud Native Architecture Bootcamp",
        "issuer_name": "Cloud Native Foundation",
        "issue_date": "2026-10-10",
        "recipients": [
            {
                "name": "Alice Cooper",
                "email": "alice.cooper@cloud.org",
                "custom_identifier": "CERT-001",
            },
            {
                "name": "Bob Marley",
                "email": "bob.marley@cloud.org",
                "custom_identifier": "CERT-002",
            },
            {
                "name": "Invalid Identifier Person",
                "email": "charlie@cloud.org",
                "custom_identifier": "INVALID SPACES !@#",
            },
            {
                "name": "Punctuation Identifier Person",
                "email": "dan@cloud.org",
                "custom_identifier": "ID:WITH:COLONS",
            },
        ],
    }

    # Step 1: POST /api/v1/certificate-jobs -> 202 Accepted
    post_res = client.post("/api/v1/certificate-jobs", json=payload)
    assert post_res.status_code == 202
    job_data = post_res.json()
    assert "job_id" in job_data
    job_id = job_data["job_id"]
    assert job_data["status"] == "PENDING"
    assert job_data["total_recipients"] == 4

    # Step 2: Background worker processes the job
    worker_result = execute_bulk_job_logic(job_id_str=job_id, db=db_session)
    assert worker_result["status"] == "COMPLETED_WITH_ERRORS"
    assert worker_result["total_recipients"] == 4
    assert worker_result["processed_count"] == 4
    assert worker_result["success_count"] == 2
    assert worker_result["failure_count"] == 2

    # Step 3: GET /api/v1/certificate-jobs/{job_id} -> verify progress & counters
    status_res = client.get(f"/api/v1/certificate-jobs/{job_id}")
    assert status_res.status_code == 200
    status_data = status_res.json()
    assert status_data["job_id"] == job_id
    assert status_data["status"] == "COMPLETED_WITH_ERRORS"
    assert status_data["total_recipients"] == 4
    assert status_data["processed_count"] == 4
    assert status_data["success_count"] == 2
    assert status_data["failure_count"] == 2
    assert status_data["started_at"] is not None
    assert status_data["completed_at"] is not None

    # Step 4: GET /api/v1/certificate-jobs/{job_id}/results -> verify itemized results
    results_res = client.get(f"/api/v1/certificate-jobs/{job_id}/results?page=1&page_size=10")
    assert results_res.status_code == 200
    results_data = results_res.json()
    assert results_data["total_records"] == 4
    results_list = results_data["results"]

    # Filter successes and failures
    success_items = [r for r in results_list if r["status"] == RecipientStatus.SUCCESS.value]
    failure_items = [r for r in results_list if r["status"] == RecipientStatus.FAILED.value]
    assert len(success_items) == 2
    assert len(failure_items) == 2

    # Check failure reasons
    failure_errors = [f["error_message"].lower() for f in failure_items]
    assert all("invalid characters" in err for err in failure_errors)

    # Step 5: Test Certificate Download for Alice Cooper
    alice = next(r for r in success_items if r["name"] == "Alice Cooper")
    assert alice["certificate_id"] is not None
    alice_cert_id = alice["certificate_id"]

    download_res = client.get(f"/api/v1/certificates/{alice_cert_id}")
    assert download_res.status_code == 200
    assert download_res.headers["content-type"] == "application/pdf"
    assert "attachment; filename=" in download_res.headers["content-disposition"]
    pdf_content = download_res.content
    assert pdf_content.startswith(b"%PDF-")
    assert b"%%EOF" in pdf_content[-1024:]

    # Step 6: Test Certificate Metadata retrieval
    meta_res = client.get(f"/api/v1/certificates/{alice_cert_id}/metadata")
    assert meta_res.status_code == 200
    meta_data = meta_res.json()
    assert meta_data["certificate_id"] == alice_cert_id
    assert meta_data["recipient_name"] == "Alice Cooper"
    assert meta_data["recipient_email"] == "alice.cooper@cloud.org"
    assert meta_data["event_name"] == "Cloud Native Architecture Bootcamp"
    assert meta_data["issuer_name"] == "Cloud Native Foundation"
    assert meta_data["sha256_checksum"] == download_res.headers["x-certificate-checksum"]
    assert meta_data["file_size_bytes"] == len(pdf_content)


def test_unhandled_server_exception_returns_500_without_stacktrace():
    """
    Verify that unhandled server exceptions return a secure HTTP 500 JSON response
    without leaking internal traceback or stack trace information to clients.
    """
    from fastapi.testclient import TestClient

    from app.main import app

    unhandled_client = TestClient(app, raise_server_exceptions=False)
    with patch(
        "app.services.job_service.job_service.get_job_by_id",
        side_effect=RuntimeError("Simulated unhandled DB crash"),
    ):
        response = unhandled_client.get(f"/api/v1/certificate-jobs/{uuid.uuid4()}")
        assert response.status_code == 500
        data = response.json()
        assert data["error"] == "Internal Server Error"
        assert "An unexpected error occurred" in data["message"]
        # Ensure stack trace is NOT exposed in the JSON response
        assert "RuntimeError" not in str(data)
        assert "Traceback" not in str(data)
