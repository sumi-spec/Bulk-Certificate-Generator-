"""SQLAlchemy 2.0 Model for Issued Certificate Artifacts."""

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.job import JobModel
    from app.models.recipient import RecipientModel


class CertificateModel(Base):
    """Represents a generated certificate artifact and storage metadata."""

    __tablename__ = "certificates"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recipient_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("recipients.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), index=True
    )

    # Relationships
    job: Mapped["JobModel"] = relationship("JobModel", back_populates="certificates")
    recipient: Mapped["RecipientModel"] = relationship(
        "RecipientModel", back_populates="certificate"
    )

    __table_args__ = (
        CheckConstraint("file_size_bytes >= 0", name="chk_cert_file_size_non_negative"),
    )
