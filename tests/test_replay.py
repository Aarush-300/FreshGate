from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from core.claim_identity import claim_fingerprint
from core.history import save_claim_snapshot
from core.models import Claim, ExtractionResult, Source
from core.persistence import SQLiteFeatureStore
from core.pipeline import FreshGatePipeline
from core.policy_models import PolicyConfig
from server.app import app


@pytest.fixture
def replay_store(monkeypatch):
    db_path = Path(__file__).resolve().parents[1] / f".freshgate-replay-test-{uuid.uuid4().hex}.sqlite3"
    store = SQLiteFeatureStore(str(db_path))
    monkeypatch.setattr("server.routes.replay.get_feature_store", lambda: store)
    try:
        yield store
    finally:
        for path in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
            path.unlink(missing_ok=True)


def history_claim(verdict, score, source_url, snippet, *, cached=False):
    return Claim(
        id="claim-1",
        text="The current release is Python 3.14",
        type="version",
        verdict=verdict,
        score=score,
        confidence=0.9,
        correction="Python 3.14 is the current release" if verdict == "contradicted" else None,
        explanation=f"Evidence says {snippet}",
        cached=cached,
        sources=[Source(
            url=source_url,
            domain=source_url.split("/")[2],
            snippet=snippet,
            published="2026-11-12",
            authority_score=0.95,
            is_primary=True,
        )],
    )


def test_pipeline_persists_one_immutable_snapshot_per_checked_claim(replay_store):
    class Extractor:
        async def extract(self, _text):
            return ExtractionResult(claims=[Claim(
                id="c1", text="The current release is Python 3.14", type="version"
            )], status="success", total_extracted=1, valid_claims=1)

    class Verifier:
        tavily_client = SimpleNamespace(status="healthy")

        async def verify_claim(self, claim):
            claim.verdict = "confirmed"
            claim.confidence = 0.9
            claim.sources = [Source(
                url="https://www.python.org/downloads/", snippet="Python 3.14 is current",
                published="2026-11-12", authority_score=0.99, is_primary=True,
            )]
            return claim

    class EmptyCache:
        def get(self, *_args, **_kwargs): return None
        def set(self, *_args, **_kwargs): return None

    pipeline = FreshGatePipeline(
        extractor=Extractor(), verifier=Verifier(), cache=EmptyCache(), history_store=replay_store
    )

    async def run():
        return await pipeline.verify("The current release is Python 3.14")

    import asyncio
    result = asyncio.run(run())
    claim = result.claims[0]
    fingerprint = claim_fingerprint(claim.text, claim.type)
    history = replay_store.list_snapshots(fingerprint)

    assert claim.verification_id
    assert claim.claim_fingerprint == fingerprint
    assert len(history) == 1
    snapshot = history[0]
    assert snapshot["verification_id"] == claim.verification_id
    assert snapshot["freshness_score"] == claim.score
    assert snapshot["verification_duration"] is not None
    assert snapshot["metadata"]["score_components"]["source_authority"] > 0.9
    assert snapshot["sources"][0]["snippet"] == "Python 3.14 is current"


@pytest.mark.asyncio
async def test_history_api_replay_comparison_timeline_and_read_only_behavior(replay_store):
    policy = PolicyConfig.model_validate({"name": "Official sources", "categories": ["version"]})
    first = save_claim_snapshot(
        history_claim("confirmed", 92, "https://docs.example.com/release", "Version 3.13 is current"),
        policy=policy,
        duration_seconds=0.4,
        store=replay_store,
    )
    second = save_claim_snapshot(
        history_claim("contradicted", 10, "https://docs.example.com/release", "Version 3.14 is current"),
        duration_seconds=0.5,
        store=replay_store,
    )
    fingerprint = first["claim_fingerprint"]
    replay_store.save_alert({
        "monitored_claim_id": None,
        "reason": "Verdict changed",
        "previous_verdict": "confirmed",
        "current_verdict": "contradicted",
        "metadata": {"claim_fingerprint": fingerprint},
    })
    count_before = replay_store.count_snapshots(fingerprint)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        history = await client.get(f"/api/history/claims/{fingerprint}?limit=1&offset=0")
        assert history.status_code == 200
        assert history.json()["total"] == 2
        assert history.json()["snapshots"][0]["verification_id"] == second["verification_id"]

        by_id = await client.get(f"/api/history/verifications/{first['verification_id']}")
        assert by_id.status_code == 200
        assert by_id.json()["verdict"] == "confirmed"

        replay = await client.get(f"/api/replay/{fingerprint}")
        assert replay.status_code == 200
        assert [row["verification_id"] for row in replay.json()["snapshots"]] == [
            first["verification_id"], second["verification_id"]
        ]
        latest = replay.json()["latest_comparison"]
        assert latest["verdict"]["changed"] is True
        assert latest["freshness_score"]["delta"] == -82
        assert latest["sources"]["changed"][0]["url"] == "https://docs.example.com/release"
        assert latest["policy"]["changed"] is True

        by_pair = await client.get(
            f"/api/replay/{fingerprint}/compare",
            params={"from": first["verification_id"], "to": second["verification_id"]},
        )
        assert by_pair.status_code == 200
        assert by_pair.json()["sources"]["changed"]

        timeline = await client.get(f"/api/replay/{fingerprint}/timeline?limit=10")
        assert timeline.status_code == 200
        assert [item["event_type"] for item in timeline.json()["events"]] == [
            "verification", "verification", "alert"
        ]
        assert (await client.get(f"/api/history/claims/{fingerprint}?limit=1&offset=1")).json()["snapshots"][0]["verification_id"] == first["verification_id"]

        mismatch = await client.get(
            f"/api/replay/{'0' * 64}/compare",
            params={"from": first["verification_id"], "to": second["verification_id"]},
        )
        assert mismatch.status_code == 404

    assert replay_store.count_snapshots(fingerprint) == count_before
