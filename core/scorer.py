"""FreshGate Multi-Dimensional Freshness & Truth Scoring Engine (V2).

Computes and separates five distinct dimensions:
1. Factual Truth (Verdict Quality):
   - confirmed: 1.0
   - partially_supported: 0.70
   - unverifiable: 0.45
   - contradicted: 0.05 (hard cap at 10)
2. Evidence Recency:
   - 1.0 for <= 30 days
   - linear decay to 0.50 at 365 days
   - floor of 0.50 for older evidence
3. Source Authority:
   - Evaluated across primary vs. secondary domains with +0.10 primary source bonus
   - Normalized between 0.40 and 1.0
4. Evidence Agreement:
   - Consensus across independent sources: min(1.0, count / 3.0), floor 0.40
5. Verification Confidence:
   - Calibrated confidence combining judge certainty and evidence consistency (0.0 - 1.0)

Formula:
  RawScore = 100.0 * factual_truth * evidence_recency * evidence_agreement * (0.80 + 0.20 * source_authority)
  If contradicted: FinalScore <= 10
  If unverifiable: FinalScore <= 50

Phase 1 Enhancement: Temporal Freshness Scoring with Multiple Decay Curves
- Supports linear, exponential, and step decay curves
- Per-claim-type half-life configuration
- Predictive indicators for monitoring
"""

import math
import time
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from core.models import Claim, Source
from core.config import get_config

VERDICT_QUALITY_WEIGHTS = {
    "confirmed": 1.0,
    "partially_supported": 0.70,
    "unverifiable": 0.50,
    "contradicted": 0.10,
    "unknown": 0.50,
}


def compute_verdict_weight(verdict: Optional[str]) -> float:
    """Return factual truth weight: confirmed=1.0, partially_supported=0.7, unverifiable=0.50, contradicted=0.10."""
    if not verdict:
        return 0.50
    return VERDICT_QUALITY_WEIGHTS.get(verdict.strip().lower(), 0.50)


def _parse_date_to_timestamp(date_str: Optional[str]) -> Optional[float]:
    """Parse ISO and standard date formats into UTC timestamp."""
    if not date_str:
        return None
    clean_date = date_str.strip()
    # Try native ISO parser first (handles ISO 8601, offsets, Z, microseconds)
    try:
        iso_str = clean_date.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        pass

    for fmt in (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%B %d, %Y",
        "%b %d, %Y",
        "%d %b %Y",
        "%d %B %Y",
    ):
        try:
            dt = datetime.strptime(clean_date, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except (ValueError, TypeError):
            continue
    return None


def compute_recency_factor(sources: List[Source]) -> float:
    """Compute publication recency factor from newest supporting source."""
    now = time.time()
    min_age_days: Optional[float] = None

    for source in sources:
        ts = _parse_date_to_timestamp(source.published)
        if ts is not None:
            # Future timestamps within a small delta (e.g. timezone or clock skew) are treated as now (age 0)
            age_days = max(0.0, (now - ts) / 86400.0)
            if min_age_days is None or age_days < min_age_days:
                min_age_days = age_days

    if min_age_days is None or min_age_days < 0:
        return 0.50

    if min_age_days <= 30.0:
        return 1.0
    elif min_age_days >= 365.0:
        return 0.50
    else:
        # Linear decay from 1.0 at 30 days to 0.50 at 365 days
        return 1.0 - 0.50 * ((min_age_days - 30.0) / (365.0 - 30.0))


def compute_source_authority_factor(sources: List[Source]) -> float:
    """Compute average source authority score with primary source bonus."""
    if not sources:
        return 0.50

    scores = [s.authority_score for s in sources if s.authority_score is not None]
    avg_score = sum(scores) / len(scores) if scores else 0.5
    has_primary = any(s.is_primary for s in sources)
    if has_primary:
        avg_score = min(1.0, avg_score + 0.10)

    return max(0.40, min(1.0, round(avg_score, 2)))


def compute_agreement_factor(agreeing_sources_count: int) -> float:
    """Compute consensus agreement factor across independent sources."""
    if agreeing_sources_count <= 0:
        return 0.40
    raw = min(1.0, agreeing_sources_count / 3.0)
    return max(0.40, raw)


def compute_score_dimensions(claim: Claim) -> Dict[str, Any]:
    """Calculate all five distinct dimensions and return composite score."""
    truth = compute_verdict_weight(claim.verdict)
    recency = compute_recency_factor(claim.sources)
    authority = compute_source_authority_factor(claim.sources)
    agreement = compute_agreement_factor(len(claim.sources))
    confidence = max(0.1, min(1.0, float(claim.confidence if claim.confidence is not None else 0.8)))

    # Composite formula
    authority_multiplier = 0.80 + 0.20 * authority
    raw_score = 100.0 * truth * recency * agreement * authority_multiplier
    final_score = int(round(raw_score))

    # Critical rule constraints:
    # 1. Contradicted claim must NEVER receive a high freshness score (hard capped at 10)
    if claim.verdict == "contradicted":
        final_score = min(10, final_score)
    # 2. Unverifiable claim must NEVER be considered confirmed (hard capped at 50)
    elif claim.verdict == "unverifiable":
        final_score = min(50, final_score)

    final_score = max(0, min(100, final_score))

    # Phase 1: Temporal Freshness - compute temporal decay factor
    temporal_decay = compute_temporal_decay(claim)

    # Phase 3: Predictive indicators
    predicted_flip_probability = calculate_flip_probability(claim)
    recommended_check_interval = calculate_recommended_interval(claim, predicted_flip_probability)

    return {
        "verdict_quality": truth,
        "factual_truth": truth,
        "recency": round(recency, 2),
        "evidence_recency": round(recency, 2),
        "source_authority": authority,
        "evidence_agreement": round(agreement, 2),
        "verification_confidence": round(confidence, 2),
        "final_score": final_score,
        # Phase 1: Temporal decay
        "temporal_decay": round(temporal_decay, 2),
        # Phase 3: Predictive indicators
        "predicted_flip_probability": round(predicted_flip_probability, 3),
        "recommended_check_interval": recommended_check_interval,
    }


def calculate_score(claim: Claim) -> int:
    """Calculate composite freshness score (0 - 100)."""
    return compute_score_dimensions(claim)["final_score"]


# Phase 1: Temporal Freshness Scoring with Multiple Decay Curves
def compute_temporal_decay(claim: Claim) -> float:
    """
    Compute temporal decay factor based on claim's half-life and degradation curve.

    Supports three curves:
    - linear: Linear decay from 1.0 to 0.5 over half-life period
    - exponential: Exponential decay with half-life
    - step: Step function - 1.0 until half-life, then 0.5

    Returns factor between min_temporal_score/100 and 1.0
    """
    config = get_config()
    now = time.time()

    # Get half-life for this claim type
    half_life = claim.freshness_half_life_seconds
    if half_life is None:
        # Try to get from config
        half_life = config.freshness_half_life_defaults.get(claim.type, 86400)  # default 24 hours

    # Get degradation curve
    curve = claim.degradation_curve or config.default_degradation_curve

    # Get last verified timestamp
    last_verified = claim.last_verified_at
    if last_verified:
        ts = _parse_date_to_timestamp(last_verified)
        if ts is not None:
            age_seconds = max(0.0, now - ts)
        else:
            age_seconds = 0.0
    else:
        # No verification timestamp, assume fresh
        age_seconds = 0.0

    # Apply decay curve
    if curve == "linear":
        # Linear decay from 1.0 to 0.5 over half-life
        if age_seconds <= half_life:
            factor = 1.0 - 0.5 * (age_seconds / half_life)
        else:
            factor = 0.5
    elif curve == "exponential":
        # Exponential decay with half-life
        factor = 0.5 ** (age_seconds / half_life)
    elif curve == "step":
        # Step function
        factor = 1.0 if age_seconds <= half_life else 0.5
    else:
        # Default to exponential
        factor = 0.5 ** (age_seconds / half_life)

    # Apply minimum floor
    min_factor = config.min_temporal_score / 100.0
    return max(min_factor, min(1.0, factor))


def calculate_flip_probability(claim: Claim) -> float:
    """
    Calculate probability that claim's verdict will flip on next verification.

    Based on:
    - volatility_score: inherent volatility of the claim type
    - evidence_gap: confidence in evidence coverage (1 - evidence_gap = coverage)
    - temporal decay: how fresh the evidence is
    """
    volatility = max(0.0, min(1.0, claim.volatility_score))
    evidence_confidence = 1.0 - max(0.0, min(1.0, claim.evidence_gap))
    temporal_freshness = compute_temporal_decay(claim)

    # Probability increases with volatility, decreases with evidence coverage and freshness
    # Base probability = volatility * (1 - evidence_confidence) * (1 - temporal_freshness)
    base_prob = volatility * (1.0 - evidence_confidence) * (1.0 - temporal_freshness)

    # Scale to 0-1 range
    return max(0.0, min(1.0, base_prob))


def calculate_recommended_interval(claim: Claim, flip_probability: float) -> int:
    """
    Calculate recommended check interval in seconds based on flip probability and half-life.
    """
    config = get_config()
    half_life = claim.freshness_half_life_seconds
    if half_life is None:
        half_life = config.freshness_half_life_defaults.get(claim.type, 86400)

    # High flip probability -> check more frequently (minimum 1 hour)
    # Low flip probability -> check at half-life interval (maximum 7 days)
    if flip_probability > 0.7:
        return min(half_life, 3600)  # 1 hour max
    elif flip_probability > 0.4:
        return min(half_life, 21600)  # 6 hours max
    elif flip_probability > 0.2:
        return min(half_life, 86400)  # 24 hours max
    else:
        return half_life  # Use half-life as interval
