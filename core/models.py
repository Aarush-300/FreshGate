from __future__ import annotations
from typing import Optional, List, Literal
from pydantic import BaseModel, Field

Verdict = Literal["confirmed", "contradicted", "partially_supported", "unverifiable"]


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


class Summary(BaseModel):
    total: int
    checked: int
    stale: int
    avg_score: int


class VerificationResult(BaseModel):
    claims: List[Claim]
    summary: Summary
