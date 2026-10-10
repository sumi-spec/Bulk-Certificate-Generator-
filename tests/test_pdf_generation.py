"""Automated tests for PDF Certificate Generation."""

import uuid
from datetime import date
from pathlib import Path

from app.services.pdf_generator import pdf_generator
from app.services.storage import storage_service


def test_render_certificate_valid_pdf():
    """Verify standard certificate rendering produces a valid PDF binary."""
    cert_id = uuid.uuid4()
    pdf_bytes = pdf_generator.render(
        recipient_name="Jane Doe",
        event_name="Advanced Cloud Computing & Kubernetes",
        issue_date=date(2026, 10, 10),
        issuer_name="Global Cloud Academy",
        certificate_id=cert_id,
    )

    assert isinstance(pdf_bytes, bytes)
    assert len(pdf_bytes) > 1000
    assert pdf_bytes.startswith(b"%PDF-")
    assert pdf_generator.validate_pdf_bytes(pdf_bytes) is True


def test_render_certificate_long_name_scaling():
    """Verify that dynamic font scaling handles long recipient names without errors."""
    cert_id = uuid.uuid4()
    long_name = "Alexander Bartholomew Montgomery-Cunningham III, Senior Chief Engineer"
    pdf_bytes = pdf_generator.render(
        recipient_name=long_name,
        event_name="Distributed Systems Architecture & High-Performance Computing",
        issue_date="2026-10-10",
        issuer_name="Software Engineering Institute",
        certificate_id=cert_id,
    )

    assert isinstance(pdf_bytes, bytes)
    assert pdf_generator.validate_pdf_bytes(pdf_bytes) is True


def test_render_certificate_unicode_and_accents():
    """Verify safe handling of accented characters and international names."""
    cert_id = uuid.uuid4()
    accented_name = "François Müller-Schütz"
    pdf_bytes = pdf_generator.render(
        recipient_name=accented_name,
        event_name="International Data Engineering",
        issue_date=date(2026, 10, 10),
        issuer_name="European Tech Consortium",
        certificate_id=cert_id,
    )

    assert isinstance(pdf_bytes, bytes)
    assert pdf_generator.validate_pdf_bytes(pdf_bytes) is True


def test_safe_filename_generation():
    """Verify filename sanitization produces clean, safe file paths."""
    cert_id = uuid.UUID("12345678-1234-5678-1234-567812345678")
    filename = pdf_generator.generate_safe_filename("Dr. Jane O'Connor, Ph.D.", cert_id)
    assert filename == "certificate_Dr_Jane_O_Connor_Ph_D_12345678.pdf"


def test_generate_sample_certificates_to_disk():
    """Generate and persist sample certificate PDFs for inspection."""
    samples_dir = Path("storage/certificates/samples")
    samples_dir.mkdir(parents=True, exist_ok=True)

    # Sample 1: Standard
    cert_id_1 = uuid.uuid4()
    bytes_1 = pdf_generator.render(
        recipient_name="Alice Wonderland",
        event_name="Full Stack Python Backend Architecture",
        issue_date=date(2026, 10, 10),
        issuer_name="Global Tech University",
        certificate_id=cert_id_1,
    )
    file_path_1, size_1, hash_1 = storage_service.save_file(
        f"samples/sample_alice_{str(cert_id_1)[:8]}.pdf", bytes_1
    )
    assert Path(file_path_1).exists()
    assert size_1 > 1000

    # Sample 2: Long name with special characters
    cert_id_2 = uuid.uuid4()
    bytes_2 = pdf_generator.render(
        recipient_name="Jean-Luc Picard & Benjamin Sisko",
        event_name="Intergalactic Starfleet Command and Operations Management",
        issue_date=date(2026, 10, 10),
        issuer_name="Starfleet Academy",
        certificate_id=cert_id_2,
    )
    file_path_2, size_2, hash_2 = storage_service.save_file(
        f"samples/sample_starfleet_{str(cert_id_2)[:8]}.pdf", bytes_2
    )
    assert Path(file_path_2).exists()
    assert size_2 > 1000


def test_pdf_formatting_helpers():
    """Verify text sanitization and date formatting helper functions."""
    from datetime import UTC, datetime

    # Empty text
    assert pdf_generator.sanitize_text("") == ""
    assert pdf_generator.sanitize_text(None) == ""

    # Date formatting variations
    now_dt = datetime(2026, 10, 10, 12, 0, 0, tzinfo=UTC)
    assert pdf_generator.format_date(now_dt) == "October 10, 2026"
    assert pdf_generator.format_date(date(2026, 10, 10)) == "October 10, 2026"
    assert pdf_generator.format_date("2026-10-10") == "October 10, 2026"
    assert pdf_generator.format_date("invalid-date-format") == "invalid-date-format"
    assert pdf_generator.format_date(12345) == "12345"


def test_validate_pdf_bytes_edge_cases():
    """Verify PDF validation logic rejects corrupt or non-PDF bytes."""
    # Empty or too short
    assert pdf_generator.validate_pdf_bytes(b"") is False
    assert pdf_generator.validate_pdf_bytes(b"%PDF-1.4 but too short") is False

    # Missing PDF header
    fake_bytes = b"A" * 600 + b"%%EOF"
    assert pdf_generator.validate_pdf_bytes(fake_bytes) is False

    # Missing EOF marker
    fake_header = b"%PDF-1.4 " + (b"B" * 600)
    assert pdf_generator.validate_pdf_bytes(fake_header) is False


def test_render_certificate_validation_failure_raises(monkeypatch):
    """Verify that render raises ValueError if validate_pdf_bytes reports failure."""
    import pytest

    from app.services.pdf_generator import PDFCertificateGenerator

    monkeypatch.setattr(
        PDFCertificateGenerator, "validate_pdf_bytes", classmethod(lambda cls, b: False)
    )
    with pytest.raises(ValueError, match="integrity validation"):
        pdf_generator.render(
            recipient_name="Corrupt Test",
            event_name="Event",
            issue_date="2026-10-10",
            issuer_name="Issuer",
            certificate_id=uuid.uuid4(),
        )
