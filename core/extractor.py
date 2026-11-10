import json
import logging
import re
from typing import List, Optional
from core.models import Claim
from core.llm import LLMClient, OpenAILLMClient

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert factual claim extractor.
Your task is to analyze the provided text and extract all discrete, atomic factual claims.

For each extracted claim, provide:
1. "id": A unique identifier (e.g., "c1", "c2").
2. "text": An atomic, standalone factual statement.
3. "type": Exactly one of:
   - "pricing": costs, token fees, subscription prices, rates.
   - "version": software/model versions, API versions, release numbers.
   - "law": laws, regulations, legal mandates, compliance rules.
   - "leadership": CEOs, founders, executives, office holders.
   - "other": all other static or general facts.

RULES:
- If the text contains no factual claims (e.g., greetings, pleasantries, opinions), return [].
- Return ONLY valid JSON: a single JSON array of objects. Do not wrap in markdown quotes if possible, and output no commentary."""


def _clean_json_text(text: str) -> str:
    """Remove markdown code fences and extraneous text outside JSON array."""
    cleaned = text.strip()
    # Remove markdown code blocks ```json ... ``` or ``` ... ```
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()

    # Locate array brackets if surrounded by other prose
    start_idx = cleaned.find("[")
    end_idx = cleaned.rfind("]")
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        cleaned = cleaned[start_idx : end_idx + 1]

    return cleaned


class ClaimExtractor:
    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client or OpenAILLMClient()

    async def extract(self, text: str) -> List[Claim]:
        """Extract atomic factual claims from text, tagged by type."""
        if not text or not text.strip():
            return []

        prompt = f"Extract all atomic factual claims from this text:\n\n{text}"

        try:
            raw_response = await self.llm_client.complete(prompt, system_prompt=SYSTEM_PROMPT)
            cleaned = _clean_json_text(raw_response)
            try:
                data = json.loads(cleaned)
            except json.JSONDecodeError:
                # Retry once on invalid JSON
                logger.warning("Invalid JSON from LLM extractor, retrying once...")
                retry_prompt = (
                    f"The following output was not valid JSON:\n{raw_response}\n\n"
                    f"Fix it and return ONLY a valid JSON array: [{{\"id\": \"c1\", \"text\": \"...\", \"type\": \"...\"}}]"
                )
                retry_response = await self.llm_client.complete(retry_prompt, system_prompt=SYSTEM_PROMPT)
                cleaned_retry = _clean_json_text(retry_response)
                data = json.loads(cleaned_retry)

            if not isinstance(data, list):
                logger.warning("Extracted JSON is not a list: %s", type(data))
                return []

            claims = []
            for idx, item in enumerate(data):
                if isinstance(item, dict) and "text" in item:
                    claim_id = str(item.get("id") or f"c{idx + 1}")
                    claim_text = str(item.get("text", "")).strip()
                    claim_type = str(item.get("type", "other")).strip().lower()
                    if claim_text:
                        claims.append(Claim(id=claim_id, text=claim_text, type=claim_type))
            return claims

        except Exception as e:
            logger.error("Claim extraction failed safely: %s", e)
            return []
