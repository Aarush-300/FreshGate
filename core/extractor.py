"""FreshGate Robust Atomic Claim Extraction Subsystem (V2).

Decomposes text into normalized, discrete atomic factual claims with:
- Centralized LLM Provider Manager integration with connection pooling and token tracking.
- Explicit ExtractionResult status: success, partial, failed, no_claims_detected.
- Compound sentence splitting and multi-claim decomposition.
- Expanded regex heuristic extractors for version, pricing, leadership, availability, cybersecurity, and law.
- Non-blocking async retry and zero silent swallowing of errors.
"""

import json
import logging
import re
import string
import asyncio
from typing import List, Optional, Dict, Any, Tuple
from core.models import Claim, ExtractionResult, ExtractionStatus
from core.llm import LLMClient, OpenAILLMClient
from core.llm_manager import LLMProviderException, LLMErrorType
from core.categories import category_registry

logger = logging.getLogger("freshgate.extractor")

SYSTEM_PROMPT = """You are an expert factual claim extraction system.
Analyze the provided text (which may include paragraphs, bullet lists, or tables) and decompose it into discrete atomic factual claims.

For each claim extracted, provide:
1. "id": Unique string identifier (e.g. "c1", "c2").
2. "text": Complete, standalone factual statement. Resolve any pronouns ("he", "it", "they") with their actual entity referents so the statement is fully self-contained.
3. "type": Exactly one classification from:
   - "pricing": costs, token prices, subscription fees, rates, tiers, discounts.
   - "version": software releases, model versions, supported branches, EOL dates, deprecations.
   - "law": enacted statutes, vetoed bills, court rulings, regulatory orders, compliance rules.
   - "leadership": executives, CEOs, directors, political office holders, appointments.
   - "cybersecurity": vulnerabilities, CVE identifiers, security patches, exploits, breaches.
   - "financial": quarterly earnings, revenues, market valuations, interest rates.
   - "government_policy": executive orders, sanctions, export restrictions, subsidies, tax credits.
   - "public_health": health guidelines, vaccine directives, outbreak alerts, isolation rules.
   - "product_availability": general availability, waitlists, launch dates, discontinuations.
   - "company_info": acquisitions, mergers, bankruptcies, headquarters relocations.
   - "scientific_findings": scientific discoveries, peer-reviewed replications, preprints.
   - "other": static general facts (geography, history, math, universal syntax).
4. "entities": Array of specific named entities referenced (e.g. ["Python"], ["ChatGPT Plus", "OpenAI"]).
5. "numerical_value": Extracted price, rate, percentage, or quantity, if present (e.g. "$20 per month").
6. "temporal_anchor": Relevant date, deadline, year, or release time, if present (e.g. "currently", "2024").
7. "confidence": Confidence score between 0.0 and 1.0 that this claim is an atomic factual claim.

RULES:
- When a sentence contains multiple factual assertions, SPLIT THEM into separate atomic objects.
  For example, "Python 2.7 is the currently supported release. ChatGPT Plus costs $20 per month." MUST produce TWO separate claims:
  1) Version claim regarding Python 2.7 support status.
  2) Pricing claim regarding ChatGPT Plus costing $20 per month.
- If the text contains no factual assertions (greetings, pleasantries, subjective opinions), return [].
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
        return cleaned[start_idx : end_idx + 1]
    return cleaned


def split_compound_sentences(text: str) -> List[str]:
    """Split text into candidate sentences while preserving version numbers, currency, and decimals."""
    # First split on explicit newlines
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    sentences: List[str] = []
    for line in lines:
        parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", line)
        for p in parts:
            p_strip = p.strip()
            if p_strip:
                sentences.append(p_strip)
    return sentences if sentences else [text.strip()]


class FallbackHeuristicExtractor:
    """Deterministic pattern-based fallback extractor for high-volatility assertions.

    Activates when external LLM extraction fails (e.g., HTTP 429 rate limit, quota exhaustion, or malformed JSON).
    """

    # 1. Version patterns: "Python 2.7 is the currently supported release"
    VERSION_PATTERN = re.compile(
        r"([A-Z][a-zA-Z0-9_\.\-]+(?:\s+[a-zA-Z0-9_\.\-]+)?)\s+(\d+(?:\.\d+)+[a-zA-Z0-9_\.\-]*)\s+"
        r"(?:is|was|has been)\s+(?:the\s+)?(currently\s+supported|current\s+supported|supported|latest\s+stable|production|active\s+LTS|deprecated|released|EOL|end[-\s]of[-\s]life)"
        r"(?:\s+(?:release|version|branch|edition))?",
        re.IGNORECASE,
    )

    # 2. Pricing patterns: "ChatGPT Plus costs $20 per month"
    PRICING_PATTERN = re.compile(
        r"([A-Z][a-zA-Z0-9_\.\-]+(?:\s+[A-Za-z0-9_\.\-]+)*?)\s+"
        r"(?:costs|is priced at|charges|has a price of|subscription is|fee is|starts at)\s+"
        r"(\$[\d\.,]+(?:\s*(?:per|\/)\s*(?:month|mo|year|yr|user|token|million tokens|GB))?)",
        re.IGNORECASE,
    )

    # 3. Leadership patterns: "Satya Nadella is the CEO of Microsoft"
    LEADERSHIP_PATTERN = re.compile(
        r"([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)+)\s+(?:is|serves as|was appointed|became)\s+(?:the\s+)?"
        r"(?:active\s+)?(CEO|Chief Executive Officer|President|CTO|Chairman|Director)\s+of\s+([A-Z][a-zA-Z0-9_\.\-]+(?:\s+[A-Z][a-zA-Z0-9_\.\-]+)*)",
        re.IGNORECASE,
    )

    # 4. Product Availability patterns: "Google Stadia was shut down in January 2023"
    AVAILABILITY_PATTERN = re.compile(
        r"([A-Z][a-zA-Z0-9_\.\-]+(?:\s+[A-Z][a-zA-Z0-9_\.\-]+)*)\s+(?:was|is)\s+"
        r"(shut down|discontinued|decommissioned|deprecated|launched|available|unreleased)"
        r"(?:\s+in\s+([A-Z][a-zA-Z]+\s+\d{4}|\d{4}))?",
        re.IGNORECASE,
    )

    # 5. Cybersecurity patterns: "CVE-2024-1234 affects Apache HTTP Server"
    CVE_PATTERN = re.compile(
        r"(CVE-\d{4}-\d{4,7})\s+(?:is|affects|was disclosed in|vulnerability in)\s+([A-Za-z0-9_\.\-]+(?:\s+[A-Za-z0-9_\.\-]+)?)",
        re.IGNORECASE,
    )

    # 6. Law / Legislation patterns: "SB 1047 was vetoed by Governor Newsom"
    LAW_PATTERN = re.compile(
        r"([A-Z]{1,3}\s*\d{1,5})\s+(?:was|is)\s+(vetoed|signed|enacted|passed|struck down|approved)",
        re.IGNORECASE,
    )

    def extract(self, text: str) -> List[Claim]:
        claims: List[Claim] = []
        sentences = split_compound_sentences(text)

        for sent in sentences:
            # Check version
            v_match = self.VERSION_PATTERN.search(sent)
            if v_match:
                ent, ver, status = v_match.group(1), v_match.group(2), v_match.group(3)
                claims.append(
                    Claim(
                        id=f"c{len(claims) + 1}",
                        text=sent.rstrip("."),
                        normalized_text=normalize_claim_text(sent),
                        type="version",
                        entities=[ent.strip()],
                        numerical_value=ver.strip(),
                        temporal_anchor="current" if "current" in status.lower() else None,
                        extraction_confidence=0.85,
                    )
                )
                continue

            # Check pricing
            p_match = self.PRICING_PATTERN.search(sent)
            if p_match:
                ent, price = p_match.group(1), p_match.group(2)
                claims.append(
                    Claim(
                        id=f"c{len(claims) + 1}",
                        text=sent.rstrip("."),
                        normalized_text=normalize_claim_text(sent),
                        type="pricing",
                        entities=[ent.strip()],
                        numerical_value=price.strip(),
                        temporal_anchor="current",
                        extraction_confidence=0.85,
                    )
                )
                continue

            # Check leadership
            l_match = self.LEADERSHIP_PATTERN.search(sent)
            if l_match:
                person, role, company = l_match.group(1), l_match.group(2), l_match.group(3)
                claims.append(
                    Claim(
                        id=f"c{len(claims) + 1}",
                        text=sent.rstrip("."),
                        normalized_text=normalize_claim_text(sent),
                        type="leadership",
                        entities=[person.strip(), company.strip()],
                        numerical_value=None,
                        temporal_anchor="current",
                        extraction_confidence=0.85,
                    )
                )
                continue

            # Check product availability
            a_match = self.AVAILABILITY_PATTERN.search(sent)
            if a_match:
                ent, status, date_str = a_match.group(1), a_match.group(2), a_match.group(3)
                claims.append(
                    Claim(
                        id=f"c{len(claims) + 1}",
                        text=sent.rstrip("."),
                        normalized_text=normalize_claim_text(sent),
                        type="product_availability",
                        entities=[ent.strip()],
                        numerical_value=None,
                        temporal_anchor=date_str if date_str else None,
                        extraction_confidence=0.85,
                    )
                )
                continue

            # Check cybersecurity
            c_match = self.CVE_PATTERN.search(sent)
            if c_match:
                cve_id, target = c_match.group(1), c_match.group(2)
                claims.append(
                    Claim(
                        id=f"c{len(claims) + 1}",
                        text=sent.rstrip("."),
                        normalized_text=normalize_claim_text(sent),
                        type="cybersecurity",
                        entities=[cve_id.upper(), target.strip()],
                        numerical_value=cve_id.upper(),
                        temporal_anchor=None,
                        extraction_confidence=0.90,
                    )
                )
                continue

            # Check law
            law_match = self.LAW_PATTERN.search(sent)
            if law_match:
                bill_id, action = law_match.group(1), law_match.group(2)
                claims.append(
                    Claim(
                        id=f"c{len(claims) + 1}",
                        text=sent.rstrip("."),
                        normalized_text=normalize_claim_text(sent),
                        type="law",
                        entities=[bill_id.strip()],
                        numerical_value=None,
                        temporal_anchor=None,
                        extraction_confidence=0.90,
                    )
                )
                continue

        return claims


class ClaimExtractor:
    """Extracts atomic factual claims using structured LLM completions with fallback resilience."""

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        max_retries: int = 1,
    ):
        self.llm_client = llm_client or OpenAILLMClient(request_type="extraction")
        self.max_retries = max_retries
        self.fallback_extractor = FallbackHeuristicExtractor()

    def _is_claim_free(self, text: str) -> bool:
        """Check if text is purely pleasantry or greeting without factual content."""
        clean = text.lower().strip()
        greetings = [
            "hello", "hi", "hey", "good morning", "good afternoon",
            "good evening", "how are you", "hope you are having a good day",
            "how can i help you", "thank you", "thanks", "you're welcome",
        ]
        clean_no_punct = clean.translate(str.maketrans("", "", string.punctuation)).strip()
        if clean_no_punct in greetings:
            return True
        for g in greetings:
            if clean_no_punct.startswith(g) and len(clean_no_punct) < len(g) + 15:
                return True
        return False

    async def extract(self, text: str) -> ExtractionResult:
        """Extract atomic factual claims, returning an explicit ExtractionResult model."""
        if not text or not text.strip():
            return ExtractionResult(claims=[], status="no_claims_detected")

        # Fast path for obvious non-factual pleasantries
        if self._is_claim_free(text):
            return ExtractionResult(claims=[], status="no_claims_detected")

        prompt = f"Analyze and extract all atomic factual claims from the following text:\n\n{text}"

        raw_response = None
        llm_error = None
        error_type: Optional[LLMErrorType] = None

        try:
            raw_response = await self.llm_client.complete(prompt, system_prompt=SYSTEM_PROMPT)
        except LLMProviderException as lpe:
            llm_error = lpe.detail.message
            error_type = lpe.detail.error_type
            logger.error("LLM Provider Manager error [%s]: %s", lpe.detail.error_type.value, lpe.detail.message)
        except Exception as e:
            llm_error = str(e)
            logger.error("LLM extraction provider failure: %s", e)

        # Handle LLM failure by engaging heuristic fallback parser
        if llm_error:
            fallback_claims = self.fallback_extractor.extract(text)
            if fallback_claims:
                logger.info("Fallback heuristic extractor recovered %d claims from input despite LLM error",
                            len(fallback_claims))
                return ExtractionResult(
                    claims=fallback_claims,
                    status="partial",
                    error=f"LLM extraction error ({error_type.value if error_type else 'provider_error'}): {llm_error}. Fallback heuristics recovered volatile claims.",
                    total_extracted=len(fallback_claims),
                    valid_claims=len(fallback_claims),
                    is_fallback=True,
                )
            # No claims could be extracted and LLM failed -> explicit failed status!
            return ExtractionResult(
                claims=[],
                status="failed",
                error=f"LLM extraction failed ({error_type.value if error_type else 'provider_error'}): {llm_error}",
            )

        # Parse LLM response
        data = None
        cleaned = _clean_json_text(raw_response or "")

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("Invalid JSON from LLM extractor, attempting single formatting retry via LLM manager...")
            try:
                retry_prompt = (
                    f"The following JSON response was invalid:\n{raw_response}\n\n"
                    f"Correct formatting and return ONLY a valid JSON array of objects:"
                )
                retry_resp = await self.llm_client.complete(retry_prompt, system_prompt=SYSTEM_PROMPT)
                cleaned_retry = _clean_json_text(retry_resp)
                data = json.loads(cleaned_retry)
            except Exception as retry_err:
                logger.error("JSON formatting retry failed: %s", retry_err)

        # If data could not be parsed as a list, try chunk regex extraction or fallback
        if not isinstance(data, list):
            fallback_claims = self.fallback_extractor.extract(text)
            if fallback_claims:
                return ExtractionResult(
                    claims=fallback_claims,
                    status="partial",
                    error="LLM returned malformed non-array JSON; fallback heuristics recovered claims.",
                    total_extracted=len(fallback_claims),
                    valid_claims=len(fallback_claims),
                    is_fallback=True,
                )
            return ExtractionResult(
                claims=[],
                status="failed",
                error=f"LLM extraction returned invalid JSON: {raw_response[:200] if raw_response else 'empty'}",
                raw_response=raw_response,
            )

        # Validate and sanitize extracted items
        claims: List[Claim] = []
        seen_normalized: set[str] = set()
        dropped_count = 0
        total_items = len(data)

        for item in data:
            if not isinstance(item, dict):
                dropped_count += 1
                continue

            claim_text = str(item.get("text", "")).strip()
            if not claim_text or len(claim_text) < 3:
                dropped_count += 1
                continue

            normalized = normalize_claim_text(claim_text)
            if normalized in seen_normalized:
                continue
            seen_normalized.add(normalized)

            # Category validation and canonical normalization
            raw_type = str(item.get("type", "other"))
            norm_type, _ = category_registry.normalize_category(raw_type)

            entities = [str(e).strip() for e in item.get("entities", []) if str(e).strip()]
            num_val = item.get("numerical_value")
            temp_anchor = item.get("temporal_anchor")

            try:
                conf = float(item.get("confidence", 1.0))
                conf = max(0.0, min(1.0, conf))
            except (ValueError, TypeError):
                conf = 0.8

            claim_id = f"c{len(claims) + 1}"

            claims.append(
                Claim(
                    id=claim_id,
                    text=claim_text,
                    normalized_text=normalized,
                    type=norm_type,
                    entities=entities,
                    numerical_value=str(num_val) if num_val else None,
                    temporal_anchor=str(temp_anchor) if temp_anchor else None,
                    extraction_confidence=conf,
                )
            )

        # Determine extraction status
        if total_items == 0:
            # Check if text actually contained obvious claims that LLM missed
            fallback_claims = self.fallback_extractor.extract(text)
            if fallback_claims:
                return ExtractionResult(
                    claims=fallback_claims,
                    status="partial",
                    error="LLM returned empty claims but heuristic detector identified volatile claims.",
                    total_extracted=len(fallback_claims),
                    valid_claims=len(fallback_claims),
                    is_fallback=True,
                )
            return ExtractionResult(claims=[], status="no_claims_detected")

        status: ExtractionStatus = "success"
        if dropped_count > 0:
            status = "partial" if claims else "failed"

        return ExtractionResult(
            claims=claims,
            status=status,
            raw_response=raw_response,
            total_extracted=total_items,
            valid_claims=len(claims),
            dropped_claims=dropped_count,
        )
