#!/usr/bin/env python3
"""
Storage & Database Retention Cleanup Script.

Enforces data retention policies by pruning expired certificate records and
reclaiming storage disk space:
1. Queries jobs completed/failed older than retention cutoff (e.g. 90 days).
2. Deletes physical PDF certificates from storage.
3. Purges or soft-deletes associated recipient and certificate records.
4. Audits and cleans up orphaned files on disk.

Usage:
  # Dry-run audit (safe preview, no deletions)
  python scripts/cleanup_retention.py --days 90 --dry-run

  # Execute cleanup and reclaim disk space
  python scripts/cleanup_retention.py --days 90 --force
"""

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models import CertificateModel, JobModel, JobStatus, RecipientModel


def cleanup_expired_records(days: int = 90, dry_run: bool = True) -> dict[str, Any]:
    cutoff_date = datetime.now(UTC) - timedelta(days=days)
    print("======================================================================")
    print("Bulk Certificate Generator - Retention & Storage Cleanup")
    print(f"Retention Window: {days} days")
    print(f"Cutoff Timestamp: {cutoff_date.isoformat()}")
    print(
        f"Execution Mode:   {'DRY-RUN (Simulated, no files deleted)' if dry_run else 'LIVE PURGE (Irreversible deletion)'}"
    )
    print("======================================================================")

    db = SessionLocal()
    storage_root = settings.resolved_storage_path

    stats = {
        "expired_jobs_count": 0,
        "certificates_deleted": 0,
        "bytes_reclaimed": 0,
        "orphaned_files_cleaned": 0,
    }

    try:
        # 1. Identify expired jobs in terminal state
        stmt = select(JobModel).where(
            JobModel.status.in_(
                [
                    JobStatus.COMPLETED,
                    JobStatus.COMPLETED_WITH_ERRORS,
                    JobStatus.FAILED,
                ]
            ),
            JobModel.completed_at != None,  # noqa: E711
            JobModel.completed_at < cutoff_date,
        )
        expired_jobs = db.execute(stmt).scalars().all()
        stats["expired_jobs_count"] = len(expired_jobs)
        print(f"\n[+] Found {len(expired_jobs)} expired jobs older than {days} days.")

        for job in expired_jobs:
            print(
                f"    - Job {job.id} (Completed: {job.completed_at}, Total: {job.total_recipients})"
            )

            # Fetch certificate records for this job
            certs_stmt = select(CertificateModel).where(CertificateModel.job_id == job.id)
            certs = db.execute(certs_stmt).scalars().all()

            for cert in certs:
                file_path = storage_root / cert.file_path
                file_size = cert.file_size_bytes or 0

                if file_path.exists() and file_path.is_file():
                    stats["bytes_reclaimed"] += file_size
                    stats["certificates_deleted"] += 1
                    if not dry_run:
                        try:
                            file_path.unlink()
                        except OSError as exc:
                            print(f"      [WARN] Could not delete {file_path}: {exc}")

                if not dry_run:
                    db.delete(cert)

            if not dry_run:
                # Remove recipients and job
                recipients_stmt = select(RecipientModel).where(RecipientModel.job_id == job.id)
                recipients = db.execute(recipients_stmt).scalars().all()
                for r in recipients:
                    db.delete(r)
                db.delete(job)

        # 2. Audit orphaned files on disk
        print("\n[+] Auditing orphaned PDF files in storage...")
        all_db_file_paths = set(db.execute(select(CertificateModel.file_path)).scalars().all())

        for item in storage_root.rglob("*.pdf"):
            rel_str = item.relative_to(storage_root).as_posix()
            if rel_str not in all_db_file_paths:
                file_size = item.stat().st_size
                stats["orphaned_files_cleaned"] += 1
                stats["bytes_reclaimed"] += file_size
                print(f"    - Orphaned file: {rel_str} ({file_size} bytes)")
                if not dry_run:
                    try:
                        item.unlink()
                    except OSError as exc:
                        print(f"      [WARN] Could not delete orphaned file {item}: {exc}")

        if not dry_run:
            db.commit()
            print("\n[+] Database and disk updates successfully committed.")
        else:
            print("\n[+] Dry run completed. Database and disk left untouched.")

    except Exception as exc:
        db.rollback()
        print(f"\n[ERROR] Retention cleanup encountered an error: {exc}")
        raise
    finally:
        db.close()

    mb_reclaimed = round(stats["bytes_reclaimed"] / (1024 * 1024), 2)
    print("======================================================================")
    print("CLEANUP SUMMARY:")
    print(f"Expired Jobs:         {stats['expired_jobs_count']}")
    print(f"Certificates Purged:  {stats['certificates_deleted']}")
    print(f"Orphaned Files:       {stats['orphaned_files_cleaned']}")
    print(f"Disk Space Reclaimed: {stats['bytes_reclaimed']} bytes (~{mb_reclaimed} MB)")
    print("======================================================================")
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Purge expired certificate records and reclaim storage disk."
    )
    parser.add_argument(
        "--days",
        type=int,
        default=settings.RETENTION_DAYS,
        help="Retention threshold in days (default from config: 90 days)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Execute real deletion. If omitted, runs in simulated dry-run mode.",
    )
    args = parser.parse_args()

    cleanup_expired_records(days=args.days, dry_run=not args.force)
