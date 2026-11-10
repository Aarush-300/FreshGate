import logging
from typing import Optional, List
from core.models import Claim, Summary, VerificationResult
from core.extractor import ClaimExtractor
from core.triage import triage_claims
from core.verifier import ClaimVerifier
from core.scorer import calculate_score
from core.cache import SQLiteClaimCache
from core.metrics import metrics

logger = logging.getLogger(__name__)


class FreshGatePipeline:
    """End-to-end freshness verification pipeline."""

    def __init__(
        self,
        extractor: Optional[ClaimExtractor] = None,
        verifier: Optional[ClaimVerifier] = None,
        cache: Optional[SQLiteClaimCache] = None,
    ):
        self.extractor = extractor or ClaimExtractor()
        self.verifier = verifier or ClaimVerifier()
        self.cache = cache or SQLiteClaimCache()

    async def verify(self, answer_text: str) -> VerificationResult:
        """Execute full FreshGate verification pipeline on an answer string."""
        if not answer_text or not answer_text.strip():
            return VerificationResult(
                claims=[],
                summary=Summary(total=0, checked=0, stale=0, avg_score=100),
            )

        # Step 1: Extract discrete factual claims
        all_claims = await self.extractor.extract(answer_text)
        total_extracted = len(all_claims)
        metrics.inc_claims_extracted(total_extracted)

        if not all_claims:
            return VerificationResult(
                claims=[],
                summary=Summary(total=0, checked=0, stale=0, avg_score=100),
            )

        # Step 2: Triage - keep only volatile claims
        volatile_claims = triage_claims(all_claims)
        metrics.inc_claims_checked(len(volatile_claims))
        if not volatile_claims:
            return VerificationResult(
                claims=[],
                summary=Summary(total=total_extracted, checked=0, stale=0, avg_score=100),
            )

        # Step 3: Check cache for each volatile claim
        processed_claims: List[Optional[Claim]] = [None] * len(volatile_claims)
        misses_indices: List[int] = []
        misses_to_verify: List[Claim] = []

        for idx, claim in enumerate(volatile_claims):
            entity = claim.entities[0] if claim.entities else "general"
            cached_result = self.cache.get(claim.text, claim.type, entity=entity)
            if cached_result is not None:
                # Cache hit
                metrics.inc_cache_hit()
                cached_result.id = claim.id
                cached_result.cached = True
                processed_claims[idx] = cached_result
            else:
                # Cache miss
                metrics.inc_cache_miss()
                misses_indices.append(idx)
                misses_to_verify.append(claim)

        # Step 4: Verify cache misses concurrently
        if misses_to_verify:
            verified_misses = await self.verifier.verify_claims(misses_to_verify)
            for miss_idx, verified_claim in zip(misses_indices, verified_misses):
                # Calculate freshness score
                verified_claim.score = calculate_score(verified_claim)
                verified_claim.cached = False
                # Store in SQLite cache
                entity = verified_claim.entities[0] if verified_claim.entities else "general"
                self.cache.set(verified_claim, entity=entity)
                processed_claims[miss_idx] = verified_claim

        # Filter out any None values (guard safety)
        final_claims: List[Claim] = [c for c in processed_claims if c is not None]

        # Step 5: Compute summary metrics
        stale_count = sum(1 for c in final_claims if c.verdict == "contradicted")
        metrics.inc_claims_stale(stale_count)
        scores = [c.score for c in final_claims if c.score is not None]
        avg_score = int(round(sum(scores) / len(scores))) if scores else 100

        summary = Summary(
            total=total_extracted,
            checked=len(final_claims),
            stale=stale_count,
            avg_score=avg_score,
        )

        return VerificationResult(claims=final_claims, summary=summary)
