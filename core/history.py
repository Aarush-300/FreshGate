"""Immutable claim verification history over the shared feature store."""

from __future__ import annotations

import time
import uuid
from typing import Any, Optional

from core.claim_identity import claim_fingerprint
from core.models import Claim
from core.persistence import SQLiteFeatureStore, get_feature_store
from core.policy_models import PolicyConfig
from core.scorer import compute_score_dimensions


def save_claim_snapshot(
    claim: Claim,
    *,
    policy: Optional[PolicyConfig] = None,
    duration_seconds: Optional[float] = None,
    jurisdiction: Optional[str] = None,
    fingerprint: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
    store: Optional[SQLiteFeatureStore] = None,
) -> dict[str, Any]:
    """Persist one result and attach its immutable history identifier to the claim."""
    effective_jurisdiction = jurisdiction or (policy.jurisdiction if policy else None)
    score_components = compute_score_dimensions(claim)
    verification_id = str(uuid.uuid4())
    identity = fingerprint or claim_fingerprint(
        claim.text,
        claim.type,
        entities=claim.entities,
        jurisdiction=effective_jurisdiction,
    )
    details = {
        "score_components": score_components,
        "correction": claim.correction,
        "explanation": claim.explanation,
        "confidence": claim.confidence,
        "policy_name": policy.name if policy else None,
        "policy_updated_at": policy.updated_at if policy else None,
        **(metadata or {}),
    }
    snapshot = (store or get_feature_store()).save_snapshot({
        "verification_id": verification_id,
        "claim_fingerprint": identity,
        "claim_text": claim.text,
        "category": claim.type,
        "verdict": claim.verdict,
        "freshness_score": claim.score,
        "sources": [source.model_dump() for source in claim.sources],
        "policy_id": policy.id if policy else None,
        "checked_at": time.time(),
        "cached": claim.cached,
        "verification_duration": duration_seconds,
        "metadata": details,
    })
    claim.verification_id = verification_id
    claim.claim_fingerprint = identity
    return snapshot


def record_claims(
    claims: list[Claim],
    *,
    policy: Optional[PolicyConfig] = None,
    duration_seconds: Optional[float] = None,
    store: Optional[SQLiteFeatureStore] = None,
) -> list[dict[str, Any]]:
    repository = store or get_feature_store()
    return [
        save_claim_snapshot(
            claim,
            policy=policy,
            duration_seconds=duration_seconds,
            store=repository,
        )
        for claim in claims
    ]


def get_claim_history(
    fingerprint: str,
    *,
    limit: int = 100,
    offset: int = 0,
    store: Optional[SQLiteFeatureStore] = None,
) -> dict[str, Any]:
    repository = store or get_feature_store()
    return {
        "claim_fingerprint": fingerprint,
        "snapshots": repository.list_snapshots(fingerprint, limit, offset),
        "total": repository.count_snapshots(fingerprint),
        "limit": limit,
        "offset": offset,
    }


def get_verification_history(
    verification_id: str, store: Optional[SQLiteFeatureStore] = None
) -> Optional[dict[str, Any]]:
    return (store or get_feature_store()).get_snapshot_by_verification_id(verification_id)
