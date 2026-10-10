#!/usr/bin/env python3
"""
Post-Deployment Workflow Verification & Smoke Test Script.

Validates that a live or staging deployment of the Bulk Certificate Generator
is fully functional end-to-end:
1. Probes /health for DB and Redis connectivity.
2. Validates API key authentication if enabled.
3. Submits an end-to-end bulk certificate generation job.
4. Polls job progress until terminal completion.
5. Verifies individual certificate generation results.
6. Downloads a generated PDF certificate and validates magic bytes (%PDF).

Usage:
  python scripts/verify_deployment.py --base-url http://localhost:8000
  python scripts/verify_deployment.py --base-url https://certs.example.com --api-key prod_secret_key
"""

import argparse
import sys
import time
from typing import Any

try:
    import httpx
except ImportError:
    print("[ERROR] httpx is required to run verify_deployment.py. Install via 'pip install httpx'.")
    sys.exit(1)


def log_step(msg: str) -> None:
    print(f"\n[+] {msg}")


def log_success(msg: str) -> None:
    print(f"    [PASS] {msg}")


def log_error(msg: str) -> None:
    print(f"    [FAIL] {msg}")


def verify_deployment(base_url: str, api_key: str | None = None, timeout: float = 60.0) -> bool:
    client = httpx.Client(base_url=base_url.rstrip("/"), timeout=15.0)
    headers = {"X-API-Key": api_key} if api_key else {}

    print("======================================================================")
    print("Bulk Certificate Generator - Deployed Workflow Verification")
    print(f"Target URL: {base_url}")
    print(f"Auth Mode:  {'API Key Protected' if api_key else 'Public / Unprotected'}")
    print("======================================================================")

    # -------------------------------------------------------------------------
    # Step 1: Health & Dependency Check
    # -------------------------------------------------------------------------
    log_step("Step 1: Checking system health and database/cache dependencies...")
    try:
        health_resp = client.get("/health")
        if health_resp.status_code != 200:
            log_error(f"/health returned HTTP {health_resp.status_code}: {health_resp.text}")
            return False

        health_data = health_resp.json()
        status = health_data.get("status")
        db_status = health_data.get("database", {}).get("status")
        redis_status = health_data.get("redis", {}).get("status")

        if status != "healthy":
            log_error(f"System status is unhealthy: {health_data}")
            return False

        log_success(f"System status: {status} (DB: {db_status}, Redis: {redis_status})")
    except Exception as exc:
        log_error(f"Could not connect to health endpoint: {exc}")
        return False

    # -------------------------------------------------------------------------
    # Step 2: Authentication Check
    # -------------------------------------------------------------------------
    if api_key:
        log_step("Step 2: Testing API Key Authentication...")
        try:
            # Unauthorized attempt
            unauth_resp = client.get("/health/authenticated")
            if unauth_resp.status_code != 401:
                log_error(f"Expected 401 for missing API key, got {unauth_resp.status_code}")
                return False

            # Authorized attempt
            auth_resp = client.get("/health/authenticated", headers=headers)
            if auth_resp.status_code != 200:
                log_error(f"Expected 200 for valid API key, got {auth_resp.status_code}")
                return False
            log_success("API Key authorization successfully validated.")
        except Exception as exc:
            log_error(f"Authentication probe failed: {exc}")
            return False
    else:
        log_step("Step 2: API Key not supplied, skipping authentication probe.")

    # -------------------------------------------------------------------------
    # Step 3: Create Bulk Certificate Job
    # -------------------------------------------------------------------------
    log_step("Step 3: Submitting end-to-end bulk certificate generation job...")
    job_payload = {
        "course_name": "Production Smoke Test Certification",
        "issuing_organization": "DevOps Automated Verification",
        "issue_date": "2026-10-10",
        "recipients": [
            {
                "recipient_name": "Alice SmokeTest",
                "recipient_email": "alice.smoketest@example.com",
            },
            {
                "recipient_name": "Bob Verification",
                "recipient_email": "bob.verification@example.com",
            },
        ],
    }

    try:
        create_resp = client.post("/api/v1/certificate-jobs", json=job_payload, headers=headers)
        if create_resp.status_code != 202:
            log_error(
                f"Job creation failed with HTTP {create_resp.status_code}: {create_resp.text}"
            )
            return False

        job_info = create_resp.json()
        job_id = job_info.get("id")
        if not job_id:
            log_error(f"Job creation response did not contain 'id': {job_info}")
            return False

        log_success(f"Job accepted with ID: {job_id} (Status: {job_info.get('status')})")
    except Exception as exc:
        log_error(f"Failed to submit bulk job: {exc}")
        return False

    # -------------------------------------------------------------------------
    # Step 4: Poll Job Until Completion
    # -------------------------------------------------------------------------
    log_step(f"Step 4: Polling job {job_id} for background completion...")
    start_time = time.time()
    final_job_data: dict[str, Any] = {}

    while time.time() - start_time < timeout:
        try:
            status_resp = client.get(f"/api/v1/certificate-jobs/{job_id}", headers=headers)
            if status_resp.status_code != 200:
                log_error(f"Error fetching job status (HTTP {status_resp.status_code})")
                return False

            final_job_data = status_resp.json()
            current_status = final_job_data.get("status")
            processed = final_job_data.get("processed_count", 0)
            total = final_job_data.get("total_recipients", 0)

            print(f"    ... Current Status: {current_status} ({processed}/{total} processed)")

            if current_status in ("COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED"):
                break
        except Exception as exc:
            log_error(f"Error polling job status: {exc}")
            return False

        time.sleep(1.5)

    if final_job_data.get("status") != "COMPLETED":
        log_error(f"Job did not complete successfully. Final status: {final_job_data}")
        return False

    log_success(
        f"Job completed in {time.time() - start_time:.2f}s: "
        f"{final_job_data.get('success_count')} succeeded, "
        f"{final_job_data.get('failure_count')} failed."
    )

    # -------------------------------------------------------------------------
    # Step 5: Retrieve Job Results
    # -------------------------------------------------------------------------
    log_step("Step 5: Querying individual certificate results...")
    try:
        results_resp = client.get(f"/api/v1/certificate-jobs/{job_id}/results", headers=headers)
        if results_resp.status_code != 200:
            log_error(f"Failed to retrieve job results (HTTP {results_resp.status_code})")
            return False

        results_data = results_resp.json()
        recipients = results_data.get("recipients", [])
        if len(recipients) != 2:
            log_error(f"Expected 2 recipient records, received {len(recipients)}")
            return False

        cert_id = None
        for r in recipients:
            if r.get("status") == "COMPLETED" and r.get("certificate_id"):
                cert_id = r["certificate_id"]
                break

        if not cert_id:
            log_error(f"No valid certificate_id found in results: {results_data}")
            return False

        log_success(f"Retrieved {len(recipients)} recipient results. Selected cert ID: {cert_id}")
    except Exception as exc:
        log_error(f"Error retrieving job results: {exc}")
        return False

    # -------------------------------------------------------------------------
    # Step 6: Download & Validate Certificate PDF
    # -------------------------------------------------------------------------
    log_step(f"Step 6: Downloading certificate {cert_id} and checking PDF integrity...")
    try:
        download_resp = client.get(f"/api/v1/certificates/{cert_id}", headers=headers)
        if download_resp.status_code != 200:
            log_error(f"Failed to download certificate (HTTP {download_resp.status_code})")
            return False

        content_type = download_resp.headers.get("Content-Type", "")
        if "application/pdf" not in content_type:
            log_error(f"Expected application/pdf Content-Type, got '{content_type}'")
            return False

        pdf_bytes = download_resp.content
        if not pdf_bytes.startswith(b"%PDF"):
            log_error("Downloaded payload does not begin with %PDF magic header")
            return False

        if len(pdf_bytes) < 1000:
            log_error(f"Downloaded PDF payload is unexpectedly small ({len(pdf_bytes)} bytes)")
            return False

        log_success(
            f"Successfully downloaded and validated PDF certificate ({len(pdf_bytes)} bytes, magic header valid)."
        )
    except Exception as exc:
        log_error(f"Error validating PDF download: {exc}")
        return False

    print("\n======================================================================")
    print("ALL DEPLOYMENT VERIFICATION CHECKS PASSED SUCCESSFULLY!")
    print("======================================================================")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Verify deployed Bulk Certificate Generator API")
    parser.add_argument(
        "--base-url", default="http://localhost:8000", help="Base URL of deployed API"
    )
    parser.add_argument("--api-key", default=None, help="API Key for authenticated endpoints")
    parser.add_argument(
        "--timeout", type=float, default=45.0, help="Max polling timeout in seconds"
    )
    args = parser.parse_args()

    success = verify_deployment(base_url=args.base_url, api_key=args.api_key, timeout=args.timeout)
    sys.exit(0) if success else sys.exit(1)
