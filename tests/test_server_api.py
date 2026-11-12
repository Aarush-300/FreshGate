import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, AsyncMock

from server.app import app
from core.models import Claim, Source, Summary, VerificationResult


@pytest.mark.asyncio
async def test_post_verify_endpoint_contract():
    mock_result = VerificationResult(
        claims=[
            Claim(
                id="c1",
                text="GPT-4 costs $30 per million input tokens",
                type="pricing",
                verdict="contradicted",
                score=10,
                sources=[Source(url="https://openai.com", published="2026-01-01", snippet="current")],
                cached=False,
                correction="Current price is $2.50",
            )
        ],
        summary=Summary(total=1, checked=1, stale=1, avg_score=10),
    )

    with patch("server.app.pipeline.verify", new=AsyncMock(return_value=mock_result)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post("/verify", json={"answer": "GPT-4 costs $30 per million input tokens."})

    assert response.status_code == 200
    data = response.json()
    assert "claims" in data
    assert len(data["claims"]) == 1
    assert data["claims"][0]["verdict"] == "contradicted"
    assert data["claims"][0]["score"] == 10
    assert data["summary"]["stale"] == 1
    assert data["summary"]["avg_score"] == 10


@pytest.mark.asyncio
async def test_health_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "freshgate"
    assert "upstream" in data


@pytest.mark.asyncio
async def test_dashboard_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/")
    assert response.status_code == 200
    assert "FreshGate" in response.text
    assert "Bifrost" in response.text


@pytest.mark.asyncio
async def test_benchmark_api_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/api/benchmark")
    assert response.status_code == 200
    data = response.json()
    assert data["total_items"] == 520
    assert data["stale_items"] == 210
    assert data["control_items"] == 261
    assert data["partial_or_unverifiable_items"] == 49
    assert "results_md" in data
