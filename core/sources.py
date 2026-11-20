"""FreshGate Source Validation Module.

Evaluates domain authority, distinguishes primary from secondary sources,
detects syndication/reprints, and measures evidence relevance.
Phase 2: Multi-Source Evidence Triangulation with source type authority multipliers.
"""

from typing import List, Optional, Set, Dict
from urllib.parse import urlparse
import re
from core.models import Source
from core.categories import category_registry

# Known primary government and regulatory TLDs / domains
GOVERNMENT_DOMAINS = {
    "gov", "europa.eu", "legislation.gov.uk", "whitehouse.gov",
    "congress.gov", "sec.gov", "ftc.gov", "fda.gov", "cdc.gov", "nih.gov"
}

# Authoritative cybersecurity vulnerability databases
CYBERSECURITY_DOMAINS = {
    "cve.org", "nvd.nist.gov", "cisa.gov", "mitre.org", "kb.cert.org",
    "security.snyk.io", "advisories.github.com", "osv.dev"
}

# Authoritative wire services and financial news
TIER1_NEWS_DOMAINS = {
    "reuters.com", "apnews.com", "bloomberg.com", "ft.com", "wsj.com",
    "nytimes.com", "bbc.com", "theguardian.com", "cnbc.com"
}

# Phase 2: New authoritative API / knowledge base domains
WIKIPEDIA_DOMAINS = {"wikipedia.org", "wikidata.org"}
GITHUB_DOMAINS = {"github.com", "gitlab.com", "bitbucket.org"}
ACADEMIC_DOMAINS = {"crossref.org", "doi.org", "arxiv.org", "pubmed.ncbi.nlm.nih.gov", "scholar.google.com", "semanticscholar.org"}
OFFICIAL_API_DOMAINS = {"api.github.com", "api.twitter.com", "api.linkedin.com", "developer.mozilla.org"}

# Uncurated user-generated content or blog platforms
LOW_AUTHORITY_DOMAINS = {
    "reddit.com", "quora.com", "medium.com", "substack.com",
    "blogspot.com", "wordpress.com", "tumblr.com", "twitter.com", "x.com"
}

# Phase 2: Source type authority multipliers
SOURCE_TYPE_AUTHORITY = {
    "government": 1.0,    # Official government sources
    "academic": 0.95,     # Peer-reviewed papers, Crossref, arXiv
    "api": 0.90,          # Official APIs (GitHub API, vendor APIs)
    "document": 0.85,     # Official documentation, whitepapers
    "web": 1.0,           # General web search results
}


def extract_clean_domain(url: str) -> str:
    """Extract clean domain name without leading 'www.' or port numbers."""
    if not url:
        return ""
    try:
        clean = url.strip()
        if "://" not in clean:
            clean = "https://" + clean
        netloc = urlparse(clean).netloc.lower()
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
        source_type: str = "web",  # Phase 2: "web" | "api" | "document" | "academic" | "government"
    ) -> Source:
        """Score source authority, determine if it is primary, and compute relevance."""
        domain = extract_clean_domain(url)
        entities = [e.lower() for e in (entities or [])]
        category_lower = category.lower()

        authority_score = 0.5
        is_primary = False

        # Phase 2: Apply source type authority multiplier
        type_multiplier = SOURCE_TYPE_AUTHORITY.get(source_type, 0.7)

        # 1. Government and official regulatory domains
        if any(domain.endswith(f".{g}") or domain == g for g in GOVERNMENT_DOMAINS):
            if category_lower in ("law", "government_policy", "public_health"):
                authority_score = 0.95
                is_primary = True
            else:
                authority_score = 0.85

        # 1b. Cybersecurity vulnerability and advisory databases
        elif category_lower == "cybersecurity" and any(domain.endswith(c) or domain == c for c in CYBERSECURITY_DOMAINS):
            authority_score = 0.98
            is_primary = True

        # 1c. Phase 2: Wikipedia and Wikidata for general knowledge
        elif any(domain.endswith(w) or domain == w for w in WIKIPEDIA_DOMAINS):
            if category_lower in ("other", "scientific_findings", "company_info"):
                authority_score = 0.9
            else:
                authority_score = 0.8

        # 1d. Phase 2: GitHub and code repositories for version/technical claims
        elif any(domain.endswith(g) or domain == g for g in GITHUB_DOMAINS):
            if category_lower in ("version", "cybersecurity", "product_availability"):
                authority_score = 0.9
                is_primary = True
            else:
                authority_score = 0.8

        # 1e. Phase 2: Academic sources (Crossref, arXiv, PubMed)
        elif any(domain.endswith(a) or domain == a for a in ACADEMIC_DOMAINS):
            if category_lower in ("scientific_findings", "public_health"):
                authority_score = 0.95
                is_primary = True
            else:
                authority_score = 0.85

        # 1f. Phase 2: Official API documentation
        elif any(domain.endswith(a) or domain == a for a in OFFICIAL_API_DOMAINS):
            if category_lower in ("version", "pricing", "product_availability"):
                authority_score = 0.9
                is_primary = True
            else:
                authority_score = 0.8

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

        # Apply source type multiplier
        authority_score *= type_multiplier

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
            source_type=source_type,
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

    def calculate_cross_source_consensus(self, sources: List[Source]) -> Dict[str, Any]:
        """
        Phase 2: Calculate cross-source consensus score based on independent source agreement.

        Returns consensus metrics for multi-source evidence triangulation.
        """
        if not sources:
            return {
                "consensus_score": 0.4,
                "agreeing_sources": 0,
                "total_sources": 0,
                "source_types": {},
                "independent_count": 0,
            }

        # Filter to independent sources
        independent = self.filter_independent_sources(sources)

        # Count source types
        source_types: Dict[str, int] = {}
        for src in independent:
            source_types[src.source_type] = source_types.get(src.source_type, 0) + 1

        # Consensus score based on number of independent sources
        # Higher weight for diverse source types
        type_diversity_bonus = min(0.2, len(source_types) * 0.05)
        base_consensus = min(1.0, len(independent) / 3.0)
        consensus_score = min(1.0, base_consensus + type_diversity_bonus)
        consensus_score = max(0.4, consensus_score)  # floor

        return {
            "consensus_score": round(consensus_score, 2),
            "agreeing_sources": len(independent),
            "total_sources": len(sources),
            "source_types": source_types,
            "independent_count": len(independent),
        }
