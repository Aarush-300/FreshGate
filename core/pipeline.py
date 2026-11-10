"""FreshGate End-to-End Freshness Verification Pipeline.

Coordinates extraction, volatility triage, structured caching, live verification,
multi-dimensional scoring, and execution status reporting.
"""

import logging
from typing import Optional, List, Dict, Any
from core.models import (
    Claim,
    Summary,
    VerificationResult,
    ExtractionResult,
    PipelineStatus,
)
from core.extractor import ClaimExtractor
from core.triage import triage_claims
from core.verifier import ClaimVerifier
from core.scorer import calculate_score
from core.cache import SQLiteClaimCache
from core.metrics import metrics

logger = logging.getLogger(__name__)


class FreshGatePipeline:
    """End-to-end freshness verification pipeline with explicit lifecycle states."""

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
                summary=Summary(
                    total=0,
                    checked=0,
                    stale=0,
                    avg_score=None,
                    status="no_claims_detected",
                    message="No input text provided.",
                    extraction_status="no_claims_detected",
                ),
                status="no_claims_detected",
            )

        # Step 1: Extract discrete factual claims
        extraction_res: ExtractionResult
        try:
            raw_ext = await self.extractor.extract(answer_text)
            if isinstance(raw_ext, list):
                # Backward-compatibility for mocked tests returning list
                extraction_res = ExtractionResult(
                    claims=raw_ext,
                    status="success" if raw_ext else "no_claims_detected",
                    total_extracted=len(raw_ext),
                    valid_claims=len(raw_ext),
                )
            else:
                extraction_res = raw_ext
        except Exception as e:
            logger.error("ClaimExtractor threw unhandled exception: %s", e)
            extraction_res = ExtractionResult(
                claims=[],
                status="failed",
                error=str(e),
            )

        # Handle Extraction Failures explicitly: NEVER assign a 100 score to a failed extraction!
        if extraction_res.status == "failed":
            logger.warning("Extraction failed permanently: %s", extraction_res.error)
            return VerificationResult(
                claims=[],
                summary=Summary(
                    total=0,
                    checked=0,
                    stale=0,
                    avg_score=None,
                    status="extraction_failed",
                    message=f"Claim extraction failed: {extraction_res.error}",
                    extraction_status="failed",
                ),
                status="extraction_failed",
                error=extraction_res.error,
            )

        # Handle genuinely claim-free text (greetings, pleasantries)
        if extraction_res.status == "no_claims_detected" or not extraction_res.claims:
            return VerificationResult(
                claims=[],
                summary=Summary(
                    total=0,
                    checked=0,
                    stale=0,
                    avg_score=100,
                    status="no_claims_detected",
                    message="No volatile factual claims found. Response passed through as stable.",
                    extraction_status="no_claims_detected",
                ),
                status="no_claims_detected",
            )

        all_claims = extraction_res.claims
        total_extracted = len(all_claims)
        metrics.inc_claims_extracted(total_extracted)

        # Step 2: Triage - filter volatile claims, normalizing categories
        volatile_claims = triage_claims(all_claims)
        metrics.inc_claims_checked(len(volatile_claims))

        # If claims were extracted but NONE are volatile (all evergreen static facts)
        if not volatile_claims:
            return VerificationResult(
                claims=all_claims,
                summary=Summary(
                    total=total_extracted,
                    checked=0,
                    stale=0,
                    avg_score=100,
                    status="completed",
                    message="All extracted claims are static or non-volatile facts.",
                    extraction_status=extraction_res.status,
                ),
                status="completed",
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
        verification_error: Optional[str] = None
        if misses_to_verify:
            try:
                verified_misses = await self.verifier.verify_claims(misses_to_verify)
                for miss_idx, verified_claim in zip(misses_indices, verified_misses):
                    # Calculate freshness score
                    verified_claim.score = calculate_score(verified_claim)
                    verified_claim.cached = False
                    # Store in cache
                    entity = verified_claim.entities[0] if verified_claim.entities else "general"
                    self.cache.set(verified_claim, entity=entity)
                    processed_claims[miss_idx] = verified_claim
            except Exception as e:
                logger.error("Batch claim verification failed: %s", e)
                verification_error = str(e)
                # Fail-open individual misses to unverifiable
                for miss_idx, unverified_claim in zip(misses_indices, misses_to_verify):
                    unverified_claim.verdict = "unverifiable"
                    unverified_claim.explanation = f"Verification error: {e}"
                    unverified_claim.score = None
                    processed_claims[miss_idx] = unverified_claim

        # Filter out any None values (guard safety)
        final_claims: List[Claim] = [c for c in processed_claims if c is not None]

        # Step 5: Compute summary metrics & determine pipeline status
        stale_count = sum(1 for c in final_claims if c.verdict == "contradicted")
        unverifiable_count = sum(1 for c in final_claims if c.verdict == "unverifiable")
        metrics.inc_claims_stale(stale_count)

        valid_scores = [c.score for c in final_claims if c.score is not None]
        avg_score: Optional[int] = (
            int(round(sum(valid_scores) / len(valid_scores))) if valid_scores else None
        )

        pipeline_status: PipelineStatus = "completed"
        message: str = f"Verification complete. Stale claims detected: {stale_count}."

        if unverifiable_count == len(final_claims) and verification_error:
            pipeline_status = "verification_failed"
            avg_score = None
            message = f"External verification failed: {verification_error}"
        elif unverifiable_count > 0 or extraction_res.status == "partial":
            pipeline_status = "partially_verified" if unverifiable_count < len(final_claims) else "completed_with_warnings"
            message = f"Partially verified: {len(final_claims) - unverifiable_count}/{len(final_claims)} claims verified."
            if extraction_res.status == "partial":
                message += " (Extraction partially degraded)."

        summary = Summary(
            total=total_extracted,
            checked=len(final_claims),
            stale=stale_count,
            avg_score=avg_score,
            status=pipeline_status,
            message=message,
            extraction_status=extraction_res.status,
        )

        return VerificationResult(
            claims=final_claims,
            summary=summary,
            status=pipeline_status,
            error=verification_error or extraction_res.error,
        )
