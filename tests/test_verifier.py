import os
import pytest
import asyncio
from unittest.mock import AsyncMock, patch

from core.models import Claim, Source
from core.llm import LLMClient
from core.verifier import ClaimVerifier, TavilySearchClient


class MockJudgeLLM(LLMClient):
    def __init__(self, response: str):
        self.response = response
        self.call_count = 0

    async def complete(self, prompt: str, system_prompt: str | None = None) -> str:
        self.call_count += 1
        return self.response


class MockTavilyClient:
    def __init__(self, results: list[dict] | None = None, should_fail: bool = False):
        self.results = results or []
        self.should_fail = should_fail
        self.call_count = 0

    async def search(self, query: str, max_results: int = 5) -> list[dict]:
        self.call_count += 1
        if self.should_fail:
            raise RuntimeError("Tavily API timeout or rate limit exceeded")
        return self.results


@pytest.mark.asyncio
async def test_verify_claim_confirmed():
    mock_sources = [
        {
            "url": "https://example.com/python312",
            "published_date": "2023-10-02",
            "content": "Python 3.12 was officially released in October 2023.",
        }
    ]
    judge_json = '{"verdict": "confirmed", "correction": null, "supporting_urls": ["https://example.com/python312"]}'

    tavily = MockTavilyClient(results=mock_sources)
    judge = MockJudgeLLM(judge_json)
    verifier = ClaimVerifier(tavily_client=tavily, judge_llm=judge)

    claim = Claim(id="c1", text="Python 3.12 was released in October 2023", type="version")
    verified = await verifier.verify_claim(claim)

    assert verified.verdict == "confirmed"
    assert len(verified.sources) == 1
    assert verified.sources[0].url == "https://example.com/python312"
    assert verified.sources[0].published == "2023-10-02"
    assert tavily.call_count == 1
    assert judge.call_count == 1


@pytest.mark.asyncio
async def test_verify_claim_contradicted_with_correction():
    mock_sources = [
        {
            "url": "https://openai.com/pricing",
            "published_date": "2026-01-15",
            "content": "GPT-4o input pricing is $2.50 per million tokens.",
        }
    ]
    judge_json = """{
        "verdict": "contradicted",
        "correction": "GPT-4o input pricing is now $2.50 per million tokens, not $30.",
        "supporting_urls": ["https://openai.com/pricing"]
    }"""

    tavily = MockTavilyClient(results=mock_sources)
    judge = MockJudgeLLM(judge_json)
    verifier = ClaimVerifier(tavily_client=tavily, judge_llm=judge)

    claim = Claim(id="c1", text="GPT-4 costs $30 per million input tokens", type="pricing")
    verified = await verifier.verify_claim(claim)

    assert verified.verdict == "contradicted"
    assert "2.50" in verified.correction
    assert len(verified.sources) == 1
    assert verified.sources[0].url == "https://openai.com/pricing"


@pytest.mark.asyncio
async def test_verify_claim_tavily_failure_returns_unverifiable():
    tavily = MockTavilyClient(should_fail=True)
    judge = MockJudgeLLM('{"verdict": "confirmed"}')
    verifier = ClaimVerifier(tavily_client=tavily, judge_llm=judge)

    claim = Claim(id="c1", text="Some obscure pricing claim", type="pricing")
    verified = await verifier.verify_claim(claim)

    # Must fail open with 'unverifiable' rather than throwing an exception
    assert verified.verdict == "unverifiable"
    assert verified.sources == []
    # Judge should not even need to be called on Tavily failure
    assert judge.call_count == 0


@pytest.mark.asyncio
async def test_verify_claims_concurrent_execution():
    mock_sources = [{"url": "https://example.com", "published_date": "2026-01-01", "content": "Evidence"}]
    judge_json = '{"verdict": "confirmed", "correction": null, "supporting_urls": ["https://example.com"]}'

    active_tasks = 0
    max_active_tasks = 0

    class TrackingTavilyClient:
        async def search(self, query: str, max_results: int = 5) -> list[dict]:
            nonlocal active_tasks, max_active_tasks
            active_tasks += 1
            max_active_tasks = max(max_active_tasks, active_tasks)
            await asyncio.sleep(0.05)
            active_tasks -= 1
            return mock_sources

    verifier = ClaimVerifier(tavily_client=TrackingTavilyClient(), judge_llm=MockJudgeLLM(judge_json), concurrency_cap=3)

    claims = [
        Claim(id=f"c{i}", text=f"Claim number {i}", type="pricing")
        for i in range(6)
    ]

    verified_claims = await verifier.verify_claims(claims)

    assert len(verified_claims) == 6
    for c in verified_claims:
        assert c.verdict == "confirmed"
    # Ensure concurrency cap of 3 was respected
    assert max_active_tasks <= 3


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_outdated_claim_contradicted():
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        pytest.skip("TAVILY_API_KEY not configured, skipping live test")

    verifier = ClaimVerifier()
    # Twitter CEO was changed from Jack Dorsey / Parag Agrawal to Linda Yaccarino
    claim = Claim(id="c1", text="Parag Agrawal is the current CEO of Twitter", type="leadership")
    verified = await verifier.verify_claim(claim)

    assert verified.verdict in ("contradicted", "unverifiable")
    assert len(verified.sources) > 0
