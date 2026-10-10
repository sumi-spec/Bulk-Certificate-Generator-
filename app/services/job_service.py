"""Business logic service for Certificate Jobs and Recipients."""

import math
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.core.logging import logger
from app.core.metrics import metrics_registry
from app.models import CertificateModel, JobModel, JobStatus, RecipientModel, RecipientStatus
from app.schemas.job import JobCreate
from app.schemas.recipient import RecipientResultResponse
from app.tasks.certificate_tasks import process_bulk_certificate_job


class JobService:
    """Orchestrates job creation, queue dispatching, and status retrieval."""

    @staticmethod
    def create_bulk_job(
        db: Session, payload: JobCreate, idempotency_key: str | None = None
    ) -> JobModel:
        """
        Creates a parent Job and child Recipient records atomically,
        then dispatches the background task.
        """
        # Idempotency check
        if idempotency_key:
            existing = db.execute(
                select(JobModel).where(JobModel.idempotency_key == idempotency_key)
            ).scalar_one_or_none()
            if existing:
                logger.info(
                    "Idempotent request detected. Returning existing job_id: %s", existing.id
                )
                return existing

        total_recipients = len(payload.recipients)
        job = JobModel(
            event_name=payload.event_name.strip(),
            issuer_name=payload.issuer_name.strip(),
            issue_date=payload.issue_date,
            status=JobStatus.PENDING,
            total_recipients=total_recipients,
            processed_count=0,
            success_count=0,
            failure_count=0,
            idempotency_key=idempotency_key,
        )
        db.add(job)
        db.flush()  # Generates job.id

        # Bulk create recipients
        recipient_models = [
            RecipientModel(
                job_id=job.id,
                name=r.name.strip(),
                email=str(r.email).strip().lower(),
                custom_identifier=r.custom_identifier.strip() if r.custom_identifier else None,
                status=RecipientStatus.PENDING,
            )
            for r in payload.recipients
        ]
        db.add_all(recipient_models)
        db.commit()
        db.refresh(job)

        # Dispatch background task to Celery
        try:
            process_bulk_certificate_job.delay(str(job.id))
            metrics_registry.record_job_submitted()
            logger.info(
                "Enqueued background job_id: %s with %d recipients", job.id, total_recipients
            )
        except Exception as exc:
            # If broker is offline during local testing, log warning without failing API creation
            logger.warning("Could not dispatch job %s to Celery broker: %s", job.id, exc)

        return job

    @staticmethod
    def get_job_by_id(db: Session, job_id: uuid.UUID) -> JobModel | None:
        """Retrieves a JobModel by primary key."""
        stmt = select(JobModel).where(JobModel.id == job_id)
        return db.execute(stmt).scalar_one_or_none()

    @staticmethod
    def get_job_results_paginated(
        db: Session,
        job_id: uuid.UUID,
        page: int = 1,
        page_size: int = 50,
        status_filter: RecipientStatus | None = None,
    ) -> tuple[int, int, list[RecipientResultResponse]]:
        """
        Retrieves a paginated list of recipient results for a specific job.
        Returns: (total_records, total_pages, list_of_RecipientResultResponse)
        """
        # Base query joining certificate to fetch certificate_id if issued
        base_query = (
            select(RecipientModel)
            .options(joinedload(RecipientModel.certificate))
            .where(RecipientModel.job_id == job_id)
        )

        if status_filter:
            base_query = base_query.where(RecipientModel.status == status_filter)

        # Count query optimized with DB aggregate
        count_stmt = select(func.count(RecipientModel.id)).where(RecipientModel.job_id == job_id)
        if status_filter:
            count_stmt = count_stmt.where(RecipientModel.status == status_filter)
        total_records = db.execute(count_stmt).scalar() or 0

        total_pages = max(1, math.ceil(total_records / page_size))
        offset = (page - 1) * page_size

        stmt = base_query.order_by(RecipientModel.created_at).offset(offset).limit(page_size)
        recipients = db.execute(stmt).scalars().unique().all()

        results = [
            RecipientResultResponse(
                id=r.id,
                name=r.name,
                email=r.email,
                custom_identifier=r.custom_identifier,
                status=r.status,
                certificate_id=r.certificate.id if r.certificate else None,
                error_message=r.error_message,
                created_at=r.created_at,
                processed_at=r.processed_at,
            )
            for r in recipients
        ]

        return total_records, total_pages, results

    @staticmethod
    def get_certificate_by_id(db: Session, certificate_id: uuid.UUID) -> CertificateModel | None:
        """Retrieves certificate record with joined recipient and job models."""
        stmt = (
            select(CertificateModel)
            .options(joinedload(CertificateModel.job), joinedload(CertificateModel.recipient))
            .where(CertificateModel.id == certificate_id)
        )
        return db.execute(stmt).scalar_one_or_none()


job_service = JobService()
