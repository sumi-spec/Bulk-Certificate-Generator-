"""Endpoints for Bulk Certificate Generation Jobs."""

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import enforce_rate_limit, verify_api_key
from app.models.enums import RecipientStatus
from app.schemas.job import (
    JobCreate,
    JobCreatedResponse,
    JobResultsPaginatedResponse,
    JobStatusResponse,
)
from app.services.job_service import job_service

router = APIRouter(prefix="/certificate-jobs", tags=["Certificate Jobs"])


@router.post(
    "",
    response_model=JobCreatedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create a bulk certificate generation job",
    description="Submits a list of recipients and event metadata. Processing runs asynchronously in the background.",
    dependencies=[Depends(enforce_rate_limit), Depends(verify_api_key)],
)
def create_certificate_job(
    payload: JobCreate,
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    """Accepts a bulk generation request and immediately returns 202 Accepted with a job_id."""
    job = job_service.create_bulk_job(db=db, payload=payload, idempotency_key=idempotency_key)
    return JobCreatedResponse(
        job_id=job.id,
        status=job.status,
        total_recipients=job.total_recipients,
        created_at=job.created_at,
    )


@router.get(
    "/{job_id}",
    response_model=JobStatusResponse,
    summary="Retrieve job status and progress counters",
    description="Poll this endpoint to monitor real-time processing status, success/failure counts, and timestamps.",
)
def get_job_status(job_id: uuid.UUID, db: Session = Depends(get_db)):
    """Retrieves current execution progress and totals for a specified job."""
    job = job_service.get_job_by_id(db=db, job_id=job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Certificate generation job '{job_id}' not found.",
        )

    return JobStatusResponse(
        job_id=job.id,
        event_name=job.event_name,
        issuer_name=job.issuer_name,
        issue_date=job.issue_date,
        status=job.status,
        total_recipients=job.total_recipients,
        processed_count=job.processed_count,
        success_count=job.success_count,
        failure_count=job.failure_count,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )


@router.get(
    "/{job_id}/results",
    response_model=JobResultsPaginatedResponse,
    summary="Retrieve itemized recipient outcomes and error messages",
    description="Returns a paginated list of recipient results, including certificate IDs for successes and error details for failures.",
)
def get_job_results(
    job_id: uuid.UUID,
    page: int = Query(1, ge=1, description="Page number starting at 1"),
    page_size: int = Query(50, ge=1, le=500, description="Items per page"),
    status_filter: RecipientStatus | None = Query(
        None, alias="status", description="Filter by recipient status"
    ),
    db: Session = Depends(get_db),
):
    """Returns paginated recipient processing outcomes."""
    job = job_service.get_job_by_id(db=db, job_id=job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Certificate generation job '{job_id}' not found.",
        )

    total_records, total_pages, results = job_service.get_job_results_paginated(
        db=db, job_id=job_id, page=page, page_size=page_size, status_filter=status_filter
    )

    return JobResultsPaginatedResponse(
        job_id=job.id,
        page=page,
        page_size=page_size,
        total_records=total_records,
        total_pages=total_pages,
        results=results,
    )
