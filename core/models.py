from __future__ import annotations
from typing import Optional, List, Literal, Dict, Any
from pydantic import BaseModel, Field

Verdict = Literal["confirmed", "contradicted", "partially_supported", "unverifiable"]
ExtractionStatus = Literal["success", "partial", "failed", "no_claims_detected"]
PipelineStatus = Literal[
    "completed",
    "completed_with_warnings",
    "no_claims_detected",
    "extraction_failed",
    "verification_failed",
    "partially_verified",
]


class Source(BaseModel):
    url: str
    domain: str = ""
    published: Optional[str] = None
    snippet: str
    authority_score: float = 0.5
    is_primary: bool = False
    relevance_score: float = 0.5
    source_type: str = "web"  # "web" | "api" | "document" | "academic" | "government"


class Claim(BaseModel):
    id: str
    text: str
    normalized_text: str = ""
    type: str  # e.g., pricing, version, law, leadership, cybersecurity, financial...
    entities: List[str] = Field(default_factory=list)
    temporal_anchor: Optional[str] = None
    numerical_value: Optional[str] = None
    extraction_confidence: float = 1.0
    extraction_method: str = "llm"  # llm, heuristic, hybrid

    verdict: Optional[str] = None  # confirmed, contradicted, partially_supported, unverifiable
    verification_method: Optional[str] = None  # llm_judge, deterministic_evidence_rule, heuristic_fallback, manual_review
    explanation: Optional[str] = None
    confidence: float = 1.0
    score: Optional[int] = None  # 0 to 100

    evidence: List[str] = Field(default_factory=list)
    sources: List[Source] = Field(default_factory=list)
    supporting_sources: List[Source] = Field(default_factory=list)
    contradicting_sources: List[Source] = Field(default_factory=list)

    cached: bool = False
    correction: Optional[str] = None
    verification_timestamp: Optional[str] = None
    verification_id: Optional[str] = None
    claim_fingerprint: Optional[str] = None

    # Phase 1: Temporal Freshness Scoring
    last_verified_at: Optional[str] = None          # ISO timestamp
    freshness_half_life_seconds: Optional[int] = None  # per claim type
    degradation_curve: str = "exponential"          # "linear" | "exponential" | "step"

    # Phase 3: Predictive Claim Decomposition
    volatility_score: float = 0.5           # 0.0-1.0, likelihood of future change
    evidence_gap: float = 0.5               # confidence in evidence coverage
    temporal_stability: Optional[str] = None # "high" | "medium" | "low"
    predicted_flip_probability: Optional[float] = None
    recommended_check_interval: Optional[int] = None  # seconds

    # Phase 1: Temporal Freshness Scoring
    last_verified_at: Optional[str] = None          # ISO timestamp
    freshness_half_life_seconds: Optional[int] = None  # per claim type
    degradation_curve: str = "exponential"          # "linear" | "exponential" | "step"

    # Phase 3: Predictive Claim Decomposition
    volatility_score: float = 0.5           # 0.0-1.0, likelihood of future change
    evidence_gap: float = 0.5               # confidence in evidence coverage
    temporal_stability: Optional[str] = None # "high" | "medium" | "low"
    predicted_flip_probability: Optional[float] = None
    recommended_check_interval: Optional[int] = None  # seconds


class ExtractionResult(BaseModel):
    claims: List[Claim] = Field(default_factory=list)
    status: ExtractionStatus = "success"
    extraction_method: str = "llm"  # llm, heuristic, hybrid
    error: Optional[str] = None
    raw_response: Optional[str] = None
    total_extracted: int = 0
    valid_claims: int = 0
    dropped_claims: int = 0
    is_fallback: bool = False
    is_complete: bool = True

    def __len__(self) -> int:
        return len(self.claims)

    def __iter__(self):
        return iter(self.claims)

    def __getitem__(self, idx):
        return self.claims[idx]

    def __eq__(self, other):
        if isinstance(other, list):
            return self.claims == other
        return super().__eq__(other)


class Summary(BaseModel):
    total: int
    checked: int
    stale: int
    avg_score: Optional[int] = None
    status: PipelineStatus = "completed"
    message: Optional[str] = None
    extraction_status: Optional[ExtractionStatus] = None
    extraction_method: Optional[str] = None
    verification_method: Optional[str] = None
    provider_health: Optional[Dict[str, Any]] = None


class VerificationResult(BaseModel):
    claims: List[Claim]
    summary: Summary
    status: PipelineStatus = "completed"
    error: Optional[str] = None
    provider_health: Optional[Dict[str, Any]] = None
    policy_evaluation: Optional[Dict[str, Any]] = None
