import os
import pytest
import asyncio
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, AsyncMock
from types import SimpleNamespace

from server.app import app
from core.audit import audit_store
from core.models import Claim, Source, Summary, VerificationResult


@pytest.fixture
def dummy_result():
    return VerificationResult(
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


@pytest.mark.asyncio
async def test_post_verify_writes_audit(dummy_result, admin_headers):
    scoped_pipeline = SimpleNamespace(
        verify=AsyncMock(return_value=dummy_result),
        verifier=SimpleNamespace(tavily_client=SimpleNamespace(aclose=AsyncMock())),
    )
    with patch("server.app.build_request_pipeline", return_value=scoped_pipeline):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            verify_resp = await ac.post(
                "/verify",
                headers={"X-OpenAI-API-Key": "sk-test-valid-key", "X-Tavily-API-Key": "tvly-test-valid-key"},
                json={"answer": "Testing audit log recording."},
            )
            assert verify_resp.status_code == 200

            audit_resp = await ac.get("/api/audits/recent?limit=10", headers=admin_headers)
            assert audit_resp.status_code == 200
            data = audit_resp.json()
            assert "audits" in data
            assert len(data["audits"]) > 0
            verify_records = [a for a in data["audits"] if a.get("source") == "verify"]
            assert len(verify_records) > 0
            rec = verify_records[0]
            assert rec["input_text"] == "Testing audit log recording."
            assert rec["total_claims"] == 1
            assert rec["stale_claims"] == 1


@pytest.mark.asyncio
async def test_shadow_mode_proxy_writes_audit(dummy_result, admin_headers):
    upstream_response = {
        "id": "chatcmpl-test-shadow",
        "choices": [{"message": {"role": "assistant", "content": "Shadow mode completion."}}],
    }

    with patch("server.app.forward_to_upstream", new=AsyncMock(return_value=(200, upstream_response, {}))), \
         patch("server.app.pipeline.verify", new=AsyncMock(return_value=dummy_result)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/proxy/v1/chat/completions",
                headers={"X-FreshGate-Mode": "shadow"},
                json={"messages": [{"role": "user", "content": "Hi"}]},
            )
            assert resp.status_code == 200
            # Allow background task to execute
            await asyncio.sleep(0.1)

            audit_resp = await ac.get("/api/audits/recent?source=proxy_async", headers=admin_headers)
            assert audit_resp.status_code == 200
            audits = audit_resp.json()["audits"]
            assert any(a.get("source") == "proxy_async" for a in audits)


@pytest.mark.asyncio
async def test_append_only_duplicate_audit_id(dummy_result):
    # Directly write two records with identical audit_id
    shared_id = "audit-collision-test"
    audit_store.record_audit(
        audit_id=shared_id,
        correlation_id="corr-1",
        input_text="First version",
        claims_data=[],
        total_claims=0,
        stale_claims=0,
        avg_score=100,
        latency_ms=10,
        status="verified",
    )
    audit_store.record_audit(
        audit_id=shared_id,
        correlation_id="corr-2",
        input_text="Second version",
        claims_data=[],
        total_claims=0,
        stale_claims=0,
        avg_score=90,
        latency_ms=12,
        status="verified",
    )

    records = audit_store.get_recent_audits(limit=50)
    matching = [r for r in records if r["id"].startswith(shared_id)]
    assert len(matching) == 2
    texts = {r["input_text"] for r in matching}
    assert "First version" in texts
    assert "Second version" in texts


@pytest.mark.asyncio
async def test_limit_validation(admin_headers):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r0 = await ac.get("/api/audits/recent?limit=0", headers=admin_headers)
        assert r0.status_code == 422

        r_neg = await ac.get("/api/audits/recent?limit=-1", headers=admin_headers)
        assert r_neg.status_code == 422

        r_huge = await ac.get("/api/audits/recent?limit=100000", headers=admin_headers)
        assert r_huge.status_code == 422


@pytest.mark.asyncio
async def test_admin_key_authorization(admin_headers):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # No key -> 401
        r_none = await ac.get("/api/audits/recent")
        assert r_none.status_code == 401

        # Wrong key -> 403
        r_wrong = await ac.get("/api/audits/recent", headers={"X-Admin-Key": "wrong-secret-token"})
        assert r_wrong.status_code == 403

        # Correct key -> 200
        r_ok = await ac.get("/api/audits/recent", headers=admin_headers)
        assert r_ok.status_code == 200


@pytest.mark.asyncio
async def test_audit_write_failure_does_not_break_verify(dummy_result):
    scoped_pipeline = SimpleNamespace(
        verify=AsyncMock(return_value=dummy_result),
        verifier=SimpleNamespace(tavily_client=SimpleNamespace(aclose=AsyncMock())),
    )
    with patch("server.app.build_request_pipeline", return_value=scoped_pipeline), \
         patch("core.audit.audit_store.arecord_audit", side_effect=RuntimeError("Disk failure")):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                "/verify",
                headers={"X-OpenAI-API-Key": "sk-test-valid-key", "X-Tavily-API-Key": "tvly-test-valid-key"},
                json={"answer": "Verification must succeed even if audit storage fails."},
            )
            assert response.status_code == 200
            assert response.json()["summary"]["total"] == 1


@pytest.mark.asyncio
async def test_warn_and_enforce_proxy_writes_audit(dummy_result, admin_headers):
    upstream_response = {
        "id": "chatcmpl-test-modes",
        "choices": [{"message": {"role": "assistant", "content": "Proxy modes completion."}}],
    }

    with patch("server.app.forward_to_upstream", new=AsyncMock(return_value=(200, upstream_response, {}))), \
         patch("server.app.pipeline.verify", new=AsyncMock(return_value=dummy_result)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # 1. Warn mode
            r_warn = await ac.post(
                "/proxy/v1/chat/completions",
                headers={"X-FreshGate-Mode": "warn"},
                json={"messages": [{"role": "user", "content": "Hi"}]},
            )
            assert r_warn.status_code == 200

            # 2. Enforce mode
            r_enforce = await ac.post(
                "/proxy/v1/chat/completions",
                headers={"X-FreshGate-Mode": "enforce"},
                json={"messages": [{"role": "user", "content": "Hi"}]},
            )
            assert r_enforce.status_code in (200, 422)

            # Check audit records
            audit_resp = await ac.get("/api/audits/recent?source=proxy", headers=admin_headers)
            assert audit_resp.status_code == 200
            audits = audit_resp.json()["audits"]
            assert any(a.get("source") == "proxy" for a in audits)
