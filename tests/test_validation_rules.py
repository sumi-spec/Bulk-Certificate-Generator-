"""Unit tests for ValidationService and business rules."""

import pytest

from app.models.enums import JobStatus
from app.services.validation_service import InvalidStateTransitionError, validation_service


def test_validate_recipient_valid():
    """Verify that correctly formatted recipients pass validation."""
    is_valid, error = validation_service.validate_recipient(
        name="Dr. Jane Doe, Ph.D.", email="jane.doe@example.org", custom_identifier="EMP-90210"
    )
    assert is_valid is True
    assert error is None


def test_validate_recipient_empty_name():
    """Verify that empty or whitespace-only names fail validation."""
    is_valid, error = validation_service.validate_recipient(name="   ", email="user@example.com")
    assert is_valid is False
    assert "cannot be empty" in error.lower()


def test_validate_recipient_oversized_name():
    """Verify that names exceeding 255 characters fail validation."""
    oversized = "A" * 256
    is_valid, error = validation_service.validate_recipient(
        name=oversized, email="test@example.com"
    )
    assert is_valid is False
    assert "exceeds maximum allowed length" in error.lower()


def test_validate_recipient_invalid_name_characters():
    """Verify that names with forbidden characters fail validation."""
    is_valid, error = validation_service.validate_recipient(
        name="<script>alert('xss')</script>", email="test@example.com"
    )
    assert is_valid is False
    assert "invalid characters" in error.lower()


def test_validate_recipient_empty_email():
    """Verify that missing or whitespace-only email addresses fail validation."""
    is_valid, error = validation_service.validate_recipient(name="John Doe", email="   ")
    assert is_valid is False
    assert "required" in error.lower()


def test_validate_recipient_invalid_email():
    """Verify that malformed email strings fail validation."""
    is_valid, error = validation_service.validate_recipient(
        name="Bob Smith", email="not-an-email-at-all"
    )
    assert is_valid is False
    assert "invalid email format" in error.lower()


def test_validate_recipient_custom_identifier_rules():
    """Verify rules for custom identifiers."""
    # Valid characters
    assert validation_service.validate_recipient("User", "u@e.com", "ID_123-abc.xyz")[0] is True

    # Disallowed characters (e.g. spaces, special symbols)
    is_valid, error = validation_service.validate_recipient("User", "u@e.com", "ID with spaces!@#")
    assert is_valid is False
    assert "invalid characters" in error.lower()

    # Oversized identifier (>100 chars)
    long_id = "X" * 101
    is_valid_long, error_long = validation_service.validate_recipient("User", "u@e.com", long_id)
    assert is_valid_long is False
    assert "exceeds maximum allowed length" in error_long.lower()


def test_check_and_flag_batch_duplicates():
    """Verify that duplicates within the same batch are flagged without failing valid items."""
    batch = [
        {"name": "Alice Primary", "email": "alice@test.com"},
        {"name": "Alice Duplicate", "email": "ALICE@test.com"},  # Case-insensitive duplicate
        {"name": "Bob Normal", "email": "bob@test.com"},
        {"name": "", "email": "badname@test.com"},  # Invalid name
    ]

    results = validation_service.check_and_flag_batch_duplicates(batch)
    assert len(results) == 4

    # First Alice is valid
    assert results[0][1] is True
    assert results[0][2] is None

    # Duplicate Alice is flagged as FAILED with specific reason
    assert results[1][1] is False
    assert "duplicate" in results[1][2].lower()

    # Bob is valid
    assert results[2][1] is True
    assert results[2][2] is None

    # Empty name is flagged as FAILED with specific reason
    assert results[3][1] is False
    assert "cannot be empty" in results[3][2].lower()


def test_job_state_machine_valid_transitions():
    """Verify that legal job state transitions succeed without error."""
    validation_service.validate_job_state_transition(JobStatus.PENDING, JobStatus.PROCESSING)
    validation_service.validate_job_state_transition(JobStatus.PROCESSING, JobStatus.COMPLETED)
    validation_service.validate_job_state_transition(
        JobStatus.PROCESSING, JobStatus.COMPLETED_WITH_ERRORS
    )
    validation_service.validate_job_state_transition(JobStatus.PROCESSING, JobStatus.FAILED)


def test_job_state_machine_invalid_transitions():
    """Verify that illegal job state jumps raise InvalidStateTransitionError."""
    # Completed jobs cannot transition back to processing
    with pytest.raises(InvalidStateTransitionError):
        validation_service.validate_job_state_transition(JobStatus.COMPLETED, JobStatus.PROCESSING)

    # Failed jobs cannot transition to completed
    with pytest.raises(InvalidStateTransitionError):
        validation_service.validate_job_state_transition(JobStatus.FAILED, JobStatus.COMPLETED)

    # Pending cannot jump straight to completed without processing
    with pytest.raises(InvalidStateTransitionError):
        validation_service.validate_job_state_transition(JobStatus.PENDING, JobStatus.COMPLETED)
