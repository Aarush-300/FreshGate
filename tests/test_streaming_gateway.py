import pytest
import json
import asyncio
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, AsyncMock

from server.app import app
from core.models import VerificationResult, Claim, Source, Summary


# --- 1. Non-Streaming Preservation Tests ---

@pytest.mark.asyncio
async def test_non_streaming_preserves_all_metadata_and_usage():
    upstream_response = {
        "id": "chatcmpl-prod-999",
        "object": "chat.completion",
        "created": 1710000000,
        "model": "claude-3-5-sonnet-20241022",
        "system_fingerprint": "fp_bifrost_custom",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Python 3.12 is the latest stable release.",
                },
                "finish_reason": "stop",
                "logprobs": None,
            }
        ],
        "usage": {
            "prompt_tokens": 15,
            "completion_tokens": 20,
            "total_tokens": 35,
        },
        "custom_provider_metadata": {"latency_ms": 120, "provider": "anthropic"},
    }

    mock_verification = VerificationResult(
        claims=[
            Claim(
                id="c1",
                text="Python 3.12 is the latest stable release",
                type="version",
                verdict="confirmed",
                score=95,
                sources=[Source(url="https://python.org", snippet="release")],
            )
        ],
        summary=Summary(total=1, checked=1, stale=0, avg_score=95),
    )

    with patch("server.app.forward_to_upstream", new=AsyncMock(return_value=(200, upstream_response, {"content-type": "application/json"}))):
        with patch("server.app.pipeline.verify", new=AsyncMock(return_value=mock_verification)):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post("/proxy/v1/chat/completions", json={"model": "claude-3-5-sonnet", "messages": [{"role": "user", "content": "hi"}]})

    assert resp.status_code == 200
    data = resp.json()

    # Preserved upstream metadata
    assert data["id"] == "chatcmpl-prod-999"
    assert data["model"] == "claude-3-5-sonnet-20241022"
    assert data["system_fingerprint"] == "fp_bifrost_custom"
    assert data["usage"]["total_tokens"] == 35
    assert data["custom_provider_metadata"]["provider"] == "anthropic"

    # Enriched FreshGate metadata
    assert "freshgate" in data
    assert data["freshgate"]["summary"]["avg_score"] == 95
    assert resp.headers.get("X-FreshGate-Score") == "95"


# --- 2. Streaming SSE Forwarding Tests ---

@pytest.mark.asyncio
async def test_streaming_sse_forwarding_and_termination():
    chunks = [
        'data: {"id":"chatcmpl-s1","choices":[{"delta":{"role":"assistant","content":"GPT-4 "},"index":0}]}\n\n',
        'data: {"id":"chatcmpl-s1","choices":[{"delta":{"content":"costs $30."},"index":0}]}\n\n',
        'data: [DONE]\n\n',
    ]

    async def mock_stream_generator():
        for c in chunks:
            yield c.encode("utf-8")

    class MockStreamResponse:
        status_code = 200
        headers = {"content-type": "text/event-stream"}
        def aiter_bytes(self):
            return mock_stream_generator()
        async def aclose(self):
            pass

    with patch("server.app.stream_from_upstream", new=AsyncMock(return_value=MockStreamResponse())):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/proxy/v1/chat/completions",
                json={
                    "model": "gpt-4o-mini",
                    "stream": True,
                    "messages": [{"role": "user", "content": "Tell me pricing"}],
                },
            )

    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    text = resp.text
    assert "GPT-4 " in text
    assert "costs $30." in text
    assert "data: [DONE]" in text


# --- 3. Streaming Tool-Call Preservation Tests ---

@pytest.mark.asyncio
async def test_streaming_preserves_tool_calls():
    tool_chunks = [
        'data: {"id":"chatcmpl-t1","choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_123","type":"function","function":{"name":"search"}}]},"index":0}]}\n\n',
        'data: {"id":"chatcmpl-t1","choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"q\\":\\"test\\"}"}}]},"index":0}]}\n\n',
        'data: [DONE]\n\n',
    ]

    async def mock_tool_stream():
        for c in tool_chunks:
            yield c.encode("utf-8")

    class MockStreamResponse:
        status_code = 200
        headers = {"content-type": "text/event-stream"}
        def aiter_bytes(self):
            return mock_tool_stream()
        async def aclose(self):
            pass

    with patch("server.app.stream_from_upstream", new=AsyncMock(return_value=MockStreamResponse())):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/proxy/v1/chat/completions",
                json={
                    "model": "gpt-4o-mini",
                    "stream": True,
                    "messages": [{"role": "user", "content": "Search query"}],
                },
            )

    assert resp.status_code == 200
    text = resp.text
    assert "call_123" in text
    assert "search" in text
    assert "data: [DONE]" in text


# --- 4. Streaming Async Verification Retrieval ---

@pytest.mark.asyncio
async def test_streaming_async_verification_retrieval():
    chunks = [
        'data: {"id":"chatcmpl-stream-retrieval-1","choices":[{"delta":{"role":"assistant","content":"PostgreSQL 16 is released."},"index":0}]}\n\n',
        'data: [DONE]\n\n',
    ]

    async def mock_stream_generator():
        for c in chunks:
            yield c.encode("utf-8")

    class MockStreamResponse:
        status_code = 200
        headers = {"content-type": "text/event-stream"}
        def aiter_bytes(self):
            return mock_stream_generator()
        async def aclose(self):
            pass

    mock_verification = VerificationResult(
        claims=[
            Claim(
                id="c1",
                text="PostgreSQL 16 is released",
                type="version",
                verdict="confirmed",
                score=90,
                sources=[Source(url="https://postgresql.org", snippet="v16")],
            )
        ],
        summary=Summary(total=1, checked=1, stale=0, avg_score=90),
    )

    with patch("server.app.stream_from_upstream", new=AsyncMock(return_value=MockStreamResponse())):
        with patch("server.app.pipeline.verify", new=AsyncMock(return_value=mock_verification)):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                stream_resp = await ac.post(
                    "/proxy/v1/chat/completions",
                    json={
                        "model": "gpt-4o-mini",
                        "stream": True,
                        "messages": [{"role": "user", "content": "PG 16 status"}],
                    },
                )
                assert stream_resp.status_code == 200

                # Background tasks in Starlette ASGI transport run upon response completion
                res_resp = await ac.get("/api/stream/verification/chatcmpl-stream-retrieval-1")
                assert res_resp.status_code == 200
                res_data = res_resp.json()
                assert res_data["status"] == "completed"
                assert res_data["result"]["summary"]["avg_score"] == 90
