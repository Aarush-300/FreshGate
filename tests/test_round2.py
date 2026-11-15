from __future__ import annotations

import asyncio
import os
import time
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

import pytest
from httpx import AsyncClient, ASGITransport
from pymongo.errors import PyMongoError
from fastapi import HTTPException

from server.app import app, _filter_upstream_headers, _consume_sse_line
import server.app as app_module
from server.users import (
    DUMMY_PASSWORD_HASH,
    check_password,
    serializer,
    _fernet,
    users_collection,
)
from core.security import (
    get_configured_api_key,
    rate_limiter,
)
from core.audit import get_audit_store
from core.audit_helpers import write_audit_failure
from core.circuit_breaker import ServiceCircuitBreaker, STATE_CLOSED, STATE_OPEN, STATE_HALF_OPEN
from core.cache import SingleFlightCoalescer, SQLiteClaimCache
from core.models import VerificationResult, Summary, Claim


# ---------------------------------------------------------------------------
# 1.1 Unknown-user login timing oracle
# ---------------------------------------------------------------------------
def test_1_1_unknown_user_login_dummy_hash():
    assert DUMMY_PASSWORD_HASH is not None
    t0 = time.perf_counter()
    res = check_password("some_random_password_attempt", DUMMY_PASSWORD_HASH)
    elapsed = time.perf_counter() - t0
    assert res is False
    assert elapsed > 0.01  # PBKDF2 actually computed, takes > 10ms


# ---------------------------------------------------------------------------
# 1.2 Strict API key & Rate Limiting on /api/plugin/verify and /v1/chat/completions
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_1_2_strict_api_key_and_rate_limiting():
    # In production with no configured API key: endpoints return 503
    with patch.dict(os.environ, {"FRESHGATE_ENV": "production", "FRESHGATE_API_KEY": ""}, clear=False):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res_plugin = await ac.post("/api/plugin/verify", json={"answer": "testing"})
            assert res_plugin.status_code == 503
            assert "FRESHGATE_API_KEY must be set" in res_plugin.text

            res_v1 = await ac.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})
            assert res_v1.status_code == 503

    # With a configured API key
    test_key = "test-prod-key-12345"
    with patch.dict(os.environ, {"FRESHGATE_ENV": "production", "FRESHGATE_API_KEY": test_key}, clear=False):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Wrong key returns 403
            res_wrong = await ac.post(
                "/api/plugin/verify",
                headers={"X-API-Key": "wrong-key"},
                json={"answer": "testing"},
            )
            assert res_wrong.status_code == 403

            # Right key authenticates successfully
            mock_res = VerificationResult(
                claims=[],
                summary=Summary(total=0, checked=0, stale=0, avg_score=100),
            )
            scoped_pipeline = SimpleNamespace(
                verify=AsyncMock(return_value=mock_res),
                verifier=SimpleNamespace(tavily_client=SimpleNamespace(aclose=AsyncMock())),
            )
            with patch("server.app.build_request_pipeline", return_value=scoped_pipeline):
                res_ok = await ac.post(
                    "/api/plugin/verify",
                    headers={"X-API-Key": test_key},
                    json={"answer": "testing"},
                )
                assert res_ok.status_code == 200

            # Rate limiting on /api/plugin
            rate_limiter.reset()
            hit_429 = False
            for _ in range(65):
                r = await ac.post(
                    "/api/plugin/verify",
                    headers={"X-API-Key": test_key},
                    json={"answer": "testing"},
                )
                if r.status_code == 429:
                    hit_429 = True
                    break
            assert hit_429, "Expected 429 after exceeding 60 requests/minute on /api/plugin/verify"
            rate_limiter.reset()


# ---------------------------------------------------------------------------
# 1.4 Rate-limit identity & proxy hygiene
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_1_4_rotating_fake_tokens_still_hit_429():
    rate_limiter.reset()
    hit_429 = False
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        for i in range(65):
            headers = {"X-API-Key": f"fake-rotating-token-{i}"}
            r = await ac.post("/verify", headers=headers, json={"answer": "testing"})
            if r.status_code == 429:
                hit_429 = True
                break
    assert hit_429, "Rotating invalid tokens must be grouped under the client IP and hit 429"
    rate_limiter.reset()


# ---------------------------------------------------------------------------
# 1.5 Placeholder secrets rejected
# ---------------------------------------------------------------------------
def test_1_5_placeholder_secrets_rejected():
    placeholders = [
        "replace_with_a_random_secret_at_least_32_characters",
        "your_secret_key_1234567890123456",
        "changeme_secret_key_1234567890123456",
        "dev-secret-1234567890123456",
        "secret",
    ]
    for p in placeholders:
        with patch.dict(os.environ, {"FRESHGATE_SESSION_SECRET": p}, clear=False):
            with pytest.raises(HTTPException) as exc_info:
                serializer()
            assert exc_info.value.status_code == 503

            with pytest.raises(HTTPException) as exc_info:
                _fernet()
            assert exc_info.value.status_code == 503

    api_key_placeholders = [
        "optional_client_api_key_here",
        "replace_me",
        "your_key",
        "changeme",
        "dev-secret",
    ]
    for p in api_key_placeholders:
        with patch.dict(os.environ, {"FRESHGATE_API_KEY": p}, clear=False):
            assert get_configured_api_key() is None


# ---------------------------------------------------------------------------
# 1.6 Header allow-list for upstream forwarding
# ---------------------------------------------------------------------------
def test_1_6_header_allow_list_for_upstream():
    inbound = {
        "authorization": "Bearer upstream-token",
        "content-type": "application/json",
        "accept": "application/json",
        "x-request-id": "req-12345",
        "x-bf-custom-header": "custom-val",
        "cookie": "freshgate_session=secret-session-cookie",
        "x-admin-key": "admin-secret-key",
        "x-freshgate-mode": "enforce",
        "host": "localhost:8000",
    }
    filtered = _filter_upstream_headers(inbound)
    assert "authorization" in filtered
    assert "content-type" in filtered
    assert "accept" in filtered
    assert "x-request-id" in filtered
    assert "x-bf-custom-header" in filtered

    assert "cookie" not in filtered
    assert "x-admin-key" not in filtered
    assert "x-freshgate-mode" not in filtered
    assert "host" not in filtered


# ---------------------------------------------------------------------------
# 2.1 One bad SSE line does not drop the rest of the chunk
# ---------------------------------------------------------------------------
def test_2_1_sse_parser_bad_line_does_not_abort_stream():
    state = {"chunks": [], "chars": 0, "completion_id": None}
    lines = [
        ": keepalive comment",
        "data: {bad-json-syntax",
        'data: {"id": "cmpl-stream-1", "choices": [{"delta": {"content": "Hello"}}]}',
        "data: not json at all",
        'data: {"choices": [{"delta": {"content": " world"}}]}',
        "data: [DONE]",
    ]
    for line in lines:
        _consume_sse_line(line, state)

    assert state["completion_id"] == "cmpl-stream-1"
    assert state["chunks"] == ["Hello", " world"]
    assert state["chars"] == len("Hello world")


# ---------------------------------------------------------------------------
# 2.2 Audit fail-open, timeout, circuit-open
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_2_2_write_audit_failure():
    store = get_audit_store()
    corr_id = f"test-audit-fail-{time.time()}"
    await write_audit_failure(
        text="A factual answer that timed out during extraction",
        source="verify",
        mode="enforce",
        reason="extraction_timeout",
        corr_id=corr_id,
        owner_id="test_user_audit",
        latency_ms=250,
    )
    audits = await store.aget_recent_audits(limit=20)
    record = next((a for a in audits if a.get("correlation_id") == corr_id), None)
    assert record is not None
    assert record.get("status") in ("fail-open", "error")
    assert record.get("source") == "verify"
    assert record.get("latency_ms") == 250


# ---------------------------------------------------------------------------
# 2.4 Circuit breaker trips on verification failure and limits HALF_OPEN probes
# ---------------------------------------------------------------------------
def test_2_4_circuit_breaker_behavior():
    cb = ServiceCircuitBreaker(
        name="test_breaker",
        failure_threshold=3,
        recovery_timeout_seconds=0.1,
        half_open_probes=2,
    )

    # 1. Trips after 3 failures
    cb.record_failure()
    cb.record_failure()
    assert cb._state == STATE_CLOSED
    cb.record_failure()
    assert cb._state == STATE_OPEN
    assert cb.can_execute() is False

    # 2. In HALF_OPEN, limits concurrent probes to half_open_probes (2)
    time.sleep(0.12)
    assert cb.can_execute() is True   # Probe 1
    assert cb._state == STATE_HALF_OPEN
    assert cb.can_execute() is True   # Probe 2
    assert cb.can_execute() is False  # Probe 3 rejected (exceeds 2 in-flight)

    # Complete one probe with success
    cb.record_success()
    assert cb.can_execute() is True   # Slot freed up
    cb.record_success()
    assert cb._state == STATE_CLOSED


# ---------------------------------------------------------------------------
# 3.1 Single-flight cancellation bug
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_3_1_singleflight_cancellation():
    coalescer = SingleFlightCoalescer()

    # (a) Cancelling one follower does not cancel leader or other followers
    started = asyncio.Event()
    release = asyncio.Event()
    call_count = 0

    async def slow_work():
        nonlocal call_count
        call_count += 1
        started.set()
        await release.wait()
        return "result_data"

    t_leader = asyncio.create_task(coalescer.do("key_cancel_follower", slow_work))
    await started.wait()

    t_follower1 = asyncio.create_task(coalescer.do("key_cancel_follower", slow_work))
    t_follower2 = asyncio.create_task(coalescer.do("key_cancel_follower", slow_work))

    await asyncio.sleep(0.01)
    t_follower1.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t_follower1

    release.set()
    res_leader = await t_leader
    res_follower2 = await t_follower2

    assert res_leader == "result_data"
    assert res_follower2 == "result_data"
    assert call_count == 1

    # (b) Cancelling leader delivers RuntimeError to followers, not CancelledError
    started2 = asyncio.Event()

    async def cancellable_leader():
        started2.set()
        await asyncio.sleep(10)
        return "never"

    t_leader2 = asyncio.create_task(coalescer.do("key_cancel_leader", cancellable_leader))
    await started2.wait()
    t_follower3 = asyncio.create_task(coalescer.do("key_cancel_leader", cancellable_leader))
    await asyncio.sleep(0.01)

    t_leader2.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t_leader2

    with pytest.raises(RuntimeError) as exc_info:
        await t_follower3
    assert "coalesced verification was cancelled" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 3.3 Expired cache rows purged
# ---------------------------------------------------------------------------
def test_3_3_expired_cache_cleanup(workspace_tmp_path):
    db_file = str(workspace_tmp_path / "test_cache.db")
    cache = SQLiteClaimCache(db_path=db_file)
    claim1 = Claim(id="c1", text="Expired claim", type="pricing", verdict="confirmed", score=90)
    claim2 = Claim(id="c2", text="Active claim", type="pricing", verdict="confirmed", score=90)

    # Expired row
    cache.set(claim1, ttl_seconds=-10)
    # Active row
    cache.set(claim2, ttl_seconds=3600)

    assert cache.get(claim1.text, claim1.type) is None
    deleted = cache.cleanup_expired()
    assert deleted >= 1
    assert cache.get(claim2.text, claim2.type) is not None


# ---------------------------------------------------------------------------
# 3.4 Mongo index retry and resilience
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_3_4_mongo_index_retry_and_resilience():
    # 1. Background retry on transient failure
    attempts = 0

    def mock_ensure():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PyMongoError("MongoDB is connecting...")
        return None

    with patch("server.app.ensure_indexes", side_effect=mock_ensure):
        with patch("asyncio.sleep", new_callable=AsyncMock):
            app_module._mongo_indexes_ready = False
            task = asyncio.create_task(app_module._ensure_indexes_with_backoff())
            await task
            assert app_module._mongo_indexes_ready is True
            assert attempts == 3

    # 2. Health check reports index status
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.get("/health/ready")
        assert r.status_code == 200
        assert r.json().get("indexes") is True

    # 3. Register duplicate check returns 409
    users_collection().insert_one({"email": "existing_user@test.com", "password_hash": "dummy"})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r_dup = await ac.post(
            "/api/auth/register",
            json={"email": "existing_user@test.com", "password": "StrongPassword123!"},
        )
        assert r_dup.status_code == 409


# ---------------------------------------------------------------------------
# 3.5 Login throttle memory sweep
# ---------------------------------------------------------------------------
def test_3_5_login_throttle_memory_sweep():
    app_module._login_attempts.clear()
    now = time.time()
    # Populate with 10,005 old entries (> 15 minutes ago)
    for i in range(10005):
        app_module._login_attempts[f"ip_{i}:user@test.com"] = [now - 1000]

    # Populate with 3 recent entries
    for i in range(3):
        app_module._login_attempts[f"active_{i}"] = [now - 10]

    assert len(app_module._login_attempts) == 10008

    # Trigger prune
    cutoff = now - 900
    app_module._prune_login_attempts_if_needed(cutoff)

    # Stale entries evicted
    assert len(app_module._login_attempts) == 3
    app_module._login_attempts.clear()
