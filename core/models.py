from __future__ import annotations
from typing import Optional, List, Literal
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


class Claim(BaseModel):
    id: str
    text: str
    normalized_text: str = ""
    type: str  # e.g., pricing, version, law, leadership, cybersecurity, financial...
    entities: List[str] = Field(default_factory=list)
    temporal_anchor: Optional[str] = None
    numerical_value: Optional[str] = None
    extraction_confidence: float = 1.0

    verdict: Optional[str] = None  # confirmed, contradicted, partially_supported, unverifiable
    explanation: Optional[str] = None
    confidence: float = 1.0
    score: Optional[int] = None  # 0 to 100

    sources: List[Source] = Field(default_factory=list)
    supporting_sources: List[Source] = Field(default_factory=list)
    contradicting_sources: List[Source] = Field(default_factory=list)

    cached: bool = False
    correction: Optional[str] = None
    verification_timestamp: Optional[str] = None


class ExtractionResult(BaseModel):
    claims: List[Claim] = Field(default_factory=list)
    status: ExtractionStatus = "success"
    error: Optional[str] = None
    raw_response: Optional[str] = None
    total_extracted: int = 0
    valid_claims: int = 0
    dropped_claims: int = 0
    is_fallback: bool = False

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


class VerificationResult(BaseModel):
    claims: List[Claim]
    summary: Summary
    status: PipelineStatus = "completed"
    error: Optional[str] = None
