"""Pydantic Schemas for Certificate Metadata."""

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class CertificateMetadataResponse(BaseModel):
    """Metadata response for an issued certificate."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    certificate_id: uuid.UUID
    job_id: uuid.UUID
    recipient_id: uuid.UUID
    recipient_name: str
    recipient_email: str
    event_name: str
    issuer_name: str
    issue_date: date
    file_name: str
    file_size_bytes: int
    sha256_checksum: str
    issued_at: datetime
