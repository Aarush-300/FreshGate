import pytest
import os
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, MagicMock

from server.app import app
from core.metrics import metrics
from core.audit import audit_store


@pytest.mark.asyncio
async def test_health_live_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/health/live")
        assert resp.status_code == 200
        assert resp.json() == {"status": "alive"}


@pytest.mark.asyncio
async def test_health_ready_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/health/ready")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ready"
        assert data["database"] == "connected"
        assert data["cache"] == "connected"


@pytest.mark.asyncio
async def test_health_ready_fails_when_db_down():
    with patch("server.app.pipeline.cache.get_stats", side_effect=Exception("DB connection failed")):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/health/ready")
            assert resp.status_code == 503
            data = resp.json()
            assert data["status"] == "not_ready"
            assert data["database"] == "unhealthy"


@pytest.mark.asyncio
async def test_prometheus_metrics_endpoint():
    # Increment some metrics
    metrics.inc_requests(2)
    metrics.inc_claims_extracted(5)
    metrics.observe_latency(0.025)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers["content-type"]
        text = resp.text

        assert "freshgate_requests_total" in text
        assert "freshgate_claims_extracted_total" in text
        assert "freshgate_verification_latency_seconds_sum" in text
        assert "freshgate_cache_hits_total" in text


@pytest.mark.asyncio
async def test_correlation_id_propagation():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Custom correlation ID passed in header
        custom_id = "test-corr-id-999"
        resp = await ac.get("/health", headers={"X-Correlation-ID": custom_id})
        assert resp.status_code == 200
        assert resp.headers.get("X-Correlation-ID") == custom_id

        # 2. Generated correlation ID when omitted
        resp2 = await ac.get("/health")
        assert resp2.status_code == 200
        assert "X-Correlation-ID" in resp2.headers
        assert len(resp2.headers["X-Correlation-ID"]) > 10


@pytest.mark.asyncio
async def test_audit_store_recording_and_retrieval():
    test_audit_id = "test-audit-rec-1"
    audit_store.record_audit(
        audit_id=test_audit_id,
        correlation_id="corr-audit-test",
        input_text="PostgreSQL 16 is GA.",
        claims_data=[{"text": "PG 16 is GA", "verdict": "confirmed"}],
        total_claims=1,
        stale_claims=0,
        avg_score=95,
        latency_ms=15,
        status="verified",
    )

    # Admin key auth for audit log retrieval
    with patch.dict(os.environ, {"FRESHGATE_ADMIN_KEY": "test-admin-secret"}):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/audits/recent", headers={"X-Admin-Key": "test-admin-secret"})
            assert resp.status_code == 200
            audits = resp.json().get("audits", [])
            assert any(a["id"] == test_audit_id for a in audits)
