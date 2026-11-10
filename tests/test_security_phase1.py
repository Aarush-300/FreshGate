import os
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, AsyncMock

from server.app import app
from core.security import is_safe_url, sanitize_error_detail, rate_limiter
from core.models import VerificationResult, Claim, Source, Summary


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    rate_limiter.reset()
    yield
    rate_limiter.reset()


# --- 1. Admin Authentication Tests ---

@pytest.mark.asyncio
async def test_admin_auth_required_for_cache_clear():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. No credentials -> 401 Unauthorized
        resp_no_auth = await ac.post("/api/cache/clear")
        assert resp_no_auth.status_code == 401

        # 2. Invalid credentials -> 403 Forbidden
        resp_bad_auth = await ac.post("/api/cache/clear", headers={"X-Admin-Key": "wrong-token"})
        assert resp_bad_auth.status_code == 403

        # 3. Valid admin token -> 200 OK
        with patch.dict(os.environ, {"FRESHGATE_ADMIN_KEY": "valid-secret-token"}):
            resp_good_auth = await ac.post(
                "/api/cache/clear",
                headers={"X-Admin-Key": "valid-secret-token"}
            )
            assert resp_good_auth.status_code == 200
            assert resp_good_auth.json() == {"status": "cleared"}


# --- 2. Security Headers Tests ---

@pytest.mark.asyncio
async def test_security_headers_present():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/health")
        assert resp.status_code == 200
        assert "Content-Security-Policy" in resp.headers
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["X-Frame-Options"] == "DENY"
        assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"


# --- 3. Body Size Limit Tests ---

@pytest.mark.asyncio
async def test_request_body_size_limit():
    large_payload = "a" * (1048576 + 500)  # > 1MB
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/verify", json={"answer": large_payload})
        assert resp.status_code == 413
        assert resp.json()["error"]["type"] == "payload_too_large"


# --- 4. Rate Limiting Tests ---

@pytest.mark.asyncio
async def test_rate_limiting_enforcement():
    # Temporarily set limiter to 2 requests per minute
    rate_limiter.requests_per_minute = 2

    mock_result = VerificationResult(
        claims=[],
        summary=Summary(total=0, checked=0, stale=0, avg_score=100)
    )

    with patch("server.app.pipeline.verify", new=AsyncMock(return_value=mock_result)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r1 = await ac.post("/verify", json={"answer": "Test 1"})
            assert r1.status_code == 200

            r2 = await ac.post("/verify", json={"answer": "Test 2"})
            assert r2.status_code == 200

            # 3rd request must be blocked with 429
            r3 = await ac.post("/verify", json={"answer": "Test 3"})
            assert r3.status_code == 429
            assert r3.json()["error"]["type"] == "rate_limit_exceeded"
            assert "Retry-After" in r3.headers


# --- 5. URL Safety Validation Tests ---

def test_url_safety_validation():
    assert is_safe_url("https://openai.com/pricing") is True
    assert is_safe_url("http://example.org/path?q=1") is True
    # Dangerous XSS pseudo-protocols
    assert is_safe_url("javascript:alert(document.cookie)") is False
    assert is_safe_url("data:text/html,<script>alert(1)</script>") is False
    assert is_safe_url("vbscript:msgbox") is False
    assert is_safe_url("file:///etc/passwd") is False
    assert is_safe_url("") is False
    assert is_safe_url(None) is False


# --- 6. Error Sanitization & Header Leakage Tests ---

def test_error_sanitization():
    sensitive_err = Exception("Database connection failure at C:\\Users\\secret\\repo\\db.sqlite with password=admin123")
    sanitized = sanitize_error_detail(sensitive_err)
    assert "admin123" not in sanitized
    assert "C:\\Users" not in sanitized
    assert sanitized == "Internal verification service error"


@pytest.mark.asyncio
async def test_proxy_never_leaks_stack_traces_in_headers():
    upstream_response = {
        "id": "chatcmpl-test",
        "choices": [{"message": {"role": "assistant", "content": "Sensitive question."}}],
    }

    with patch("server.app.forward_to_upstream", new=AsyncMock(return_value=(200, upstream_response, {}))):
        with patch("server.app.pipeline.verify", new=AsyncMock(side_effect=RuntimeError("Internal Traceback in file C:\\foo\\bar"))):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post("/proxy/v1/chat/completions", json={"messages": [{"role": "user", "content": "test"}]})

    assert resp.status_code == 200
    assert resp.headers.get("X-FreshGate-Status") == "fail-open"
    # Ensure raw trace and paths are not in the response headers
    assert "Traceback" not in resp.headers.get("X-FreshGate-Reason", "")
    assert "C:\\foo" not in resp.headers.get("X-FreshGate-Reason", "")


# --- 7. Comprehensive Fail-Open Integration Tests ---

@pytest.mark.asyncio
async def test_fail_open_when_tavily_and_llm_down():
    """Verify that even when external APIs are completely unreachable, the proxy returns the original completion untouched."""
    upstream_response = {
        "id": "chatcmpl-down",
        "model": "gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "The original intact LLM answer."},
                "finish_reason": "stop",
            }
        ],
    }

    with patch("server.app.forward_to_upstream", new=AsyncMock(return_value=(200, upstream_response, {}))):
        # Pipeline throws catastrophic connection error
        with patch("server.app.pipeline.verify", new=AsyncMock(side_effect=ConnectionResetError("Remote host down"))):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post("/proxy/v1/chat/completions", json={"messages": [{"role": "user", "content": "query"}]})

    assert resp.status_code == 200
    data = resp.json()
    # The original answer must remain completely intact
    assert data["choices"][0]["message"]["content"] == "The original intact LLM answer."
    assert resp.headers.get("X-FreshGate-Status") == "fail-open"
