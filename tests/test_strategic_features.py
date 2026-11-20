"""Strategic Feature Enhancement Unit and Integration Tests (Phases 1-7)."""

import pytest
import time
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

def test_export_executive_summary_and_knowledge_base(workspace_tmp_path):
    from core.persistence import SQLiteFeatureStore
    feature_store = SQLiteFeatureStore(str(workspace_tmp_path / "test_features.sqlite3"))

    claim = Claim(
        id="c-export",
        text="Claude 3.5 Sonnet released by Anthropic",
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
