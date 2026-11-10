"""FreshGate Tavily Verifier & LLM Judge.

Tavily API Reference:
- Endpoint: POST https://api.tavily.com/search
- Docs: https://docs.tavily.com/docs/tavily-api/rest_api
- Auth: Authorization: Bearer <TAVILY_API_KEY> (or api_key field in payload)
- Payload: query, search_depth='basic', max_results=5, topic='general'
- Response: results array with 'url', 'content', 'published_date'
"""

import os
import json
import logging
import asyncio
import re
from typing import List, Optional, Dict, Any
import httpx

from core.models import Claim, Source, Verdict
from core.llm import LLMClient, OpenAILLMClient

logger = logging.getLogger(__name__)

TAVILY_API_URL = "https://api.tavily.com/search"

JUDGE_SYSTEM_PROMPT = """You are an expert fact-checking judge verifying the freshness and truth of factual claims against live web search results.

Compare the provided claim with the live search results evidence.
Determine whether the claim is:
- "confirmed": Supported and accurate based on recent sources.
- "contradicted": Outdated, false, superseded, or changed (e.g., prices lowered/raised, CEO resigned/replaced, law vetoed or passed, newer version released).
- "unverifiable": The search results do not provide enough clear, credible, or specific evidence to confirm or contradict the claim.

If "contradicted", formulate a concise and factual "correction" stating what the current reality is based on the evidence. If confirmed or unverifiable, "correction" must be null.
List the "supporting_urls" from the search results that justify your verdict.

Return ONLY valid JSON in this exact structure:
{
  "verdict": "confirmed" | "contradicted" | "unverifiable",
  "correction": "..." or null,
  "supporting_urls": ["..."]
}"""


def _clean_json_text(text: str) -> str:
    """Remove markdown fences and isolate JSON object."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()

    start_idx = cleaned.find("{")
    end_idx = cleaned.rfind("}")
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        cleaned = cleaned[start_idx : end_idx + 1]
    return cleaned


class TavilySearchClient:
    """Client for Tavily Search API with retry, backoff, and error recovery."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = TAVILY_API_URL,
        timeout: float = 8.0,
        max_retries: int = 2,
    ):
        self.api_key = api_key or os.getenv("TAVILY_API_KEY", "")
        self.base_url = base_url
        self.timeout = timeout
        self.max_retries = max_retries

    async def search(self, query: str, max_results: int = 5) -> List[Dict[str, Any]]:
        """Perform search on Tavily. Returns list of source items, or raises on failure."""
        if not self.api_key:
            logger.warning("TAVILY_API_KEY is not set. Cannot execute live web search.")
            return []

        payload = {
            "api_key": self.api_key,
            "query": query,
            "search_depth": "basic",
            "topic": "general",
            "max_results": max(1, min(max_results, 10)),
            "include_answer": False,
            "include_raw_content": False,
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(self.base_url, json=payload, headers=headers)
                    if resp.status_code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                        delay = 0.5 * (2**attempt)
                        logger.warning("Tavily returned %s, retrying in %.1fs...", resp.status_code, delay)
                        await asyncio.sleep(delay)
                        continue
                    resp.raise_for_status()
                    data = resp.json()
                    return data.get("results", [])
            except (httpx.RequestError, httpx.HTTPStatusError) as e:
                if attempt < self.max_retries:
                    delay = 0.5 * (2**attempt)
                    logger.warning("Tavily request error: %s, retrying in %.1fs...", e, delay)
                    await asyncio.sleep(delay)
                else:
                    raise e
        return []


class ClaimVerifier:
    """Verifies factual claims against live Tavily search results using an LLM judge."""

    def __init__(
        self,
        tavily_client: Optional[Any] = None,
        judge_llm: Optional[LLMClient] = None,
        concurrency_cap: int = 5,
    ):
        self.tavily_client = tavily_client or TavilySearchClient()
        self.judge_llm = judge_llm or OpenAILLMClient()
        self.concurrency_cap = concurrency_cap
        self._semaphore = asyncio.Semaphore(concurrency_cap)

    def _build_search_query(self, claim: Claim) -> str:
        """Create a targeted search query for the claim."""
        # For volatile claims, query the subject directly with current year or status
        text = claim.text.strip().rstrip(".")
        if claim.type == "pricing":
            return f"{text} current pricing"
        elif claim.type == "leadership":
            return f"{text} current who is"
        elif claim.type == "version":
            return f"{text} latest version release date"
        elif claim.type == "law":
            return f"{text} status current law"
        return text

    async def verify_claim(self, claim: Claim) -> Claim:
        """Verify an individual claim against live sources. Fail open to 'unverifiable' on errors."""
        async with self._semaphore:
            query = self._build_search_query(claim)

            # Step 1: Tavily search
            try:
                raw_results = await self.tavily_client.search(query, max_results=5)
            except Exception as e:
                logger.error("Tavily search failed for claim %s: %s. Returning unverifiable.", claim.id, e)
                claim.verdict = "unverifiable"
                claim.sources = []
                claim.correction = None
                return claim

            if not raw_results:
                claim.verdict = "unverifiable"
                claim.sources = []
                claim.correction = None
                return claim

            # Parse returned sources
            sources: List[Source] = []
            for item in raw_results:
                url = item.get("url") or ""
                snippet = item.get("content") or item.get("snippet") or ""
                published = item.get("published_date") or item.get("published")
                if url and snippet:
                    sources.append(Source(url=url, published=published, snippet=snippet))

            claim.sources = sources

            # Step 2: LLM Judge evaluation
            evidence_blocks = []
            for i, src in enumerate(sources[:5], 1):
                date_str = f" (Date: {src.published})" if src.published else ""
                evidence_blocks.append(f"[{i}] URL: {src.url}{date_str}\nSnippet: {src.snippet}")
            evidence_text = "\n\n".join(evidence_blocks)

            judge_prompt = (
                f"Claim to evaluate ({claim.type}):\n\"{claim.text}\"\n\n"
                f"Search Results Evidence:\n{evidence_text}\n\n"
                f"Determine verdict ('confirmed', 'contradicted', 'unverifiable'), "
                f"correction if contradicted, and supporting_urls."
            )

            try:
                raw_judge = await self.judge_llm.complete(judge_prompt, system_prompt=JUDGE_SYSTEM_PROMPT)
                cleaned = _clean_json_text(raw_judge)
                data = json.loads(cleaned)

                verdict = str(data.get("verdict", "unverifiable")).strip().lower()
                if verdict not in ("confirmed", "contradicted", "unverifiable"):
                    verdict = "unverifiable"

                claim.verdict = verdict
                claim.correction = data.get("correction") if verdict == "contradicted" else None

                # Keep supporting URLs prioritized if present
                supporting_urls = set(data.get("supporting_urls") or [])
                if supporting_urls:
                    prioritized = [s for s in sources if s.url in supporting_urls]
                    others = [s for s in sources if s.url not in supporting_urls]
                    claim.sources = prioritized + others

            except Exception as e:
                logger.error("LLM judge failed for claim %s: %s. Defaulting to unverifiable.", claim.id, e)
                claim.verdict = "unverifiable"
                claim.correction = None

            return claim

    async def verify_claims(self, claims: List[Claim]) -> List[Claim]:
        """Verify multiple claims concurrently, respecting concurrency limit."""
        if not claims:
            return []

        tasks = [self.verify_claim(c) for c in claims]
        verified_claims = await asyncio.gather(*tasks)
        return list(verified_claims)
