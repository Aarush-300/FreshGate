"""FreshGate Extensible Category Registry.

Maintains verification policies, search query strategies, source authority preferences,
canonical category normalization, alias resolution, and cache TTLs.
"""

import re
from typing import Dict, List, Optional, Callable, Tuple
from dataclasses import dataclass, field
from core.models import Claim


@dataclass
class CategoryDefinition:
    name: str
    display_name: str
    description: str
    is_volatile: bool = True
    default_ttl_seconds: int = 86400
    min_confidence: float = 0.7
    preferred_domains: List[str] = field(default_factory=list)
    search_query_builder: Optional[Callable[[Claim], str]] = None


CATEGORY_ALIASES: Dict[str, str] = {
    # Version aliases
    "software_version": "version",
    "software_versions": "version",
    "version_number": "version",
    "release": "version",
    "releases": "version",
    "package_version": "version",
    "model_version": "version",
    "deprecation": "version",
    "supported_version": "version",
    "supported_release": "version",

    # Pricing aliases
    "subscription_price": "pricing",
    "price": "pricing",
    "prices": "pricing",
    "cost": "pricing",
    "costs": "pricing",
    "pricing_tier": "pricing",
    "pricing_plan": "pricing",
    "fees": "pricing",
    "fee": "pricing",
    "subscription": "pricing",
    "plan": "pricing",
    "plans": "pricing",
    "tier": "pricing",

    # Law aliases
    "regulation": "law",
    "regulations": "law",
    "statute": "law",
    "statutes": "law",
    "legislation": "law",
    "legal": "law",
    "court_ruling": "law",
    "compliance": "law",
    "act": "law",

    # Leadership aliases
    "executive": "leadership",
    "executives": "leadership",
    "management": "leadership",
    "ceo": "leadership",
    "director": "leadership",
    "minister": "leadership",
    "leader": "leadership",
    "appointment": "leadership",

    # Cybersecurity aliases
    "security": "cybersecurity",
    "cve": "cybersecurity",
    "vulnerability": "cybersecurity",
    "vulnerabilities": "cybersecurity",
    "exploit": "cybersecurity",
    "patch": "cybersecurity",
    "outage": "cybersecurity",

    # Financial aliases
    "finance": "financial",
    "earnings": "financial",
    "revenue": "financial",
    "valuation": "financial",
    "interest_rate": "financial",
    "rates": "financial",
    "market_cap": "financial",

    # Government policy aliases
    "policy": "government_policy",
    "policies": "government_policy",
    "subsidy": "government_policy",
    "tax_credit": "government_policy",
    "sanction": "government_policy",
    "directive": "government_policy",

    # Public health aliases
    "health": "public_health",
    "medical": "public_health",
    "guidance": "public_health",
    "disease": "public_health",
    "vaccine": "public_health",
    "pandemic": "public_health",

    # Product availability aliases
    "availability": "product_availability",
    "launch": "product_availability",
    "discontinued": "product_availability",
    "end_of_life": "product_availability",
    "eol": "product_availability",
    "stock": "product_availability",

    # Company info aliases
    "company": "company_info",
    "corporate": "company_info",
    "acquisition": "company_info",
    "merger": "company_info",
    "mergers": "company_info",
    "headquarters": "company_info",

    # Scientific findings aliases
    "science": "scientific_findings",
    "scientific": "scientific_findings",
    "discovery": "scientific_findings",
    "research": "scientific_findings",
    "study": "scientific_findings",

    # Dated event outcomes and reported results
    "event": "dated_event",
    "event_result": "dated_event",
    "sports_result": "dated_event",
    "match_result": "dated_event",
    "competition_result": "dated_event",
    "tournament_result": "dated_event",
    "election_result": "dated_event",
    "award_result": "dated_event",

    # Other / Static evergreen facts
    "general": "other",
    "general_fact": "other",
    "static": "other",
    "evergreen": "other",
    "history": "other",
    "geography": "other",
    "math": "other",
    "syntax": "other",
}


class CategoryRegistry:
    """Registry managing claim categories, aliases, and operational policies."""

    def __init__(self):
        self._categories: Dict[str, CategoryDefinition] = {}
        self._init_defaults()

    def register(self, category: CategoryDefinition):
        self._categories[category.name.strip().lower()] = category

    def normalize_category(self, name: str) -> Tuple[str, bool]:
        """Normalize raw category string via canonical names and aliases.

        Returns (canonical_name, is_known).
        """
        if not name:
            return "other", False

        clean = name.strip().lower()
        clean = re.sub(r"[\s\-]+", "_", clean)

        if clean in self._categories:
            return clean, True

        if clean in CATEGORY_ALIASES:
            return CATEGORY_ALIASES[clean], True

        return clean, False

    def get(self, name: str) -> CategoryDefinition:
        """Retrieve category definition. Unknown categories are treated conservatively as volatile."""
        norm_name, is_known = self.normalize_category(name)
        if is_known and norm_name in self._categories:
            return self._categories[norm_name]

        # Unknown category: do NOT default to non-volatile "other"!
        # Treat conservatively as volatile so facts are verified rather than silently dropped.
        return CategoryDefinition(
            name=norm_name,
            display_name=f"Unclassified ({norm_name})",
            description="Unclassified factual assertion subject to conservative live verification",
            is_volatile=True,
            default_ttl_seconds=3600,
            min_confidence=0.75,
            search_query_builder=lambda c: f"{c.text.rstrip('.')} status verification",
        )

    def is_volatile(self, name: str) -> bool:
        """Check if category is volatile.

        Only categories explicitly canonicalized to 'other' are non-volatile.
        Unknown categories are conservatively treated as volatile.
        """
        norm_name, _ = self.normalize_category(name)
        if norm_name == "other":
            return False
        cat = self.get(norm_name)
        return cat.is_volatile

    def get_all_volatile(self) -> List[str]:
        return [k for k, v in self._categories.items() if v.is_volatile]

    def get_all_categories(self) -> List[str]:
        """Return registered canonical category names in declaration order."""
        return list(self._categories)

    def _init_defaults(self):
        # 1. Pricing
        self.register(CategoryDefinition(
            name="pricing",
            display_name="Product & API Pricing",
            description="Costs, rates, subscription plans, token pricing, fees",
            is_volatile=True,
            default_ttl_seconds=3600,  # 1 hour
            min_confidence=0.85,
            preferred_domains=["pricing", "plans", "billing", "store"],
            search_query_builder=lambda c: f"{c.text.rstrip('.')} official current pricing"
        ))

        # 2. Software Versions
        self.register(CategoryDefinition(
            name="version",
            display_name="Software & Model Releases",
            description="Library versions, API deprecations, model release numbers, supported branches",
            is_volatile=True,
            default_ttl_seconds=21600,  # 6 hours
            min_confidence=0.8,
            preferred_domains=["github.com", "pypi.org", "npmjs.com", "docs", "python.org"],
            search_query_builder=lambda c: f"{c.text.rstrip('.')} latest stable release version supported branches"
        ))

        # 3. Laws & Regulations
        self.register(CategoryDefinition(
            name="law",
            display_name="Legislation & Court Rulings",
            description="Enacted bills, vetoes, regulatory mandates, antitrust actions",
            is_volatile=True,
            default_ttl_seconds=604800,  # 7 days
            min_confidence=0.9,
            preferred_domains=[".gov", ".europa.eu", "legislation.gov.uk", "courtlistener.com"],
            search_query_builder=lambda c: f"{c.text.rstrip('.')} bill signed vetoed current legal status"
        ))

        # 4. Leadership & Executive Roles
        self.register(CategoryDefinition(
            name="leadership",
            display_name="Corporate & Political Leadership",
            description="CEOs, ministers, governors, board members, agency chairs",
            is_volatile=True,
            default_ttl_seconds=86400,  # 24 hours
            min_confidence=0.85,
            preferred_domains=["reuters.com", "apnews.com", "bloomberg.com", "sec.gov"],
            search_query_builder=lambda c: f"{c.text.rstrip('.')} current who is executive"
        ))

        # 5. Product Availability
        self.register(CategoryDefinition(
            name="product_availability",
            display_name="Product Availability & Stock",
            description="Regional launches, general availability, waitlists, discontinuations, EOL",
            is_volatile=True,
            default_ttl_seconds=14400,  # 4 hours
            min_confidence=0.8,
            search_query_builder=lambda c: f"{c.text.rstrip('.')} available launch status discontinued"
        ))

        # 6. Cybersecurity Vulnerabilities
        self.register(CategoryDefinition(
            name="cybersecurity",
            display_name="Cybersecurity & CVEs",
            description="Zero-days, CVE identifiers, patches, exploits in the wild",
            is_volatile=True,
            default_ttl_seconds=14400,  # 4 hours
            min_confidence=0.9,
            preferred_domains=["nvd.nist.gov", "cve.org", "cisa.gov", "github.com/advisories"],
            search_query_builder=lambda c: f"{c.text.rstrip('.')} CVE security advisory patch"
        ))

        # 7. Financial Information
        self.register(CategoryDefinition(
            name="financial",
            display_name="Financial Earnings & Metrics",
            description="Quarterly earnings, market cap, IPO dates, revenue figures, interest rates",
            is_volatile=True,
            default_ttl_seconds=3600,  # 1 hour
            min_confidence=0.9,
            preferred_domains=["sec.gov", "bloomberg.com", "cnbc.com", "investor."],
            search_query_builder=lambda c: f"{c.text.rstrip('.')} latest earnings report SEC filing rate"
        ))

        # 8. Government Policy
        self.register(CategoryDefinition(
            name="government_policy",
            display_name="Government Policies & Directives",
            description="Executive orders, travel bans, export controls, tariffs, subsidies",
            is_volatile=True,
            default_ttl_seconds=172800,  # 48 hours
            min_confidence=0.85,
            preferred_domains=[".gov", ".europa.eu", "state.gov"],
            search_query_builder=lambda c: f"{c.text.rstrip('.')} official policy announcement rule"
        ))

        # 9. Public Health Guidance
        self.register(CategoryDefinition(
            name="public_health",
            display_name="Public Health Guidelines",
            description="Vaccine approvals, outbreak alerts, clinical recommendations, isolation rules",
            is_volatile=True,
            default_ttl_seconds=86400,  # 24 hours
            min_confidence=0.95,
            preferred_domains=["cdc.gov", "who.int", "fda.gov", "nih.gov"],
            search_query_builder=lambda c: f"{c.text.rstrip('.')} CDC WHO official health guidance"
        ))

        # 10. Scientific Findings
        self.register(CategoryDefinition(
            name="scientific_findings",
            display_name="Scientific Discoveries & Preprints",
            description="Published papers, retracted studies, Nobel prizes, replications",
            is_volatile=True,
            default_ttl_seconds=604800,  # 7 days
            min_confidence=0.8,
            preferred_domains=["nature.com", "science.org", "arxiv.org", "ncbi.nlm.nih.gov"],
            search_query_builder=lambda c: f"{c.text.rstrip('.')} journal publication retraction status"
        ))

        # 11. Company Information
        self.register(CategoryDefinition(
            name="company_info",
            display_name="Company Mergers & Operations",
            description="Acquisitions, bankruptcies, headquarters relocations",
            is_volatile=True,
            default_ttl_seconds=86400,  # 24 hours
            min_confidence=0.8,
            search_query_builder=lambda c: f"{c.text.rstrip('.')} acquisition merger headquarters"
        ))

        # Dated events and outcomes require evidence even when the event is historical.
        self.register(CategoryDefinition(
            name="dated_event",
            display_name="Dated Events & Outcomes",
            description="Results and outcomes of dated events, competitions, elections, and awards",
            is_volatile=True,
            default_ttl_seconds=86400,
            min_confidence=0.8,
            search_query_builder=lambda c: f"{c.text.rstrip('.')} official result outcome"
        ))

        # Other / Non-Volatile Static Facts
        self.register(CategoryDefinition(
            name="other",
            display_name="General & Static Facts",
            description="History, geography, mathematical constants, language syntax, universal truths",
            is_volatile=False,
            default_ttl_seconds=86400,
            min_confidence=0.7,
            search_query_builder=lambda c: c.text.rstrip(".")
        ))


category_registry = CategoryRegistry()
