import math
import time
from datetime import datetime, timezone
from typing import List, Optional
from core.models import Claim, Source

VERDICT_WEIGHTS = {
    "confirmed": 1.0,
    "unverifiable": 0.5,
    "contradicted": 0.1,
}


def compute_verdict_weight(verdict: Optional[str]) -> float:
    """Return verdict weight: confirmed=1.0, unverifiable=0.5, contradicted=0.1."""
    if not verdict:
        return 0.5
    return VERDICT_WEIGHTS.get(verdict.strip().lower(), 0.5)


def _parse_date_to_timestamp(date_str: Optional[str]) -> Optional[float]:
    """Parse various ISO date formats into unix timestamp."""
    if not date_str:
        return None
    # Strip time part if only date is provided
    clean_date = date_str.strip().replace("Z", "+00:00")
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%B %d, %Y",
    ):
        try:
            dt = datetime.strptime(clean_date[:19] if "T" in clean_date and fmt == "%Y-%m-%d" else clean_date, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except (ValueError, TypeError):
            continue
    return None


def compute_recency_factor(sources: List[Source]) -> float:
    """Compute recency factor from the newest supporting source.

    - 1.0 if newest supporting source is <= 30 days old.
    - Decaying linearly to 0.5 at 365 days.
    - Floor 0.5 if > 365 days or date is missing/unknown.
    """
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


def compute_agreement_factor(agreeing_sources_count: int) -> float:
    """Compute agreement factor: min(1, agreeing_sources / 3), with floor 0.4."""
    if agreeing_sources_count <= 0:
        return 0.4
    raw = min(1.0, agreeing_sources_count / 3.0)
    return max(0.4, raw)


def calculate_score(claim: Claim) -> int:
    """Calculate freshness score (0 - 100) per SPEC.md:

    score = 100 * verdict_weight * recency_factor * agreement_factor
    """
    weight = compute_verdict_weight(claim.verdict)
    recency = compute_recency_factor(claim.sources)
    # Number of sources returned that support the verdict
    agreement = compute_agreement_factor(len(claim.sources))

    raw_score = 100.0 * weight * recency * agreement
    score = int(round(raw_score))
    return max(0, min(100, score))
