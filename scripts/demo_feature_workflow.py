"""Exercise Policy Studio, Verify, Watchtower, Replay, and the CLI API client.

Start FreshGate with server-side OPENAI_API_KEY and TAVILY_API_KEY, then run
``python scripts/demo_feature_workflow.py``. The script creates a policy and
monitoring record in the configured feature database.
"""

from __future__ import annotations

import os

import httpx

from cli.client import FreshGateClient


ANSWER = "Python 3.14 is the current stable release."


def main() -> int:
    base_url = os.getenv("FRESHGATE_URL", "http://127.0.0.1:8000").rstrip("/")
    timeout = float(os.getenv("FRESHGATE_CLI_TIMEOUT", "30"))
    with httpx.Client(base_url=base_url, timeout=timeout) as api:
        policy_response = api.post("/api/policies", json={
            "name": "FreshGate workflow demo",
            "description": "Demo policy for official Python release information",
            "categories": ["version"],
            "preferred_domains": ["python.org"],
            "minimum_source_count": 1,
            "minimum_freshness_score": 50,
            "verification_mode": "warn",
        })
        policy_response.raise_for_status()
        policy = policy_response.json()
        print(f"Created policy: {policy['name']} ({policy['id']})")

    with FreshGateClient(
        base_url,
        timeout=timeout,
        retries=1,
        openai_api_key=os.getenv("FRESHGATE_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY"),
        tavily_api_key=os.getenv("FRESHGATE_TAVILY_API_KEY") or os.getenv("TAVILY_API_KEY"),
    ) as client:
        verified = client.verify(ANSWER, policy_id=policy["id"])
    claims = [claim for claim in verified.get("claims", []) if claim.get("verdict") != "unverifiable"]
    if not claims:
        print("The live verification returned no verifiable claims; no monitoring record was created.")
        return 1
    claim = claims[0]
    print(f"Verified: {claim['verdict']} · score {claim.get('score')} · {claim['text']}")

    monitoring = httpx.post(
        f"{base_url}/api/watchtower/monitor",
        timeout=timeout,
        json={
            "claim_text": claim["text"],
            "category": claim.get("type") or claim.get("category"),
            "entities": claim.get("entities", []),
            "policy_id": policy["id"],
            "monitoring_interval": 3600,
            "baseline": {
                "verdict": claim["verdict"],
                "score": claim.get("score"),
                "sources": claim.get("sources", []),
            },
        },
    )
    monitoring.raise_for_status()
    monitored = monitoring.json()
    print(f"Registered Watchtower claim: {monitored['id']}")

    check = httpx.post(
        f"{base_url}/api/watchtower/claims/{monitored['id']}/check", timeout=timeout
    )
    check.raise_for_status()
    print(f"Watchtower check: {check.json()['status']} · alert={check.json().get('alert', False)}")

    replay = httpx.get(f"{base_url}/api/replay/{claim['claim_fingerprint']}", timeout=timeout)
    replay.raise_for_status()
    comparison = replay.json().get("latest_comparison")
    print(f"Replay snapshots: {replay.json()['total']} · comparison ready={comparison is not None}")

    with FreshGateClient(
        base_url,
        timeout=timeout,
        retries=1,
        openai_api_key=os.getenv("FRESHGATE_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY"),
        tavily_api_key=os.getenv("FRESHGATE_TAVILY_API_KEY") or os.getenv("TAVILY_API_KEY"),
    ) as cli:
        cli_result = cli.verify(ANSWER, policy_id=policy["id"])
    print(f"CLI API verification: {len(cli_result.get('claims', []))} claim(s) returned")
    print("Open the dashboard's Policy Studio, Watchtower, and Replay tabs to inspect the saved workflow.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
