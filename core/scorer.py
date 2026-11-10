"""FreshGate Multi-Dimensional Freshness & Truth Scoring Engine.

Separates and computes four distinct dimensions:
1. Verdict Quality (truth status): confirmed (1.0), partially_supported (0.70), unverifiable (0.50), contradicted (0.10).
2. Evidence Recency: 1.0 for <=30 days, linear decay to 0.5 at 365 days, floor 0.5.
3. Source Authority: Independent domain authority with primary source bonus (+0.10).
4. Agreement & Verification Confidence: Consensus across independent sources (min(1, N/3), floor 0.4).

Formula:
  Score = 100 * VerdictQuality * EvidenceRecency * AgreementFactor * (0.8 + 0.2 * SourceAuthority)
"""

import math
import time
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from core.models import Claim, Source

VERDICT_QUALITY_WEIGHTS = {
    "confirmed": 1.0,
    "partially_supported": 0.70,
    "unverifiable": 0.50,
    "contradicted": 0.10,
}


def compute_verdict_weight(verdict: Optional[str]) -> float:
    """Return verdict quality weight: confirmed=1.0, partially_supported=0.7, unverifiable=0.5, contradicted=0.1."""
    if not verdict:
        return 0.50
    return VERDICT_QUALITY_WEIGHTS.get(verdict.strip().lower(), 0.50)


def _parse_date_to_timestamp(date_str: Optional[str]) -> Optional[float]:
    """Parse various ISO and standard date formats into a UTC timestamp."""
    if not date_str:
        return None
    clean_date = date_str.strip().replace("Z", "+00:00")
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%B %d, %Y",
    ):
        try:
            dt = datetime.strptime(
                clean_date[:19] if "T" in clean_date and fmt == "%Y-%m-%d" else clean_date,
                fmt,
            )
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except (ValueError, TypeError):
            continue
    return None


def compute_recency_factor(sources: List[Source]) -> float:
    """Compute publication recency factor from the newest supporting source."""
    now = time.time()
    min_age_days: Optional[float] = None

    for source in sources:
        ts = _parse_date_to_timestamp(source.published)
        if ts is not None and ts <= now:
            age_days = (now - ts) / 86400.0
            if min_age_days is None or age_days < min_age_days:
                min_age_days = age_days

    if min_age_days is None or min_age_days < 0:
        return 0.5

    if min_age_days <= 30.0:
        return 1.0
    elif min_age_days >= 365.0:
        return 0.5
    else:
        # Linear decay from 1.0 at 30 days to 0.5 at 365 days
        return 1.0 - 0.5 * ((min_age_days - 30.0) / (365.0 - 30.0))


def compute_source_authority_factor(sources: List[Source]) -> float:
    """Compute average source authority score with primary source bonus."""
    if not sources:
        return 0.50

    scores = [s.authority_score for s in sources if s.authority_score is not None]
    avg_score = sum(scores) / len(scores) if scores else 0.5

    # Primary source bonus
    has_primary = any(s.is_primary for s in sources)
    if has_primary:
        avg_score = min(1.0, avg_score + 0.10)

    return max(0.40, min(1.0, round(avg_score, 2)))


def compute_agreement_factor(agreeing_sources_count: int) -> float:
    """Compute agreement factor across independent sources."""
    if agreeing_sources_count <= 0:
        return 0.40
    raw = min(1.0, agreeing_sources_count / 3.0)
    return max(0.40, raw)


def compute_score_dimensions(claim: Claim) -> Dict[str, Any]:
    """Calculate all four distinct dimensions and return the composite score."""
    vq = compute_verdict_weight(claim.verdict)
    recency = compute_recency_factor(claim.sources)
    authority = compute_source_authority_factor(claim.sources)
    agreement = compute_agreement_factor(len(claim.sources))

    # Authority multiplier scales between 0.88 and 1.0
    authority_multiplier = 0.80 + 0.20 * authority
    raw_score = 100.0 * vq * recency * agreement * authority_multiplier
    final_score = int(round(raw_score))
    final_score = max(0, min(100, final_score))

    return {
        "verdict_quality": vq,
        "recency": round(recency, 2),
        "source_authority": authority,
        "confidence": round(agreement, 2),
        "final_score": final_score,
    }


def calculate_score(claim: Claim) -> int:
    """Calculate composite freshness score (0 - 100)."""
    return compute_score_dimensions(claim)["final_score"]
