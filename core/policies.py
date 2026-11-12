"""Policy storage adapters and deterministic policy evaluation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from core.models import Claim, Source
from core.persistence import SQLiteFeatureStore, get_feature_store
from core.policy_models import PolicyConfig, PolicyPreviewClaim
from core.sources import extract_clean_domain


def policy_from_record(record: dict[str, Any]) -> PolicyConfig:
    values = dict(record.get("configuration") or {})
    values.update({
        "id": record["id"],
        "name": record["name"],
        "description": record["description"],
        "enabled": record["enabled"],
        "created_at": record["created_at"],
        "updated_at": record["updated_at"],
    })
    return PolicyConfig.model_validate(values)


def get_policy(policy_id: str, store: Optional[SQLiteFeatureStore] = None) -> Optional[PolicyConfig]:
    record = (store or get_feature_store()).get_policy(policy_id)
    return policy_from_record(record) if record else None


def list_policies(store: Optional[SQLiteFeatureStore] = None, limit: int = 100, offset: int = 0) -> list[PolicyConfig]:
    return [policy_from_record(row) for row in (store or get_feature_store()).list_policies(limit, offset)]


def create_policy(values: dict[str, Any], store: Optional[SQLiteFeatureStore] = None) -> PolicyConfig:
    policy = PolicyConfig.model_validate(values)
    repository = store or get_feature_store()
    record = repository.create_policy({
        "id": policy.id,
        "name": policy.name,
        "description": policy.description,
        "enabled": policy.enabled,
        "configuration": policy.settings(),
        "created_at": policy.created_at,
        "updated_at": policy.updated_at,
    })
    return policy_from_record(record)


def update_policy(policy_id: str, values: dict[str, Any], store: Optional[SQLiteFeatureStore] = None) -> Optional[PolicyConfig]:
    repository = store or get_feature_store()
    current_record = repository.get_policy(policy_id)
    if current_record is None:
        return None
    current = policy_from_record(current_record)
    changes = {key: value for key, value in values.items() if value is not None}
    merged = current.model_dump()
    merged.update(changes)
    merged["id"] = policy_id
    merged["created_at"] = current.created_at
    merged["updated_at"] = datetime.now(timezone.utc).timestamp()
    policy = PolicyConfig.model_validate(merged)
    record = repository.update_policy(policy_id, {
        "name": policy.name,
        "description": policy.description,
        "enabled": policy.enabled,
        "configuration": policy.settings(),
        "updated_at": policy.updated_at,
    })
    return policy_from_record(record) if record else None


def domain_matches(domain: str, patterns: list[str]) -> bool:
    return any(domain == pattern or domain.endswith(f".{pattern}") for pattern in patterns)


def source_is_eligible(source: Source, policy: PolicyConfig, category: str) -> bool:
    domain = extract_clean_domain(source.url) or source.domain.lower().removeprefix("www.")
    if policy.allowed_domains and not domain_matches(domain, policy.allowed_domains):
        return False
    if domain_matches(domain, policy.blocked_domains):
        return False
    if source.authority_score < policy.minimum_source_authority:
        return False

    max_age = policy.category_maximum_evidence_age_days.get(category, policy.maximum_evidence_age_days)
    if max_age is not None:
        if not source.published:
            return False
        try:
            published = datetime.fromisoformat(source.published.replace("Z", "+00:00"))
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return False
        age_seconds = (datetime.now(timezone.utc) - published.astimezone(timezone.utc)).total_seconds()
        if age_seconds < -86400 or age_seconds > max_age * 86400:
            return False
    return True


def cached_result_is_eligible(claim: Claim, policy: PolicyConfig) -> bool:
    sources = claim.sources or []
    eligible = [source for source in sources if source_is_eligible(source, policy, claim.type)]
    if len(eligible) != len(sources):
        return False
    return len(eligible) >= policy.minimum_source_count


def evaluate_policy_preview(policy: PolicyConfig, claims: list[PolicyPreviewClaim]) -> dict[str, Any]:
    evaluations = []
    failed = False
    for claim in claims:
        included = claim.category in policy.categories
        eligible_sources = []
        for raw_source in claim.sources:
            try:
                source = Source.model_validate(raw_source)
            except Exception:
                continue
            if source_is_eligible(source, policy, claim.category):
                eligible_sources.append(source)
        meets_sources = len(eligible_sources) >= policy.minimum_source_count if included else True
        score_threshold = policy.category_minimum_freshness_scores.get(
            claim.category, policy.minimum_freshness_score
        )
        meets_score = (
            claim.freshness_score is not None and claim.freshness_score >= score_threshold
        ) if included else True
        claim_passes = included and meets_sources and meets_score
        if included and not claim_passes:
            failed = True
        evaluations.append({
            "claim_id": claim.id,
            "category": claim.category,
            "included": included,
            "eligible_source_count": len(eligible_sources),
            "required_source_count": policy.minimum_source_count if included else 0,
            "meets_source_requirements": meets_sources,
            "freshness_score": claim.freshness_score,
            "minimum_freshness_score": score_threshold if included else None,
            "meets_freshness_threshold": meets_score,
            "decision": "pass" if claim_passes else ("not_applicable" if not included else "policy_failed"),
        })

    if failed:
        overall = {"shadow": "would_fail", "warn": "warning", "enforce": "rejected"}[policy.verification_mode]
    else:
        overall = "accepted"
    return {
        "policy_id": policy.id,
        "policy_name": policy.name,
        "verification_mode": policy.verification_mode,
        "decision": overall,
        "accepted": not (failed and policy.verification_mode == "enforce"),
        "claims": evaluations,
    }


def evaluate_policy_result(policy: PolicyConfig, claims: list[Claim]) -> dict[str, Any]:
    preview_claims = [
        PolicyPreviewClaim(
            id=claim.id,
            text=claim.text,
            category=claim.type,
            sources=[source.model_dump() for source in claim.sources],
            freshness_score=claim.score,
        )
        for claim in claims
    ]
    return evaluate_policy_preview(policy, preview_claims)
