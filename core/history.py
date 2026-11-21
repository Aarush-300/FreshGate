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


# Phase 5: Executive Summary Export Functions
def export_executive_summary(
    fingerprint: str,
    *,
    limit: int = 100,
    store: Optional[SQLiteFeatureStore] = None,
) -> dict[str, Any]:
    """Export executive summary of claim history with trends, verdict distribution, score timeline."""
    repository = store or get_feature_store()
    history = repository.list_snapshots(fingerprint, limit, 0)

    if not history:
        return {
            "claim_fingerprint": fingerprint,
            "summary": "No verification history found.",
            "verdict_distribution": {},
            "score_timeline": [],
            "total_verifications": 0,
        }

    # Verdict distribution
    verdict_counts: dict[str, int] = {}
    score_timeline = []
    total_score = 0
    scored_count = 0

    for snapshot in history:
        verdict = snapshot.get("verdict", "unknown")
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1

        score = snapshot.get("freshness_score")
        if score is not None:
            total_score += score
            scored_count += 1

        score_timeline.append({
            "checked_at": snapshot.get("checked_at"),
            "score": score,
            "verdict": verdict,
            "verification_id": snapshot.get("verification_id"),
        })

    avg_score = int(round(total_score / scored_count)) if scored_count > 0 else None

    # Score trend
    scores = [s["score"] for s in score_timeline if s["score"] is not None]
    trend = "stable"
    if len(scores) >= 2:
        if scores[0] > scores[-1] + 10:
            trend = "declining"
        elif scores[0] < scores[-1] - 10:
            trend = "improving"

    return {
        "claim_fingerprint": fingerprint,
        "total_verifications": len(history),
        "average_score": avg_score,
        "verdict_distribution": verdict_counts,
        "score_trend": trend,
        "score_timeline": score_timeline,
        "first_verified": history[-1].get("checked_at") if history else None,
        "last_verified": history[0].get("checked_at") if history else None,
    }


def export_temporal_trends(
    fingerprint: str,
    *,
    limit: int = 100,
    store: Optional[SQLiteFeatureStore] = None,
) -> dict[str, Any]:
    """Export temporal trends with decay visualization data."""
    repository = store or get_feature_store()
    history = repository.list_snapshots(fingerprint, limit, 0)

    if not history:
        return {
            "claim_fingerprint": fingerprint,
            "decay_data": [],
            "half_life_estimate": None,
        }

    # Build decay data points
    decay_data = []
    for snapshot in history:
        metadata = snapshot.get("metadata", {})
        score_components = metadata.get("score_components", {})
        temporal_decay = score_components.get("temporal_decay")

        decay_data.append({
            "checked_at": snapshot.get("checked_at"),
            "freshness_score": snapshot.get("freshness_score"),
            "temporal_decay": temporal_decay,
            "evidence_recency": score_components.get("evidence_recency"),
            "verdict": snapshot.get("verdict"),
        })

    # Estimate effective half-life from score decay
    scores_with_time = [(s["checked_at"], s["freshness_score"]) for s in decay_data if s["freshness_score"] is not None and s["checked_at"] is not None]
    half_life_estimate = None
    if len(scores_with_time) >= 2:
        # Simple linear regression on score vs time
        import numpy as np
        try:
            times = [(t[0] - scores_with_time[0][0]) / 3600.0 for t in scores_with_time]  # hours since first
            scores = [t[1] for t in scores_with_time]
            if len(times) >= 2 and max(scores) > min(scores):
                # Fit exponential decay: score = a * exp(-b * t)
                log_scores = np.log([max(1, s) for s in scores])
                coeffs = np.polyfit(times, log_scores, 1)
                decay_rate = -coeffs[0]
                if decay_rate > 0:
                    half_life_estimate = int(np.log(2) / decay_rate * 3600)  # convert to seconds
        except (ValueError, np.linalg.LinAlgError):
            pass

    return {
        "claim_fingerprint": fingerprint,
        "decay_data": decay_data,
        "half_life_estimate_seconds": half_life_estimate,
    }


def export_prediction_accuracy(
    claim_id: str,
    *,
    store: Optional[SQLiteFeatureStore] = None,
) -> dict[str, Any]:
    """Compare predictions vs actual outcomes for a claim."""
    # For now, return placeholder since we need claim_id to fingerprint mapping
    return {
        "claim_fingerprint": claim_id,
        "predictions": [],
        "actual_outcomes": [],
        "accuracy_metrics": {},
        "note": "Prediction accuracy tracking requires historical predicted vs actual data.",
    }


def export_knowledge_base(
    fingerprint: str,
    *,
    format: str = "json",
    store: Optional[SQLiteFeatureStore] = None,
) -> dict[str, Any]:
    """Export ISO 25964-compliant knowledge base entry."""
    repository = store or get_feature_store()
    history = repository.list_snapshots(fingerprint, 1, 0)

    if not history:
        return {
            "claim_fingerprint": fingerprint,
            "error": "No verification history found.",
        }

    latest = history[0]
    metadata = latest.get("metadata", {})

    kb_entry = {
        "identifier": fingerprint,
        "claim_text": latest.get("claim_text"),
        "category": latest.get("category"),
        "current_verdict": latest.get("verdict"),
        "current_score": latest.get("freshness_score"),
        "last_verified": latest.get("checked_at"),
        "verification_id": latest.get("verification_id"),
        "sources": latest.get("sources", []),
        "score_components": metadata.get("score_components", {}),
        "confidence": metadata.get("confidence"),
        "explanation": metadata.get("explanation"),
    }

    if format == "json":
        return kb_entry
    elif format == "xml":
        from xml.etree import ElementTree as ET

        def populate(node, value):
            if isinstance(value, dict):
                for key, child in value.items():
                    populate(ET.SubElement(node, key), child)
            elif isinstance(value, list):
                for child in value:
                    populate(ET.SubElement(node, "item"), child)
            elif value is not None:
                node.text = str(value)

        root = ET.Element("knowledge_base")
        populate(root, kb_entry)
        return {"xml": ET.tostring(root, encoding="unicode")}
    elif format == "csv":
        # Flatten for CSV
        import csv
        import io
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["field", "value"])
        for k, v in kb_entry.items():
            writer.writerow([k, str(v)])
        return {"csv": output.getvalue()}

    return kb_entry
