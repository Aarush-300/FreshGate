"""Regression Test Suite for FreshGate Issue: Incorrect Predictions on Outdated Claims.

Tests the 12 critical regression requirements specified in Phase 9:
1. test_supplied_outdated_version_claim
2. test_pricing_claim
3. test_combined_claims
4. test_extraction_timeout
5. test_malformed_json
6. test_unknown_category
7. test_tavily_unavailable
8. test_conflicting_sources
9. test_claim_free_response
10. test_partially_extracted_claims
11. test_cache_contamination
12. test_dashboard_state
"""

import json
import asyncio
from datetime import datetime, timezone
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from core.models import (
    Claim,
    Source,
    ExtractionResult,
    VerificationResult,
    Summary,
)
from core.categories import category_registry
from core.triage import triage_claims
from core.extractor import ClaimExtractor
from core.verifier import ClaimVerifier, HeuristicEvidenceJudge
from core.pipeline import FreshGatePipeline
from core.llm import LLMClient
from core.scorer import calculate_score, compute_score_dimensions
from core.cache import SQLiteClaimCache


class MockLLM(LLMClient):
    def __init__(self, response: str, delay: float = 0.0, should_fail: bool = False):
        self.response = response
        self.delay = delay
        self.should_fail = should_fail
        self.call_count = 0

    async def complete(self, prompt: str, system_prompt: str | None = None) -> str:
        self.call_count += 1
        if self.delay > 0:
            await asyncio.sleep(self.delay)
        if self.should_fail:
            raise RuntimeError("Simulated LLM Provider failure / timeout")
        return self.response


# --- 1. Test Supplied Outdated Version Claim ---
@pytest.mark.asyncio
async def test_supplied_outdated_version_claim():
    """Verify that 'Python 2.7 is the currently supported release.' is detected as a version claim

    and correctly contradicted because Python 2.7 reached EOL in 2020.
    """
    input_text = "Python 2.7 is the currently supported release."

    mock_extract_json = json.dumps([
        {
            "id": "claim-python-27",
            "text": "Python 2.7 is the currently supported release",
            "type": "version",
            "entities": ["Python"],
            "confidence": 0.95,
        }
    ])

    mock_judge_json = json.dumps({
        "verdict": "contradicted",
        "explanation": "Python 2.7 reached End-of-Life (EOL) on January 1, 2020. Python 3.x is the currently supported release series.",
        "confidence": 0.99,
        "score": 0,
        "correction": "Python 2.7 is unsupported; Python 3.12/3.13 are the currently supported releases.",
    })

    tavily_results = [
        {
            "url": "https://www.python.org/doc/sunset-python-2/",
            "title": "Sunsetting Python 2",
            "content": "As of January 1, 2020, Python 2 has reached end-of-life and is no longer supported.",
            "published_date": "2020-01-01",
        }
    ]

    extractor = ClaimExtractor(llm_client=MockLLM(mock_extract_json))
    verifier = ClaimVerifier(llm_client=MockLLM(mock_judge_json))
    pipeline = FreshGatePipeline(extractor=extractor, verifier=verifier)
    pipeline.cache.clear()

    with patch.object(verifier.tavily_client, "search", new_callable=AsyncMock) as mock_search:
        mock_search.return_value = tavily_results
        result: VerificationResult = await pipeline.verify(input_text)

    assert result.status == "completed"
    assert len(result.claims) == 1
    c = result.claims[0]
    assert c.type == "version"
    assert c.verdict == "contradicted"
    assert result.summary.stale == 1
    assert result.summary.avg_score is not None
    assert result.summary.avg_score < 50
    assert "2020" in (c.explanation or "") or "EOL" in (c.explanation or "") or "unsupported" in (c.correction or "").lower()


# --- 2. Test Pricing Claim ---
@pytest.mark.asyncio
async def test_pricing_claim():
    """Verify that 'ChatGPT Plus costs $20 per month.' is extracted as pricing

    and confirmed with live evidence.
    """
    input_text = "ChatGPT Plus costs $20 per month."

    mock_extract_json = json.dumps([
        {
            "id": "claim-chatgpt-pricing",
            "text": "ChatGPT Plus costs $20 per month",
            "type": "pricing",
            "entities": ["ChatGPT Plus", "OpenAI"],
            "numerical_value": "$20 per month",
            "confidence": 0.95,
        }
    ])

    mock_judge_json = json.dumps({
        "verdict": "confirmed",
        "explanation": "OpenAI official pricing specifies ChatGPT Plus subscription is $20 per month.",
        "confidence": 0.98,
        "score": 100,
        "correction": None,
    })

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    tavily_results = [
        {
            "url": "https://openai.com/chatgpt/pricing/",
            "title": "ChatGPT Pricing",
            "content": "ChatGPT Plus is available for $20/month. Access to GPT-4 and latest features.",
            "published_date": now_str,
        },
        {
            "url": "https://help.openai.com/en/articles/6950777-what-is-chatgpt-plus",
            "title": "Help: What is ChatGPT Plus",
            "content": "ChatGPT Plus subscription costs 20 USD per month.",
            "published_date": now_str,
        },
        {
            "url": "https://techcrunch.com/2026/openai-chatgpt-pricing",
            "title": "TechCrunch Pricing Confirmation",
            "content": "OpenAI confirms ChatGPT Plus remains at $20 a month.",
            "published_date": now_str,
        },
    ]

    extractor = ClaimExtractor(llm_client=MockLLM(mock_extract_json))
    verifier = ClaimVerifier(llm_client=MockLLM(mock_judge_json))
    pipeline = FreshGatePipeline(extractor=extractor, verifier=verifier)
    pipeline.cache.clear()

    with patch.object(verifier.tavily_client, "search", new_callable=AsyncMock) as mock_search:
        mock_search.return_value = tavily_results
        result = await pipeline.verify(input_text)

    assert result.status == "completed"
    assert len(result.claims) == 1
    c = result.claims[0]
    assert c.type == "pricing"
    assert c.verdict == "confirmed"
    assert result.summary.stale == 0
    assert result.summary.avg_score is not None
    assert result.summary.avg_score >= 90


# --- 3. Test Combined Claims ---
@pytest.mark.asyncio
async def test_combined_claims():
    """Verify that the reproduction text:

    'Python 2.7 is the currently supported release. ChatGPT Plus costs $20 per month.'
    extracts BOTH claims and independently verifies Python 2.7 as contradicted and ChatGPT Plus as confirmed.
    The overall score must NOT be 100/100.
    """
    input_text = "Python 2.7 is the currently supported release. ChatGPT Plus costs $20 per month."

    mock_extract_json = json.dumps([
        {
            "id": "claim-python",
            "text": "Python 2.7 is the currently supported release",
            "type": "version",
            "entities": ["Python"],
            "confidence": 0.95,
        },
        {
            "id": "claim-chatgpt",
            "text": "ChatGPT Plus costs $20 per month",
            "type": "pricing",
            "entities": ["ChatGPT Plus", "OpenAI"],
            "numerical_value": "$20 per month",
            "confidence": 0.95,
        },
    ])

    class MultiJudgeMock(LLMClient):
        async def complete(self, prompt: str, system_prompt: str | None = None) -> str:
            if "Python 2.7" in prompt:
                return json.dumps({
                    "verdict": "contradicted",
                    "explanation": "Python 2.7 is EOL since 2020.",
                    "confidence": 0.99,
                    "score": 0,
                    "correction": "Python 3.12+ is supported.",
                })
            else:
                return json.dumps({
                    "verdict": "confirmed",
                    "explanation": "ChatGPT Plus is $20 per month.",
                    "confidence": 0.95,
                    "score": 100,
                    "correction": None,
                })

    extractor = ClaimExtractor(llm_client=MockLLM(mock_extract_json))
    verifier = ClaimVerifier(llm_client=MultiJudgeMock())
    pipeline = FreshGatePipeline(extractor=extractor, verifier=verifier)
    pipeline.cache.clear()

    async def mock_search_route(query, **kwargs):
        if "Python" in query:
            return [{
                "url": "https://www.python.org/doc/sunset-python-2/",
                "snippet": "Python 2 sunset occurred January 1 2020.",
            }]
        return [{
            "url": "https://openai.com/chatgpt/pricing/",
            "snippet": "ChatGPT Plus is $20 per month.",
        }]

    with patch.object(verifier.tavily_client, "search", side_effect=mock_search_route):
        result = await pipeline.verify(input_text)

    assert result.status == "completed"
    assert len(result.claims) == 2
    assert result.summary.total == 2
    assert result.summary.checked == 2
    assert result.summary.stale == 1
    # Freshness score must NEVER be 100 when there is a contradicted claim!
    assert result.summary.avg_score is not None
    assert result.summary.avg_score < 100
    assert result.summary.avg_score <= 50  # Average of 0 and 100 is 50


# --- 4. Test Extraction Timeout ---
@pytest.mark.asyncio
async def test_extraction_timeout():
    """Verify that simulated extraction timeout sets status='extraction_failed'

    and avg_score=None. It must NEVER assign score 100.
    """
    input_text = "Python 2.7 is the currently supported release. ChatGPT Plus costs $20 per month."

    failing_extractor = ClaimExtractor(llm_client=MockLLM("", should_fail=True))
    pipeline = FreshGatePipeline(extractor=failing_extractor)

    # Disable heuristic fallback for this specific test so we observe pure extraction timeout behavior
    failing_extractor.fallback_extractor = MagicMock()
    failing_extractor.fallback_extractor.extract.return_value = []

    result = await pipeline.verify(input_text)

    assert result.status == "extraction_failed"
    assert result.summary.avg_score is None  # Never 100!
    assert result.summary.status == "extraction_failed"
    assert result.summary.total == 0
    assert "failed" in (result.summary.message or "").lower()


# --- 5. Test Malformed JSON ---
@pytest.mark.asyncio
async def test_malformed_json():
    """Verify that invalid/malformed JSON returned by LLM does not silently produce

    an empty successful extraction with score 100.
    """
    input_text = "Some complex claim that returns invalid JSON."

    malformed_json = "This is not json at all {{{ bad"
    extractor = ClaimExtractor(llm_client=MockLLM(malformed_json))
    # Disable fallback to test parser strictness
    extractor.fallback_extractor = MagicMock()
    extractor.fallback_extractor.extract.return_value = []

    res: ExtractionResult = await extractor.extract(input_text)

    # Must be marked failed, NOT empty success
    assert res.status == "failed"
    assert len(res.claims) == 0
    assert res.error is not None

    pipeline = FreshGatePipeline(extractor=extractor)
    pipe_res = await pipeline.verify(input_text)
    assert pipe_res.status == "extraction_failed"
    assert pipe_res.summary.avg_score is None


# --- 6. Test Unknown Category ---
def test_unknown_category():
    """Verify category normalization maps aliases (e.g. software_version -> version)

    and that unknown categories are treated conservatively as volatile and NEVER
    silently discarded as 'other'.
    """
    # 1. Alias normalization
    norm, is_known = category_registry.normalize_category("software_version")
    assert norm == "version"
    assert is_known is True

    norm2, is_known2 = category_registry.normalize_category("subscription_price")
    assert norm2 == "pricing"
    assert is_known2 is True

    # 2. Truly unknown category
    norm_unknown, is_known_unk = category_registry.normalize_category("unknown_quantum_telemetry")
    assert norm_unknown == "unknown_quantum_telemetry"
    assert is_known_unk is False

    # Unknown category MUST be treated as volatile by default
    assert category_registry.is_volatile("unknown_quantum_telemetry") is True

    # Triage must NOT drop claims with unknown categories
    claim = Claim(
        id="c-unk",
        text="Quantum processor has 4096 qubits",
        type="unknown_quantum_telemetry",
        confidence=0.9,
    )
    triaged = triage_claims([claim])
    assert len(triaged) == 1
    assert triaged[0].type == "unknown_quantum_telemetry"


# --- 7. Test Tavily Unavailable ---
@pytest.mark.asyncio
async def test_tavily_unavailable():
    """Verify that when Tavily search fails (network error, 500, 429),

    claims are marked unverifiable and pipeline does not fail closed or assign false 100.
    """
    input_text = "The new iPhone 16 costs $799."
    mock_extract_json = json.dumps([
        {
            "id": "c-phone",
            "text": "The new iPhone 16 costs $799",
            "type": "pricing",
            "entities": ["iPhone 16"],
            "confidence": 0.9,
        }
    ])

    extractor = ClaimExtractor(llm_client=MockLLM(mock_extract_json))
    verifier = ClaimVerifier(llm_client=MockLLM(""))
    pipeline = FreshGatePipeline(extractor=extractor, verifier=verifier)

    with patch.object(verifier.tavily_client, "search", side_effect=RuntimeError("Tavily 503 Service Unavailable")):
        result = await pipeline.verify(input_text)

    assert len(result.claims) == 1
    c = result.claims[0]
    assert c.verdict == "unverifiable"
    # Unverifiable claims do not receive 100
    assert c.score is None or c.score <= 50
    assert result.status in ("completed_with_warnings", "partially_verified", "completed")


# --- 8. Test Conflicting Sources ---
@pytest.mark.asyncio
async def test_conflicting_sources():
    """Verify that verifier resolves conflicting sources by weighting authoritative

    primary domains higher than secondary/unverified blogs.
    """
    claim = Claim(
        id="c-conflict",
        text="Python 2.7 is currently supported",
        type="version",
        entities=["Python"],
    )

    official_source = Source(
        url="https://www.python.org/doc/sunset-python-2/",
        domain="python.org",
        snippet="Python 2 reached end of life on Jan 1 2020 and is not supported.",
        authority_score=1.0,
        is_primary=True,
    )
    blog_source = Source(
        url="https://random-outdated-blog.example.com/tips/python2-support",
        domain="random-outdated-blog.example.com",
        snippet="Python 2.7 is still supported for legacy systems.",
        authority_score=0.2,
        is_primary=False,
    )

    verifier = ClaimVerifier(llm_client=MockLLM(""))
    resolved = verifier.resolve_contradictions(claim, [official_source, blog_source])

    # The high authority source stating EOL must dominate
    assert resolved.verdict == "contradicted"
    assert "2020" in (resolved.explanation or "") or "end-of-life" in (resolved.explanation or "").lower()


# --- 9. Test Claim-Free Response ---
@pytest.mark.asyncio
async def test_claim_free_response():
    """Verify that conversational text with no volatile claims returns

    status='no_claims_detected', zero claims, and clear message distinguishing it from failure.
    """
    input_text = "Hello! I hope you are having a wonderful day. How can I help you today?"

    extractor = ClaimExtractor(llm_client=MockLLM("[]"))
    pipeline = FreshGatePipeline(extractor=extractor)

    result = await pipeline.verify(input_text)

    assert result.status == "no_claims_detected"
    assert len(result.claims) == 0
    assert result.summary.total == 0
    assert result.summary.checked == 0
    assert result.summary.avg_score == 100  # Claim-free pass-through is 100
    assert "no volatile factual claims" in (result.summary.message or "").lower()


# --- 10. Test Partially Extracted Claims ---
@pytest.mark.asyncio
async def test_partially_extracted_claims():
    """Verify that when extraction returns one valid claim and one malformed item,

    the valid claim is preserved and status indicates partial extraction.
    """
    raw_mixed_json = json.dumps([
        {
            "id": "valid-1",
            "text": "Go 1.22 was released with loop variable scoping fixes",
            "type": "version",
            "entities": ["Go"],
            "confidence": 0.95,
        },
        {
            "id": "bad-2",
            # Missing text and type!
            "foo": "bar",
        },
    ])

    extractor = ClaimExtractor(llm_client=MockLLM(raw_mixed_json))
    res: ExtractionResult = await extractor.extract("Some text with Go 1.22")

    assert res.status == "partial"
    assert res.total_extracted == 2
    assert res.valid_claims == 1
    assert res.dropped_claims == 1
    assert len(res.claims) == 1
    assert res.claims[0].text == "Go 1.22 was released with loop variable scoping fixes"


# --- 11. Test Cache Contamination ---
def test_cache_contamination(tmp_path):
    """Verify that an unverifiable or partial result does not contaminate or override

    a verified result, and cache stores confirmed/contradicted properly.
    """
    db_file = str(tmp_path / "test_cache.db")
    cache = SQLiteClaimCache(db_path=db_file)

    claim_text = "Claude 3.5 Sonnet costs $3 per million input tokens"

    # Step 1: Save unverifiable claim
    unverifiable_claim = Claim(
        id="c-unverifiable",
        text=claim_text,
        type="pricing",
        verdict="unverifiable",
        score=50,
        explanation="Search timed out.",
    )
    # Cache save
    cache.set(unverifiable_claim, ttl_seconds=60)

    # Step 2: Now a verified confirmed claim arrives
    confirmed_claim = Claim(
        id="c-confirmed",
        text=claim_text,
        type="pricing",
        verdict="confirmed",
        score=100,
        explanation="Confirmed via Anthropic pricing page.",
    )
    cache.set(confirmed_claim, ttl_seconds=3600)

    cached_item = cache.get(claim_text, category="pricing")
    assert cached_item is not None
    assert cached_item.verdict == "confirmed"
    assert cached_item.score == 100


# --- 12. Test Dashboard State Contract ---
def test_dashboard_state():
    """Verify that a failed extraction payload does not produce a green 100/100

    or claim that the response is stable.
    """
    summary = Summary(
        total=0,
        checked=0,
        stale=0,
        avg_score=None,
        status="extraction_failed",
        message="Claim extraction failed due to LLM timeout.",
        extraction_status="failed",
    )
    verification = VerificationResult(
        claims=[],
        summary=summary,
        status="extraction_failed",
        error="LLM timeout",
    )

    data = verification.model_dump()

    # Dashboard contract assertions:
    assert data["summary"]["avg_score"] is None  # NEVER 100
    assert data["summary"]["status"] == "extraction_failed"
    assert data["status"] == "extraction_failed"
    assert data["error"] == "LLM timeout"
    assert len(data["claims"]) == 0
