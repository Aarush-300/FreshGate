from typing import List
from core.models import Claim
from core.categories import category_registry

# Backward-compatible set
VOLATILE_TYPES = set(category_registry.get_all_volatile())


def is_volatile(claim_type: str) -> bool:
    """Check if claim type is marked volatile in the category registry."""
    return category_registry.is_volatile(claim_type)


def triage_claims(claims: List[Claim]) -> List[Claim]:
    """Filter claims to keep only volatile ones subject to live verification."""
    return [c for c in claims if is_volatile(c.type)]
