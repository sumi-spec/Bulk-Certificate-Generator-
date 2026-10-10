"""SQLAlchemy 2.0 Model for Certificate Generation Jobs."""

import uuid
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import JobStatus

if TYPE_CHECKING:
    from app.models.certificate import CertificateModel
    from app.models.recipient import RecipientModel


class JobModel(Base):
    """Represents a bulk certificate generation job batch."""

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True
    )
    event_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    issuer_name: Mapped[str] = mapped_column(String(255), nullable=False)
    issue_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        SAEnum(JobStatus, name="job_status_enum", native_enum=False),
        nullable=False,
        default=JobStatus.PENDING,
        index=True,
    )
    total_recipients: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    idempotency_key: Mapped[str | None] = mapped_column(
        String(128), nullable=True, unique=True, index=True
    )
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    recipients: Mapped[list["RecipientModel"]] = relationship(
        "RecipientModel",
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="RecipientModel.created_at",
    )
    certificates: Mapped[list["CertificateModel"]] = relationship(
        "CertificateModel", back_populates="job", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("total_recipients >= 0", name="chk_jobs_total_non_negative"),
        CheckConstraint("processed_count >= 0", name="chk_jobs_processed_non_negative"),
        CheckConstraint("success_count >= 0", name="chk_jobs_success_non_negative"),
        CheckConstraint("failure_count >= 0", name="chk_jobs_failure_non_negative"),
        CheckConstraint("processed_count <= total_recipients", name="chk_jobs_processed_le_total"),
        Index("ix_jobs_status_created_at", "status", "created_at"),
    )
