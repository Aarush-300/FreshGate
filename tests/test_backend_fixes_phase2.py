import json
import pytest
import httpx
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, AsyncMock
from types import SimpleNamespace

from server.app import app, get_request_pipeline
from core.models import Claim, Summary, VerificationResult


@pytest.mark.asyncio
async def test_non_ascii_admin_key_returns_403_not_500():
    """Verify that a non-ASCII admin key header returns 403 rather than throwing TypeError."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/audits/recent", headers=[(b"x-admin-key", "é".encode("utf-8"))])
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_ssrf_via_header_base_url_rejected():
    """Verify that dangerous local/metadata base URLs in headers return 400 under production mode."""
    with patch.dict("os.environ", {"FRESHGATE_ENV": "production"}):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/verify",
                headers={
                    "X-LLM-Provider": "openai",
                    "X-LLM-API-Key": "sk-test-valid-mock-key-1234567890",
                    "X-LLM-Base-URL": "http://169.254.169.254",
                },
                json={"answer": "A test answer to verify."},
            )
        assert resp.status_code == 400


@pytest.mark.asyncio
async def test_streaming_sse_parser_handles_split_chunks():
    """Verify that event_generator stitches JSON tokens across chunk boundaries and triggers verification."""
    # Chunk 1 splits the json object mid-way
    part1 = 'data: {"id":"chatcmpl-split-1","choices":[{"delta":{"role":"assistant","conte'
    part2 = 'nt":"Hello world!"},"index":0}]}\n\n'
    part3 = 'data: [DONE]\n\n'

    async def mock_aiter_bytes():
        for piece in [part1.encode("utf-8"), part2.encode("utf-8"), part3.encode("utf-8")]:
            yield piece

    mock_resp = SimpleNamespace(
        status_code=200,
        headers={"content-type": "text/event-stream"},
        aiter_bytes=mock_aiter_bytes,
        aclose=AsyncMock(),
    )

    captured_verification = {}

    async def fake_run_streaming(completion_id, full_content):
        captured_verification["id"] = completion_id
        captured_verification["content"] = full_content

    with patch("server.app.stream_from_upstream", new=AsyncMock(return_value=mock_resp)), \
         patch("server.app.run_streaming_verification", side_effect=fake_run_streaming):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/proxy/v1/chat/completions",
                json={"model": "gpt-4o-mini", "stream": True, "messages": [{"role": "user", "content": "hi"}]},
            )
            assert resp.status_code == 200
            content = resp.text
            assert "Hello world!" in content

    assert captured_verification.get("id") == "chatcmpl-split-1"
    assert captured_verification.get("content") == "Hello world!"


@pytest.mark.asyncio
async def test_chunked_body_size_limit_returns_413():
    """Verify that chunked request bodies exceeding max_body_bytes return 413."""
    async def over_limit_generator():
        # Stream 1.2 MB without Content-Length
        chunk = b"A" * 65536
        for _ in range(20):
            yield chunk

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/verify",
            content=over_limit_generator(),
            headers={"Transfer-Encoding": "chunked", "Content-Type": "application/json"},
        )
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_login_throttling_by_ip_alone():
    """Verify that multiple failed logins from the same IP trigger 429 even when rotating emails."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        for i in range(30):
            r = await ac.post("/api/auth/login", json={"email": f"test_{i}@unknown.org", "password": "wrongpassword123"})
            if r.status_code == 429:
                break
        else:
            # 31st request should definitely be rate limited
            r = await ac.post("/api/auth/login", json={"email": "another@unknown.org", "password": "wrongpassword123"})
        assert r.status_code == 429
        assert "Too many attempts" in r.json()["detail"]


@pytest.mark.asyncio
async def test_stream_verification_requires_auth(admin_headers):
    """Verify /api/stream/verification/{id} returns 401 without auth and 200/404 with auth."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Unauthenticated request
        unauth_resp = await ac.get("/api/stream/verification/nonexistent-id")
        assert unauth_resp.status_code == 401

        # Authenticated with admin
        auth_resp = await ac.get("/api/stream/verification/nonexistent-id", headers=admin_headers)
        assert auth_resp.status_code == 404


@pytest.mark.asyncio
async def test_plugin_verify_endpoint(admin_headers):
    """Verify dedicated /api/plugin/verify endpoint requires valid api key and performs verification."""
    mock_result = VerificationResult(
        claims=[
            Claim(
                id="c1",
                text="Python 3.11 is released",
                type="version",
                verdict="confirmed",
                score=100,
            )
        ],
        summary=Summary(total=1, checked=1, stale=0, avg_score=100),
    )

    with patch.dict("os.environ", {"FRESHGATE_API_KEY": "secret-plugin-key"}), \
         patch("server.app.pipeline.verify", new=AsyncMock(return_value=mock_result)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Missing key -> 401
            r1 = await ac.post("/api/plugin/verify", json={"answer": "Python 3.11 is released."})
            assert r1.status_code == 401

            # Invalid key -> 403
            r2 = await ac.post(
                "/api/plugin/verify",
                headers={"X-API-Key": "wrong-key"},
                json={"answer": "Python 3.11 is released."},
            )
            assert r2.status_code == 403

            # Correct key -> 200
            r3 = await ac.post(
                "/api/plugin/verify",
                headers={"X-API-Key": "secret-plugin-key"},
                json={"answer": "Python 3.11 is released."},
            )
            assert r3.status_code == 200
            assert r3.json()["summary"]["avg_score"] == 100


@pytest.mark.asyncio
async def test_placeholder_admin_key_returns_403():
    """Verify that when FRESHGATE_ADMIN_KEY is set to placeholder value, admin endpoints return 403."""
    with patch.dict("os.environ", {"FRESHGATE_ADMIN_KEY": "your_admin_secret_token_here"}):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/audits/recent", headers={"X-Admin-Key": "your_admin_secret_token_here"})
            assert resp.status_code == 403
            assert "not configured" in resp.json()["detail"].lower()
