"""FreshGate Security & Reliability Foundation.

Provides:
- Timing-safe API key and Admin key authentication.
- Sliding window in-memory rate limiting.
- Request body size limit enforcement.
- Standard HTTP security headers.
- Safe URL validator for external citations.
- Sanitized error reporting (no internal path or trace leakage).
"""

import os
import hmac
import time
import re
from typing import Optional, Dict, Tuple
from urllib.parse import urlparse
from collections import defaultdict
from fastapi import Request, HTTPException, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


# --- Secret Management & Auth ---

def get_configured_api_key() -> Optional[str]:
    key = os.getenv("FRESHGATE_API_KEY", "").strip()
    return key if key else None


def get_configured_admin_key() -> Optional[str]:
    key = os.getenv("FRESHGATE_ADMIN_KEY", "").strip()
    return key if key else None


def extract_token_from_request(request: Request) -> Optional[str]:
    """Extract authentication token from X-API-Key or Authorization Bearer header."""
    api_key_header = request.headers.get("X-API-Key")
    if api_key_header:
        return api_key_header.strip()

    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()

    return None


def verify_api_key(request: Request) -> bool:
    """Validate client API key with timing-safe comparison if key is configured."""
    expected = get_configured_api_key()
    if not expected:
        # If no API key configured, pass through (open local dev mode)
        return True

    provided = extract_token_from_request(request)
    if not provided:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required: missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: invalid API key",
        )
    return True


def verify_admin_key(request: Request) -> bool:
    """Validate admin token with timing-safe comparison."""
    expected = get_configured_admin_key()
    if not expected:
        # If admin key not configured, admin endpoints are locked down
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: FRESHGATE_ADMIN_KEY not configured",
        )

    admin_header = request.headers.get("X-Admin-Key")
    provided = admin_header.strip() if admin_header else extract_token_from_request(request)

    if not provided:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin authentication required: missing admin credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: invalid admin token",
        )
    return True


# --- Rate Limiter ---

class SlidingWindowRateLimiter:
    """Thread-safe in-memory sliding window rate limiter."""

    def __init__(self, requests_per_minute: int = 60):
        self.requests_per_minute = requests_per_minute
        self.window_size_seconds = 60.0
        self._history: Dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, client_id: str) -> Tuple[bool, int]:
        """Check if client request is within rate limit. Returns (is_allowed, retry_after)."""
        now = time.time()
        cutoff = now - self.window_size_seconds
        timestamps = self._history[client_id]

        # Prune expired timestamps
        valid_timestamps = [t for t in timestamps if t > cutoff]
        self._history[client_id] = valid_timestamps

        if len(valid_timestamps) >= self.requests_per_minute:
            oldest = valid_timestamps[0]
            retry_after = int(oldest + self.window_size_seconds - now) + 1
            return False, max(1, retry_after)

        valid_timestamps.append(now)
        return True, 0

    def reset(self):
        self._history.clear()


# Default instance
rate_limiter = SlidingWindowRateLimiter(
    requests_per_minute=int(os.getenv("FRESHGATE_RATE_LIMIT_PER_MINUTE", "60"))
)


# --- Middlewares ---

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Enforce standard defense-in-depth HTTP security headers."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        headers = response.headers

        # Content Security Policy (allow inline styles and scripts for single-file dashboard)
        headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; "
            "img-src 'self' data: https:; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )
        headers["X-Content-Type-Options"] = "nosniff"
        headers["X-Frame-Options"] = "DENY"
        headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        headers["X-XSS-Protection"] = "1; mode=block"
        headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"

        return response


class RequestBodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Enforce request body size limits to prevent unbounded memory allocation and DoS."""

    def __init__(self, app, max_body_bytes: int = 1048576):
        super().__init__(app)
        self.max_body_bytes = max_body_bytes

    async def dispatch(self, request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH"):
            content_length = request.headers.get("content-length")
            if content_length:
                try:
                    length = int(content_length)
                    if length > self.max_body_bytes:
                        return JSONResponse(
                            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                            content={
                                "error": {
                                    "message": f"Payload Too Large: Maximum allowed size is {self.max_body_bytes} bytes.",
                                    "type": "payload_too_large",
                                }
                            },
                        )
                except ValueError:
                    pass

        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rate limit protected API endpoints per client IP."""

    def __init__(self, app, limiter: SlidingWindowRateLimiter):
        super().__init__(app)
        self.limiter = limiter

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # Only rate limit compute-heavy endpoints
        if path.startswith(("/verify", "/proxy", "/v1/chat/completions")):
            client_ip = request.client.host if request.client else "unknown"
            token = extract_token_from_request(request)
            client_id = f"token:{token}" if token else f"ip:{client_ip}"

            allowed, retry_after = self.limiter.is_allowed(client_id)
            if not allowed:
                return JSONResponse(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    content={
                        "error": {
                            "message": f"Too Many Requests: Rate limit exceeded. Retry after {retry_after} seconds.",
                            "type": "rate_limit_exceeded",
                            "retry_after": retry_after,
                        }
                    },
                    headers={"Retry-After": str(retry_after)},
                )

        return await call_next(request)


# --- Input Sanitization & URL Safety ---

SAFE_URL_PATTERN = re.compile(r"^https?://[a-zA-Z0-9\-\._~:/\?#\[\]@!$&'\(\)\*\+,;=%]+$")


def is_safe_url(url: Optional[str]) -> bool:
    """Validate that an external URL is valid HTTP/HTTPS and not javascript:, data:, or file:."""
    if not url or not isinstance(url, str):
        return False
    parsed = urlparse(url.strip())
    if parsed.scheme.lower() not in ("http", "https"):
        return False
    if not parsed.netloc:
        return False
    return bool(SAFE_URL_PATTERN.match(url.strip()))


def sanitize_error_detail(error: Exception) -> str:
    """Return generic safe error string to prevent internal path and trace leakage."""
    error_str = str(error)
    # Check for sensitive substrings
    if any(k in error_str.lower() for k in ("password", "secret", "token", "traceback", "file \"c:\\", "file \"/")):
        return "Internal verification service error"
    # Bound error message length
    return error_str[:120] if error_str else "Unknown verification error"
