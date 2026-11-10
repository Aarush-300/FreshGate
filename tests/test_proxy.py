import pytest
import asyncio
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, AsyncMock

from server.app import app
from core.models import VerificationResult, Claim, Source, Summary


@pytest.mark.asyncio
async def test_proxy_forwards_and_enriches_response():
    upstream_response = {
        "id": "chatcmpl-123",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "GPT-4 costs $30 per million tokens.",
                },
                "finish_reason": "stop",
            }
        ],
    }

    mock_verification = VerificationResult(
        claims=[
            Claim(
                id="c1",
                text="GPT-4 costs $30 per million tokens",
                type="pricing",
                verdict="contradicted",
                score=10,
                sources=[Source(url="https://openai.com", snippet="now $2.50")],
                correction="Current price is $2.50",
            )
        ],
        summary=Summary(total=1, checked=1, stale=1, avg_score=10),
    )

    with patch("server.app.forward_to_upstream", new=AsyncMock(return_value=(200, upstream_response, {}))):
        with patch("server.app.pipeline.verify", new=AsyncMock(return_value=mock_verification)):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post(
                    "/proxy/v1/chat/completions",
                    json={
                        "model": "openai/gpt-4o-mini",
                        "messages": [{"role": "user", "content": "How much is GPT-4?"}],
                    },
                )

    assert response.status_code == 200
    data = response.json()
    assert "choices" in data
    assert data["choices"][0]["message"]["content"] == "GPT-4 costs $30 per million tokens."
    assert "freshgate" in data
    assert data["freshgate"]["summary"]["stale"] == 1
    assert data["freshgate"]["summary"]["avg_score"] == 10
    assert response.headers.get("X-FreshGate-Score") == "10"
    assert response.headers.get("X-FreshGate-Stale-Count") == "1"


@pytest.mark.asyncio
async def test_proxy_fails_open_on_freshgate_error():
    upstream_response = {
        "id": "chatcmpl-456",
        "choices": [{"message": {"role": "assistant", "content": "Hello world!"}}],
    }

    # Pipeline raises an unexpected exception
    with patch("server.app.forward_to_upstream", new=AsyncMock(return_value=(200, upstream_response, {}))):
        with patch("server.app.pipeline.verify", new=AsyncMock(side_effect=RuntimeError("Tavily down"))):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post(
                    "/proxy/v1/chat/completions",
                    json={"messages": [{"role": "user", "content": "Hi"}]},
                )

    # Must fail open: returns original response intact with 200
    assert response.status_code == 200
    data = response.json()
    assert data["choices"][0]["message"]["content"] == "Hello world!"
    assert response.headers.get("X-FreshGate-Status") == "fail-open"


@pytest.mark.asyncio
async def test_proxy_fails_open_on_timeout():
    upstream_response = {
        "id": "chatcmpl-789",
        "choices": [{"message": {"role": "assistant", "content": "Slow response answer."}}],
    }

    async def slow_verify(text):
        await asyncio.sleep(0.5)
        return VerificationResult(claims=[], summary=Summary(total=0, checked=0, stale=0, avg_score=100))

    with patch("server.app.forward_to_upstream", new=AsyncMock(return_value=(200, upstream_response, {}))):
        with patch("server.app.pipeline.verify", side_effect=slow_verify):
            with patch("server.app.FRESHGATE_TIMEOUT", 0.05):  # 50ms timeout
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    response = await ac.post(
                        "/proxy/v1/chat/completions",
                        json={"messages": [{"role": "user", "content": "Slow question"}]},
                    )

    assert response.status_code == 200
    data = response.json()
    assert data["choices"][0]["message"]["content"] == "Slow response answer."
    assert response.headers.get("X-FreshGate-Status") == "fail-open"


@pytest.mark.asyncio
async def test_proxy_forwards_upstream_errors():
    error_payload = {"error": {"message": "Invalid API key", "type": "invalid_request_error"}}

    with patch("server.app.forward_to_upstream", new=AsyncMock(return_value=(401, error_payload, {}))):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                "/proxy/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "Error question"}]},
            )

    assert response.status_code == 401
    assert response.json() == error_payload
