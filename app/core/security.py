"""Security utilities: authentication, rate limiting, and PII masking."""

import re
import time
from collections import defaultdict

from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader, HTTPBearer

from app.core.config import settings
from app.core.logging import logger

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
bearer_scheme = HTTPBearer(auto_error=False)


def mask_email(email: str) -> str:
    """Masks email address to protect Personally Identifiable Information (PII) in logs."""
    if not email or "@" not in email:
        return "***"
    user, domain = email.split("@", 1)
    if len(user) <= 2:
        masked_user = user[0] + "*"
    else:
        masked_user = user[0] + "*" * (len(user) - 2) + user[-1]
    return f"{masked_user}@{domain}"


def sanitize_log_message(msg: str) -> str:
    """Replaces email patterns in log strings with masked equivalents."""
    email_regex = re.compile(r"[\w\.-]+@[\w\.-]+\.\w+")
    return email_regex.sub(lambda m: mask_email(m.group(0)), msg)


def verify_api_key(request: Request, api_key: str | None = Security(api_key_header)) -> bool:
    """
    Validates API Key if configured in application settings.
    If API_KEY is set in settings, requires matching X-API-Key header.
    If API_KEY is not set (e.g., open development mode), allows access.
    """
    configured_key = getattr(settings, "API_KEY", None)
    if not configured_key:
        return True  # Dev mode: open access

    if not api_key or api_key != configured_key:
        logger.warning(
            "Unauthorized access attempt to %s from IP %s",
            request.url.path,
            request.client.host if request.client else "unknown",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API Key in X-API-Key header.",
        )

    return True


class InMemoryRateLimiter:
    """Sliding-window rate limiter per client IP address."""

    def __init__(self, requests_per_minute: int = 120):
        self.rpm = requests_per_minute
        self.requests = defaultdict(list)

    def check_rate_limit(self, client_ip: str) -> bool:
        now = time.time()
        window_start = now - 60.0

        # Retain only timestamps within the current 60s sliding window
        self.requests[client_ip] = [ts for ts in self.requests[client_ip] if ts > window_start]

        if len(self.requests[client_ip]) >= self.rpm:
            return False

        self.requests[client_ip].append(now)
        return True


rate_limiter = InMemoryRateLimiter(requests_per_minute=120)


async def enforce_rate_limit(request: Request):
    """FastAPI dependency to rate limit requests by IP address."""
    client_ip = request.client.host if request.client else "127.0.0.1"
    if not rate_limiter.check_rate_limit(client_ip):
        logger.warning("Rate limit exceeded for IP %s on %s", client_ip, request.url.path)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Please retry after one minute.",
        )
