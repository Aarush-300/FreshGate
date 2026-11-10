"""FreshGate Extensible Category Registry.

Maintains verification policies, search query strategies, source authority preferences,
and cache TTLs for volatile and non-volatile claim types.
"""

from typing import Dict, List, Optional, Callable
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


class CategoryRegistry:
    """Registry managing claim categories and their operational policies."""

    def __init__(self):
        self._categories: Dict[str, CategoryDefinition] = {}
        self._init_defaults()

    def register(self, category: CategoryDefinition):
        self._categories[category.name.strip().lower()] = category

    def get(self, name: str) -> Optional[CategoryDefinition]:
        return self._categories.get(name.strip().lower(), self._categories.get("other"))

    def is_volatile(self, name: str) -> bool:
        cat = self.get(name)
        return cat.is_volatile if cat else False

    def get_all_volatile(self) -> List[str]:
        return [k for k, v in self._categories.items() if v.is_volatile]

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
            description="Library versions, API deprecations, model release numbers",
            is_volatile=True,
            default_ttl_seconds=21600,  # 6 hours
            min_confidence=0.8,
            preferred_domains=["github.com", "pypi.org", "npmjs.com", "docs"],
            search_query_builder=lambda c: f"{c.text.rstrip('.')} latest stable release version"
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
            description="Regional launches, general availability, waitlists, discontinuations",
            is_volatile=True,
            default_ttl_seconds=14400,  # 4 hours
            min_confidence=0.8,
            search_query_builder=lambda c: f"{c.text.rstrip('.')} available launch status"
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
            description="Quarterly earnings, market cap, IPO dates, revenue figures",
            is_volatile=True,
            default_ttl_seconds=3600,  # 1 hour
            min_confidence=0.9,
            preferred_domains=["sec.gov", "bloomberg.com", "cnbc.com", "investor."],
            search_query_builder=lambda c: f"{c.text.rstrip('.')} latest earnings report SEC filing"
        ))

        # 8. Government Policy
        self.register(CategoryDefinition(
            name="government_policy",
            display_name="Government Policies & Directives",
            description="Executive orders, travel bans, export controls, tariffs",
            is_volatile=True,
            default_ttl_seconds=172800,  # 48 hours
            min_confidence=0.85,
            preferred_domains=[".gov", ".europa.eu", "state.gov"],
            search_query_builder=lambda c: f"{c.text.rstrip('.')} official policy announcement"
        ))

        # 9. Public Health Guidance
        self.register(CategoryDefinition(
            name="public_health",
            display_name="Public Health Guidelines",
            description="Vaccine approvals, outbreak alerts, clinical recommendations",
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
            description="Published papers, retracted studies, Nobel prizes",
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

        # 12. Other / Non-Volatile Static Facts
        self.register(CategoryDefinition(
            name="other",
            display_name="General & Static Facts",
            description="History, geography, mathematical constants, language syntax",
            is_volatile=False,
            default_ttl_seconds=86400,
            min_confidence=0.7,
            search_query_builder=lambda c: c.text.rstrip(".")
        ))


category_registry = CategoryRegistry()
