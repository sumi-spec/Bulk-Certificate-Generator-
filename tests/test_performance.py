"""Automated performance SLA regression tests and throughput assertions."""

import time
import uuid
from datetime import date

from app.core.config import settings
from app.models import JobModel, JobStatus, RecipientModel, RecipientStatus
from app.services.job_service import job_service
from app.services.pdf_generator import pdf_generator
from app.services.storage import storage_service


def test_pdf_rendering_latency_sla():
    """Assert that vector PDF rendering latency is strictly within the < 50ms SLA."""
    cert_id = uuid.uuid4()
    t0 = time.perf_counter()
    pdf_bytes = pdf_generator.render(
        recipient_name="Performance SLA Validation User",
        event_name="Automated Latency Regression Testing",
        issue_date=date(2026, 10, 10),
        issuer_name="Quality Assurance Board",
        certificate_id=cert_id,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert len(pdf_bytes) > 1000
    assert pdf_bytes.startswith(b"%PDF")
    # In benchmarks, rendering takes ~2.7ms - 4.5ms. SLA gate is 50ms.
    assert elapsed_ms < 50.0, f"PDF rendering exceeded 50ms SLA: took {elapsed_ms:.2f}ms"


def test_paginated_query_latency_sla(db_session):
    """Assert that paginated query on indexed table executes under 100ms."""
    job_id = uuid.uuid4()
    job = JobModel(
        id=job_id,
        event_name="Paginated Query SLA",
        issuer_name="SLA Tester",
        issue_date=date(2026, 10, 10),
        status=JobStatus.COMPLETED,
        total_recipients=200,
    )
    db_session.add(job)
    db_session.commit()

    recipients = [
        RecipientModel(
            id=uuid.uuid4(),
            job_id=job_id,
            name=f"Student {i}",
            email=f"student_{i}@perf.com",
            status=RecipientStatus.SUCCESS,
        )
        for i in range(200)
    ]
    db_session.add_all(recipients)
    db_session.commit()

    t0 = time.perf_counter()
    total_records, total_pages, results = job_service.get_job_results_paginated(
        db_session, job_id, page=1, page_size=50
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert total_records == 200
    assert total_pages == 4
    assert len(results) == 50
    assert elapsed_ms < 100.0, f"Paginated query exceeded 100ms SLA: took {elapsed_ms:.2f}ms"


def test_storage_service_io_bandwidth():
    """Assert that saving and reading files operates with expected throughput."""
    n_files = 20
    test_id = f"perf_test_{uuid.uuid4().hex[:6]}"
    sample_content = b"%PDF-1.4 sample performance test payload for storage IOPS measurement"

    t0 = time.perf_counter()
    saved_paths = []
    for i in range(n_files):
        rel_path = f"{test_id}/file_{i}.pdf"
        storage_service.save_file(rel_path, sample_content)
        saved_paths.append(rel_path)
    write_time = time.perf_counter() - t0

    t1 = time.perf_counter()
    for rel_path in saved_paths:
        data = storage_service.read_file(rel_path)
        assert data == sample_content
    read_time = time.perf_counter() - t1

    # Cleanup
    import shutil

    target_dir = settings.resolved_storage_path / test_id
    if target_dir.exists():
        shutil.rmtree(target_dir, ignore_errors=True)

    assert write_time < 2.0, f"Writing 20 files took too long: {write_time:.2f}s"
    assert read_time < 2.0, f"Reading 20 files took too long: {read_time:.2f}s"
