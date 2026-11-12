"""Validated policy configuration and policy-preview contracts."""

from __future__ import annotations

import re
import time
import uuid
from typing import Literal, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.categories import category_registry

VerificationMode = Literal["shadow", "warn", "enforce"]


def normalize_domain(value: str) -> str:
    """Validate and normalize a DNS domain pattern, without URL paths or wildcards."""
    candidate = value.strip().lower().rstrip(".")
    if not candidate:
        raise ValueError("domain must not be empty")
    if "://" in candidate:
        parsed = urlparse(candidate)
        if parsed.scheme not in ("http", "https") or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError("source settings accept domain names, not URLs with paths or query strings")
        candidate = (parsed.hostname or "").lower().rstrip(".")
    candidate = candidate.removeprefix("www.")
    if not re.fullmatch(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", candidate):
        raise ValueError(f"invalid source domain: {value}")
    return candidate


class PolicyConfig(BaseModel):
    """Stored policy plus its normalized verification and cache settings."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    enabled: bool = True
    categories: list[str] = Field(default_factory=lambda: category_registry.get_all_volatile(), min_length=1)
    preferred_domains: list[str] = Field(default_factory=list, max_length=50)
    allowed_domains: list[str] = Field(default_factory=list, max_length=50)
    blocked_domains: list[str] = Field(default_factory=list, max_length=50)
    minimum_source_count: int = Field(default=1, ge=0, le=10)
    minimum_source_authority: float = Field(default=0.0, ge=0.0, le=1.0)
    minimum_freshness_score: int = Field(default=0, ge=0, le=100)
    maximum_evidence_age_days: Optional[int] = Field(default=None, ge=1, le=3650)
    category_maximum_evidence_age_days: dict[str, int] = Field(default_factory=dict)
    category_minimum_freshness_scores: dict[str, int] = Field(default_factory=dict)
    verification_mode: VerificationMode = "warn"
    cache_enabled: bool = True
    cache_ttl_seconds: dict[str, int] = Field(default_factory=dict)
    jurisdiction: Optional[str] = Field(default=None, max_length=120)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)

    @field_validator("name", "description", mode="before")
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("categories")
    @classmethod
    def normalize_categories(cls, values: list[str]) -> list[str]:
        supported = set(category_registry.get_all_categories())
        result = []
        for value in values:
            category, known = category_registry.normalize_category(value)
            if not known or category not in supported:
                raise ValueError(f"unsupported category: {value}")
            if category not in result:
                result.append(category)
        if not result:
            raise ValueError("at least one category is required")
        return result

    @field_validator("preferred_domains", "allowed_domains", "blocked_domains")
    @classmethod
    def validate_domains(cls, values: list[str]) -> list[str]:
        normalized = []
        for value in values:
            domain = normalize_domain(value)
            if domain not in normalized:
                normalized.append(domain)
        return normalized

    @field_validator("category_maximum_evidence_age_days")
    @classmethod
    def validate_category_age(cls, values: dict[str, int]) -> dict[str, int]:
        supported = set(category_registry.get_all_categories())
        result = {}
        for raw_category, age in values.items():
            category, known = category_registry.normalize_category(raw_category)
            if not known or category not in supported:
                raise ValueError(f"unsupported category in evidence-age settings: {raw_category}")
            if not 1 <= age <= 3650:
                raise ValueError("category evidence ages must be between 1 and 3650 days")
            result[category] = age
        return result

    @field_validator("category_minimum_freshness_scores")
    @classmethod
    def validate_category_scores(cls, values: dict[str, int]) -> dict[str, int]:
        supported = set(category_registry.get_all_categories())
        result = {}
        for raw_category, score in values.items():
            category, known = category_registry.normalize_category(raw_category)
            if not known or category not in supported:
                raise ValueError(f"unsupported category in freshness settings: {raw_category}")
            if not 0 <= score <= 100:
                raise ValueError("category freshness scores must be between 0 and 100")
            result[category] = score
        return result

    @field_validator("cache_ttl_seconds")
    @classmethod
    def validate_cache_ttls(cls, values: dict[str, int]) -> dict[str, int]:
        supported = set(category_registry.get_all_categories())
        result = {}
        for raw_category, ttl in values.items():
            category, known = category_registry.normalize_category(raw_category)
            if not known or category not in supported:
                raise ValueError(f"unsupported category in cache settings: {raw_category}")
            if not 1 <= ttl <= 31_536_000:
                raise ValueError("category cache TTLs must be between 1 and 31536000 seconds")
            result[category] = ttl
        return result

    @model_validator(mode="after")
    def validate_domain_conflicts(self):
        blocked = set(self.blocked_domains)
        conflicts = blocked & (set(self.allowed_domains) | set(self.preferred_domains))
        if conflicts:
            raise ValueError(f"domains cannot be both preferred/allowed and blocked: {', '.join(sorted(conflicts))}")
        return self

    def settings(self) -> dict:
        return self.model_dump(exclude={"id", "name", "description", "enabled", "created_at", "updated_at"})


class PolicyPreviewClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    text: str = Field(min_length=1, max_length=5000)
    category: str
    sources: list[dict] = Field(default_factory=list, max_length=20)
    freshness_score: Optional[int] = Field(default=None, ge=0, le=100)

    @field_validator("category")
    @classmethod
    def normalize_category(cls, value: str) -> str:
        category, known = category_registry.normalize_category(value)
        if not known:
            raise ValueError(f"unsupported category: {value}")
        return category


class PolicyPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claims: list[PolicyPreviewClaim] = Field(min_length=1, max_length=50)


class PolicyCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    enabled: bool = True
    categories: list[str] = Field(default_factory=lambda: category_registry.get_all_volatile(), min_length=1)
    preferred_domains: list[str] = Field(default_factory=list, max_length=50)
    allowed_domains: list[str] = Field(default_factory=list, max_length=50)
    blocked_domains: list[str] = Field(default_factory=list, max_length=50)
    minimum_source_count: int = Field(default=1, ge=0, le=10)
    minimum_source_authority: float = Field(default=0.0, ge=0.0, le=1.0)
    minimum_freshness_score: int = Field(default=0, ge=0, le=100)
    maximum_evidence_age_days: Optional[int] = Field(default=None, ge=1, le=3650)
    category_maximum_evidence_age_days: dict[str, int] = Field(default_factory=dict)
    category_minimum_freshness_scores: dict[str, int] = Field(default_factory=dict)
    verification_mode: VerificationMode = "warn"
    cache_enabled: bool = True
    cache_ttl_seconds: dict[str, int] = Field(default_factory=dict)
    jurisdiction: Optional[str] = Field(default=None, max_length=120)
