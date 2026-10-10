"""Tests for Database Models, Relationships, and Pydantic Schemas."""

import uuid
from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from app.models import CertificateModel, JobModel, JobStatus, RecipientModel, RecipientStatus
from app.schemas.job import JobCreate
from app.schemas.recipient import RecipientCreate


def test_create_job_and_recipients_relationship(db_session):
    """Verify Job creation and cascading association with RecipientModel."""
    job_id = uuid.uuid4()
    job = JobModel(
        id=job_id,
        event_name="Full Stack AI Engineering 2026",
        issuer_name="AI Research Org",
        issue_date=date(2026, 10, 10),
        status=JobStatus.PENDING,
        total_recipients=2,
        processed_count=0,
        success_count=0,
        failure_count=0,
    )
    db_session.add(job)
    db_session.flush()

    recipient1 = RecipientModel(
        job_id=job.id,
        name="Alice Wonderland",
        email="alice@example.com",
        status=RecipientStatus.PENDING,
    )
    recipient2 = RecipientModel(
        job_id=job.id, name="Bob Builder", email="bob@example.com", status=RecipientStatus.PENDING
    )
    db_session.add_all([recipient1, recipient2])
    db_session.commit()

    # Query back and verify relationships
    queried_job = db_session.query(JobModel).filter_by(id=job_id).first()
    assert queried_job is not None
    assert queried_job.event_name == "Full Stack AI Engineering 2026"
    assert len(queried_job.recipients) == 2
    assert queried_job.recipients[0].name == "Alice Wonderland"
    assert queried_job.recipients[0].job.id == job_id


def test_certificate_model_linked_to_recipient(db_session):
    """Verify CertificateModel linkage and metadata integrity."""
    job = JobModel(
        event_name="Data Science Intensive",
        issuer_name="Tech University",
        issue_date=date(2026, 10, 1),
        status=JobStatus.PROCESSING,
        total_recipients=1,
    )
    db_session.add(job)
    db_session.flush()

    recipient = RecipientModel(
        job_id=job.id,
        name="Charlie Brown",
        email="charlie@example.com",
        status=RecipientStatus.SUCCESS,
    )
    db_session.add(recipient)
    db_session.flush()

    certificate = CertificateModel(
        job_id=job.id,
        recipient_id=recipient.id,
        file_name="cert_charlie.pdf",
        file_path="2026/10/cert_charlie.pdf",
        file_size_bytes=104520,
        sha256_checksum="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        issued_at=datetime.now(UTC),
    )
    db_session.add(certificate)
    db_session.commit()

    queried_cert = db_session.query(CertificateModel).filter_by(id=certificate.id).first()
    assert queried_cert is not None
    assert queried_cert.recipient.name == "Charlie Brown"
    assert queried_cert.job.event_name == "Data Science Intensive"
    assert queried_cert.file_size_bytes == 104520


def test_job_create_schema_validation():
    """Verify Pydantic validation on JobCreate payload."""
    valid_data = {
        "event_name": "Cloud Architecture Masterclass",
        "issuer_name": "Cloud Institute",
        "issue_date": "2026-10-10",
        "recipients": [{"name": "Valid User", "email": "valid@example.com"}],
    }
    schema = JobCreate(**valid_data)
    assert schema.event_name == "Cloud Architecture Masterclass"
    assert len(schema.recipients) == 1

    # Empty recipients list must fail validation
    with pytest.raises(ValidationError):
        JobCreate(
            event_name="Test Event",
            issuer_name="Test Org",
            issue_date=date(2026, 10, 10),
            recipients=[],
        )

    # Invalid email must fail validation
    with pytest.raises(ValidationError):
        RecipientCreate(name="Bad Email", email="not-an-email")


def test_database_get_db_session_lifecycle():
    """Verify that get_db yields an active session and closes it cleanly, rolling back on exception."""
    from app.core.database import get_db

    gen = get_db()
    session = next(gen)
    assert session is not None
    assert session.is_active

    # Clean close
    try:
        next(gen)
    except StopIteration:
        pass  # Expected generator exhaustion

    # Test error rollback branch
    gen_err = get_db()
    next(gen_err)
    with pytest.raises(RuntimeError):
        gen_err.throw(RuntimeError("Simulated query execution failure"))


def test_storage_service_methods(tmp_path):
    """Verify StorageService file_exists, get_file_path, and FileNotFoundError handling."""
    from app.services.storage import StorageService

    service = StorageService(base_dir=tmp_path)
    relative_file = "test_dir/sample.txt"
    content = b"Storage test data"

    # Before saving
    assert service.file_exists(relative_file) is False
    with pytest.raises(FileNotFoundError):
        service.get_file_path(relative_file)

    # After saving
    abs_path, size, sha = service.save_file(relative_file, content)
    assert service.file_exists(relative_file) is True
    assert service.get_file_path(relative_file).exists()

    # Path traversal returns False for file_exists
    assert service.file_exists("../../../outside.txt") is False
