"""Pydantic Schemas for Bulk Certificate Generation Jobs."""

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import JobStatus
from app.schemas.recipient import RecipientCreate, RecipientResultResponse


class JobCreate(BaseModel):
    """Schema for submitting a bulk certificate generation request."""

    event_name: str = Field(..., min_length=1, max_length=255, description="Course or event name")
    issuer_name: str = Field(
        ..., min_length=1, max_length=255, description="Name of issuing organization"
    )
    issue_date: date = Field(..., description="Date printed on certificate (YYYY-MM-DD)")
    recipients: list[RecipientCreate] = Field(
        ...,
        min_length=1,
        max_length=5000,
        description="List of recipients to generate certificates for (up to 5000)",
    )


class JobCreatedResponse(BaseModel):
    """Immediate response after submitting a bulk generation job."""

    model_config = ConfigDict(from_attributes=True)

    job_id: uuid.UUID
    status: JobStatus
    total_recipients: int
    created_at: datetime


class JobStatusResponse(BaseModel):
    """Comprehensive status and progress metrics for a generation job."""

    model_config = ConfigDict(from_attributes=True)

    job_id: uuid.UUID
    event_name: str
    issuer_name: str
    issue_date: date
    status: JobStatus
    total_recipients: int
    processed_count: int
    success_count: int
    failure_count: int
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class JobResultsPaginatedResponse(BaseModel):
    """Paginated list of itemized recipient results and error reasons."""

    job_id: uuid.UUID
    page: int
    page_size: int
    total_records: int
    total_pages: int
    results: list[RecipientResultResponse]
