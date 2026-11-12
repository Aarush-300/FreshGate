from __future__ import annotations

import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from core.claim_identity import claim_fingerprint, normalize_claim_text
from core.config import load_config
from core.persistence import FeatureStoreError, SQLiteFeatureStore


@pytest.fixture
def feature_store():
    # Windows sandboxing may deny newly-created test temp directories; use a
    # uniquely named file directly under the writable workspace instead.
    db_path = Path(__file__).resolve().parents[2] / f".freshgate-feature-test-{uuid.uuid4().hex}.sqlite3"
    try:
        yield SQLiteFeatureStore(str(db_path))
    finally:
        for path in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
            path.unlink(missing_ok=True)


def test_claim_identity_normalizes_case_and_whitespace_but_preserves_values():
    assert normalize_claim_text("  API   Version 2.4.1 ") == "api version 2.4.1"
    assert claim_fingerprint("The current API version is 2.4.", "version") == claim_fingerprint(
        " the CURRENT api version IS 2.4. ", " VERSION "
    )
    assert claim_fingerprint("API version 2.4", "version") != claim_fingerprint(
        "API version 2.5", "version"
    )
    assert claim_fingerprint("Company acquired Acme", "company_info") != claim_fingerprint(
        "Company acquired Acme", "other"
    )


def test_claim_identity_includes_entity_and_jurisdiction_context():
    base = claim_fingerprint("The law takes effect in 2027", "law")
    assert base != claim_fingerprint("The law takes effect in 2027", "law", jurisdiction="California")
    assert base != claim_fingerprint("The law takes effect in 2027", "law", entities=["Acme"])
    assert claim_fingerprint(
        "Claim text", "law", entities=["Acme", "OpenAI"]
    ) == claim_fingerprint("Claim text", "law", entities=["openai", "acme"])


@pytest.mark.parametrize("text,category", [("", "law"), ("claim", "")])
def test_claim_identity_rejects_empty_identity_fields(text, category):
    with pytest.raises(ValueError):
        claim_fingerprint(text, category)


def test_feature_store_creates_versioned_schema_and_policy(feature_store):
    policy = feature_store.create_policy({
        "name": "Official sources",
        "description": "Prefer primary sources",
        "configuration": {"preferred_domains": ["example.gov"]},
    })

    assert feature_store.get_policy(policy["id"]) == policy
    assert feature_store.list_policies() == [policy]
    with sqlite3.connect(feature_store.db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"policies", "monitored_claims", "verification_snapshots", "watchtower_alerts"} <= tables


def test_feature_store_uses_configured_database_path():
    configured = load_config(override_env={"FRESHGATE_FEATURE_DB": "data/feature-history.sqlite3"})
    assert configured.feature_db_path == "data/feature-history.sqlite3"


def test_monitored_claim_and_snapshot_round_trip(feature_store):
    policy = feature_store.create_policy({"name": "monitoring-policy"})
    claim = feature_store.save_monitored_claim({
        "claim_text": "Python 3.13 is the current stable release",
        "category": "version",
        "monitoring_interval": 3600,
        "policy_id": policy["id"],
        "metadata": {"origin": "test"},
    })
    snapshot = feature_store.save_snapshot({
        "verification_id": "verify-001",
        "claim_text": claim["claim_text"],
        "category": claim["category"],
        "verdict": "confirmed",
        "freshness_score": 91,
        "sources": [{"url": "https://python.org", "snippet": "Python 3.13"}],
        "cached": True,
        "verification_duration": 0.31,
        "metadata": {"test": True},
    })

    assert feature_store.get_monitored_claim(claim["id"]) == claim
    assert feature_store.get_snapshot(snapshot["id"]) == snapshot
    assert feature_store.get_snapshot_by_verification_id("verify-001") == snapshot
    assert feature_store.list_snapshots(claim["claim_fingerprint"]) == [snapshot]
    assert snapshot["claim_fingerprint"] == claim["claim_fingerprint"]


def test_snapshots_survive_store_reinitialization(feature_store):
    snapshot = feature_store.save_snapshot({
        "verification_id": "durable-verify-1",
        "claim_text": "An existing verified claim",
        "category": "other",
        "verdict": "confirmed",
    })
    reopened = SQLiteFeatureStore(feature_store.db_path)
    assert reopened.get_snapshot(snapshot["id"]) == snapshot


def test_verification_snapshots_are_append_only(feature_store):
    snapshot = feature_store.save_snapshot({
        "verification_id": "immutable-1",
        "claim_text": "A factual claim",
        "category": "other",
        "verdict": "confirmed",
    })
    with sqlite3.connect(feature_store.db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("UPDATE verification_snapshots SET verdict = 'contradicted' WHERE id = ?", (snapshot["id"],))
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("DELETE FROM verification_snapshots WHERE id = ?", (snapshot["id"],))
    conn.close()
    assert feature_store.get_snapshot(snapshot["id"])["verdict"] == "confirmed"


def test_alerts_store_evidence_and_acknowledge_once(feature_store):
    monitored = feature_store.save_monitored_claim({
        "claim_text": "Current API version is 2.4",
        "category": "version",
        "monitoring_interval": 600,
    })
    alert = feature_store.save_alert({
        "monitored_claim_id": monitored["id"],
        "previous_verdict": "confirmed",
        "current_verdict": "contradicted",
        "previous_score": 90,
        "current_score": 20,
        "reason": "New primary source contradicts the previous result",
        "old_evidence": [{"url": "https://old.example"}],
        "new_evidence": [{"url": "https://new.example"}],
    })
    assert feature_store.get_alert(alert["id"]) == alert
    assert feature_store.list_alerts(unacknowledged_only=True) == [alert]
    assert feature_store.acknowledge_alert(alert["id"], acknowledged_at=123.0)
    assert feature_store.get_alert(alert["id"])["acknowledged_at"] == 123.0
    assert feature_store.list_alerts(unacknowledged_only=True) == []


def test_snapshot_ids_are_unique_and_pagination_is_validated(feature_store):
    data = {"claim_text": "A claim", "category": "other", "verification_id": "same-id"}
    feature_store.save_snapshot(data)
    with pytest.raises(sqlite3.IntegrityError):
        feature_store.save_snapshot(data)
    with pytest.raises(ValueError):
        feature_store.list_snapshots("fingerprint", limit=0)


def test_concurrent_snapshot_writes_are_not_lost(feature_store):
    def write(index: int):
        return feature_store.save_snapshot({
            "verification_id": f"parallel-{index}",
            "claim_text": f"Claim {index}",
            "category": "other",
        })

    with ThreadPoolExecutor(max_workers=8) as executor:
        snapshots = list(executor.map(write, range(24)))
    assert len({snapshot["id"] for snapshot in snapshots}) == 24
    assert sum(len(feature_store.list_snapshots(snapshot["claim_fingerprint"])) for snapshot in snapshots) == 24


def test_database_refuses_a_newer_schema():
    db_path = Path(__file__).resolve().parents[2] / f".freshgate-feature-future-{uuid.uuid4().hex}.sqlite3"
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute("PRAGMA user_version = 999")
        conn.close()
        with pytest.raises(FeatureStoreError, match="newer than supported"):
            SQLiteFeatureStore(str(db_path))
    finally:
        for path in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
            path.unlink(missing_ok=True)


def test_database_migrates_phase1_schema_to_watchtower_leases():
    db_path = Path(__file__).resolve().parents[1] / f".freshgate-feature-v1-{uuid.uuid4().hex}.sqlite3"
    try:
        conn = sqlite3.connect(db_path)
        try:
            SQLiteFeatureStore._migrate_to_v1(conn)
            conn.execute("PRAGMA user_version = 1")
        finally:
            conn.close()
        migrated = SQLiteFeatureStore(str(db_path))
        conn = sqlite3.connect(migrated.db_path)
        try:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
            monitored_columns = {row[1] for row in conn.execute("PRAGMA table_info(monitored_claims)")}
            alert_columns = {row[1] for row in conn.execute("PRAGMA table_info(watchtower_alerts)")}
        finally:
            conn.close()
        assert {"lease_token", "lease_until", "retry_count", "last_error", "last_successful_at"} <= monitored_columns
        assert "transition_key" in alert_columns
    finally:
        for path in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
            path.unlink(missing_ok=True)
