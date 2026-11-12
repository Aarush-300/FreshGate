from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from core.models import Claim, ExtractionResult, Source, Summary, VerificationResult
from core.persistence import SQLiteFeatureStore
from core.pipeline import FreshGatePipeline
from core.policies import create_policy, source_is_eligible
from core.policy_models import PolicyConfig
from core.verifier import ClaimVerifier
from server.app import app


@pytest.fixture
def policy_store(monkeypatch):
    db_path = Path(__file__).resolve().parents[2] / f".freshgate-policy-test-{uuid.uuid4().hex}.sqlite3"
    store = SQLiteFeatureStore(str(db_path))
    monkeypatch.setattr("core.policies.get_feature_store", lambda: store)
    monkeypatch.setattr("server.routes.policies.get_feature_store", lambda: store)
    try:
        yield store
    finally:
        for path in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
            path.unlink(missing_ok=True)


def valid_policy(**overrides):
    values = {
        "name": "Official software versions",
        "description": "Use official documentation",
        "categories": ["version", "software_version"],
        "allowed_domains": ["python.org"],
        "preferred_domains": ["docs.python.org"],
        "minimum_source_count": 1,
        "minimum_source_authority": 0.8,
        "minimum_freshness_score": 70,
        "category_minimum_freshness_scores": {"version": 80},
        "category_maximum_evidence_age_days": {"version": 30},
        "cache_ttl_seconds": {"version": 1800},
        "verification_mode": "enforce",
    }
    values.update(overrides)
    return values


def test_policy_config_normalizes_supported_categories_and_domains():
    policy = PolicyConfig.model_validate(valid_policy())
    assert policy.categories == ["version"]
    assert policy.allowed_domains == ["python.org"]
    assert policy.preferred_domains == ["docs.python.org"]
    assert policy.settings()["category_minimum_freshness_scores"] == {"version": 80}


@pytest.mark.parametrize("overrides", [
    {"categories": ["unknown_category"]},
    {"allowed_domains": ["https://example.com/path"]},
    {"allowed_domains": ["not a domain"]},
    {"allowed_domains": ["example.com"], "blocked_domains": ["example.com"]},
    {"minimum_freshness_score": 101},
])
def test_policy_config_rejects_invalid_settings(overrides):
    with pytest.raises(ValueError):
        PolicyConfig.model_validate(valid_policy(**overrides))


def test_policy_source_filter_matches_domain_boundaries():
    policy = PolicyConfig.model_validate(valid_policy())
    assert source_is_eligible(Source(url="https://docs.python.org/3/", snippet="docs", authority_score=0.95, published="2026-11-10"), policy, "version")
    assert not source_is_eligible(Source(url="https://python.org.attacker.test/", snippet="fake", authority_score=0.95, published="2026-11-10"), policy, "version")
    assert not source_is_eligible(Source(url="https://python.org/", snippet="weak", authority_score=0.2, published="2026-11-10"), policy, "version")
    blocked = PolicyConfig.model_validate(valid_policy(blocked_domains=["attacker.test"]))
    assert not source_is_eligible(Source(url="https://attacker.test/releases", snippet="blocked", authority_score=0.99, published="2026-11-10"), blocked, "version")


@pytest.mark.asyncio
async def test_policy_crud_preview_validate_and_duplicate_endpoints(policy_store):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        categories = await client.get("/api/policies/categories")
        assert categories.status_code == 200
        assert {item["name"] for item in categories.json()["categories"]} >= {"pricing", "version", "law"}

        created = await client.post("/api/policies", json=valid_policy())
        assert created.status_code == 201, created.text
        policy = created.json()

        listing = await client.get("/api/policies")
        assert [item["id"] for item in listing.json()["policies"]] == [policy["id"]]
        assert (await client.get(f"/api/policies/{policy['id']}")).json()["name"] == policy["name"]

        validation = await client.post(f"/api/policies/{policy['id']}/validate", json=valid_policy(
            preferred_domains=["docs.python.org"], allowed_domains=["example.com"]
        ))
        assert validation.status_code == 200
        assert validation.json()["warnings"][0]["code"] == "preferred_domain_not_allowlisted"

        preview = await client.post(f"/api/policies/{policy['id']}/preview", json={"claims": [
            {"id": "ok", "text": "A current release", "category": "version", "freshness_score": 92,
             "sources": [{"url": "https://docs.python.org/3/", "snippet": "Current docs", "published": "2026-11-10", "authority_score": 0.95}]},
            {"id": "blocked", "text": "An unsupported source", "category": "version", "freshness_score": 40,
             "sources": [{"url": "https://attacker.test/", "snippet": "Untrusted", "published": "2026-11-10", "authority_score": 0.95}]},
        ]})
        assert preview.status_code == 200, preview.text
        assert preview.json()["decision"] == "rejected"
        assert [item["decision"] for item in preview.json()["claims"]] == ["pass", "policy_failed"]

        duplicate = await client.post(f"/api/policies/{policy['id']}/duplicate")
        assert duplicate.status_code == 201
        assert duplicate.json()["name"] == f"{policy['name']} copy"

        update = await client.put(f"/api/policies/{policy['id']}", json=valid_policy(name="Updated policy", enabled=False))
        assert update.status_code == 200
        assert update.json()["name"] == "Updated policy"
        assert update.json()["enabled"] is False
        assert (await client.delete(f"/api/policies/{policy['id']}")).status_code == 204
        assert (await client.get(f"/api/policies/{policy['id']}")).status_code == 404


@pytest.mark.asyncio
async def test_policy_reads_are_concurrent_and_default_pipeline_has_no_policy_result(policy_store):
    policy = create_policy(valid_policy(), store=policy_store)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        responses = await asyncio.gather(*[
            client.get(f"/api/policies/{policy.id}") for _ in range(12)
        ])
    assert all(response.status_code == 200 for response in responses)
    assert all(response.json()["id"] == policy.id for response in responses)

    pipeline = FreshGatePipeline(extractor=StaticExtractor(), verifier=ClaimVerifier(
        tavily_client=PolicySearch(), judge_llm=ConfirmJudge()
    ), cache=EmptyCache())
    result = await pipeline.verify("Python 3.13 is the latest release")
    assert result.policy_evaluation is None


@pytest.mark.asyncio
async def test_verify_endpoint_passes_selected_policy_to_pipeline(policy_store, monkeypatch):
    policy = create_policy(valid_policy(), store=policy_store)

    class ScopedPipeline:
        def __init__(self):
            self.verifier = SimpleNamespace(tavily_client=SimpleNamespace(aclose=AsyncMock()))
            self.verify = AsyncMock(return_value=VerificationResult(
                claims=[], summary=Summary(total=0, checked=0, stale=0), policy_evaluation={"accepted": True}
            ))

    scoped_pipeline = ScopedPipeline()
    monkeypatch.setattr("server.app.build_request_pipeline", lambda *_args: scoped_pipeline)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/verify",
            headers={"X-OpenAI-API-Key": "sk-test-valid-key", "X-Tavily-API-Key": "tvly-test-valid-key"},
            json={"answer": "A sample answer", "policy_id": policy.id},
        )

    assert response.status_code == 200, response.text
    assert scoped_pipeline.verify.await_args.kwargs["policy"].id == policy.id
    scoped_pipeline.verifier.tavily_client.aclose.assert_awaited_once()


class StaticExtractor:
    async def extract(self, _text):
        return ExtractionResult(claims=[Claim(
            id="c1", text="Python 3.13 is the latest release", type="version", entities=["Python"]
        )], status="success", total_extracted=1, valid_claims=1)


class EmptyCache:
    def get(self, *_args, **_kwargs):
        return None

    def set(self, *_args, **_kwargs):
        return None


class PolicySearch:
    def __init__(self):
        self.include_domains = None

    async def search(self, _query, max_results=5, include_domains=None):
        self.include_domains = include_domains
        return [{
            "url": "https://attacker.test/releases",
            "content": "Python 3.13 is current",
            "published_date": "2026-11-10",
        }]


class NeverJudge:
    def __init__(self):
        self.calls = 0

    async def complete(self, *_args, **_kwargs):
        self.calls += 1
        return '{"verdict":"confirmed","supporting_urls":[]}'


class ConfirmJudge(NeverJudge):
    async def complete(self, *_args, **_kwargs):
        self.calls += 1
        return '{"verdict":"confirmed","confidence":0.9,"supporting_urls":["https://attacker.test/releases"]}'


@pytest.mark.asyncio
async def test_enforcing_policy_filters_sources_before_judging_and_reports_decision():
    search = PolicySearch()
    judge = NeverJudge()
    verifier = ClaimVerifier(tavily_client=search, judge_llm=judge)
    pipeline = FreshGatePipeline(extractor=StaticExtractor(), verifier=verifier, cache=EmptyCache())
    policy = PolicyConfig.model_validate(valid_policy(preferred_domains=["python.org"]))

    result = await pipeline.verify("Python 3.13 is the latest release", policy=policy)

    assert search.include_domains == ["python.org"]
    assert judge.calls == 0
    assert result.claims[0].verdict == "unverifiable"
    assert result.policy_evaluation["decision"] == "rejected"
    assert result.policy_evaluation["accepted"] is False


@pytest.mark.asyncio
async def test_shadow_policy_reports_would_fail_without_filtering_evidence():
    search = PolicySearch()
    judge = ConfirmJudge()
    verifier = ClaimVerifier(tavily_client=search, judge_llm=judge)
    pipeline = FreshGatePipeline(extractor=StaticExtractor(), verifier=verifier, cache=EmptyCache())
    policy = PolicyConfig.model_validate(valid_policy(
        verification_mode="shadow", minimum_source_count=1, allowed_domains=["python.org"]
    ))

    result = await pipeline.verify("Python 3.13 is the latest release", policy=policy)

    assert search.include_domains is None
    assert result.claims[0].verdict == "confirmed"
    assert result.policy_evaluation["decision"] == "would_fail"
    assert result.policy_evaluation["accepted"] is True
