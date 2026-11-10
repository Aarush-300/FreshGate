import pytest
import os
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, AsyncMock

from server.app import app
from core.models import VerificationResult, Claim, Source, Summary
from core.circuit_breaker import circuit_breaker


@pytest.fixture(autouse=True)
def reset_cb():
    circuit_breaker.reset()
    yield
    circuit_breaker.reset()


@pytest.mark.asyncio
async def test_shadow_mode_does_not_alter_response():
    upstream_resp = {
        "id": "chatcmpl-shadow-1",
        "choices": [{"message": {"role": "assistant", "content": "PostgreSQL 16 is GA."}}],
    }

    with patch("server.app.forward_to_upstream", new=AsyncMock(return_value=(200, upstream_resp, {}))):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/proxy/v1/chat/completions",
                headers={"X-FreshGate-Mode": "shadow"},
                json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
            )
            assert resp.status_code == 200
            data = resp.json()
            # Must NOT have freshgate payload in shadow mode
            assert "freshgate" not in data
            assert resp.headers.get("X-FreshGate-Mode") == "shadow"


@pytest.mark.asyncio
async def test_warn_mode_attaches_headers_only():
    upstream_resp = {
        "id": "chatcmpl-warn-1",
        "choices": [{"message": {"role": "assistant", "content": "PostgreSQL 16 is GA."}}],
    }
    mock_ver = VerificationResult(
        claims=[Claim(id="c1", text="PG 16 is GA", type="version", verdict="confirmed", score=95)],
        summary=Summary(total=1, checked=1, stale=0, avg_score=95),
    )

    with patch("server.app.forward_to_upstream", new=AsyncMock(return_value=(200, upstream_resp, {}))):
        with patch("server.app.pipeline.verify", new=AsyncMock(return_value=mock_ver)):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post(
                    "/proxy/v1/chat/completions",
                    headers={"X-FreshGate-Mode": "warn"},
                    json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
                )
                assert resp.status_code == 200
                data = resp.json()
                # Headers attached
                assert resp.headers.get("X-FreshGate-Score") == "95"
                assert resp.headers.get("X-FreshGate-Mode") == "warn"
                # JSON body NOT modified
                assert "freshgate" not in data


@pytest.mark.asyncio
async def test_policy_enforcement_blocks_stale():
    upstream_resp = {
        "id": "chatcmpl-stale-1",
        "choices": [{"message": {"role": "assistant", "content": "Python 2.7 is current."}}],
    }
    mock_stale_ver = VerificationResult(
        claims=[Claim(id="c1", text="Python 2.7 is current", type="version", verdict="contradicted", score=10)],
        summary=Summary(total=1, checked=1, stale=1, avg_score=10),
    )

    with patch("server.app.forward_to_upstream", new=AsyncMock(return_value=(200, upstream_resp, {}))):
        with patch("server.app.pipeline.verify", new=AsyncMock(return_value=mock_stale_ver)):
            with patch("server.app.FRESHGATE_BLOCK_STALE", True):
                with patch("server.app.FRESHGATE_MIN_SCORE", 70):
                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                        resp = await ac.post(
                            "/proxy/v1/chat/completions",
                            headers={"X-FreshGate-Mode": "enforce"},
                            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
                        )
                        # Blocked with 422 Unprocessable Content policy error
                        assert resp.status_code == 422
                        assert resp.headers.get("X-FreshGate-Policy") == "violated"
                        assert "FreshGate Policy Violation" in resp.json()["error"]


@pytest.mark.asyncio
async def test_circuit_breaker_tripping_and_fail_open():
    upstream_resp = {
        "id": "chatcmpl-cb-1",
        "choices": [{"message": {"role": "assistant", "content": "Sample statement"}}],
    }

    # Simulate 5 consecutive verification failures to trip circuit breaker
    with patch("server.app.forward_to_upstream", new=AsyncMock(return_value=(200, upstream_resp, {}))):
        with patch("server.app.pipeline.verify", side_effect=Exception("External Service Timeout")):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                for _ in range(5):
                    resp = await ac.post(
                        "/proxy/v1/chat/completions",
                        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
                    )
                    assert resp.status_code == 200
                    assert resp.headers.get("X-FreshGate-Status") == "fail-open"

    # Circuit breaker is now OPEN
    assert circuit_breaker.get_state()["state"] == "open"

    # Next request immediately bypasses pipeline without calling verify
    with patch("server.app.forward_to_upstream", new=AsyncMock(return_value=(200, upstream_resp, {}))):
        with patch("server.app.pipeline.verify") as mock_verify:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post(
                    "/proxy/v1/chat/completions",
                    json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
                )
                assert resp.status_code == 200
                assert resp.headers.get("X-FreshGate-Circuit-Breaker") == "open"
                assert resp.headers.get("X-FreshGate-Status") == "fail-open"
                mock_verify.assert_not_called()


@pytest.mark.asyncio
async def test_circuit_breaker_admin_reset():
    # Force trip
    for _ in range(5):
        circuit_breaker.record_failure()
    assert circuit_breaker.get_state()["state"] == "open"

    with patch.dict(os.environ, {"FRESHGATE_ADMIN_KEY": "admin-secret-key"}):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/api/circuit-breaker/reset", headers={"X-Admin-Key": "admin-secret-key"})
            assert resp.status_code == 200
            assert resp.json()["circuit_breaker"]["state"] == "closed"

    assert circuit_breaker.get_state()["state"] == "closed"
