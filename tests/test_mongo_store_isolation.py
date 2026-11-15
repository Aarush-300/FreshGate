"""Tests for MongoFeatureStore isolation and ownership scoping."""
from __future__ import annotations

import time
import mongomock
import pytest

from core.mongo_store import MongoFeatureStore, _ANY


@pytest.fixture
def mock_db():
    client = mongomock.MongoClient()
    return client["test_freshgate"]


def test_user_isolation(mock_db):
    root_store = MongoFeatureStore(owner_id=_ANY, db=mock_db)
    store_a = root_store.for_owner("user_a")
    store_b = root_store.for_owner("user_b")

    # 1. User A creates a policy, a claim, an alert, and a snapshot
    policy_a = store_a.create_policy({
        "name": "Policy A",
        "description": "Policy for user A",
        "configuration": {"max_age_days": 7},
    })

    now = time.time()
    claim_a = store_a.save_monitored_claim({
        "claim_text": "Sample claim for A",
        "category": "leadership",
        "monitoring_interval": 3600,
        "next_check_at": now - 10,
    })

    alert_a = store_a.save_alert({
        "monitored_claim_id": claim_a["id"],
        "reason": "Verdict changed for A",
        "transition_key": "conf_to_contra",
    })

    snapshot_a = store_a.save_snapshot({
        "claim_text": "Sample claim for A",
        "category": "leadership",
        "verdict": "confirmed",
        "freshness_score": 90,
    })

    # 2. User B's scoped store sees NONE of them
    assert store_b.get_policy(policy_a["id"]) is None
    assert len(store_b.list_policies()) == 0

    assert store_b.get_monitored_claim(claim_a["id"]) is None
    assert len(store_b.list_monitored_claims()) == 0
    assert store_b.count_monitored_claims() == 0

    assert store_b.get_alert(alert_a["id"]) is None
    assert len(store_b.list_alerts()) == 0

    assert store_b.get_snapshot(snapshot_a["id"]) is None
    assert len(store_b.list_snapshots(snapshot_a["claim_fingerprint"])) == 0
    assert store_b.count_snapshots(snapshot_a["claim_fingerprint"]) == 0

    # 3. User B cannot delete user A's claim
    assert store_b.delete_monitored_claim(claim_a["id"]) is False
    # User A's claim is still there
    assert store_a.get_monitored_claim(claim_a["id"]) is not None

    # 4. Root store's claim_due_monitored_claims returns A's due claim with owner_id set
    due = root_store.claim_due_monitored_claims(limit=10, lease_seconds=60, now=now)
    assert len(due) == 1
    assert due[0]["id"] == claim_a["id"]
    assert due[0]["owner_id"] == "user_a"

    # 5. Double leasing the same claim returns it only once
    due_second = root_store.claim_due_monitored_claims(limit=10, lease_seconds=60, now=now)
    assert len(due_second) == 0

    # 6. User B cannot lease or finish user A's claim
    assert store_b.claim_monitored_claim(claim_a["id"], lease_seconds=60, now=now, force=True) is None

    # 7. Non-root cannot call claim_due_monitored_claims
    with pytest.raises(RuntimeError):
        store_a.claim_due_monitored_claims(limit=10, lease_seconds=60)
