import time
import os
import pytest
from unittest.mock import AsyncMock

from core.models import Claim, Source, VerificationResult
from core.scorer import calculate_score, compute_verdict_weight, compute_recency_factor, compute_agreement_factor
from core.cache import SQLiteClaimCache
from core.pipeline import FreshGatePipeline
from core.extractor import ClaimExtractor
from core.verifier import ClaimVerifier
from core.llm import LLMClient


class MockLLM(LLMClient):
    def __init__(self, response: str):
        self.response = response
        self.call_count = 0

    async def complete(self, prompt: str, system_prompt: str | None = None) -> str:
        self.call_count += 1
        return self.response


class MockTavily:
    def __init__(self, results: list[dict]):
        self.results = results
        self.call_count = 0

    async def search(self, query: str, max_results: int = 5) -> list[dict]:
        self.call_count += 1
        return self.results


# --- Scorer Tests ---

def test_verdict_weights():
    assert compute_verdict_weight("confirmed") == 1.0
    assert compute_verdict_weight("unverifiable") == 0.5
    assert compute_verdict_weight("contradicted") == 0.1
    assert compute_verdict_weight("unknown") == 0.5


def test_recency_factor():
    now = time.time()
    # Less than 30 days old (e.g. 10 days ago)
    date_10_days = time.strftime("%Y-%m-%d", time.gmtime(now - 10 * 86400))
    sources_recent = [Source(url="https://a.com", published=date_10_days, snippet="s")]
    assert compute_recency_factor(sources_recent) == 1.0

    # Over 365 days old (e.g. 400 days ago) -> floor 0.5
    date_400_days = time.strftime("%Y-%m-%d", time.gmtime(now - 400 * 86400))
    sources_old = [Source(url="https://a.com", published=date_400_days, snippet="s")]
    assert compute_recency_factor(sources_old) == 0.5

    # Missing or unparseable date -> 0.5
    sources_no_date = [Source(url="https://a.com", published=None, snippet="s")]
    assert compute_recency_factor(sources_no_date) == 0.5


def test_agreement_factor():
    assert compute_agreement_factor(3) == 1.0
    assert compute_agreement_factor(4) == 1.0
    assert abs(compute_agreement_factor(2) - (2 / 3)) < 0.01
    # 1 source has floor of 0.4 (since 1/3 = 0.333 < 0.4)
    assert compute_agreement_factor(1) == 0.4
    # 0 sources has floor of 0.4
    assert compute_agreement_factor(0) == 0.4


def test_contradicted_claim_old_source_score_under_15():
    # Acceptance test: "Score of a contradicted claim with one old source is under 15"
    now = time.time()
    date_400_days = time.strftime("%Y-%m-%d", time.gmtime(now - 400 * 86400))
    claim = Claim(
        id="c1",
        text="GPT-4 costs $30",
        type="pricing",
        verdict="contradicted",
        sources=[Source(url="https://old.com", published=date_400_days, snippet="old price")],
    )
    score = calculate_score(claim)
    assert score < 15
    # Calculation: 100 * 0.1 * 0.5 * 0.4 = 2
    assert score == 2


# --- Cache Tests ---

def test_cache_set_and_get(workspace_tmp_path):
    db_file = str(workspace_tmp_path / "test_cache.db")
    cache = SQLiteClaimCache(db_path=db_file)

    claim = Claim(
        id="c1",
        text="GPT-4 costs $30 per million tokens",
        type="pricing",
        verdict="contradicted",
        score=10,
        sources=[Source(url="https://openai.com", published="2026-01-01", snippet="current")],
        correction="Current price is $2.50",
    )

    cache.set(claim)

    cached_claim = cache.get("GPT-4 costs $30 per million tokens", "pricing")
    assert cached_claim is not None
    assert cached_claim.cached is True
    assert cached_claim.verdict == "contradicted"
    assert cached_claim.score == 10
    assert cached_claim.correction == "Current price is $2.50"
    assert len(cached_claim.sources) == 1


def test_cache_expiration(workspace_tmp_path):
    db_file = str(workspace_tmp_path / "test_cache.db")
    cache = SQLiteClaimCache(db_path=db_file)

    claim = Claim(
        id="c1",
        text="Expiring claim text",
        type="pricing",
        verdict="confirmed",
        score=90,
    )

    # Set with custom ttl_seconds = -1 (already expired)
    cache.set(claim, ttl_seconds=-1)

    result = cache.get("Expiring claim text", "pricing")
    assert result is None


# --- Pipeline & Contract Schema Tests ---

@pytest.mark.asyncio
async def test_pipeline_cached_second_call_zero_tavily(workspace_tmp_path):
    # Acceptance test: Same claim twice: second call has cached: true and makes zero Tavily calls
    db_file = str(workspace_tmp_path / "pipeline_cache.db")
    cache = SQLiteClaimCache(db_path=db_file)

    extractor_json = '[{"id": "c1", "text": "Parag Agrawal is CEO of Twitter", "type": "leadership"}]'
    judge_json = '{"verdict": "contradicted", "correction": "Linda Yaccarino is CEO", "supporting_urls": ["https://x.com"]}'
    mock_search = [{"url": "https://x.com", "published_date": "2026-01-01", "content": "CEO is Linda"}]

    mock_llm_extractor = MockLLM(extractor_json)
    mock_llm_judge = MockLLM(judge_json)
    mock_tavily = MockTavily(mock_search)

    extractor = ClaimExtractor(llm_client=mock_llm_extractor)
    verifier = ClaimVerifier(tavily_client=mock_tavily, judge_llm=mock_llm_judge)
    pipeline = FreshGatePipeline(extractor=extractor, verifier=verifier, cache=cache)

    answer = "Parag Agrawal is CEO of Twitter."

    # First call - cache miss
    result1 = await pipeline.verify(answer)
    assert len(result1.claims) == 1
    assert result1.claims[0].cached is False
    assert result1.claims[0].verdict == "contradicted"
    assert mock_tavily.call_count == 1

    # Second call - cache hit!
    # Mock extractor returns same claim
    mock_llm_extractor.response = extractor_json
    result2 = await pipeline.verify(answer)
    assert len(result2.claims) == 1
    assert result2.claims[0].cached is True
    assert result2.claims[0].verdict == "contradicted"
    # Tavily must NOT be called again
    assert mock_tavily.call_count == 1


@pytest.mark.asyncio
async def test_pipeline_contract_json_schema(workspace_tmp_path):
    db_file = str(workspace_tmp_path / "schema_cache.db")
    cache = SQLiteClaimCache(db_path=db_file)

    extractor_json = """[
        {"id": "c1", "text": "GPT-4 costs $30 per million input tokens", "type": "pricing"},
        {"id": "c2", "text": "Python was created by Guido van Rossum", "type": "other"}
    ]"""
    judge_json = '{"verdict": "contradicted", "correction": "Current price is $2.50", "supporting_urls": ["https://openai.com"]}'
    mock_search = [{"url": "https://openai.com", "published_date": "2026-01-01", "content": "GPT-4o is $2.50"}]

    pipeline = FreshGatePipeline(
        extractor=ClaimExtractor(llm_client=MockLLM(extractor_json)),
        verifier=ClaimVerifier(tavily_client=MockTavily(mock_search), judge_llm=MockLLM(judge_json)),
        cache=cache,
    )

    result = await pipeline.verify("GPT-4 costs $30 per million input tokens. Python was created by Guido.")
    data = result.model_dump()

    # Validate target JSON structure per SPEC.md
    assert "claims" in data
    assert "summary" in data
    assert isinstance(data["claims"], list)
    assert len(data["claims"]) == 1  # "other" dropped by triage
    c = data["claims"][0]
    assert c["id"] == "c1"
    assert c["type"] == "pricing"
    assert c["verdict"] == "contradicted"
    assert isinstance(c["score"], int)
    assert isinstance(c["sources"], list)
    assert isinstance(c["cached"], bool)
    assert "correction" in c

    s = data["summary"]
    assert s["total"] == 2
    assert s["checked"] == 1
    assert s["stale"] == 1
    assert isinstance(s["avg_score"], int)
