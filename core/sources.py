"""FreshGate Source Validation Module.

Evaluates domain authority, distinguishes primary from secondary sources,
detects syndication/reprints, and measures evidence relevance.
"""

from typing import List, Optional, Set
from urllib.parse import urlparse
import re
from core.models import Source
from core.categories import category_registry

# Known primary government and regulatory TLDs / domains
GOVERNMENT_DOMAINS = {
    "gov", "europa.eu", "legislation.gov.uk", "whitehouse.gov",
    "congress.gov", "sec.gov", "ftc.gov", "fda.gov", "cdc.gov", "nih.gov"
}

# Authoritative wire services and financial news
TIER1_NEWS_DOMAINS = {
    "reuters.com", "apnews.com", "bloomberg.com", "ft.com", "wsj.com",
    "nytimes.com", "bbc.com", "theguardian.com", "cnbc.com"
}

# Uncurated user-generated content or blog platforms
LOW_AUTHORITY_DOMAINS = {
    "reddit.com", "quora.com", "medium.com", "substack.com",
    "blogspot.com", "wordpress.com", "tumblr.com", "twitter.com", "x.com"
}


def extract_clean_domain(url: str) -> str:
    """Extract clean domain name without leading 'www.' or port numbers."""
    if not url:
        return ""
    try:
        netloc = urlparse(url.strip()).netloc.lower()
        if ":" in netloc:
            netloc = netloc.split(":")[0]
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc
    except Exception:
        return ""


class SourceValidator:
    """Evaluates the credibility, independence, and relevance of web evidence."""

    def __init__(self):
        self.category_registry = category_registry

    def evaluate_source(
        self,
        url: str,
        snippet: str,
        category: str,
        entities: Optional[List[str]] = None,
        published: Optional[str] = None,
    ) -> Source:
        """Score source authority, determine if it is primary, and compute relevance."""
        domain = extract_clean_domain(url)
        entities = [e.lower() for e in (entities or [])]
        category_lower = category.lower()

        authority_score = 0.5
        is_primary = False

        # 1. Government and official regulatory domains
        if any(domain.endswith(f".{g}") or domain == g for g in GOVERNMENT_DOMAINS):
            if category_lower in ("law", "government_policy", "public_health"):
                authority_score = 0.95
                is_primary = True
            else:
                authority_score = 0.85

        # 2. Entity match for official vendor domains
        # e.g., if entity is "Python", and domain is "python.org"
        elif any(e in domain or domain.startswith(e.replace(" ", "")) for e in entities if len(e) > 3):
            if category_lower in ("version", "pricing", "company_info", "product_availability"):
                authority_score = 0.95
                is_primary = True
            else:
                authority_score = 0.85

        # 3. Preferred category domains
        cat_def = self.category_registry.get(category_lower)
        if cat_def and any(pref in domain for pref in cat_def.preferred_domains):
            authority_score = max(authority_score, 0.85)

        # 4. Tier-1 news and wire services
        elif any(domain.endswith(t) for t in TIER1_NEWS_DOMAINS):
            authority_score = 0.85

        # 5. Low-authority forums and uncurated blogs
        elif any(domain.endswith(l) for l in LOW_AUTHORITY_DOMAINS):
            authority_score = 0.35

        # Relevance scoring based on entity mentions in snippet
        relevance_score = 0.6
        if snippet:
            snippet_lower = snippet.lower()
            matched_entities = sum(1 for e in entities if e in snippet_lower)
            if entities and matched_entities > 0:
                relevance_score = min(1.0, 0.6 + 0.2 * matched_entities)

        return Source(
            url=url,
            domain=domain,
            published=published,
            snippet=snippet,
            authority_score=round(authority_score, 2),
            is_primary=is_primary,
            relevance_score=round(relevance_score, 2),
        )

    def filter_independent_sources(self, sources: List[Source]) -> List[Source]:
        """Group or deduplicate syndicated reprints sharing the same core reporting."""
        seen_domains: Set[str] = set()
        seen_snippet_hashes: Set[str] = set()
        independent: List[Source] = []

        for src in sources:
            domain = src.domain
            # Deduplicate multiple articles from the exact same domain
            if domain in seen_domains:
                continue

            # Check for syndicated text chunks (first 40 characters)
            snippet_lead = re.sub(r"\W+", "", src.snippet[:50].lower())
            if snippet_lead and snippet_lead in seen_snippet_hashes:
                continue

            seen_domains.add(domain)
            if snippet_lead:
                seen_snippet_hashes.add(snippet_lead)

            independent.append(src)

        return independent if independent else sources
