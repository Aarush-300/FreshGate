"""FreshGate Centralized LLM Provider Manager & Rate-Limit Engine (Phase 1 & 2).

Manages all LLM interactions across FreshGate:
- Connection pooling and HTTP client lifecycle management via httpx.
- Deep error classification distinguishing transient rate limits from permanent quota/billing/auth failures.
- Header-aware retry engine respecting Retry-After, x-ratelimit-reset-requests, and x-ratelimit-reset-tokens.
- Exponential backoff with randomized jitter and per-request deadlines.
- Global and operational-level concurrency semaphores (extraction vs. judging).
- Queue timeouts, backpressure, and cancellation safety.
- Token budget and usage tracking (RPM, TPM, usage history).
"""

import os
import time
import json
import random
import asyncio
import logging
from enum import Enum
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass, field
from contextlib import asynccontextmanager
import httpx

from core.config import is_valid_credential, get_config

logger = logging.getLogger("freshgate.llm_manager")


class LLMErrorType(str, Enum):
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"        # Transient: TPM/RPM limit hit
    INSUFFICIENT_QUOTA = "insufficient_quota"          # Permanent: Billing / quota exhausted
    BILLING_LIMIT = "billing_limit"                    # Permanent: Usage limit hit
    INVALID_API_KEY = "invalid_api_key"                # Permanent: 401 Unauthorized
    MODEL_NOT_FOUND = "model_not_found"                # Permanent: 404 Model doesn't exist
    CONTEXT_LENGTH_EXCEEDED = "context_length_exceeded"# Permanent: Prompt too long
    SERVER_ERROR = "server_error"                      # Transient: 500, 502, 503, 504
    REQUEST_TIMEOUT = "request_timeout"                # Transient: Timeout
    CONNECTION_ERROR = "connection_error"              # Transient: Network connectivity
    MALFORMED_REQUEST = "malformed_request"            # Permanent: 400 Bad Request
    UNKNOWN = "unknown"


@dataclass
class LLMErrorDetail:
    error_type: LLMErrorType
    message: str
    status_code: Optional[int] = None
    retry_after_seconds: Optional[float] = None
    is_transient: bool = False
    raw_response: Optional[str] = None


class LLMProviderException(Exception):
    """Exception raised by LLMProviderManager with classified error information."""

    def __init__(self, detail: LLMErrorDetail):
        super().__init__(detail.message)
        self.detail = detail


def parse_reset_duration(val: Optional[str]) -> Optional[float]:
    """Parse OpenAI reset durations such as '20ms', '1.5s', '2m30s' or standard seconds into float seconds."""
    if not val:
        return None
    val = val.strip().lower()
    try:
        # Standard numeric float seconds
        return float(val)
    except ValueError:
        pass

    total_seconds = 0.0
    idx = 0
    num_buf = ""
    while idx < len(val):
        ch = val[idx]
        if ch.isdigit() or ch == ".":
            num_buf += ch
            idx += 1
        elif ch == "m" and idx + 1 < len(val) and val[idx + 1] == "s":
            if num_buf:
                total_seconds += float(num_buf) / 1000.0
                num_buf = ""
            idx += 2
        elif ch == "s":
            if num_buf:
                total_seconds += float(num_buf)
                num_buf = ""
            idx += 1
        elif ch == "m":
            if num_buf:
                total_seconds += float(num_buf) * 60.0
                num_buf = ""
            idx += 1
        elif ch == "h":
            if num_buf:
                total_seconds += float(num_buf) * 3600.0
                num_buf = ""
            idx += 1
        else:
            idx += 1
    return total_seconds if total_seconds > 0 else None


def classify_http_error(status_code: int, response_body: str, headers: Dict[str, str]) -> LLMErrorDetail:
    """Classify an HTTP response from an OpenAI-compatible provider with high precision."""
    error_type = LLMErrorType.UNKNOWN
    message = f"HTTP {status_code}"
    is_transient = False
    retry_after: Optional[float] = None

    # Parse Retry-After header
    retry_after_hdr = headers.get("retry-after") or headers.get("Retry-After")
    if retry_after_hdr:
        try:
            retry_after = float(retry_after_hdr)
        except ValueError:
            retry_after = 2.0

    # Parse OpenAI specific reset headers if Retry-After is absent
    if retry_after is None:
        reset_tokens = headers.get("x-ratelimit-reset-tokens")
        reset_requests = headers.get("x-ratelimit-reset-requests")
        parsed_tokens = parse_reset_duration(reset_tokens)
        parsed_requests = parse_reset_duration(reset_requests)
        candidates = [c for c in (parsed_tokens, parsed_requests) if c is not None]
        if candidates:
            retry_after = max(candidates)

    # Attempt parsing JSON error body
    body_json = {}
    try:
        body_json = json.loads(response_body)
    except Exception:
        pass

    err_obj = body_json.get("error", {}) if isinstance(body_json, dict) else {}
    if isinstance(err_obj, dict):
        err_code = str(err_obj.get("code") or "").strip().lower()
        err_type_str = str(err_obj.get("type") or "").strip().lower()
        err_msg = str(err_obj.get("message") or "")
        if err_msg:
            message = err_msg
    else:
        err_code, err_type_str, err_msg = "", "", str(err_obj)

    body_lower = response_body.lower()

    if status_code == 401:
        error_type = LLMErrorType.INVALID_API_KEY
        is_transient = False
    elif status_code == 404:
        error_type = LLMErrorType.MODEL_NOT_FOUND
        is_transient = False
    elif status_code == 400:
        if "context_length_exceeded" in err_code or "maximum context length" in body_lower:
            error_type = LLMErrorType.CONTEXT_LENGTH_EXCEEDED
            is_transient = False
        else:
            error_type = LLMErrorType.MALFORMED_REQUEST
            is_transient = False
    elif status_code == 429:
        # Critical distinction: insufficient_quota vs temporary rate limit
        if (
            err_code == "insufficient_quota"
            or err_type_str == "insufficient_quota"
            or "quota" in body_lower
            or "billing" in body_lower
            or "exceeded your current quota" in body_lower
            or "credit balance is too low" in body_lower
        ):
            error_type = LLMErrorType.INSUFFICIENT_QUOTA
            is_transient = False  # NEVER retry permanent quota exhaustion!
        elif "billing" in err_code:
            error_type = LLMErrorType.BILLING_LIMIT
            is_transient = False
        else:
            error_type = LLMErrorType.RATE_LIMIT_EXCEEDED
            is_transient = True  # Transient TPM / RPM limit
    elif status_code in (500, 502, 503, 504):
        error_type = LLMErrorType.SERVER_ERROR
        is_transient = True
    elif status_code == 408:
        error_type = LLMErrorType.REQUEST_TIMEOUT
        is_transient = True
    else:
        error_type = LLMErrorType.UNKNOWN
        is_transient = False

    return LLMErrorDetail(
        error_type=error_type,
        message=message[:250],
        status_code=status_code,
        retry_after_seconds=retry_after,
        is_transient=is_transient,
        raw_response=response_body[:500] if response_body else None,
    )


@dataclass
class ProviderUsageStats:
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    rate_limit_count: int = 0
    quota_exhausted_count: int = 0
    retries_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    remaining_requests_budget: Optional[int] = None
    remaining_tokens_budget: Optional[int] = None
    last_rate_limit_timestamp: Optional[float] = None
    last_error_message: Optional[str] = None
    status: str = "healthy"  # healthy, rate_limited, quota_exhausted, degraded, unavailable


class LLMProviderManager:
    """Production LLM Gateway Manager for OpenAI-compatible providers.

    Features:
    - Shared persistent connection pool.
    - Tiered concurrency control (global, extraction, judging).
    - Intelligent retry with jitter, bounded duration, and deadline enforcement.
    - Strict classification ensuring permanent quota errors are never retried.
    - Token and budget tracking.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        max_global_concurrency: int = 10,
        max_extraction_concurrency: int = 5,
        max_judging_concurrency: int = 5,
        max_pending_queue: int = 50,
        queue_timeout_seconds: float = 8.0,
        max_retries: int = 3,
        max_retry_duration_seconds: float = 12.0,
        default_request_timeout_seconds: float = 15.0,
    ):
        cfg = get_config()
        self.api_key = api_key if api_key is not None else (cfg.openai_api_key or os.getenv("OPENAI_API_KEY", ""))
        self.base_url = (base_url or cfg.openai_base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        self.model = (model or cfg.llm_model or os.getenv("LLM_MODEL", "gpt-4o-mini")).strip()

        self.max_retries = max_retries
        self.max_retry_duration = max_retry_duration_seconds
        self.default_timeout = default_request_timeout_seconds
        self.queue_timeout = queue_timeout_seconds

        # Concurrency management
        self.global_semaphore = asyncio.Semaphore(max_global_concurrency)
        self.extraction_semaphore = asyncio.Semaphore(max_extraction_concurrency)
        self.judging_semaphore = asyncio.Semaphore(max_judging_concurrency)
        self.queue_semaphore = asyncio.Semaphore(max_pending_queue)

        self._active_requests = 0
        self._queued_requests = 0
        self._lock = asyncio.Lock()

        # Operational metrics & budget tracking
        self.stats = ProviderUsageStats()
        if is_valid_credential(self.api_key):
            self.stats.status = "configured"
        else:
            self.stats.status = "missing_credentials"

        # Reusable HTTP connection pool
        limits = httpx.Limits(
            max_keepalive_connections=20,
            max_connections=50,
            keepalive_expiry=30.0,
        )
        self._client = httpx.AsyncClient(
            limits=limits,
            timeout=httpx.Timeout(self.default_timeout, connect=5.0),
        )

    async def aclose(self):
        """Gracefully close HTTP client connection pool on shutdown."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    def get_stats_summary(self) -> Dict[str, Any]:
        """Return provider health and usage metrics for the dashboard and observability."""
        return {
            "status": self.stats.status,
            "model": self.model,
            "total_requests": self.stats.total_requests,
            "successful_requests": self.stats.successful_requests,
            "failed_requests": self.stats.failed_requests,
            "rate_limit_count": self.stats.rate_limit_count,
            "quota_exhausted_count": self.stats.quota_exhausted_count,
            "retries_count": self.stats.retries_count,
            "prompt_tokens": self.stats.prompt_tokens,
            "completion_tokens": self.stats.completion_tokens,
            "total_tokens": self.stats.total_tokens,
            "remaining_requests_budget": self.stats.remaining_requests_budget,
            "remaining_tokens_budget": self.stats.remaining_tokens_budget,
            "last_error": self.stats.last_error_message,
            "active_requests": self._active_requests,
            "queued_requests": self._queued_requests,
        }

    def _select_semaphore(self, request_type: str) -> Optional[asyncio.Semaphore]:
        if request_type == "extraction":
            return self.extraction_semaphore
        elif request_type == "judging":
            return self.judging_semaphore
        return None

    @asynccontextmanager
    async def _acquire_concurrency(self, sub_semaphore: Optional[asyncio.Semaphore]):
        """Acquire global and optional sub-service semaphores safely without double-locking."""
        async with self.global_semaphore:
            if sub_semaphore is not None:
                async with sub_semaphore:
                    yield
            else:
                yield

    async def complete(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        request_type: str = "general",
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
        deadline_seconds: Optional[float] = None,
        api_key: Optional[str] = None,
    ) -> str:
        """Execute completion through centralized gateway with queue bounds, token tracking, and resilient retry."""
        overall_deadline = deadline_seconds or self.max_retry_duration
        start_time = time.time()

        # Check API key validity BEFORE constructing headers or attempting network call
        # A caller may supply a request-scoped key. Never copy it onto this
        # shared manager: concurrent users must not overwrite one another.
        effective_key = api_key if api_key is not None else self.api_key
        if effective_key is None:
            effective_key = get_config().openai_api_key
        effective_key = (effective_key or "").strip()

        if not is_valid_credential(effective_key):
            self.stats.status = "missing_credentials"
            self.stats.last_error_message = "OpenAI API key is missing, empty, or placeholder. Request aborted."
            raise LLMProviderException(
                LLMErrorDetail(
                    error_type=LLMErrorType.INVALID_API_KEY,
                    message="OpenAI API key is missing or invalid. Provide X-OpenAI-API-Key or configure OPENAI_API_KEY.",
                    is_transient=False,
                )
            )

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {effective_key}",
        }

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens

        # Check queue backpressure
        if self.queue_semaphore.locked():
            logger.warning("LLM Provider Manager queue saturated, waiting for capacity...")

        try:
            await asyncio.wait_for(self.queue_semaphore.acquire(), timeout=self.queue_timeout)
        except asyncio.TimeoutError:
            self.stats.failed_requests += 1
            raise LLMProviderException(
                LLMErrorDetail(
                    error_type=LLMErrorType.REQUEST_TIMEOUT,
                    message="Gateway capacity saturated: queue wait timeout exceeded.",
                    is_transient=True,
                )
            )

        self._queued_requests += 1
        sub_semaphore = self._select_semaphore(request_type)

        try:
            async with self._acquire_concurrency(sub_semaphore):
                self._queued_requests = max(0, self._queued_requests - 1)
                self._active_requests += 1
                try:
                    return await self._execute_with_retry(
                        payload=payload,
                        headers=headers,
                        effective_key=effective_key,
                        request_type=request_type,
                        start_time=start_time,
                        overall_deadline=overall_deadline,
                    )
                finally:
                    self._active_requests = max(0, self._active_requests - 1)
        finally:
            self.queue_semaphore.release()

    async def _execute_with_retry(
        self,
        payload: Dict[str, Any],
        headers: Dict[str, str],
        effective_key: str,
        request_type: str,
        start_time: float,
        overall_deadline: float,
    ) -> str:
        attempt = 0
        target_url = f"{self.base_url}/chat/completions"

        while True:
            attempt += 1
            self.stats.total_requests += 1
            t_call_start = time.time()

            # Check overall deadline
            elapsed = t_call_start - start_time
            if elapsed >= overall_deadline:
                self.stats.failed_requests += 1
                raise LLMProviderException(
                    LLMErrorDetail(
                        error_type=LLMErrorType.REQUEST_TIMEOUT,
                        message=f"LLM request exceeded total deadline budget of {overall_deadline:.1f}s across {attempt-1} attempts.",
                        is_transient=True,
                    )
                )

            try:
                # Per-attempt timeout bounded by remaining deadline
                remaining_deadline = max(1.0, overall_deadline - elapsed)
                timeout_for_attempt = min(self.default_timeout, remaining_deadline)

                resp = await self._client.post(
                    target_url,
                    headers=headers,
                    json=payload,
                    timeout=timeout_for_attempt,
                )

                # Track provider budget headers
                self._update_budget_headers(resp.headers)

                if resp.status_code == 200:
                    data = resp.json()
                    self._record_success(data)
                    return data["choices"][0]["message"]["content"]

                # Non-200: Classify error
                resp_text = resp.text.replace(effective_key, "[REDACTED]")
                error_detail = classify_http_error(resp.status_code, resp_text, dict(resp.headers))

            except httpx.TimeoutException as e:
                error_detail = LLMErrorDetail(
                    error_type=LLMErrorType.REQUEST_TIMEOUT,
                    message=f"Request timeout on connection to {self.base_url}: {e}",
                    is_transient=True,
                )
            except httpx.NetworkError as e:
                error_detail = LLMErrorDetail(
                    error_type=LLMErrorType.CONNECTION_ERROR,
                    message=f"Network connection error to {self.base_url}: {e}",
                    is_transient=True,
                )
            except Exception as e:
                error_detail = LLMErrorDetail(
                    error_type=LLMErrorType.UNKNOWN,
                    message=f"Unexpected error communicating with LLM provider: {e}",
                    is_transient=False,
                )

            # Provider messages and transport errors can echo request details.
            # Redact the request-scoped key before metrics, logs, or responses.
            if effective_key:
                error_detail.message = error_detail.message.replace(effective_key, "[REDACTED]")

            # Record failure state
            self._record_failure(error_detail)

            # Determine whether retry is permitted
            if not error_detail.is_transient:
                logger.error(
                    "Permanent LLM error [%s]: %s. Not retrying.",
                    error_detail.error_type.value,
                    error_detail.message,
                )
                raise LLMProviderException(error_detail)

            if attempt >= self.max_retries:
                logger.error(
                    "Exhausted maximum retry budget (%d attempts) for [%s]: %s",
                    self.max_retries,
                    error_detail.error_type.value,
                    error_detail.message,
                )
                raise LLMProviderException(error_detail)

            # Compute backoff delay with jitter
            delay = self._compute_backoff(error_detail, attempt)
            remaining_time = overall_deadline - (time.time() - start_time)
            if delay >= remaining_time:
                logger.warning(
                    "Required backoff delay (%.2fs) exceeds remaining deadline (%.2fs). Aborting retries.",
                    delay,
                    remaining_time,
                )
                raise LLMProviderException(error_detail)

            self.stats.retries_count += 1
            logger.warning(
                "Transient error [%s] on %s attempt %d/%d: %s. Backing off for %.2fs...",
                error_detail.error_type.value,
                request_type,
                attempt,
                self.max_retries,
                error_detail.message,
                delay,
            )
            await asyncio.sleep(delay)

    def _compute_backoff(self, error_detail: LLMErrorDetail, attempt: int) -> float:
        """Compute intelligent backoff delay, respecting Retry-After and adding randomized jitter."""
        if error_detail.retry_after_seconds is not None and error_detail.retry_after_seconds > 0:
            # Respect server Retry-After plus a slight randomized margin (0.1 - 0.4s)
            return min(self.max_retry_duration, error_detail.retry_after_seconds + random.uniform(0.1, 0.4))

        # Exponential backoff: base 0.5s * 2^(attempt-1), capped at 6.0s
        base_delay = min(6.0, 0.5 * (2 ** (attempt - 1)))
        # Randomized jitter +/- 25% to avoid synchronized retry storms
        jitter = base_delay * random.uniform(-0.25, 0.25)
        return max(0.2, base_delay + jitter)

    def _update_budget_headers(self, headers: httpx.Headers):
        req_rem = headers.get("x-ratelimit-remaining-requests")
        tok_rem = headers.get("x-ratelimit-remaining-tokens")
        if req_rem:
            try:
                self.stats.remaining_requests_budget = int(req_rem)
            except ValueError:
                pass
        if tok_rem:
            try:
                self.stats.remaining_tokens_budget = int(tok_rem)
            except ValueError:
                pass

    def _record_success(self, data: Dict[str, Any]):
        self.stats.successful_requests += 1
        self.stats.status = "healthy"
        usage = data.get("usage")
        if isinstance(usage, dict):
            p_tok = int(usage.get("prompt_tokens") or 0)
            c_tok = int(usage.get("completion_tokens") or 0)
            t_tok = int(usage.get("total_tokens") or (p_tok + c_tok))
            self.stats.prompt_tokens += p_tok
            self.stats.completion_tokens += c_tok
            self.stats.total_tokens += t_tok

    def _record_failure(self, error_detail: LLMErrorDetail):
        self.stats.failed_requests += 1
        self.stats.last_error_message = error_detail.message

        if error_detail.error_type == LLMErrorType.INSUFFICIENT_QUOTA:
            self.stats.quota_exhausted_count += 1
            self.stats.status = "quota_exhausted"
        elif error_detail.error_type == LLMErrorType.RATE_LIMIT_EXCEEDED:
            self.stats.rate_limit_count += 1
            self.stats.last_rate_limit_timestamp = time.time()
            self.stats.status = "rate_limited"
        elif error_detail.error_type == LLMErrorType.INVALID_API_KEY:
            self.stats.status = "unavailable"
        elif error_detail.is_transient:
            self.stats.status = "degraded"


# Shared singleton management
_shared_llm_manager: Optional[LLMProviderManager] = None


def get_llm_manager() -> LLMProviderManager:
    """Return the shared LLMProviderManager, lazily initialized with current config."""
    global _shared_llm_manager
    if _shared_llm_manager is None:
        cfg = get_config()
        _shared_llm_manager = LLMProviderManager(
            api_key=cfg.openai_api_key,
            base_url=cfg.openai_base_url,
            model=cfg.llm_model,
            max_global_concurrency=cfg.max_global_concurrency,
            max_extraction_concurrency=cfg.max_extraction_concurrency,
            max_judging_concurrency=cfg.max_judging_concurrency,
            max_pending_queue=cfg.max_pending_queue,
            queue_timeout_seconds=cfg.queue_timeout_seconds,
            max_retries=cfg.max_retries,
            max_retry_duration_seconds=cfg.max_retry_duration_seconds,
            default_request_timeout_seconds=cfg.extraction_timeout,
        )
    return _shared_llm_manager


def set_shared_llm_manager(manager: LLMProviderManager):
    """Set the shared LLMProviderManager instance (e.g. from FastAPI lifespan)."""
    global _shared_llm_manager
    _shared_llm_manager = manager


def reset_shared_llm_manager():
    """Reset the shared LLMProviderManager instance (useful for clean test isolation)."""
    global _shared_llm_manager
    _shared_llm_manager = None


def __getattr__(name: str):
    if name == "llm_manager":
        return get_llm_manager()
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
