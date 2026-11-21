"""Strategic Feature Enhancement Unit and Integration Tests (Phases 1-7)."""

import pytest
import time
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
from core.models import Claim, Source
from core.scorer import (
    compute_temporal_decay,
    calculate_flip_probability,
    calculate_recommended_interval,
    compute_score_dimensions,
)
from core.categories import category_registry
from core.sources import SourceValidator
from core.extractor import (
    ClaimExtractor,
    detect_future_claim,
    extract_text_from_file_bytes,
)
from core.history import (
    export_executive_summary,
    export_temporal_trends,
    export_knowledge_base,
    save_claim_snapshot,
)
from core.email import EmailService
from core.config import FreshGateConfig, get_config
from core.security import validate_upload
from fastapi.testclient import TestClient
from server.app import app


# --- Phase 1: Temporal Freshness Scoring Tests ---

def test_temporal_decay_curves():
    # Linear curve
    claim_linear = Claim(
        id="c1",
        text="Bill passed",
        type="law",
        freshness_half_life_seconds=86400,
        degradation_curve="linear",
        last_verified_at=time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - 43200)),  # 12 hours ago
    )
    decay_linear = compute_temporal_decay(claim_linear)
    assert 0.7 <= decay_linear <= 0.8  # halfway through half-life from 1.0 to 0.5 is 0.75

    # Exponential curve
    claim_exp = Claim(
        id="c2",
        text="Pricing plan $20",
        type="pricing",
        freshness_half_life_seconds=3600,
        degradation_curve="exponential",
        last_verified_at=time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - 3600)),  # 1 half-life ago
    )
    decay_exp = compute_temporal_decay(claim_exp)
    assert 0.45 <= decay_exp <= 0.55  # after 1 half-life, exponential decay factor is ~0.5

    # Step curve
    claim_step_fresh = Claim(
        id="c3",
        text="Version 3.12",
        type="version",
        freshness_half_life_seconds=21600,
        degradation_curve="step",
        last_verified_at=time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - 10000)),  # within half-life
    )
    assert compute_temporal_decay(claim_step_fresh) == 1.0

    claim_step_stale = Claim(
        id="c4",
        text="Version 3.10",
        type="version",
        freshness_half_life_seconds=21600,
        degradation_curve="step",
        last_verified_at=time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - 30000)),  # past half-life
    )
    assert compute_temporal_decay(claim_step_stale) == 0.5


def test_score_dimensions_includes_temporal_and_predictive():
    now_date = time.strftime("%Y-%m-%d")
    claim = Claim(
        id="c1",
        text="ChatGPT Plus costs $20",
        type="pricing",
        verdict="confirmed",
        confidence=0.9,
        volatility_score=0.8,
        evidence_gap=0.2,
        sources=[
            Source(url="https://openai.com/pricing", snippet="Price is $20", authority_score=0.95, is_primary=True, published=now_date),
            Source(url="https://reuters.com/openai", snippet="OpenAI costs $20", authority_score=0.85, published=now_date),
            Source(url="https://bloomberg.com/chatgpt", snippet="Subscription $20", authority_score=0.85, published=now_date),
        ],
    )
    dimensions = compute_score_dimensions(claim)
    assert "temporal_decay" in dimensions
    assert "predicted_flip_probability" in dimensions
    assert "recommended_check_interval" in dimensions
    assert dimensions["final_score"] >= 80


# --- Phase 2: Multi-Source Consensus & Authority Tests ---

def test_source_validator_cross_source_consensus():
    validator = SourceValidator()
    sources = [
        validator.evaluate_source("https://python.org/downloads", "Python 3.13 released", "version", source_type="web"),
        validator.evaluate_source("https://github.com/python/cpython", "CPython repository", "version", source_type="api"),
        validator.evaluate_source("https://arxiv.org/abs/1234", "Paper on Python", "version", source_type="academic"),
    ]
    consensus = validator.calculate_cross_source_consensus(sources)
    assert consensus["independent_count"] == 3
    assert consensus["consensus_score"] >= 0.8
    assert "api" in consensus["source_types"]
    assert "academic" in consensus["source_types"]


# --- Phase 3: Future Claims & File Text Extraction Tests ---

def test_future_claim_pattern_detection():
    assert detect_future_claim("Python 3.14 will release in October 2026") is True
    assert detect_future_claim("OpenAI announced GPT-5 is scheduled for Q3 2025") is True
    assert detect_future_claim("Python 2.7 reached end of life in 2020") is False


def test_file_text_extraction():
    # Plain text
    txt_bytes = b"This is a factual statement about Python 3.12."
    assert "Python 3.12" in extract_text_from_file_bytes(txt_bytes, "doc.txt")

    # JSON file
    json_bytes = b'{"claim": "Google launched Gemini 1.5 in 2024"}'
    assert "Gemini 1.5" in extract_text_from_file_bytes(json_bytes, "data.json")


# --- Phase 4: Predictive Monitoring Tests ---

def test_calculate_flip_probability_and_interval():
    high_vol_claim = Claim(
        id="c1",
        text="Bitcoin price is $95000",
        type="financial",
        volatility_score=0.95,
        evidence_gap=0.8,
        freshness_half_life_seconds=3600,
        degradation_curve="exponential",
        last_verified_at=time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - 7200)),
    )
    prob = calculate_flip_probability(high_vol_claim)
    assert prob > 0.3
    interval = calculate_recommended_interval(high_vol_claim, prob)
    assert interval <= 3600  # Should recommend frequent checks


# --- Phase 5: Export Functionality Tests ---

def test_export_executive_summary_and_knowledge_base(workspace_tmp_path, auth_cookie):
    from core.persistence import SQLiteFeatureStore
    feature_store = SQLiteFeatureStore(str(workspace_tmp_path / "test_features.sqlite3"))

    claim = Claim(
        id="c-export",
        text="Release A & B <preview>",
        type="version",
        verdict="confirmed",
        score=95,
        confidence=0.9,
        sources=[Source(url="https://anthropic.com", snippet="Claude 3.5 Sonnet is now available")],
    )
    save_claim_snapshot(claim, store=feature_store)

    # Executive summary
    summary = export_executive_summary(claim.claim_fingerprint, store=feature_store)
    assert summary["total_verifications"] >= 1
    assert summary["average_score"] == 95
    assert summary["verdict_distribution"]["confirmed"] == 1

    # Temporal trends
    trends = export_temporal_trends(claim.claim_fingerprint, store=feature_store)
    assert len(trends["decay_data"]) >= 1

    # Knowledge base export
    kb_json = export_knowledge_base(claim.claim_fingerprint, format="json", store=feature_store)
    assert kb_json["current_verdict"] == "confirmed"
    assert kb_json["current_score"] == 95

    kb_xml = export_knowledge_base(claim.claim_fingerprint, format="xml", store=feature_store)
    assert "<knowledge_base>" in kb_xml["xml"]
    assert "confirmed" in kb_xml["xml"]

    kb_csv = export_knowledge_base(claim.claim_fingerprint, format="csv", store=feature_store)
    assert "field,value" in kb_csv["csv"]

    from xml.etree import ElementTree
    with patch("server.routes.export.get_feature_store", return_value=feature_store):
        client = TestClient(app, cookies=auth_cookie)
        url = f"/api/export/claim/{claim.claim_fingerprint}"
        xml = client.get(f"{url}/knowledge-base?format=xml&download=true")
        assert xml.headers["content-type"].startswith("application/xml")
        assert ElementTree.fromstring(xml.content).findtext("claim_text") == claim.text
        csv = client.get(f"{url}/knowledge-base?format=csv&download=true")
        assert csv.content.decode() == kb_csv["csv"]
        html = client.get(f"{url}/full-report?format=html&download=true")
        assert html.headers["content-type"].startswith("text/html")
        assert "Release A &amp; B &lt;preview&gt;" in html.text



# --- Phase 6: Email Service Template Tests ---

@pytest.mark.asyncio
async def test_email_service_formatting():
    config = FreshGateConfig(
        smtp_host="localhost",
        smtp_port=587,
        smtp_user="user@test.com",
        smtp_password="password",
        smtp_from="alerts@freshgate.ai",
        admin_email="admin@freshgate.ai",
    )
    service = EmailService(config)
    assert service._is_configured() is True

    # Test report creation does not crash
    claims = [
        Claim(id="c1", text="Version check", type="version", verdict="confirmed", score=90),
        Claim(id="c2", text="Price check", type="pricing", verdict="contradicted", score=10),
    ]
    alerts = [{"title": "Price Alert", "message": "Price changed"}]

    # Should not raise exception even without active SMTP server
    sent = await service.send_watchtower_report(["test@test.com"], claims, alerts)
    # sent will be False since localhost is not running, but method executes cleanly
    assert isinstance(sent, bool)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("port", "expected_use_tls", "expected_start_tls"),
    [(587, False, True), (465, True, False)],
)
async def test_email_service_uses_tls_mode_for_standard_smtp_ports(
    port, expected_use_tls, expected_start_tls, monkeypatch
):
    import core.email as email_module

    send = AsyncMock()
    monkeypatch.setattr(email_module, "HAS_AIOSMTPLIB", True)
    monkeypatch.setattr(email_module, "aiosmtplib", SimpleNamespace(send=send))
    config = FreshGateConfig(
        smtp_host="smtp.example.com",
        smtp_port=port,
        smtp_user="sender@example.com",
        smtp_password="app-password",
        smtp_from="sender@example.com",
        smtp_tls=True,
    )

    assert await EmailService(config).send_test_email(["recipient@example.com"])
    assert send.await_args.kwargs["use_tls"] is expected_use_tls
    assert send.await_args.kwargs["start_tls"] is expected_start_tls


@pytest.mark.asyncio
async def test_watchtower_custom_time_period_email_schedule(monkeypatch):
    from server.routes.watchtower import set_email_schedule, get_email_schedule, EmailScheduleRequest
    from starlette.requests import Request
    from core.persistence import SQLiteFeatureStore
    import tempfile
    import os

    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "test_feat.db")
        store = SQLiteFeatureStore(db_path)
        monkeypatch.setattr("core.persistence.get_feature_store", lambda: store)

        user = {"_id": "test-user-123", "email": "user@example.com"}
        monkeypatch.setattr("server.routes.watchtower.current_user", lambda r: user)
        scope = {
            "type": "http",
            "state": {"user": user, "feature_store": store},
            "headers": [],
        }
        req = Request(scope)

        # 1. Configure custom interval: 4 hours
        req_data = EmailScheduleRequest(
            enabled=True,
            custom_amount=4,
            custom_unit="hours",
            recipient_email="alerts@custom.org",
        )
        res = await set_email_schedule(req_data, req)
        assert res["status"] == "configured"
        assert res["schedule"]["custom_amount"] == 4
        assert res["schedule"]["custom_unit"] == "hours"
        assert res["schedule"]["interval_seconds"] == 14400
        assert res["schedule"]["recipient_email"] == "alerts@custom.org"

        # 2. Retrieve schedule
        get_res = await get_email_schedule(req)
        assert get_res["schedule"]["custom_amount"] == 4
        assert get_res["schedule"]["interval_seconds"] == 14400

        # 3. Test WatchtowerService.run_due_email_reports
        from core.watchtower import WatchtowerService
        config = FreshGateConfig(
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_user="sender@example.com",
            smtp_password="app-password",
            smtp_from="sender@example.com",
            smtp_tls=True,
            admin_email="admin@example.com",
        )
        ws = WatchtowerService(store=store, config=config)
        # Mock send_scheduled_report
        send_mock = AsyncMock(return_value=True)
        monkeypatch.setattr(ws, "send_scheduled_report", send_mock)

        # Not due yet because next_run_at was set into the future
        sent = await ws.run_due_email_reports()
        assert sent == []
        assert send_mock.call_count == 0

        # Force due by moving next_run_at into the past
        meta = store.get_user_metadata("test-user-123")
        meta["email_schedule"]["next_run_at"] = time.time() - 10
        store.update_user_metadata("test-user-123", meta)

        # Now it should be due and sent
        sent = await ws.run_due_email_reports()
        assert "alerts@custom.org" in sent
        assert send_mock.call_count == 1
        assert send_mock.await_args.kwargs["recipient_email"] == "alerts@custom.org"
        assert send_mock.await_args.kwargs["owner_id"] == "test-user-123"

        # Updated next_run_at in store should now be in the future
        meta_after = store.get_user_metadata("test-user-123")
        assert meta_after["email_schedule"]["last_sent_at"] is not None
        assert meta_after["email_schedule"]["next_run_at"] > time.time()

        # 4. Stop schedule
        from server.routes.watchtower import stop_email_schedule
        stop_res = await stop_email_schedule(req)
        assert stop_res["status"] == "stopped"
        assert stop_res["schedule"]["enabled"] is False

        # Now when due again, it should NOT send because enabled is False
        meta_stopped = store.get_user_metadata("test-user-123")
        meta_stopped["email_schedule"]["next_run_at"] = time.time() - 10
        store.update_user_metadata("test-user-123", meta_stopped)
        send_mock.reset_mock()
        sent_stopped = await ws.run_due_email_reports()
        assert sent_stopped == []
        assert send_mock.call_count == 0


@pytest.mark.asyncio
async def test_email_service_resend_api_delivery(monkeypatch):
    """Test HTTPS email delivery via Resend API (used on Render where SMTP is blocked)."""
    config = FreshGateConfig(
        resend_api_key="re_test_key_12345",
        smtp_from="alerts@mytestdomain.com",
    )
    svc = EmailService(config)
    assert svc._is_configured() is True

    posted_url = None
    posted_headers = None
    posted_json = None

    class MockResponse:
        is_success = True
        status_code = 200
        text = '{"id": "msg_123"}'

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, url, headers=None, json=None):
            nonlocal posted_url, posted_headers, posted_json
            posted_url = url
            posted_headers = headers
            posted_json = json
            return MockResponse()

    monkeypatch.setattr("httpx.AsyncClient", MockAsyncClient)

    success = await svc.send_html(
        to=["recipient@example.com"],
        subject="Watchtower Digest",
        html="<p>Test digest</p>",
    )
    assert success is True
    assert posted_url == "https://api.resend.com/emails"
    assert posted_headers["Authorization"] == "Bearer re_test_key_12345"
    assert posted_json["to"] == ["recipient@example.com"]
    assert posted_json["from"] == "alerts@mytestdomain.com"


@pytest.mark.asyncio
async def test_email_service_brevo_api_delivery(monkeypatch):
    """Test HTTPS email delivery via Brevo REST API (no custom domain required)."""
    config = FreshGateConfig(
        brevo_api_key="xkeysib-test-12345",
        smtp_from="freshgate67@gmail.com",
    )
    svc = EmailService(config)
    assert svc._is_configured() is True

    posted_url = None
    posted_headers = None
    posted_json = None

    class MockResponse:
        is_success = True
        status_code = 201
        text = '{"messageId": "<2026.123@smtp-relay.mailin.fr>"}'

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, url, headers=None, json=None):
            nonlocal posted_url, posted_headers, posted_json
            posted_url = url
            posted_headers = headers
            posted_json = json
            return MockResponse()

    monkeypatch.setattr("httpx.AsyncClient", MockAsyncClient)

    success = await svc.send_html(
        to=["recipient@example.com"],
        subject="Watchtower Report via Brevo",
        html="<p>Test digest</p>",
    )
    assert success is True
    assert posted_url == "https://api.brevo.com/v3/smtp/email"
    assert posted_headers["api-key"] == "xkeysib-test-12345"
    assert posted_json["sender"]["name"] == "FreshGate Watchtower"
    assert posted_json["sender"]["email"] == "freshgate67@gmail.com"
    assert posted_json["to"][0]["email"] == "recipient@example.com"

    # Also test formatted sender name "FreshGate <freshgate67@gmail.com>"
    svc.config.smtp_from = "FreshGate Alerts <freshgate67@gmail.com>"
    await svc.send_html(
        to=["recipient2@example.com"],
        subject="Report 2",
        html="<p>Test</p>",
    )
    assert posted_json["sender"]["name"] == "FreshGate Alerts"
    assert posted_json["sender"]["email"] == "freshgate67@gmail.com"


@pytest.mark.asyncio
async def test_watchtower_scheduled_report_zero_claims(monkeypatch):
    """Verify that send_scheduled_report delivers a clean status digest even when no claims are monitored."""
    from core.watchtower import WatchtowerService
    from core.persistence import SQLiteFeatureStore
    import tempfile
    import os

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        tmp_db = f.name
    try:
        store = SQLiteFeatureStore(tmp_db)
        config = FreshGateConfig(
            brevo_api_key="xkeysib-test",
            admin_email="aarush.relhan3009@gmail.com",
            smtp_from="freshgate67@gmail.com",
        )
        ws = WatchtowerService(store=store, config=config)
        email_svc = await ws._get_email_service()

        mock_send = AsyncMock(return_value=True)
        monkeypatch.setattr(email_svc, "send_watchtower_report", mock_send)

        sent = await ws.send_scheduled_report(owner_id="test-empty-owner")
        assert sent is True
        assert mock_send.call_count == 1
        assert mock_send.await_args.kwargs["claims"] == []
        assert mock_send.await_args.kwargs["alerts"] == []
        assert mock_send.await_args.kwargs["to"] == ["aarush.relhan3009@gmail.com"]
    finally:
        if os.path.exists(tmp_db):
            os.unlink(tmp_db)


@pytest.mark.asyncio
async def test_email_service_render_smtp_timeout_diagnosis(monkeypatch):
    """Verify that SMTP timeouts on Render produce an actionable diagnostic error."""
    import asyncio
    monkeypatch.setenv("RENDER", "true")
    import core.email as email_module
    monkeypatch.setattr(email_module, "HAS_AIOSMTPLIB", True)

    async def mock_timeout_send(*args, **kwargs):
        raise asyncio.TimeoutError("Connection timed out connecting to smtp.gmail.com on port 587")

    monkeypatch.setattr(email_module, "aiosmtplib", SimpleNamespace(send=mock_timeout_send))

    config = FreshGateConfig(
        smtp_host="smtp.gmail.com",
        smtp_port=587,
        smtp_user="user@gmail.com",
        smtp_password="pwd",
        smtp_from="user@gmail.com",
    )
    svc = EmailService(config)
    success, err_msg = await svc.send_test_email_with_error(["test@example.com"])
    assert success is False
    assert "Render Free tier blocks outbound SMTP ports" in err_msg
    assert "RESEND_API_KEY" in err_msg


# --- Phase 7: File Upload API Tests ---

def test_validate_upload_limits():
    valid, _ = validate_upload("report.pdf", 1024)
    assert valid is True

    valid_md, _ = validate_upload("notes.md", 500)
    assert valid_md is True

    invalid_ext, err = validate_upload("malware.exe", 1024)
    assert invalid_ext is False
    assert "Unsupported file type" in err

    too_large, err = validate_upload("large.pdf", 20 * 1024 * 1024)
    assert too_large is False
    assert "exceeds maximum limit" in err


def test_verify_upload_json_endpoint(auth_cookie):
    client = TestClient(app, cookies=auth_cookie)
    payload = {
        "answer": "Python 3.12 is the latest release.",
        "filename": "claims.txt",
        "file_content": "ChatGPT Plus subscription is $20/month.",
    }
    response = client.post("/verify/upload", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "claims" in data
    assert "summary" in data
