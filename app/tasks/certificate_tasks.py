import time
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import logger, set_job_id
from app.core.metrics import metrics_registry
from app.models import CertificateModel, JobModel, JobStatus, RecipientModel, RecipientStatus
from app.services.pdf_generator import pdf_generator
from app.services.storage import storage_service
from app.services.validation_service import validation_service


def execute_bulk_job_logic(job_id_str: str, db: Session) -> dict[str, Any]:
    """
    Core resilient bulk job execution engine.
    - Idempotent: safe to rerun without duplicating work.
    - Resilient: failure on one recipient never prevents others from completing.
    - Durable: updates progress and status directly in the database per chunk.
    """
    set_job_id(job_id_str)
    job_id = uuid.UUID(job_id_str)
    job = db.execute(select(JobModel).where(JobModel.id == job_id)).scalar_one_or_none()
    if not job:
        logger.error("Background task invoked for non-existent job_id: %s", job_id_str)
        return {"status": "NOT_FOUND", "job_id": job_id_str}

    # 1. Idempotency Check: Terminal jobs should not be re-processed
    if job.status in (JobStatus.COMPLETED, JobStatus.COMPLETED_WITH_ERRORS, JobStatus.FAILED):
        logger.info("Job %s is already in terminal state '%s'. Skipping.", job_id, job.status.value)
        return {
            "status": job.status.value,
            "job_id": job_id_str,
            "processed": job.processed_count,
            "success": job.success_count,
            "failure": job.failure_count,
        }

    # 2. Transition state to PROCESSING if PENDING, or resume if already PROCESSING
    if job.status == JobStatus.PENDING:
        validation_service.validate_job_state_transition(job.status, JobStatus.PROCESSING)
        job.status = JobStatus.PROCESSING
        job.started_at = datetime.now(UTC)
        db.commit()
        logger.info(
            "Job %s transitioned to PROCESSING. Total recipients: %d", job.id, job.total_recipients
        )
    else:
        logger.info(
            "Resuming in-progress job %s after worker restart. Total recipients: %d",
            job.id,
            job.total_recipients,
        )

    # 3. Stream and process recipients in persistent chunks
    chunk_size = settings.CHUNK_SIZE
    while True:
        # Query only recipients that are still PENDING
        stmt = (
            select(RecipientModel)
            .where(
                RecipientModel.job_id == job_id, RecipientModel.status == RecipientStatus.PENDING
            )
            .limit(chunk_size)
        )
        chunk = db.execute(stmt).scalars().all()
        if not chunk:
            break

        for r in chunk:
            try:
                # Step A: Validate recipient data fields
                is_valid, val_error = validation_service.validate_recipient(
                    name=r.name, email=r.email, custom_identifier=r.custom_identifier
                )
                if not is_valid:
                    r.status = RecipientStatus.FAILED
                    r.error_message = val_error
                    r.processed_at = datetime.now(UTC)
                    job.failure_count += 1
                    job.processed_count += 1
                    continue

                # Step B: Render Certificate PDF
                render_start = time.perf_counter()
                cert_id = uuid.uuid4()
                pdf_bytes = pdf_generator.render(
                    recipient_name=r.name,
                    event_name=job.event_name,
                    issue_date=job.issue_date,
                    issuer_name=job.issuer_name,
                    certificate_id=cert_id,
                )
                render_time = time.perf_counter() - render_start
                metrics_registry.record_certificate_rendered(render_time, success=True)

                # Step C: Save binary to storage
                file_name = pdf_generator.generate_safe_filename(r.name, cert_id)
                rel_path = f"{job.id}/{file_name}"
                full_path, size_bytes, sha256_hash = storage_service.save_file(rel_path, pdf_bytes)

                # Step D: Record Certificate metadata in DB
                cert = CertificateModel(
                    id=cert_id,
                    job_id=job.id,
                    recipient_id=r.id,
                    file_name=file_name,
                    file_path=rel_path,
                    file_size_bytes=size_bytes,
                    sha256_checksum=sha256_hash,
                    issued_at=datetime.now(UTC),
                )
                db.add(cert)

                # Step E: Mark recipient as SUCCESS
                r.status = RecipientStatus.SUCCESS
                r.error_message = None
                r.processed_at = datetime.now(UTC)
                job.success_count += 1
                job.processed_count += 1

            except Exception as exc:
                # Isolate unexpected failure for this recipient
                metrics_registry.record_certificate_rendered(0.0, success=False)
                logger.error(
                    "Exception generating certificate for recipient %s: %s",
                    r.id,
                    exc,
                    exc_info=True,
                )
                r.status = RecipientStatus.FAILED
                r.error_message = f"Generation error: {str(exc)}"
                r.processed_at = datetime.now(UTC)
                job.failure_count += 1
                job.processed_count += 1

        # Commit progress after each chunk
        db.commit()

    # 4. Compute and finalize Job State
    if job.failure_count == 0:
        final_status = JobStatus.COMPLETED
    elif job.success_count > 0:
        final_status = JobStatus.COMPLETED_WITH_ERRORS
    else:
        final_status = JobStatus.FAILED

    validation_service.validate_job_state_transition(job.status, final_status)
    job.status = final_status
    job.completed_at = datetime.now(UTC)
    db.commit()
    db.refresh(job)

    # Record Job Level Telemetry
    duration = (
        (job.completed_at - job.started_at).total_seconds()
        if (job.started_at and job.completed_at)
        else 0.0
    )
    if final_status == JobStatus.FAILED:
        metrics_registry.record_job_failed(duration_seconds=duration)
    else:
        metrics_registry.record_job_completed(
            duration_seconds=duration,
            success_count=job.success_count,
            failure_count=job.failure_count,
        )

    logger.info(
        "Job %s completed with status '%s'. Success: %d, Failures: %d, Total: %d",
        job.id,
        final_status.value,
        job.success_count,
        job.failure_count,
        job.total_recipients,
    )

    set_job_id(None)
    return {
        "job_id": str(job.id),
        "status": final_status.value,
        "total_recipients": job.total_recipients,
        "processed_count": job.processed_count,
        "success_count": job.success_count,
        "failure_count": job.failure_count,
    }


@celery_app.task(
    bind=True,
    name="process_bulk_certificate_job",
    ignore_result=True,
    max_retries=3,
    default_retry_delay=5,
    autoretry_for=(OperationalError,),
)
def process_bulk_certificate_job(self, job_id: str):
    """
    Celery background worker entrypoint.
    Executes resilient bulk generation logic with automatic retry for transient DB errors.
    """
    logger.info("Celery task %s started for job_id=%s", self.request.id, job_id)
    db = SessionLocal()
    try:
        return execute_bulk_job_logic(job_id_str=job_id, db=db)
    except Exception as exc:
        logger.error("Celery task %s failed for job %s: %s", self.request.id, job_id, exc)
        raise
    finally:
        db.close()
