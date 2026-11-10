from typing import List
from core.models import Claim

VOLATILE_TYPES = {"pricing", "version", "law", "leadership"}


def is_volatile(claim_type: str) -> bool:
    """Check if claim type is considered volatile and subject to verification."""
    return claim_type.strip().lower() in VOLATILE_TYPES


def triage_claims(claims: List[Claim]) -> List[Claim]:
    """Filter claims to keep only volatile ones (pricing, version, law, leadership)."""
    return [c for c in claims if is_volatile(c.type)]
