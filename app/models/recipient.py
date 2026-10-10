"""SQLAlchemy 2.0 Model for Recipient Records."""

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import RecipientStatus

if TYPE_CHECKING:
    from app.models.certificate import CertificateModel
    from app.models.job import JobModel


class RecipientModel(Base):
    """Represents an individual recipient entry in a generation job."""

    __tablename__ = "recipients"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    custom_identifier: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[RecipientStatus] = mapped_column(
        SAEnum(RecipientStatus, name="recipient_status_enum", native_enum=False),
        nullable=False,
        default=RecipientStatus.PENDING,
        index=True,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    job: Mapped["JobModel"] = relationship("JobModel", back_populates="recipients")
    certificate: Mapped[Optional["CertificateModel"]] = relationship(
        "CertificateModel", back_populates="recipient", uselist=False, cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_recipients_job_status", "job_id", "status"),
        Index("ix_recipients_email_job", "job_id", "email"),
    )
