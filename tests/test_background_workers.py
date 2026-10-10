"""Integration tests for background worker processing, partial failures, and idempotency."""

import uuid
from datetime import date

from app.models import CertificateModel, JobModel, JobStatus, RecipientModel, RecipientStatus
from app.tasks.certificate_tasks import execute_bulk_job_logic


def test_worker_full_success(db_session):
    """Verify worker processes all valid recipients and marks job COMPLETED."""
    job = JobModel(
        event_name="Full Stack AI",
        issuer_name="AI Institute",
        issue_date=date(2026, 10, 10),
        status=JobStatus.PENDING,
        total_recipients=2,
    )
    db_session.add(job)
    db_session.flush()

    r1 = RecipientModel(
        job_id=job.id, name="User A", email="usera@test.com", status=RecipientStatus.PENDING
    )
    r2 = RecipientModel(
        job_id=job.id, name="User B", email="userb@test.com", status=RecipientStatus.PENDING
    )
    db_session.add_all([r1, r2])
    db_session.commit()

    result = execute_bulk_job_logic(job_id_str=str(job.id), db=db_session)
    assert result["status"] == "COMPLETED"
    assert result["success_count"] == 2
    assert result["failure_count"] == 0

    db_session.refresh(job)
    assert job.status == JobStatus.COMPLETED
    assert job.success_count == 2
    assert job.failure_count == 0
    assert len(job.certificates) == 2


def test_worker_partial_success_failure_isolation(db_session):
    """Verify an individual invalid recipient fails without stopping other valid ones."""
    job = JobModel(
        event_name="Cloud Architecture",
        issuer_name="Cloud Org",
        issue_date=date(2026, 10, 10),
        status=JobStatus.PENDING,
        total_recipients=3,
    )
    db_session.add(job)
    db_session.flush()

    r_valid_1 = RecipientModel(
        job_id=job.id, name="Valid One", email="valid1@test.com", status=RecipientStatus.PENDING
    )
    r_bad = RecipientModel(
        job_id=job.id, name="Bad Email", email="not-an-email", status=RecipientStatus.PENDING
    )
    r_valid_2 = RecipientModel(
        job_id=job.id, name="Valid Two", email="valid2@test.com", status=RecipientStatus.PENDING
    )
    db_session.add_all([r_valid_1, r_bad, r_valid_2])
    db_session.commit()

    result = execute_bulk_job_logic(job_id_str=str(job.id), db=db_session)
    assert result["status"] == "COMPLETED_WITH_ERRORS"
    assert result["success_count"] == 2
    assert result["failure_count"] == 1

    db_session.refresh(job)
    assert job.status == JobStatus.COMPLETED_WITH_ERRORS

    # Check recipient statuses
    db_session.refresh(r_valid_1)
    db_session.refresh(r_bad)
    db_session.refresh(r_valid_2)

    assert r_valid_1.status == RecipientStatus.SUCCESS
    assert r_valid_2.status == RecipientStatus.SUCCESS
    assert r_bad.status == RecipientStatus.FAILED
    assert "invalid email format" in r_bad.error_message.lower()


def test_worker_complete_failure(db_session):
    """Verify job transitions to FAILED when all recipients fail."""
    job = JobModel(
        event_name="Cybersecurity",
        issuer_name="Security Guild",
        issue_date=date(2026, 10, 10),
        status=JobStatus.PENDING,
        total_recipients=2,
    )
    db_session.add(job)
    db_session.flush()

    r1 = RecipientModel(
        job_id=job.id, name="", email="bad1@test.com", status=RecipientStatus.PENDING
    )  # Empty name
    r2 = RecipientModel(
        job_id=job.id, name="Bad Email", email="invalid-email", status=RecipientStatus.PENDING
    )
    db_session.add_all([r1, r2])
    db_session.commit()

    result = execute_bulk_job_logic(job_id_str=str(job.id), db=db_session)
    assert result["status"] == "FAILED"
    assert result["success_count"] == 0
    assert result["failure_count"] == 2

    db_session.refresh(job)
    assert job.status == JobStatus.FAILED


def test_worker_idempotency_terminal_jobs(db_session):
    """Verify that calling worker on an already COMPLETED job does not duplicate work."""
    job = JobModel(
        event_name="Idempotency Test",
        issuer_name="Reliability Lab",
        issue_date=date(2026, 10, 10),
        status=JobStatus.PENDING,
        total_recipients=1,
    )
    db_session.add(job)
    db_session.flush()

    r = RecipientModel(
        job_id=job.id, name="User Once", email="once@test.com", status=RecipientStatus.PENDING
    )
    db_session.add(r)
    db_session.commit()

    # First run
    res1 = execute_bulk_job_logic(job_id_str=str(job.id), db=db_session)
    assert res1["status"] == "COMPLETED"

    # Second run on same job
    res2 = execute_bulk_job_logic(job_id_str=str(job.id), db=db_session)
    assert res2["status"] == "COMPLETED"

    # Ensure only 1 certificate exists
    certs = db_session.query(CertificateModel).filter_by(job_id=job.id).all()
    assert len(certs) == 1


def test_worker_crash_recovery_skips_processed_recipients(db_session):
    """Simulate a worker crash midway: completed recipients are skipped, pending are finished."""
    job = JobModel(
        event_name="Crash Recovery Test",
        issuer_name="Fault Tolerance Inc",
        issue_date=date(2026, 10, 10),
        status=JobStatus.PROCESSING,
        total_recipients=2,
        processed_count=1,
        success_count=1,
        failure_count=0,
    )
    db_session.add(job)
    db_session.flush()

    # Recipient 1 was already completed before crash
    cert_id = uuid.uuid4()
    r1 = RecipientModel(
        job_id=job.id, name="User Completed", email="done@test.com", status=RecipientStatus.SUCCESS
    )
    db_session.add(r1)
    db_session.flush()

    cert1 = CertificateModel(
        id=cert_id,
        job_id=job.id,
        recipient_id=r1.id,
        file_name="cert_done.pdf",
        file_path="mock/path.pdf",
        file_size_bytes=2048,
        sha256_checksum="mockchecksum12345",
    )
    db_session.add(cert1)

    # Recipient 2 was still PENDING when worker crashed
    r2 = RecipientModel(
        job_id=job.id, name="User Pending", email="pending@test.com", status=RecipientStatus.PENDING
    )
    db_session.add(r2)
    db_session.commit()

    # Restart worker on job
    res = execute_bulk_job_logic(job_id_str=str(job.id), db=db_session)
    assert res["status"] == "COMPLETED"

    db_session.refresh(job)
    assert job.status == JobStatus.COMPLETED
    assert job.success_count == 2
    assert len(job.certificates) == 2


def test_worker_non_existent_job(db_session):
    """Verify worker returns NOT_FOUND status when given a non-existent job_id."""
    random_id = str(uuid.uuid4())
    result = execute_bulk_job_logic(job_id_str=random_id, db=db_session)
    assert result["status"] == "NOT_FOUND"
    assert result["job_id"] == random_id


def test_worker_handles_unexpected_rendering_exception(db_session, monkeypatch):
    """Verify that an unexpected runtime crash during rendering is caught and isolated per recipient."""
    from app.services.pdf_generator import pdf_generator

    job = JobModel(
        event_name="Crash Handling Course",
        issuer_name="Fault Isolation Org",
        issue_date=date(2026, 10, 10),
        status=JobStatus.PENDING,
        total_recipients=2,
    )
    db_session.add(job)
    db_session.flush()

    r1 = RecipientModel(
        job_id=job.id, name="Lucky User", email="lucky@example.com", status=RecipientStatus.PENDING
    )
    r2 = RecipientModel(
        job_id=job.id,
        name="Unlucky User",
        email="unlucky@example.com",
        status=RecipientStatus.PENDING,
    )
    db_session.add_all([r1, r2])
    db_session.commit()

    original_render = pdf_generator.render

    def flaky_render(**kwargs):
        if kwargs.get("recipient_name") == "Unlucky User":
            raise RuntimeError("Unexpected canvas rendering error")
        return original_render(**kwargs)

    monkeypatch.setattr(pdf_generator, "render", flaky_render)

    result = execute_bulk_job_logic(job_id_str=str(job.id), db=db_session)
    assert result["status"] == "COMPLETED_WITH_ERRORS"
    assert result["success_count"] == 1
    assert result["failure_count"] == 1

    db_session.refresh(r2)
    assert r2.status == RecipientStatus.FAILED
    assert "Unexpected canvas rendering error" in r2.error_message


def test_celery_task_wrapper_execution(db_session, monkeypatch):
    """Verify that the Celery task wrapper binds, runs, and uses a database session properly."""
    from unittest.mock import MagicMock

    from app.tasks.certificate_tasks import process_bulk_certificate_job

    job = JobModel(
        event_name="Celery Task Event",
        issuer_name="Celery Org",
        issue_date=date(2026, 10, 10),
        status=JobStatus.PENDING,
        total_recipients=1,
    )
    db_session.add(job)
    db_session.flush()

    r = RecipientModel(
        job_id=job.id,
        name="Task Recipient",
        email="task@example.com",
        status=RecipientStatus.PENDING,
    )
    db_session.add(r)
    db_session.commit()

    # Provide our test database session to SessionLocal in tasks
    from app.tasks import certificate_tasks as tasks_module

    monkeypatch.setattr(tasks_module, "SessionLocal", lambda: db_session)

    # Mock the bound Celery task request object
    mock_request = MagicMock()
    mock_request.id = "mock-task-uuid-1234"

    # Call task function with mock self
    res = process_bulk_certificate_job(job_id=str(job.id))
    assert res["status"] == "COMPLETED"
    assert res["success_count"] == 1


def test_celery_task_wrapper_exception_propagation(monkeypatch):
    """Verify that unexpected worker exceptions inside process_bulk_certificate_job are logged and re-raised."""
    from unittest.mock import MagicMock

    import pytest

    from app.tasks import certificate_tasks as tasks_module

    # Mock execute_bulk_job_logic to raise RuntimeError
    monkeypatch.setattr(
        tasks_module,
        "execute_bulk_job_logic",
        MagicMock(side_effect=RuntimeError("Worker disk exhaustion failure")),
    )

    with pytest.raises(RuntimeError, match="Worker disk exhaustion failure"):
        tasks_module.process_bulk_certificate_job(job_id=str(uuid.uuid4()))
