"""Pydantic Schemas for Recipients."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.enums import RecipientStatus


class RecipientBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="Full name of the recipient")
    email: EmailStr = Field(..., description="Valid email address of the recipient")
    custom_identifier: str | None = Field(
        None,
        max_length=100,
        description="Optional unique identifier like student ID or employee ID",
    )


class RecipientCreate(RecipientBase):
    """Schema for individual recipient within a bulk job creation request."""

    pass


class RecipientResultResponse(BaseModel):
    """Itemized execution result for an individual recipient."""

    model_config = ConfigDict(from_attributes=True)

    recipient_id: uuid.UUID = Field(..., alias="id")
    name: str
    email: str
    custom_identifier: str | None = None
    status: RecipientStatus
    certificate_id: uuid.UUID | None = None
    error_message: str | None = None
    created_at: datetime
    processed_at: datetime | None = None
