"""Integration tests for Certificate Jobs and Certificate endpoints."""

import uuid
from datetime import UTC, date, datetime

from app.models import CertificateModel, JobModel, JobStatus, RecipientModel, RecipientStatus
from app.services.storage import storage_service


def test_create_certificate_job_success(client):
    """Verify that POST /api/v1/certificate-jobs creates a job and returns 202 Accepted."""
    payload = {
        "event_name": "FastAPI Masterclass 2026",
        "issuer_name": "Python Software Foundation",
        "issue_date": "2026-10-10",
        "recipients": [
            {
                "name": "Alice Developer",
                "email": "alice@example.com",
                "custom_identifier": "ID-001",
            },
            {"name": "Bob Architect", "email": "bob@example.com"},
        ],
    }
    response = client.post("/api/v1/certificate-jobs", json=payload)
    assert response.status_code == 202
    data = response.json()
    assert "job_id" in data
    assert data["status"] == "PENDING"
    assert data["total_recipients"] == 2


def test_create_certificate_job_with_idempotency_key(client):
    """Verify that sending the same Idempotency-Key returns the existing job."""
    idempotency_key = f"key-{uuid.uuid4()}"
    payload = {
        "event_name": "Idempotent Event",
        "issuer_name": "Tech Corp",
        "issue_date": "2026-10-10",
        "recipients": [{"name": "Single User", "email": "user@example.com"}],
    }
    headers = {"Idempotency-Key": idempotency_key}

    res1 = client.post("/api/v1/certificate-jobs", json=payload, headers=headers)
    assert res1.status_code == 202
    job_id_1 = res1.json()["job_id"]

    res2 = client.post("/api/v1/certificate-jobs", json=payload, headers=headers)
    assert res2.status_code == 202
    job_id_2 = res2.json()["job_id"]

    assert job_id_1 == job_id_2


def test_create_certificate_job_validation_error(client):
    """Verify that malformed requests return 422 Unprocessable Entity."""
    # Empty recipients
    res_empty = client.post(
        "/api/v1/certificate-jobs",
        json={
            "event_name": "Event",
            "issuer_name": "Issuer",
            "issue_date": "2026-10-10",
            "recipients": [],
        },
    )
    assert res_empty.status_code == 422

    # Malformed email
    res_bad_email = client.post(
        "/api/v1/certificate-jobs",
        json={
            "event_name": "Event",
            "issuer_name": "Issuer",
            "issue_date": "2026-10-10",
            "recipients": [{"name": "User", "email": "invalid-email"}],
        },
    )
    assert res_bad_email.status_code == 422


def test_get_job_status_and_not_found(client, db_session):
    """Verify status retrieval and 404 handling."""
    # 404 check
    non_existent_id = uuid.uuid4()
    res_404 = client.get(f"/api/v1/certificate-jobs/{non_existent_id}")
    assert res_404.status_code == 404

    # Create job in DB
    job = JobModel(
        event_name="PostgreSQL Optimization",
        issuer_name="Database Guild",
        issue_date=date(2026, 10, 10),
        status=JobStatus.PROCESSING,
        total_recipients=5,
        processed_count=3,
        success_count=2,
        failure_count=1,
    )
    db_session.add(job)
    db_session.commit()

    res_ok = client.get(f"/api/v1/certificate-jobs/{job.id}")
    assert res_ok.status_code == 200
    data = res_ok.json()
    assert data["job_id"] == str(job.id)
    assert data["status"] == "PROCESSING"
    assert data["total_recipients"] == 5
    assert data["processed_count"] == 3
    assert data["success_count"] == 2
    assert data["failure_count"] == 1


def test_get_job_results_pagination(client, db_session):
    """Verify paginated recipient results."""
    job = JobModel(
        event_name="Testing Course",
        issuer_name="QA Academy",
        issue_date=date(2026, 10, 10),
        status=JobStatus.COMPLETED,
        total_recipients=3,
    )
    db_session.add(job)
    db_session.flush()

    r1 = RecipientModel(
        job_id=job.id, name="User One", email="one@test.com", status=RecipientStatus.SUCCESS
    )
    r2 = RecipientModel(
        job_id=job.id,
        name="User Two",
        email="two@test.com",
        status=RecipientStatus.FAILED,
        error_message="Invalid formatting",
    )
    r3 = RecipientModel(
        job_id=job.id, name="User Three", email="three@test.com", status=RecipientStatus.SUCCESS
    )
    db_session.add_all([r1, r2, r3])
    db_session.commit()

    # Query page 1 with page_size 2
    res = client.get(f"/api/v1/certificate-jobs/{job.id}/results?page=1&page_size=2")
    assert res.status_code == 200
    data = res.json()
    assert data["total_records"] == 3
    assert data["total_pages"] == 2
    assert len(data["results"]) == 2

    # Query with status filter FAILED
    res_filtered = client.get(f"/api/v1/certificate-jobs/{job.id}/results?status=FAILED")
    assert res_filtered.status_code == 200
    filtered_data = res_filtered.json()
    assert filtered_data["total_records"] == 1
    assert filtered_data["results"][0]["status"] == "FAILED"
    assert filtered_data["results"][0]["error_message"] == "Invalid formatting"


def test_certificate_download_and_metadata(client, db_session, tmp_path):
    """Verify downloading certificate PDF binary and retrieving metadata."""
    # 404 for unknown certificate
    res_404 = client.get(f"/api/v1/certificates/{uuid.uuid4()}")
    assert res_404.status_code == 404

    # Setup real file in storage
    test_pdf_content = b"%PDF-1.4 test certificate binary content"
    relative_path = "test_job/test_cert.pdf"
    full_path, size_bytes, sha256_hash = storage_service.save_file(relative_path, test_pdf_content)

    job = JobModel(
        event_name="Docker & K8s Certification",
        issuer_name="DevOps Alliance",
        issue_date=date(2026, 10, 10),
        status=JobStatus.COMPLETED,
        total_recipients=1,
    )
    db_session.add(job)
    db_session.flush()

    recipient = RecipientModel(
        job_id=job.id,
        name="DevOps Engineer",
        email="devops@example.com",
        status=RecipientStatus.SUCCESS,
    )
    db_session.add(recipient)
    db_session.flush()

    cert = CertificateModel(
        job_id=job.id,
        recipient_id=recipient.id,
        file_name="certificate_DevOps_Engineer.pdf",
        file_path=relative_path,
        file_size_bytes=size_bytes,
        sha256_checksum=sha256_hash,
        issued_at=datetime.now(UTC),
    )
    db_session.add(cert)
    db_session.commit()

    # Download certificate binary
    res_download = client.get(f"/api/v1/certificates/{cert.id}")
    assert res_download.status_code == 200
    assert res_download.headers["content-type"] == "application/pdf"
    assert "attachment; filename=" in res_download.headers["content-disposition"]
    assert res_download.content == test_pdf_content

    # Retrieve metadata
    res_meta = client.get(f"/api/v1/certificates/{cert.id}/metadata")
    assert res_meta.status_code == 200
    meta_data = res_meta.json()
    assert meta_data["certificate_id"] == str(cert.id)
    assert meta_data["recipient_name"] == "DevOps Engineer"
    assert meta_data["sha256_checksum"] == sha256_hash


def test_get_job_results_not_found(client):
    """Verify that requesting results for a non-existent job returns 404."""
    non_existent_id = uuid.uuid4()
    response = client.get(f"/api/v1/certificate-jobs/{non_existent_id}/results")
    assert response.status_code == 404


def test_certificate_metadata_not_found(client):
    """Verify that requesting metadata for a non-existent certificate returns 404."""
    non_existent_id = uuid.uuid4()
    response = client.get(f"/api/v1/certificates/{non_existent_id}/metadata")
    assert response.status_code == 404


def test_certificate_download_file_missing_on_disk(client, db_session):
    """Verify that 404 is returned when the DB certificate record exists but the file is missing from disk."""
    job = JobModel(
        event_name="Storage Failure Test",
        issuer_name="Storage Org",
        issue_date=date(2026, 10, 10),
        status=JobStatus.COMPLETED,
        total_recipients=1,
    )
    db_session.add(job)
    db_session.flush()

    recipient = RecipientModel(
        job_id=job.id,
        name="Ghost File Recipient",
        email="ghost@example.com",
        status=RecipientStatus.SUCCESS,
    )
    db_session.add(recipient)
    db_session.flush()

    cert = CertificateModel(
        job_id=job.id,
        recipient_id=recipient.id,
        file_name="non_existent_file.pdf",
        file_path="non_existent_folder/missing.pdf",
        file_size_bytes=1024,
        sha256_checksum="abc123mockhash",
        issued_at=datetime.now(UTC),
    )
    db_session.add(cert)
    db_session.commit()

    response = client.get(f"/api/v1/certificates/{cert.id}")
    assert response.status_code == 404
    assert "missing from storage" in response.json()["detail"].lower()


def test_create_job_broker_dispatch_failure_gracefully_handled(client, monkeypatch):
    """Verify that a broker dispatch failure logs a warning but does not fail the HTTP request."""
    from unittest.mock import MagicMock

    from app.tasks.certificate_tasks import process_bulk_certificate_job

    mock_delay = MagicMock(side_effect=ConnectionError("Redis broker is temporarily unavailable"))
    monkeypatch.setattr(process_bulk_certificate_job, "delay", mock_delay)

    payload = {
        "event_name": "Broker Resilience Event",
        "issuer_name": "Resilience Lab",
        "issue_date": "2026-10-10",
        "recipients": [{"name": "Resilient User", "email": "resilient@example.com"}],
    }
    response = client.post("/api/v1/certificate-jobs", json=payload)
    assert response.status_code == 202
    data = response.json()
    assert "job_id" in data
