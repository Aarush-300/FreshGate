from __future__ import annotations
from typing import Optional, List, Literal
from pydantic import BaseModel, Field


ClaimType = Literal["pricing", "version", "law", "leadership", "other"]
Verdict = Literal["confirmed", "contradicted", "unverifiable"]


class Source(BaseModel):
    url: str
    published: Optional[str] = None
    snippet: str


class Claim(BaseModel):
    id: str
    text: str
    type: str  # pricing, version, law, leadership, other
    verdict: Optional[str] = None  # confirmed, contradicted, unverifiable
    score: Optional[int] = None  # 0 to 100
    sources: List[Source] = Field(default_factory=list)
    cached: bool = False
    correction: Optional[str] = None


class Summary(BaseModel):
    total: int
    checked: int
    stale: int
    avg_score: int


class VerificationResult(BaseModel):
    claims: List[Claim]
    summary: Summary
