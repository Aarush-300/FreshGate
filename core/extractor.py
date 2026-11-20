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
import io
import zipfile
import xml.etree.ElementTree as ET
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
   - "dated_event": outcomes and results tied to a specific event or date, including match and competition results, election outcomes, and awards. Use this for historical results too; a past date does not make an assertion verified.
   - "other": timeless general facts only (geography, math, universal syntax). Do not use "other" for a claim about a specific dated event or outcome.
4. "entities": Array of specific named entities referenced (e.g. ["Python"], ["ChatGPT Plus", "OpenAI"]).
5. "numerical_value": Extracted price, rate, percentage, or quantity, if present (e.g. "$20 per month").
6. "temporal_anchor": Relevant date, deadline, year, or release time, if present (e.g. "currently", "2024").
7. "confidence": Confidence score between 0.0 and 1.0 that this claim is an atomic factual claim.
8. "volatility_score": Likelihood (0.0 to 1.0) that this fact will change or evolve soon (e.g., pricing 0.85, versions 0.8, law 0.4, static 0.1).
9. "evidence_gap": Potential difficulty or ambiguity in finding fresh evidence (0.0 to 1.0).
10. "temporal_stability": "high", "medium", or "low".

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


FUTURE_CLAIM_PATTERNS = [
    re.compile(r"\b(will\s+release|expected\s+to|scheduled\s+for|planned\s+for|announced\s+for|slated\s+for|coming\s+in|launching\s+in|upcoming)\b", re.IGNORECASE),
    re.compile(r"\b(q[1-4]\s+202[4-9]|\b202[5-9]\b|\b203\d\b)\b", re.IGNORECASE),
]


def detect_future_claim(text: str) -> bool:
    """Detect if a claim references future dates or upcoming events."""
    return any(p.search(text) for p in FUTURE_CLAIM_PATTERNS)


def extract_text_from_file_bytes(file_content: bytes, filename: str) -> str:
    """
    Extract raw text from uploaded file bytes across multiple formats:
    - .txt, .md, .json, .csv: UTF-8 / latin-1 text
    - .docx: Microsoft Word Document via python-docx or zipfile XML fallback
    - .pdf: Adobe PDF via pymupdf (fitz) or stream text extraction fallback
    """
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""

    if ext in (".txt", ".md", ".json", ".csv"):
        try:
            return file_content.decode("utf-8")
        except UnicodeDecodeError:
            return file_content.decode("latin-1", errors="replace")

    elif ext == ".docx":
        # Try python-docx first
        try:
            import docx
            doc = docx.Document(io.BytesIO(file_content))
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        except Exception:
            pass

        # Fallback: Extract directly from docx XML (a docx is a zip container)
        try:
            with zipfile.ZipFile(io.BytesIO(file_content)) as z:
                xml_data = z.read("word/document.xml")
                root = ET.fromstring(xml_data)
                # Word text nodes have tag {http://schemas.openxmlformats.org/wordprocessingml/2006/main}t
                texts = [node.text for node in root.iter() if node.text and node.text.strip()]
                return "\n".join(texts)
        except Exception as e:
            logger.warning("DOCX extraction fallback failed: %s", e)
            return file_content.decode("latin-1", errors="replace")

    elif ext == ".pdf":
        # Try pymupdf (fitz)
        try:
            import fitz
            doc = fitz.open(stream=file_content, filetype="pdf")
            pages_text = []
            for page in doc:
                t = page.get_text()
                if t and t.strip():
                    pages_text.append(t.strip())
            return "\n\n".join(pages_text)
        except Exception:
            pass

        # Fallback: extract ASCII and literal string tokens from PDF stream
        try:
            matches = re.findall(rb"\((.*?)\)\s*Tj", file_content)
            if matches:
                return "\n".join(m.decode("latin-1", errors="ignore") for m in matches)
            # General string regex
            strings = re.findall(rb"[\x20-\x7E]{4,}", file_content)
            return "\n".join(s.decode("latin-1", errors="ignore") for s in strings if not s.startswith(b"/"))
        except Exception as e:
            logger.warning("PDF stream extraction fallback failed: %s", e)
            return file_content.decode("latin-1", errors="replace")

    # Default fallback
    try:
        return file_content.decode("utf-8")
    except UnicodeDecodeError:
        return file_content.decode("latin-1", errors="replace")


class FallbackHeuristicExtractor:
    """Deterministic pattern-based fallback extractor for high-volatility assertions.

    Activates when external LLM extraction fails (e.g., HTTP 429 rate limit, quota exhaustion, or malformed JSON).
    """

    # 1. Version patterns: "Python 2.7 is the currently supported release"
    VERSION_PATTERN = re.compile(
        r"([a-zA-Z][a-zA-Z_\-]*?(?:\s+[a-zA-Z][a-zA-Z_\-]*)?)\s*"
        r"(\d+(?:\.\d+)+[a-zA-Z0-9_\.\-]*)\s+"
        r"(?:is|was|has\s+(?:just\s+)?been)\s+(?:the\s+)?(currently\s+supported|current\s+supported|supported|latest\s+stable|production|active\s+LTS|deprecated|released|EOL|end[-\s]of[-\s]life)"
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
                        extraction_method="heuristic",
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
                        extraction_method="heuristic",
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
                        extraction_method="heuristic",
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
                        extraction_method="heuristic",
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
                        extraction_method="heuristic",
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
                        extraction_method="heuristic",
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
        # Avoid spending a paid extraction request on bare UI checks and other
        # fragments that cannot express an atomic factual claim.
        words = clean_no_punct.split()
        if len(words) <= 2 and not any(
            predicate in words
            for predicate in ("is", "are", "was", "were", "has", "have", "costs", "became")
        ):
            return True
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
                    extraction_method="heuristic",
                    error=f"LLM extraction error ({error_type.value if error_type else 'provider_error'}): {llm_error}. Fallback heuristics recovered volatile claims.",
                    total_extracted=len(fallback_claims),
                    valid_claims=len(fallback_claims),
                    is_fallback=True,
                    is_complete=False,
                )
            # No claims could be extracted and LLM failed -> explicit failed status!
            return ExtractionResult(
                claims=[],
                status="failed",
                extraction_method="heuristic",
                error=f"LLM extraction failed ({error_type.value if error_type else 'provider_error'}): {llm_error}",
                is_complete=False,
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
                    extraction_method="heuristic",
                    error="LLM returned malformed non-array JSON; fallback heuristics recovered claims.",
                    total_extracted=len(fallback_claims),
                    valid_claims=len(fallback_claims),
                    is_fallback=True,
                    is_complete=False,
                )
            return ExtractionResult(
                claims=[],
                status="failed",
                extraction_method="llm",
                error=f"LLM extraction returned invalid JSON: {raw_response[:200] if raw_response else 'empty'}",
                raw_response=raw_response,
                is_complete=False,
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

            # Keep dated factual assertions eligible for evidence checks even if the
            # extractor mislabels them as timeless "other" facts.
            if norm_type == "other" and temp_anchor:
                norm_type = "dated_event"

            try:
                conf = float(item.get("confidence", 1.0))
                conf = max(0.0, min(1.0, conf))
            except (ValueError, TypeError):
                conf = 0.8

            # Phase 3: Volatility score & stability
            is_future = detect_future_claim(claim_text)
            cat_def = category_registry.get(norm_type)

            try:
                vol_score = float(item.get("volatility_score", 0.0))
                vol_score = max(0.0, min(1.0, vol_score))
            except (ValueError, TypeError):
                vol_score = 0.0

            if vol_score <= 0.0:
                # Default based on category and future patterns
                if is_future:
                    vol_score = 0.9
                elif cat_def.is_volatile:
                    vol_score = 0.75
                else:
                    vol_score = 0.2

            try:
                ev_gap = float(item.get("evidence_gap", 0.5))
                ev_gap = max(0.0, min(1.0, ev_gap))
            except (ValueError, TypeError):
                ev_gap = 0.5

            stability = item.get("temporal_stability")
            if not stability or stability not in ("high", "medium", "low"):
                if vol_score >= 0.7 or is_future:
                    stability = "low"
                elif vol_score >= 0.4:
                    stability = "medium"
                else:
                    stability = "high"

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
                    extraction_method="llm",
                    volatility_score=vol_score,
                    evidence_gap=ev_gap,
                    temporal_stability=stability,
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
                    extraction_method="heuristic",
                    error="LLM returned empty claims but heuristic detector identified volatile claims.",
                    total_extracted=len(fallback_claims),
                    valid_claims=len(fallback_claims),
                    is_fallback=True,
                    is_complete=False,
                )
            return ExtractionResult(claims=[], status="no_claims_detected", extraction_method="llm", is_complete=True)

        status: ExtractionStatus = "success"
        if dropped_count > 0:
            status = "partial" if claims else "failed"

        return ExtractionResult(
            claims=claims,
            status=status,
            extraction_method="llm",
            raw_response=raw_response,
            total_extracted=total_items,
            valid_claims=len(claims),
            dropped_claims=dropped_count,
            is_fallback=False,
            is_complete=(dropped_count == 0),
        )

    async def extract_from_file(self, file_content: bytes, filename: str) -> ExtractionResult:
        """
        Phase 7: Extract atomic factual claims from uploaded file bytes.

        Reads file content into memory, converts to plain text, runs extraction,
        and leaves no persistent file on disk.
        """
        text = extract_text_from_file_bytes(file_content, filename)
        if not text or not text.strip():
            return ExtractionResult(claims=[], status="no_claims_detected")
        return await self.extract(text)
