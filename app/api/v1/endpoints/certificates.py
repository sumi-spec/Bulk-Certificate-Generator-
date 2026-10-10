"""Endpoints for Certificate Retrieval and Downloads."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.certificate import CertificateMetadataResponse
from app.services.job_service import job_service
from app.services.storage import storage_service

router = APIRouter(prefix="/certificates", tags=["Certificates"])


@router.get(
    "/{certificate_id}",
    summary="Download or stream an issued certificate PDF",
    response_class=FileResponse,
    responses={
        200: {
            "content": {"application/pdf": {}},
            "description": "Returns the generated vector PDF certificate binary file.",
        },
        404: {"description": "Certificate record or storage file not found."},
    },
)
def download_certificate(certificate_id: uuid.UUID, db: Session = Depends(get_db)):
    """Streams the issued PDF certificate file."""
    certificate = job_service.get_certificate_by_id(db=db, certificate_id=certificate_id)
    if not certificate:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Certificate '{certificate_id}' not found.",
        )

    try:
        file_path = storage_service.get_file_path(certificate.file_path)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Certificate file is missing from storage: {exc}",
        ) from exc

    return FileResponse(
        path=str(file_path),
        media_type="application/pdf",
        filename=certificate.file_name,
        headers={
            "Content-Disposition": f'attachment; filename="{certificate.file_name}"',
            "X-Certificate-Checksum": certificate.sha256_checksum,
        },
    )


@router.get(
    "/{certificate_id}/metadata",
    response_model=CertificateMetadataResponse,
    summary="Retrieve certificate metadata and checksum",
    description="Retrieves verification metadata, recipient name, issue date, file size, and SHA256 integrity hash.",
)
def get_certificate_metadata(certificate_id: uuid.UUID, db: Session = Depends(get_db)):
    """Returns JSON metadata for an issued certificate."""
    cert = job_service.get_certificate_by_id(db=db, certificate_id=certificate_id)
    if not cert:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Certificate '{certificate_id}' not found.",
        )

    return CertificateMetadataResponse(
        certificate_id=cert.id,
        job_id=cert.job_id,
        recipient_id=cert.recipient_id,
        recipient_name=cert.recipient.name,
        recipient_email=cert.recipient.email,
        event_name=cert.job.event_name,
        issuer_name=cert.job.issuer_name,
        issue_date=cert.job.issue_date,
        file_name=cert.file_name,
        file_size_bytes=cert.file_size_bytes,
        sha256_checksum=cert.sha256_checksum,
        issued_at=cert.issued_at,
    )
