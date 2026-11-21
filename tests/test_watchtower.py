from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from core.config import load_config
from core.models import Claim, Source, Summary, VerificationResult
from core.persistence import SQLiteFeatureStore
from core.policies import create_policy
from core.watchtower import WatchtowerService
from server.app import app


@pytest.fixture
def watch_store(monkeypatch):
    db_path = Path(__file__).resolve().parents[1] / f".freshgate-watchtower-test-{uuid.uuid4().hex}.sqlite3"
    store = SQLiteFeatureStore(str(db_path))
    monkeypatch.setattr("server.routes.watchtower.get_feature_store", lambda: store)
    monkeypatch.setattr("core.policies.get_feature_store", lambda: store)
    try:
        yield store
    finally:
        for path in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
            path.unlink(missing_ok=True)


def make_result(verdict="contradicted", score=20, sources=None, provider_health=None, explanation="Current official evidence contradicts the claim"):
    claim = Claim(
        id="claim-1",
        text="Python 3.13 is the current stable release",
        type="version",
        verdict=verdict,
        score=score,
        explanation=explanation,
        sources=sources if sources is not None else [Source(
            url="https://www.python.org/downloads/", snippet="Python 3.14 is current",
            published="2026-11-12", authority_score=0.99, is_primary=True,
        )],
    )
    return VerificationResult(
        claims=[claim],
        summary=Summary(total=1, checked=1, stale=1 if verdict == "contradicted" else 0),
        provider_health=provider_health or {"openai": "configured", "tavily": "healthy"},
    )


class FakePipeline:
    def __init__(self, result):
        self.result = result
        self.policies = []

    async def verify(self, answer_text, policy=None, **kwargs):
        assert answer_text == "Python 3.13 is the current stable release"
        self.policies.append(policy)
        return self.result


def make_service(store, pipeline):
    config = load_config(override_env={
        "FRESHGATE_WATCHTOWER_MAX_CONCURRENCY": "1",
        "FRESHGATE_WATCHTOWER_MAX_RETRIES": "2",
        "FRESHGATE_WATCHTOWER_RETRY_BASE_SECONDS": "5",
    })
    return WatchtowerService(store=store, pipeline=pipeline, config=config)


@pytest.mark.asyncio
async def test_register_reverify_contradiction_creates_persistent_alert_and_acknowledges(watch_store, monkeypatch, auth_cookie):
    policy = create_policy({
        "name": "Official versions", "categories": ["version"], "verification_mode": "warn"
    }, store=watch_store)
    pipeline = FakePipeline(make_result())
    service = make_service(watch_store, pipeline)
    monkeypatch.setattr("server.routes.watchtower.watchtower_service", service)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=auth_cookie) as client:
        registered = await client.post("/api/watchtower/monitor", json={
            "claim_text": "Python 3.13 is the current stable release",
            "category": "version",
            "monitoring_interval": 3600,
            "policy_id": policy.id,
            "baseline": {
                "verdict": "confirmed", "score": 92,
                "sources": [{"url": "https://docs.python.org/3/", "snippet": "3.13 is current", "authority_score": 0.99}],
            },
        })
        assert registered.status_code == 201, registered.text
        claim_id = registered.json()["id"]
        detail = await client.get(f"/api/watchtower/claims/{claim_id}")
        assert detail.status_code == 200
        assert detail.json()["last_verdict"] == "confirmed"
        paused = await client.patch(f"/api/watchtower/claims/{claim_id}", json={"enabled": False})
        assert paused.status_code == 200 and paused.json()["enabled"] is False
        assert (await client.post(f"/api/watchtower/claims/{claim_id}/check")).status_code == 409
        resumed = await client.patch(f"/api/watchtower/claims/{claim_id}", json={"enabled": True})
        assert resumed.status_code == 200 and resumed.json()["enabled"] is True
        outcome = await client.post(f"/api/watchtower/claims/{claim_id}/check")
        assert outcome.status_code == 200, outcome.text
        assert outcome.json()["alert"] is True
        assert pipeline.policies[0].id == policy.id
        alerts = await client.get("/api/watchtower/alerts")
        assert len(alerts.json()["alerts"]) == 1
        alert = alerts.json()["alerts"][0]
        assert alert["previous_verdict"] == "confirmed"
        assert alert["current_verdict"] == "contradicted"
        assert alert["old_evidence"][0]["url"] == "https://docs.python.org/3/"
        assert alert["new_evidence"][0]["url"] == "https://www.python.org/downloads/"
        acknowledged = await client.post(f"/api/watchtower/alerts/{alert['id']}/acknowledge")
        assert acknowledged.status_code == 200
        assert acknowledged.json()["acknowledged_at"] is not None


@pytest.mark.asyncio
async def test_temporary_search_failure_retries_without_false_change_alert(watch_store):
    claim = watch_store.save_monitored_claim({
        "claim_text": "Python 3.13 is the current stable release",
        "category": "version", "monitoring_interval": 3600,
        "last_verdict": "confirmed", "last_score": 93,
        "metadata": {"last_evidence": [{"url": "https://docs.python.org/3/"}]},
    })
    failed_pipeline = FakePipeline(make_result(
        verdict="unverifiable", score=None, sources=[],
        provider_health={"tavily": "rate_limited"}, explanation="No authoritative search results found",
    ))
    service = make_service(watch_store, failed_pipeline)

    outcome = await service.check_now(claim["id"])

    assert outcome["status"] == "failed"
    refreshed = watch_store.get_monitored_claim(claim["id"])
    assert refreshed["last_verdict"] == "confirmed"
    assert refreshed["retry_count"] == 1
    assert "rate_limited" in refreshed["last_error"]
    assert watch_store.list_alerts() == []


@pytest.mark.asyncio
async def test_repeated_inconclusive_evidence_creates_alert_only_after_retry_and_clears_score(watch_store):
    claim = watch_store.save_monitored_claim({
        "claim_text": "Python 3.13 is the current stable release",
        "category": "version", "monitoring_interval": 3600,
        "last_verdict": "confirmed", "last_score": 92,
        "metadata": {"last_evidence": [{"url": "https://docs.python.org/3/"}]},
    })
    result = make_result(
        verdict="unverifiable", score=None, sources=[], explanation="Evidence was inconclusive"
    )
    service = make_service(watch_store, FakePipeline(result))

    first = await service.check_now(claim["id"])
    assert first["status"] == "retrying"
    assert watch_store.list_alerts() == []
    second = await service.check_now(claim["id"])

    assert second["status"] == "completed" and second["alert"] is True
    current = watch_store.get_monitored_claim(claim["id"])
    assert current["last_verdict"] == "unverifiable"
    assert current["last_score"] is None
    assert len(watch_store.list_alerts()) == 1


def test_due_job_leases_prevent_duplicate_workers_and_expire_for_restart_recovery(watch_store):
    claim = watch_store.save_monitored_claim({
        "claim_text": "Python version is current", "category": "version",
        "monitoring_interval": 3600, "next_check_at": 100.0,
    })
    other_process_store = SQLiteFeatureStore(watch_store.db_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(watch_store.claim_due_monitored_claims, 1, 60, 101.0)
        second = executor.submit(other_process_store.claim_due_monitored_claims, 1, 60, 102.0)
        first_worker, second_worker = first.result(), second.result()
    claimed = first_worker or second_worker
    assert len(claimed) == 1
    assert len(first_worker) + len(second_worker) == 1
    recovered = watch_store.claim_due_monitored_claims(1, lease_seconds=60, now=162.0)
    assert recovered[0]["id"] == claim["id"]
    assert recovered[0]["lease_token"] != claimed[0]["lease_token"]


@pytest.mark.asyncio
async def test_unresolved_transition_does_not_create_duplicate_alert(watch_store):
    claim = watch_store.save_monitored_claim({
        "claim_text": "Python 3.13 is the current stable release", "category": "version",
        "monitoring_interval": 3600, "last_verdict": "confirmed", "last_score": 92,
        "metadata": {"last_evidence": [{"url": "https://docs.python.org/3/"}]},
    })
    service = make_service(watch_store, FakePipeline(make_result()))
    await service.check_now(claim["id"])
    await service.check_now(claim["id"])
    assert len(watch_store.list_alerts()) == 1

@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "mongo"])
async def test_email_includes_baselines_pending_latest_and_all_pages(tmp_path, backend):
    from unittest.mock import AsyncMock
    from core.config import FreshGateConfig
    from core.email import EmailService
    if backend == "mongo":
        import mongomock
        from core.mongo_store import MongoFeatureStore, _ANY
        root = MongoFeatureStore(owner_id=_ANY, db=mongomock.MongoClient().db)
    else:
        root = SQLiteFeatureStore(str(tmp_path / "digest.db"))
    store = root.for_owner("alice")
    rows = []
    for index in range(101):
        rows.append(store.save_monitored_claim({
            "claim_text": f"Version claim {index}", "category": "version", "monitoring_interval": 3600,
            "last_verdict": "confirmed" if index else None,
            "last_score": 90 if index else None,
            "last_successful_at": 100 if index else None,
            "metadata": {"last_evidence": [{"url": "https://example.com/baseline", "snippet": "Baseline evidence"}]},
        }))
    store.save_snapshot({
        "claim_fingerprint": rows[1]["claim_fingerprint"],
        "claim_text": rows[1]["claim_text"], "category": "version",
        "verdict": "contradicted", "freshness_score": 12, "checked_at": 200,
    })
    alert = store.save_alert({"monitored_claim_id": rows[1]["id"],
                              "previous_verdict": "confirmed", "current_verdict": "contradicted",
                              "reason": "Updated evidence", "transition_key": "change"})
    store.acknowledge_alert(alert["id"])
    if backend == "mongo":
        root.for_owner("bob").save_monitored_claim({"claim_text": "Private Bob claim", "category": "version", "monitoring_interval": 3600})
    config = FreshGateConfig(brevo_api_key="fake-test-credential")
    email = EmailService(config)
    email.send_html = AsyncMock(return_value=True)
    service = WatchtowerService(store=root, config=config)
    service._email_service = email
    assert await service.send_scheduled_report("alice", "alice@example.com")
    html = email.send_html.call_args.args[2]
    assert html.count('class="claim-text"') == 101
    assert "No claims are currently" not in html
    assert "Pending" in html
    assert "12/100" in html
    assert "1970-01-01T00:03:20+00:00" in html
    assert "https://example.com/baseline" in html
    assert "Updated evidence" not in html
    assert "Private Bob claim" not in html


@pytest.mark.asyncio
async def test_failed_digest_does_not_advance_schedule(watch_store):
    from unittest.mock import AsyncMock, Mock
    service = make_service(watch_store, FakePipeline(make_result()))
    service._email_service = Mock()
    service._email_service._is_configured.return_value = True
    service.send_scheduled_report = AsyncMock(return_value=False)
    schedule = {"enabled": True, "next_run_at": 1, "interval_seconds": 60,
                "recipient_email": "alice@example.com"}
    watch_store.update_user_metadata("alice", {"email_schedule": schedule})
    assert await service.run_due_email_reports() == []
    assert watch_store.get_user_metadata("alice")["email_schedule"] == schedule
