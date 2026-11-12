"""Read-only comparison and timeline services for immutable snapshots."""

from __future__ import annotations

from typing import Any, Optional

from core.history import get_claim_history
from core.persistence import SQLiteFeatureStore, get_feature_store


def _source_map(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result = {}
    for source in snapshot.get("sources", []):
        url = source.get("url")
        if url:
            result[url] = source
    return result


def compare_snapshots(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    before_sources = _source_map(previous)
    after_sources = _source_map(current)
    old_urls, new_urls = set(before_sources), set(after_sources)
    common = old_urls & new_urls
    changed_sources = [
        {"url": url, "previous": before_sources[url], "current": after_sources[url]}
        for url in sorted(common)
        if any(before_sources[url].get(key) != after_sources[url].get(key) for key in ("snippet", "published", "authority_score"))
    ]
    before_metadata = previous.get("metadata") or {}
    current_metadata = current.get("metadata") or {}
    before_components = before_metadata.get("score_components") or {}
    current_components = current_metadata.get("score_components") or {}
    before_policy = previous.get("policy_id")
    current_policy = current.get("policy_id")
    before_policy_revision = before_metadata.get("policy_updated_at")
    current_policy_revision = current_metadata.get("policy_updated_at")
    return {
        "previous": previous,
        "current": current,
        "verdict": {"previous": previous.get("verdict"), "current": current.get("verdict"), "changed": previous.get("verdict") != current.get("verdict")},
        "freshness_score": {
            "previous": previous.get("freshness_score"),
            "current": current.get("freshness_score"),
            "delta": (current.get("freshness_score") - previous.get("freshness_score"))
            if previous.get("freshness_score") is not None and current.get("freshness_score") is not None else None,
        },
        "sources": {
            "added": [after_sources[url] for url in sorted(new_urls - old_urls)],
            "removed": [before_sources[url] for url in sorted(old_urls - new_urls)],
            "changed": changed_sources,
            "authority": {
                "previous": before_components.get("source_authority"),
                "current": current_components.get("source_authority"),
            },
            "recency": {
                "previous": before_components.get("evidence_recency"),
                "current": current_components.get("evidence_recency"),
            },
            "agreement": {
                "previous": before_components.get("evidence_agreement"),
                "current": current_components.get("evidence_agreement"),
            },
        },
        "correction": {"previous": before_metadata.get("correction"), "current": current_metadata.get("correction")},
        "policy": {
            "previous_id": before_policy,
            "current_id": current_policy,
            "previous_name": before_metadata.get("policy_name"),
            "current_name": current_metadata.get("policy_name"),
            "previous_revision": before_policy_revision,
            "current_revision": current_policy_revision,
            "changed": before_policy != current_policy or before_policy_revision != current_policy_revision,
        },
        "evidence_changed": bool(new_urls != old_urls or changed_sources),
    }


def replay_claim(
    fingerprint: str,
    *,
    limit: int = 100,
    offset: int = 0,
    store: Optional[SQLiteFeatureStore] = None,
) -> dict[str, Any]:
    repository = store or get_feature_store()
    snapshots = repository.list_snapshots(fingerprint, limit, offset, newest_first=False)
    latest = repository.list_snapshots(fingerprint, 2, 0, newest_first=True)
    latest_comparison = compare_snapshots(latest[1], latest[0]) if len(latest) >= 2 else None
    return {
        "claim_fingerprint": fingerprint,
        "snapshots": snapshots,
        "total": repository.count_snapshots(fingerprint),
        "limit": limit,
        "offset": offset,
        "latest_comparison": latest_comparison,
    }


def compare_by_id(
    fingerprint: str,
    from_id: str,
    to_id: str,
    store: Optional[SQLiteFeatureStore] = None,
) -> Optional[dict[str, Any]]:
    repository = store or get_feature_store()
    previous = repository.get_snapshot(from_id) or repository.get_snapshot_by_verification_id(from_id)
    current = repository.get_snapshot(to_id) or repository.get_snapshot_by_verification_id(to_id)
    if not previous or not current:
        return None
    if previous["claim_fingerprint"] != fingerprint or current["claim_fingerprint"] != fingerprint:
        return None
    return compare_snapshots(previous, current)


def replay_timeline(
    fingerprint: str,
    *,
    limit: int = 100,
    offset: int = 0,
    store: Optional[SQLiteFeatureStore] = None,
) -> dict[str, Any]:
    repository = store or get_feature_store()
    snapshot_count = repository.count_snapshots(fingerprint)
    all_snapshots = []
    for page_offset in range(0, snapshot_count, 1000):
        all_snapshots.extend(repository.list_snapshots(fingerprint, 1000, page_offset, newest_first=False))
    all_alerts = []
    page_offset = 0
    while True:
        page = repository.list_alerts(1000, page_offset, False)
        all_alerts.extend(page)
        if len(page) < 1000:
            break
        page_offset += 1000
    matching_alerts = [
        alert for alert in all_alerts
        if (alert.get("metadata") or {}).get("claim_fingerprint") == fingerprint
    ]
    events = [
        {"event_type": "verification", "timestamp": row["checked_at"], "verification": row}
        for row in all_snapshots
    ] + [
        {"event_type": "alert", "timestamp": row["created_at"], "alert": row}
        for row in matching_alerts
    ]
    events.sort(key=lambda event: (event["timestamp"], event["event_type"]))
    return {
        "claim_fingerprint": fingerprint,
        "events": events[offset:offset + limit],
        "limit": limit,
        "offset": offset,
        "total": snapshot_count + len(matching_alerts),
    }
