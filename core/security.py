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
import hashlib
import time
import re
import threading
import asyncio
from typing import Optional, Dict, Tuple
from urllib.parse import urlparse
from collections import defaultdict
from fastapi import Request, HTTPException, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


# --- Secret Management & Auth ---

def _is_placeholder_secret(val: str) -> bool:
    v = (val or "").strip().lower()
    return (
        v.startswith("replace")
        or v.startswith("your_")
        or v.startswith("changeme")
        or v.startswith("dev-secret")
        or v.startswith("optional")
        or v == "secret"
    )


def get_configured_api_key() -> Optional[str]:
    key = os.getenv("FRESHGATE_API_KEY", "").strip()
    if not key or _is_placeholder_secret(key):
        return None
    return key


_PLACEHOLDER_ADMIN = {"your_admin_secret_token_here", "changeme", "admin", "test"}

def get_configured_admin_key() -> Optional[str]:
    key = os.getenv("FRESHGATE_ADMIN_KEY", "").strip()
    if not key or key.lower() in _PLACEHOLDER_ADMIN or _is_placeholder_secret(key):
        return None   # treated as "not configured" -> admin routes return 403
    return key


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

    if not hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: invalid API key",
        )
    return True


def require_api_key_strict(request: Request) -> bool:
    """Validate API key strictly; in production with no key set, return 503."""
    env = os.getenv("FRESHGATE_ENV", "").strip().lower()
    if env == "production" and not get_configured_api_key():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="FRESHGATE_API_KEY must be set for this endpoint",
        )
    return verify_api_key(request)


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

    if not hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
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
        self._lock = threading.Lock()
        self._call_count = 0

    def is_allowed(self, client_id: str) -> Tuple[bool, int]:
        """Check if client request is within rate limit. Returns (is_allowed, retry_after)."""
        now = time.time()
        cutoff = now - self.window_size_seconds
        with self._lock:
            self._call_count += 1
            if self._call_count % 1000 == 0:
                to_delete = [
                    cid for cid, ts_list in self._history.items()
                    if not ts_list or max(ts_list) <= cutoff
                ]
                for cid in to_delete:
                    self._history.pop(cid, None)

            timestamps = self._history[client_id]

            # Prune expired timestamps
            valid_timestamps = [t for t in timestamps if t > cutoff]
            if not valid_timestamps:
                self._history.pop(client_id, None)
            else:
                self._history[client_id] = valid_timestamps

            if len(valid_timestamps) >= self.requests_per_minute:
                oldest = valid_timestamps[0]
                retry_after = int(oldest + self.window_size_seconds - now) + 1
                return False, max(1, retry_after)

            if client_id not in self._history:
                self._history[client_id] = [now]
            else:
                self._history[client_id].append(now)
            return True, 0

    def reset(self):
        with self._lock:
            self._history.clear()
            self._call_count = 0


# Default instance
rate_limiter = SlidingWindowRateLimiter(
    requests_per_minute=int(os.getenv("FRESHGATE_RATE_LIMIT_PER_MINUTE", "60"))
)


class DistributedRateLimiter:
    """Distributed rate limiter with Redis backend and automatic in-memory fallback."""

    def __init__(self, requests_per_minute: int = 60, redis_url: Optional[str] = None):
        self._requests_per_minute = requests_per_minute
        self.window_size_seconds = 60.0
        self.local_limiter = SlidingWindowRateLimiter(requests_per_minute=requests_per_minute)
        self.redis_client = None
        url = redis_url or os.getenv("REDIS_URL")
        if url:
            try:
                import redis
                client = redis.Redis.from_url(
                    url,
                    decode_responses=True,
                    socket_timeout=0.2,
                    socket_connect_timeout=0.5,
                )
                client.ping()
                self.redis_client = client
            except Exception:
                self.redis_client = None

    @property
    def requests_per_minute(self) -> int:
        return self._requests_per_minute

    @requests_per_minute.setter
    def requests_per_minute(self, value: int):
        self._requests_per_minute = value
        self.local_limiter.requests_per_minute = value

    def is_allowed(self, client_id: str) -> Tuple[bool, int]:
        if not self.redis_client:
            return self.local_limiter.is_allowed(client_id)

        try:
            now = time.time()
            cutoff = now - self.window_size_seconds
            key = f"ratelimit:{client_id}"
            pipe = self.redis_client.pipeline()
            pipe.zremrangebyscore(key, 0, cutoff)
            pipe.zcard(key)
            pipe.zrange(key, 0, 0, withscores=True)
            _, count, oldest = pipe.execute()

            if count >= self.requests_per_minute:
                if oldest and len(oldest) > 0:
                    oldest_ts = float(oldest[0][1])
                    retry_after = max(1, int(self.window_size_seconds - (now - oldest_ts)))
                else:
                    retry_after = max(1, int(self.window_size_seconds / self.requests_per_minute))
                return False, retry_after

            pipe = self.redis_client.pipeline()
            pipe.zadd(key, {str(now): now})
            pipe.expire(key, int(self.window_size_seconds) + 5)
            pipe.execute()
            return True, 0
        except Exception:
            return self.local_limiter.is_allowed(client_id)

    def reset(self):
        self.local_limiter.reset()
        if self.redis_client:
            try:
                keys = list(self.redis_client.scan_iter(match="ratelimit:*", count=500))
                if keys:
                    self.redis_client.delete(*keys)
            except Exception:
                pass


# Production distributed rate limiter
distributed_rate_limiter = DistributedRateLimiter(
    requests_per_minute=int(os.getenv("FRESHGATE_RATE_LIMIT_PER_MINUTE", "60"))
)
rate_limiter = distributed_rate_limiter


# --- Middlewares ---

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Enforce standard defense-in-depth HTTP security headers."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        headers = response.headers

        # Strict Content Security Policy (zero inline scripts)
        headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self'; "
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


class RequestBodySizeLimitMiddleware:
    """Enforce request body size limits to prevent unbounded memory allocation and DoS."""

    def __init__(self, app, max_body_bytes: int = 1048576):
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in ("POST", "PUT", "PATCH"):
            return await self.app(scope, receive, send)

        headers = dict(scope.get("headers", []))
        content_length_header = headers.get(b"content-length")
        if content_length_header:
            try:
                if int(content_length_header) > self.max_body_bytes:
                    response = JSONResponse(
                        status_code=413,
                        content={
                            "error": {
                                "message": f"Payload Too Large: Maximum allowed size is {self.max_body_bytes} bytes.",
                                "type": "payload_too_large",
                            }
                        },
                    )
                    return await response(scope, receive, send)
            except ValueError:
                pass

        # For requests without Content-Length (or chunked), read up front into a buffer
        messages = []
        total_bytes = 0
        while True:
            message = await receive()
            if message["type"] == "http.request":
                body = message.get("body", b"")
                total_bytes += len(body)
                if total_bytes > self.max_body_bytes:
                    response = JSONResponse(
                        status_code=413,
                        content={
                            "error": {
                                "message": f"Payload Too Large: Maximum allowed size is {self.max_body_bytes} bytes.",
                                "type": "payload_too_large",
                            }
                        },
                    )
                    return await response(scope, receive, send)
                messages.append(message)
                if not message.get("more_body", False):
                    break
            elif message["type"] == "http.disconnect":
                return
            else:
                messages.append(message)
                break

        # Replay the buffered messages
        msg_idx = 0
        async def replaying_receive():
            nonlocal msg_idx
            if msg_idx < len(messages):
                msg = messages[msg_idx]
                msg_idx += 1
                return msg
            return await receive()

        return await self.app(scope, replaying_receive, send)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rate limit protected API endpoints per client IP and valid token."""

    def __init__(
        self,
        app,
        limiter: SlidingWindowRateLimiter,
        token_limiter: Optional[SlidingWindowRateLimiter] = None,
    ):
        super().__init__(app)
        self.limiter = limiter
        rpm = getattr(limiter, "requests_per_minute", 60)
        self.token_limiter = token_limiter or SlidingWindowRateLimiter(requests_per_minute=rpm * 2)

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # Only rate limit compute-heavy endpoints
        if path.startswith(("/verify", "/proxy", "/v1/chat/completions", "/api/plugin")):
            client_ip = request.client.host if request.client else "unknown"
            ip_id = f"ip:{client_ip}"

            allowed, retry_after = await asyncio.to_thread(self.limiter.is_allowed, ip_id)
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

            token = extract_token_from_request(request)
            expected = get_configured_api_key()
            if token and expected and hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8")):
                rpm = getattr(self.limiter, "requests_per_minute", 60)
                self.token_limiter.requests_per_minute = max(2, rpm * 2)
                token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
                token_id = f"token:{token_hash}"
                t_allowed, t_retry_after = await asyncio.to_thread(self.token_limiter.is_allowed, token_id)
                if not t_allowed:
                    return JSONResponse(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        content={
                            "error": {
                                "message": f"Too Many Requests: Rate limit exceeded. Retry after {t_retry_after} seconds.",
                                "type": "rate_limit_exceeded",
                                "retry_after": t_retry_after,
                            }
                        },
                        headers={"Retry-After": str(t_retry_after)},
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


# Phase 7: File Upload Validation
ALLOWED_UPLOAD_TYPES = {".pdf", ".txt", ".md", ".json", ".docx", ".csv"}
MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10MB


def validate_upload(filename: Optional[str], size: int) -> Tuple[bool, str]:
    """Validate uploaded file extension and size limits."""
    if not filename:
        return False, "Filename cannot be empty"

    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    if ext not in ALLOWED_UPLOAD_TYPES:
        return False, f"Unsupported file type '{ext}'. Allowed types: {', '.join(sorted(ALLOWED_UPLOAD_TYPES))}"

    if size > MAX_UPLOAD_SIZE:
        return False, f"File size ({size} bytes) exceeds maximum limit of {MAX_UPLOAD_SIZE // (1024*1024)}MB"

    if size <= 0:
        return False, "File is empty"

    return True, "Valid"
