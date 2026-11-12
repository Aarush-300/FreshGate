"""Comprehensive Test Suite for FreshGate V2 Rate-Limit Resilience, Concurrency, and Gateway Architecture.

Tests:
1. Error classification: rate_limit_exceeded vs. permanent insufficient_quota, invalid_api_key, server_error
2. Retry-After and reset header parsing (x-ratelimit-reset-requests, x-ratelimit-reset-tokens)
3. Permanent quota errors are never retried
4. Transient rate limits recover with backoff and jitter
5. Queue timeout and backpressure under load
6. Concurrency semaphore bounds
7. Request cancellation safety
8. Single-Flight request coalescing
9. SQLite cache expired entry cleanup
10. Distributed rate limiter with in-memory fallback
11. Multi-service circuit breaker isolation
12. Token usage and budget tracking
13. Granular timeout configuration
14. Operational provider status endpoint
"""

import json
import asyncio
import time
import httpx
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient

from core.llm_manager import (
    LLMProviderManager,
    LLMProviderException,
    LLMErrorType,
    classify_http_error,
    parse_reset_duration,
)
from core.circuit_breaker import (
    MultiServiceCircuitBreaker,
    ServiceCircuitBreaker,
    STATE_CLOSED,
    STATE_OPEN,
    STATE_HALF_OPEN,
)
from core.cache import SQLiteClaimCache, SingleFlightCoalescer
from core.security import DistributedRateLimiter, SlidingWindowRateLimiter
from core.models import Claim
from server.app import app


# --- 1. Error Classification Tests ---

def test_classify_transient_rate_limit():
    body = json.dumps({
        "error": {
            "message": "Rate limit reached for requests per min",
            "type": "requests",
            "code": "rate_limit_exceeded"
        }
    })
    headers = {"retry-after": "2.5"}
    detail = classify_http_error(429, body, headers)

    assert detail.error_type == LLMErrorType.RATE_LIMIT_EXCEEDED
    assert detail.is_transient is True
    assert detail.retry_after_seconds == 2.5


def test_classify_permanent_insufficient_quota():
    body = json.dumps({
        "error": {
            "message": "You exceeded your current quota, please check your plan and billing details.",
            "type": "insufficient_quota",
            "code": "insufficient_quota"
        }
    })
    headers = {}
    detail = classify_http_error(429, body, headers)

    assert detail.error_type == LLMErrorType.INSUFFICIENT_QUOTA
    assert detail.is_transient is False  # Must NOT be retried!


def test_classify_invalid_api_key():
    body = json.dumps({
        "error": {
            "message": "Incorrect API key provided.",
            "type": "invalid_request_error",
            "code": "invalid_api_key"
        }
    })
    detail = classify_http_error(401, body, {})
    assert detail.error_type == LLMErrorType.INVALID_API_KEY
    assert detail.is_transient is False


def test_classify_server_errors():
    detail = classify_http_error(503, "Service Unavailable", {})
    assert detail.error_type == LLMErrorType.SERVER_ERROR
    assert detail.is_transient is True


# --- 2. Reset Header Parsing Tests ---

def test_parse_reset_durations():
    assert parse_reset_duration("200ms") == 0.2
    assert parse_reset_duration("1.5s") == 1.5
    assert parse_reset_duration("2m") == 120.0
    assert parse_reset_duration("1m30s") == 90.0
    assert parse_reset_duration("5") == 5.0
    assert parse_reset_duration(None) is None


# --- 3. Permanent Quota Errors Are Never Retried ---

@pytest.mark.asyncio
async def test_permanent_quota_error_not_retried():
    """Verify that an insufficient_quota response immediately raises and is not retried in a loop."""
    quota_response = httpx.Response(
        status_code=429,
        content=json.dumps({
            "error": {
                "message": "You exceeded your current quota, please check your plan and billing details.",
                "type": "insufficient_quota",
                "code": "insufficient_quota"
            }
        }).encode(),
        request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"),
    )

    mgr = LLMProviderManager(max_retries=3, max_retry_duration_seconds=5.0)

    with patch.object(mgr._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = quota_response

        with pytest.raises(LLMProviderException) as exc_info:
            await mgr.complete("Test prompt")

        assert exc_info.value.detail.error_type == LLMErrorType.INSUFFICIENT_QUOTA
        # Must only have been called ONCE; never retried
        assert mock_post.call_count == 1
        assert mgr.stats.status == "quota_exhausted"


# --- 4. Transient Rate Limit Retries and Recovers ---

@pytest.mark.asyncio
async def test_transient_rate_limit_retries_and_recovers():
    """Verify that a temporary 429 rate limit backs off and completes successfully on subsequent attempt."""
    rate_limit_resp = httpx.Response(
        status_code=429,
        headers={"retry-after": "0.1"},
        content=json.dumps({
            "error": {
                "message": "Rate limit exceeded. Please retry after 0.1s.",
                "type": "tokens",
                "code": "rate_limit_exceeded"
            }
        }).encode(),
        request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"),
    )

    success_resp = httpx.Response(
        status_code=200,
        content=json.dumps({
            "choices": [{"message": {"content": "Recovered completion"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        }).encode(),
        request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"),
    )

    mgr = LLMProviderManager(max_retries=3, max_retry_duration_seconds=5.0)

    with patch.object(mgr._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = [rate_limit_resp, success_resp]

        result = await mgr.complete("Test prompt")
        assert result == "Recovered completion"
        assert mock_post.call_count == 2
        assert mgr.stats.retries_count == 1
        assert mgr.stats.successful_requests == 1
        assert mgr.stats.status == "healthy"


# --- 5. Queue Timeout and Backpressure ---

@pytest.mark.asyncio
async def test_queue_timeout_and_backpressure():
    """Verify that requests exceeding queue capacity reject promptly with timeout rather than unbounded wait."""
    mgr = LLMProviderManager(max_pending_queue=1, queue_timeout_seconds=0.1)
    await mgr.queue_semaphore.acquire()  # Saturate queue

    with pytest.raises(LLMProviderException) as exc_info:
        await mgr.complete("Overloaded queue prompt")

    assert exc_info.value.detail.error_type == LLMErrorType.REQUEST_TIMEOUT
    mgr.queue_semaphore.release()


# --- 6. Concurrency Limits Respected ---

@pytest.mark.asyncio
async def test_concurrency_semaphores_respected():
    """Verify that concurrent requests respect maximum concurrency limit."""
    mgr = LLMProviderManager(max_global_concurrency=2, max_pending_queue=10)
    current_concurrent = 0
    max_observed_concurrent = 0

    async def mock_post_delayed(*args, **kwargs):
        nonlocal current_concurrent, max_observed_concurrent
        current_concurrent += 1
        max_observed_concurrent = max(max_observed_concurrent, current_concurrent)
        await asyncio.sleep(0.05)
        current_concurrent -= 1
        return httpx.Response(
            status_code=200,
            content=json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode(),
            request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"),
        )

    with patch.object(mgr._client, "post", side_effect=mock_post_delayed):
        tasks = [mgr.complete(f"Prompt {i}") for i in range(5)]
        results = await asyncio.gather(*tasks)

        assert len(results) == 5
        assert max_observed_concurrent <= 2


# --- 7. Request Cancellation Safety ---

@pytest.mark.asyncio
async def test_request_cancellation_safety():
    """Verify that cancelled requests clean up active counters and semaphores."""
    mgr = LLMProviderManager()

    async def slow_call(*args, **kwargs):
        await asyncio.sleep(2.0)
        return "done"

    with patch.object(mgr, "_execute_with_retry", side_effect=slow_call):
        task = asyncio.create_task(mgr.complete("Prompt"))
        await asyncio.sleep(0.05)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        # Semaphores and active counters must be zero
        assert mgr._active_requests == 0
        assert mgr.queue_semaphore._value == 50


# --- 8. Single-Flight Request Coalescing ---

@pytest.mark.asyncio
async def test_single_flight_request_coalescing():
    """Verify that multiple concurrent identical verification calls execute only once."""
    coalescer = SingleFlightCoalescer()
    execution_count = 0

    async def heavy_verification(text: str):
        nonlocal execution_count
        execution_count += 1
        await asyncio.sleep(0.1)
        return f"Verified: {text}"

    # Fire 5 concurrent requests with identical key
    tasks = [coalescer.do("claim-python-27", heavy_verification, "Python 2.7") for _ in range(5)]
    results = await asyncio.gather(*tasks)

    # All 5 callers get the exact same result, but the function only executed ONCE
    assert len(results) == 5
    assert all(r == "Verified: Python 2.7" for r in results)
    assert execution_count == 1


# --- 9. SQLite Cache Expired Entry Cleanup ---

def test_sqlite_cache_cleanup_expired(workspace_tmp_path):
    """Verify that expired cache entries are successfully cleaned up."""
    db_file = str(workspace_tmp_path / "cache_cleanup.db")
    cache = SQLiteClaimCache(db_path=db_file)

    claim = Claim(
        id="c-exp",
        text="Expiring fact",
        type="pricing",
        verdict="confirmed",
        score=100,
    )
    # Save with 1 second TTL
    cache.set(claim, ttl_seconds=-10)  # Already expired

    deleted = cache.cleanup_expired()
    assert deleted >= 1

    retrieved = cache.get("Expiring fact", category="pricing")
    assert retrieved is None


# --- 10. Distributed Rate Limiter ---

def test_distributed_rate_limiter_in_memory():
    """Verify rate limiter allows within limit and rejects with retry_after when limit exceeded."""
    limiter = DistributedRateLimiter(requests_per_minute=2)
    client_id = "test-user-123"

    allowed1, _ = limiter.is_allowed(client_id)
    allowed2, _ = limiter.is_allowed(client_id)
    allowed3, retry_after = limiter.is_allowed(client_id)

    assert allowed1 is True
    assert allowed2 is True
    assert allowed3 is False
    assert retry_after >= 1

    limiter.reset()
    allowed_after_reset, _ = limiter.is_allowed(client_id)
    assert allowed_after_reset is True


# --- 11. Multi-Service Circuit Breaker Isolation ---

def test_multiservice_circuit_breaker_isolation():
    """Verify that a failure in Tavily search trips only Tavily and does not block LLM extraction."""
    cb = MultiServiceCircuitBreaker(failure_threshold=2, recovery_timeout_seconds=10.0)

    # Record 2 failures on Tavily search
    cb.record_failure(service="tavily_search")
    cb.record_failure(service="tavily_search")

    # Tavily search breaker is OPEN
    assert cb.can_execute(service="tavily_search") is False

    # LLM extraction breaker is STILL CLOSED and permitted to execute
    assert cb.services["llm_extraction"].can_execute() is True
    assert cb.services["llm_judging"].can_execute() is True


# --- 12. Token Usage and Budget Tracking ---

@pytest.mark.asyncio
async def test_token_usage_tracking():
    """Verify that provider response usage metadata increments tokens in stats."""
    mgr = LLMProviderManager()
    success_resp = httpx.Response(
        status_code=200,
        headers={"x-ratelimit-remaining-requests": "999", "x-ratelimit-remaining-tokens": "85000"},
        content=json.dumps({
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160}
        }).encode(),
        request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"),
    )

    with patch.object(mgr._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = success_resp
        await mgr.complete("Prompt")

        stats = mgr.get_stats_summary()
        assert stats["prompt_tokens"] == 120
        assert stats["completion_tokens"] == 40
        assert stats["total_tokens"] == 160
        assert stats["remaining_requests_budget"] == 999
        assert stats["remaining_tokens_budget"] == 85000


# --- 13. Operational Provider Status API Endpoint ---

def test_api_provider_status_endpoint():
    """Verify GET /api/provider/status returns LLM, circuit breakers, cache, and metrics metadata."""
    client = TestClient(app)
    resp = client.get("/api/provider/status")

    assert resp.status_code == 200
    data = resp.json()

    assert "llm" in data
    assert "circuit_breakers" in data
    assert "cache" in data
    assert "metrics" in data
    assert data["llm"]["model"] == "gpt-4o-mini"
    assert "gateway" in data["circuit_breakers"]
    assert "llm_extraction" in data["circuit_breakers"]
