import json
import logging
import re
import string
from typing import List, Optional
from core.models import Claim
from core.llm import LLMClient, OpenAILLMClient
from core.categories import category_registry

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert factual claim extraction system.
Analyze the provided text (which may include paragraphs, bullet lists, or markdown tables) and decompose it into atomic factual claims.

For each claim extracted, provide:
1. "id": Unique string identifier (e.g. "c1", "c2").
2. "text": Complete, standalone factual statement. Resolve any pronouns ("he", "it", "they") with their actual entity referents so the statement is fully self-contained.
3. "type": Exactly one classification from:
   - "pricing": costs, token prices, rates, subscription fees, discounts.
   - "version": software/model releases, deprecations, active LTS versions.
   - "law": enacted statutes, vetoed bills, court rulings, regulatory orders.
   - "leadership": executives, CEOs, directors, political office holders.
   - "cybersecurity": vulnerabilities, CVE identifiers, security patches.
   - "financial": quarterly earnings, revenues, market valuations.
   - "government_policy": executive orders, sanctions, export restrictions.
   - "public_health": health guidelines, vaccine directives, outbreak alerts.
   - "product_availability": launch availability, waitlists, general availability.
   - "company_info": acquisitions, mergers, headquarters.
   - "other": static general facts (geography, history, math, syntax).
4. "entities": Array of specific named entities referenced (e.g. ["OpenAI", "GPT-4"]).
5. "numerical_value": Extracted price, rate, percentage, or quantity, if present (e.g. "$30 per million tokens", "4%").
6. "temporal_anchor": Relevant date, deadline, year, or release time, if present (e.g. "October 2023", "2024").
7. "confidence": Confidence score between 0.0 and 1.0 that this claim is an atomic factual claim.

RULES:
- When a sentence contains multiple factual claims, split them into separate atomic objects.
- Deconstruct tables and bullet points row-by-row or item-by-item into discrete statements.
- If the text has no factual assertions (greetings, pleasantries, subjective opinions), return [].
- Output MUST be a valid JSON array of objects. Do not include markdown commentary."""


def normalize_claim_text(text: str) -> str:
    """Normalize claim text for deterministic comparison and duplicate detection."""
    clean = text.lower().strip()
    clean = re.sub(r"\s+", " ", clean)
    clean = clean.translate(str.maketrans("", "", string.punctuation))
    return clean


def _clean_json_text(text: str) -> str:
    """Strip markdown code fences and locate JSON array boundaries."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()

    start_idx = cleaned.find("[")
    end_idx = cleaned.rfind("]")
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        cleaned = cleaned[start_idx : end_idx + 1]

    return cleaned


class ClaimExtractor:
    """Extracts, normalizes, tags, and deduplicates atomic factual claims."""

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client or OpenAILLMClient()

    async def extract(self, text: str) -> List[Claim]:
        """Extract atomic factual claims from text, supporting tables, lists, and multi-claim sentences."""
        if not text or not text.strip():
            return []

        prompt = f"Analyze and extract all atomic factual claims from the following text:\n\n{text}"

        try:
            raw_response = await self.llm_client.complete(prompt, system_prompt=SYSTEM_PROMPT)
            cleaned = _clean_json_text(raw_response)

            try:
                data = json.loads(cleaned)
            except json.JSONDecodeError:
                logger.warning("Invalid JSON from LLM extractor, retrying with error prompt...")
                retry_prompt = (
                    f"The following JSON response was invalid:\n{raw_response}\n\n"
                    f"Correct formatting and return ONLY a valid JSON array of objects:"
                )
                retry_response = await self.llm_client.complete(retry_prompt, system_prompt=SYSTEM_PROMPT)
                cleaned_retry = _clean_json_text(retry_response)
                data = json.loads(cleaned_retry)

            if not isinstance(data, list):
                logger.warning("Extracted JSON response is not an array: %s", type(data))
                return []

            claims: List[Claim] = []
            seen_normalized: set[str] = set()

            for idx, item in enumerate(data):
                if isinstance(item, dict) and "text" in item:
                    claim_text = str(item.get("text", "")).strip()
                    if not claim_text:
                        continue

                    normalized = normalize_claim_text(claim_text)
                    # Deduplicate repeated identical claims
                    if normalized in seen_normalized:
                        continue
                    seen_normalized.add(normalized)

                    claim_id = str(item.get("id") or f"c{len(claims) + 1}")
                    claim_type = str(item.get("type", "other")).strip().lower()

                    entities = [str(e).strip() for e in item.get("entities", []) if str(e).strip()]
                    numerical_value = item.get("numerical_value")
                    temporal_anchor = item.get("temporal_anchor")
                    confidence = float(item.get("confidence", 1.0))

                    claims.append(
                        Claim(
                            id=claim_id,
                            text=claim_text,
                            normalized_text=normalized,
                            type=claim_type,
                            entities=entities,
                            numerical_value=str(numerical_value) if numerical_value else None,
                            temporal_anchor=str(temporal_anchor) if temporal_anchor else None,
                            extraction_confidence=confidence,
                        )
                    )

            return claims

        except Exception as e:
            logger.error("Claim extraction failed safely: %s", e)
            return []
