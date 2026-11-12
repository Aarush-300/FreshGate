from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient

from cli.client import FreshGateClient
from core.config import load_config
from core.models import Claim, ExtractionResult, Source
from core.persistence import SQLiteFeatureStore
from core.pipeline import FreshGatePipeline
from core.policies import get_policy
from core.watchtower import WatchtowerService
from server.app import app


def test_policy_verify_watchtower_alert_replay_and_cli_end_to_end(monkeypatch):
    db_path = Path(__file__).resolve().parents[1] / f".freshgate-integration-{uuid.uuid4().hex}.sqlite3"
    store = SQLiteFeatureStore(str(db_path))

    class Extractor:
        async def extract(self, _answer):
            claim = Claim(
                id="release", text="Python 3.13 is the current stable release",
                type="version", entities=["Python"],
            )
            return ExtractionResult(claims=[claim], total_extracted=1, valid_claims=1)

    class Verifier:
        tavily_client = SimpleNamespace(status="healthy")

        def __init__(self):
            self.calls = 0

        async def verify_claim(self, claim, policy=None):
            self.calls += 1
            verdict = "contradicted" if self.calls == 2 else "confirmed"
            source_url = "https://www.python.org/downloads/" if verdict == "confirmed" else "https://docs.python.org/3/"
            claim.verdict = verdict
            claim.explanation = f"Deterministic integration evidence: {verdict}"
            claim.sources = [Source(
                url=source_url, domain=source_url.split("/")[2],
                snippet=f"Evidence for {verdict}", published="2026-11-12",
                authority_score=0.99, is_primary=True, relevance_score=1.0,
            )]
            return claim

    class EmptyCache:
        def get(self, *_args, **_kwargs): return None
        def set(self, *_args, **_kwargs): return None

    pipeline = FreshGatePipeline(Extractor(), Verifier(), EmptyCache(), history_store=store)
    watchtower = WatchtowerService(
        store=store,
        pipeline=pipeline,
        config=load_config(override_env={
            "FRESHGATE_WATCHTOWER_MAX_CONCURRENCY": "1",
            "FRESHGATE_WATCHTOWER_MAX_RETRIES": "2",
        }),
    )
    monkeypatch.setattr("server.app.pipeline", pipeline)
    monkeypatch.setattr("server.app.get_policy", lambda policy_id: get_policy(policy_id, store=store))
    monkeypatch.setattr(
        "core.llm_manager.get_llm_manager",
        lambda: SimpleNamespace(stats=SimpleNamespace(status="configured")),
    )
    monkeypatch.setattr("server.routes.policies.get_feature_store", lambda: store)
    monkeypatch.setattr("server.routes.watchtower.get_feature_store", lambda: store)
    monkeypatch.setattr("server.routes.watchtower.watchtower_service", watchtower)
    monkeypatch.setattr("server.routes.replay.get_feature_store", lambda: store)
    monkeypatch.setattr("core.policies.get_feature_store", lambda: store)

    try:
        with TestClient(app) as api:
            policy_response = api.post("/api/policies", json={
                "name": "Official Python releases",
                "categories": ["version"],
                "preferred_domains": ["python.org"],
                "minimum_freshness_score": 50,
                "verification_mode": "warn",
            })
            assert policy_response.status_code == 201, policy_response.text
            policy_id = policy_response.json()["id"]

            verified = api.post("/verify", json={
                "answer": "Python 3.13 is the current stable release",
                "policy_id": policy_id,
            })
            assert verified.status_code == 200, verified.text
            first = verified.json()["claims"][0]
            assert first["verdict"] == "confirmed"
            assert first["verification_id"]
            assert verified.json()["policy_evaluation"]["policy_id"] == policy_id

            monitored = api.post("/api/watchtower/monitor", json={
                "claim_text": first["text"], "category": first["type"],
                "entities": first["entities"], "monitoring_interval": 3600,
                "policy_id": policy_id,
                "baseline": {
                    "verdict": first["verdict"], "score": first["score"],
                    "sources": first["sources"],
                },
            })
            assert monitored.status_code == 201, monitored.text
            claim_id = monitored.json()["id"]

            checked = api.post(f"/api/watchtower/claims/{claim_id}/check")
            assert checked.status_code == 200, checked.text
            assert checked.json()["status"] == "completed"
            assert checked.json()["verdict"] == "contradicted"
            assert checked.json()["alert"] is True

            alerts = api.get("/api/watchtower/alerts?limit=10")
            alert = alerts.json()["alerts"][0]
            fingerprint = alert["metadata"]["claim_fingerprint"]
            replay = api.get(f"/api/replay/{fingerprint}")
            assert replay.status_code == 200, replay.text
            assert replay.json()["total"] == 2
            assert replay.json()["latest_comparison"]["verdict"]["changed"] is True
            timeline = api.get(f"/api/replay/{fingerprint}/timeline")
            assert timeline.status_code == 200
            assert {event["event_type"] for event in timeline.json()["events"]} == {"verification", "alert"}

            def route_through_api(request):
                response = api.request(
                    request.method, str(request.url), headers=dict(request.headers), content=request.content
                )
                return httpx.Response(
                    response.status_code, headers=dict(response.headers), content=response.content,
                    request=request,
                )

            with FreshGateClient(
                "http://freshgate.test", retries=0, transport=httpx.MockTransport(route_through_api)
            ) as cli:
                cli_result = cli.verify("Python 3.13 is the current stable release", policy_id=policy_id)
            assert cli_result["claims"][0]["verdict"] == "confirmed"
            assert cli_result["claims"][0]["verification_id"]
    finally:
        for suffix in ("", "-wal", "-shm"):
            Path(f"{db_path}{suffix}").unlink(missing_ok=True)
