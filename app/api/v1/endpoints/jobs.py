"""Endpoints for Bulk Certificate Generation Jobs."""

import io
import re
import uuid
import zipfile

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import enforce_rate_limit, verify_api_key
from app.models import CertificateModel
from app.models.enums import RecipientStatus
from app.schemas.job import (
    JobCreate,
    JobCreatedResponse,
    JobResultsPaginatedResponse,
    JobStatusResponse,
)
from app.services.job_service import job_service
from app.services.storage import storage_service

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


@router.get(
    "/{job_id}/download-zip",
    summary="Download all generated certificates as a single ZIP archive",
    description="Packages all issued PDF certificates for the job into an unencrypted ZIP archive for 1-click batch download.",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {"application/zip": {}},
            "description": "Returns a ZIP archive containing all generated certificate PDFs.",
        },
        404: {"description": "Job not found."},
        400: {"description": "No certificates available for download."},
    },
)
def download_all_certificates_zip(job_id: uuid.UUID, db: Session = Depends(get_db)):
    """Packages all generated certificates for a job into a downloadable ZIP archive."""
    job = job_service.get_job_by_id(db=db, job_id=job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Certificate generation job '{job_id}' not found.",
        )

    certificates = (
        db.execute(select(CertificateModel).where(CertificateModel.job_id == job_id))
        .scalars()
        .all()
    )

    if not certificates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No certificates have been successfully generated for this job yet.",
        )

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        for cert in certificates:
            try:
                full_path = storage_service.get_file_path(cert.file_path)
                if full_path.exists():
                    zip_file.write(full_path, arcname=cert.file_name)
            except Exception:
                continue

    zip_buffer.seek(0)
    event_slug = re.sub(r"[^a-zA-Z0-9_-]", "_", job.event_name)[:25]
    zip_filename = f"certificates_{event_slug}_{str(job.id)[:8]}.zip"

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_filename}"'},
    )
