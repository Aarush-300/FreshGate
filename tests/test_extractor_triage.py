import pytest
from unittest.mock import AsyncMock

from core.models import Claim
from core.extractor import ClaimExtractor, LLMClient
from core.triage import triage_claims, VOLATILE_TYPES


class MockLLM(LLMClient):
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.call_count = 0

    async def complete(self, prompt: str, system_prompt: str | None = None) -> str:
        self.call_count += 1
        if self.responses:
            return self.responses.pop(0)
        return "[]"


@pytest.mark.asyncio
async def test_extract_factual_claims():
    mock_response = """[
        {"id": "c1", "text": "GPT-4 costs $30 per million tokens", "type": "pricing"},
        {"id": "c2", "text": "The CEO of X is Linda Yaccarino", "type": "leadership"}
    ]"""
    llm = MockLLM([mock_response])
    extractor = ClaimExtractor(llm_client=llm)

    claims = await extractor.extract("GPT-4 costs $30 per million tokens and the CEO of X is Linda Yaccarino.")

    assert len(claims) == 2
    assert claims[0].id == "c1"
    assert claims[0].type == "pricing"
    assert claims[1].id == "c2"
    assert claims[1].type == "leadership"
    assert llm.call_count == 1


@pytest.mark.asyncio
async def test_extract_no_facts_greeting():
    mock_response = "[]"
    llm = MockLLM([mock_response])
    extractor = ClaimExtractor(llm_client=llm)

    claims = await extractor.extract("Hello! How can I help you today?")
    assert claims == []
    assert llm.call_count == 1


@pytest.mark.asyncio
async def test_extract_code_fences():
    mock_response = """```json
    [
        {"id": "c1", "text": "Python 3.12 was released in October 2023", "type": "version"}
    ]
    ```"""
    llm = MockLLM([mock_response])
    extractor = ClaimExtractor(llm_client=llm)

    claims = await extractor.extract("Python 3.12 was released in October 2023.")
    assert len(claims) == 1
    assert claims[0].type == "version"
    assert claims[0].id == "c1"


@pytest.mark.asyncio
async def test_extract_malformed_json_retry_success():
    # First returns broken JSON, second returns valid JSON
    responses = [
        "This is not valid json { broken",
        '[{"id": "c1", "text": "New California AI bill passed", "type": "law"}]'
    ]
    llm = MockLLM(responses)
    extractor = ClaimExtractor(llm_client=llm)

    claims = await extractor.extract("New California AI bill passed.")
    assert len(claims) == 1
    assert claims[0].type == "law"
    assert llm.call_count == 2


@pytest.mark.asyncio
async def test_extract_malformed_json_fails_open_gracefully():
    # Both calls return bad JSON -> should return empty list and not crash
    responses = [
        "Unparseable garbage",
        "Still unparseable garbage"
    ]
    llm = MockLLM(responses)
    extractor = ClaimExtractor(llm_client=llm)

    claims = await extractor.extract("Some input text")
    assert claims == []
    assert llm.call_count == 2


def test_triage_drops_other_and_keeps_volatile():
    claims = [
        Claim(id="c1", text="GPT-4 costs $30", type="pricing"),
        Claim(id="c2", text="France is in Europe", type="other"),
        Claim(id="c3", text="Python 3.12 is latest", type="version"),
        Claim(id="c4", text="Water freezes at 0C", type="other"),
        Claim(id="c5", text="SB 1047 was vetoed", type="law"),
        Claim(id="c6", text="Sundar Pichai is CEO", type="leadership"),
        Claim(id="c7", text="Random claim", type="unknown_type"),
    ]

    triaged = triage_claims(claims)

    # In Phase 2, unknown categories are conservatively kept as volatile; only explicitly non-volatile "other" is dropped
    assert len(triaged) == 5
    types = [c.type for c in triaged]
    assert types == ["pricing", "version", "law", "leadership", "unknown_type"]
