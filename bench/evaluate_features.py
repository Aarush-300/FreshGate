"""Deterministic regression and microbenchmark scenarios for FreshGate features.

This supplements the existing verification dataset without changing it or
calling external providers. Run with ``python bench/evaluate_features.py``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from cli.report import evaluate_result, render_single
from core.config import load_config
from core.models import Claim, Source, Summary, VerificationResult
from core.persistence import SQLiteFeatureStore
from core.policies import evaluate_policy_preview
from core.policy_models import PolicyConfig, PolicyPreviewClaim
from core.replay import compare_snapshots
from core.watchtower import WatchtowerService


def _measure(function: Callable[[], Any], repeats: int = 100) -> dict[str, float]:
    durations = []
    for _ in range(repeats):
        started = time.perf_counter()
        function()
        durations.append((time.perf_counter() - started) * 1000)
    ordered = sorted(durations)
    return {
        "p50_ms": round(statistics.median(ordered), 4),
        "p95_ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 4),
        "mean_ms": round(statistics.fmean(ordered), 4),
        "samples": len(ordered),
    }


def _run_watchtower_batch() -> dict[str, Any]:
    path = ROOT_DIR / f".freshgate-feature-benchmark-{uuid.uuid4().hex}.sqlite3"
    store = SQLiteFeatureStore(str(path))

    class SyntheticPipeline:
        def __init__(self):
            self.active = 0
            self.maximum_active = 0

        async def verify(self, answer_text: str, policy=None):
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
            try:
                await asyncio.sleep(0.002)
            finally:
                self.active -= 1
            return VerificationResult(
                claims=[Claim(
                    id="benchmark-claim", text=answer_text, type="version",
                    verdict="confirmed", score=90,
                )],
                summary=Summary(total=1, checked=1, stale=0, avg_score=90),
                provider_health={"openai": "configured", "tavily": "healthy"},
            )

    pipeline = SyntheticPipeline()
    config = load_config(override_env={
        "FRESHGATE_WATCHTOWER_MAX_CONCURRENCY": "3",
        "FRESHGATE_WATCHTOWER_MAX_RETRIES": "2",
    })
    service = WatchtowerService(store=store, pipeline=pipeline, config=config)
    now = time.time()
    for index in range(6):
        store.save_monitored_claim({
            "claim_text": f"Synthetic monitored release claim {index}",
            "category": "version",
            "monitoring_interval": 3600,
            "next_check_at": now - 1,
            "last_verdict": "confirmed",
            "last_score": 90,
            "metadata": {"entities": [], "last_evidence": []},
        })

    started = time.perf_counter()
    try:
        results = asyncio.run(service.run_due_checks())
    finally:
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{path}{suffix}")
            if candidate.exists():
                candidate.unlink()
    elapsed_ms = (time.perf_counter() - started) * 1000
    return {
        "batch_duration_ms": round(elapsed_ms, 4),
        "completed_jobs": sum(result.get("status") == "completed" for result in results),
        "selected_jobs": len(results),
        "configured_concurrency": config.watchtower_max_concurrency,
        "maximum_observed_concurrency": pipeline.maximum_active,
    }


def run_scenarios() -> dict[str, Any]:
    source = {
        "url": "https://www.python.org/downloads/",
        "snippet": "Official Python release information",
        "published": "2026-11-12",
        "authority_score": 0.99,
        "is_primary": True,
    }
    policy = PolicyConfig.model_validate({
        "name": "Feature benchmark policy",
        "categories": ["version"],
        "allowed_domains": ["python.org"],
        "minimum_freshness_score": 70,
        "verification_mode": "enforce",
    })
    policy_cases = [
        ("allowed_source_and_score_pass", [{**source, "domain": "www.python.org"}], 90, "accepted"),
        ("blocked_source_rejected", [{**source, "url": "https://attacker.example/release", "domain": "attacker.example"}], 90, "rejected"),
        ("policy_freshness_threshold_rejected", [{**source, "domain": "www.python.org"}], 40, "rejected"),
    ]
    policy_results = []
    policy_timings = []
    for name, sources, score, expected in policy_cases:
        def evaluate(sources=sources, score=score):
            return evaluate_policy_preview(policy, [PolicyPreviewClaim(**{
                "id": name,
                "text": "Python 3.14 is the current release",
                "category": "version",
                "sources": sources,
                "freshness_score": score,
            })])

        result = evaluate()
        policy_timings.append(_measure(evaluate))
        policy_results.append({"name": name, "expected": expected, "actual": result["decision"], "passed": result["decision"] == expected})

    changed_alert = WatchtowerService._transition_reason(
        "confirmed", "contradicted", 90, 35, 60, [source], [{**source, "url": "https://docs.python.org/3/"}]
    )
    stable_alert = WatchtowerService._transition_reason(
        "confirmed", "confirmed", 90, 88, 60, [source], [source]
    )
    watchtower_cases = [
        {"name": "verdict_change_alert", "passed": changed_alert is not None},
        {"name": "stable_claim_no_false_alert", "passed": stable_alert is None},
    ]
    batch_metrics = _run_watchtower_batch()
    watchtower_cases.append({
        "name": "scheduled_job_concurrency_is_bounded",
        "passed": (
            batch_metrics["completed_jobs"] == batch_metrics["selected_jobs"]
            and 1 < batch_metrics["maximum_observed_concurrency"]
            <= batch_metrics["configured_concurrency"]
        ),
    })
    watchtower_timing = _measure(lambda: WatchtowerService._transition_reason(
        "confirmed", "contradicted", 90, 35, 60, [source], [{**source, "url": "https://docs.python.org/3/"}]
    ))

    previous = {
        "verdict": "confirmed", "freshness_score": 90, "policy_id": policy.id,
        "sources": [{**source, "url": "https://www.python.org/downloads/", "snippet": "Release 3.13"}],
        "metadata": {"policy_name": policy.name, "policy_updated_at": 1, "correction": None,
                     "score_components": {"source_authority": 0.9, "evidence_recency": 0.8, "evidence_agreement": 1.0}},
    }
    current = {
        "verdict": "contradicted", "freshness_score": 35, "policy_id": policy.id,
        "sources": [{**source, "url": "https://docs.python.org/3/", "snippet": "Release 3.14"}],
        "metadata": {"policy_name": policy.name, "policy_updated_at": 1, "correction": "Python 3.14 is current",
                     "score_components": {"source_authority": 0.95, "evidence_recency": 1.0, "evidence_agreement": 0.9}},
    }

    def compare():
        return compare_snapshots(previous, current)

    comparison = compare()
    replay_cases = [
        {"name": "verdict_change_replayed", "passed": comparison["verdict"]["changed"]},
        {"name": "score_delta_accurate", "passed": comparison["freshness_score"]["delta"] == -55},
        {"name": "evidence_change_detected", "passed": comparison["evidence_changed"] and len(comparison["sources"]["added"]) == 1},
    ]

    cli_result = {"claims": [{"text": "Python 3.14 is current", "verdict": "confirmed", "score": 60, "sources": []}]}

    def render_cli():
        evaluation = evaluate_result(cli_result, min_score=70)
        return render_single({"result": cli_result, "evaluation": evaluation}, "json")

    rendered = render_cli()
    cli_cases = [{
        "name": "minimum_score_failure_rendered",
        "passed": '"passed": false' in rendered and "minimum freshness score" in rendered,
    }]
    scenario_groups = {
        "policy": {"cases": policy_results, "timing": {
            "p50_ms": round(statistics.median([sample["p50_ms"] for sample in policy_timings]), 4),
            "samples_per_case": 100,
        }},
        "watchtower": {
            "cases": watchtower_cases,
            "transition_timing": watchtower_timing,
            "scheduled_job_batch": batch_metrics,
        },
        "replay": {"cases": replay_cases, "comparison_timing": _measure(compare)},
        "cli": {"cases": cli_cases, "report_timing": _measure(render_cli)},
    }
    all_cases = [case for group in scenario_groups.values() for case in group["cases"]]
    passed = sum(case["passed"] for case in all_cases)
    return {
        "benchmark": "freshgate-feature-scenarios",
        "mode": "deterministic-no-network",
        "passed": passed,
        "total": len(all_cases),
        "accuracy_percent": round(passed / len(all_cases) * 100, 2) if all_cases else 100.0,
        "groups": scenario_groups,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("json", "text"), default="text")
    args = parser.parse_args()
    result = run_scenarios()
    if args.format == "json":
        print(json.dumps(result, indent=2))
    else:
        print(f"Feature scenarios: {result['passed']}/{result['total']} passed ({result['accuracy_percent']:.2f}%)")
        for name, group in result["groups"].items():
            count = sum(case["passed"] for case in group["cases"])
            print(f"- {name}: {count}/{len(group['cases'])}")
            for timing_name, timing in group.items():
                if timing_name.endswith("timing") or timing_name == "report_timing":
                    p95 = timing.get("p95_ms", "not sampled")
                    print(f"  {timing_name}: p50 {timing['p50_ms']} ms, p95 {p95} ms")
                if timing_name == "scheduled_job_batch":
                    print(
                        "  scheduled_job_batch: "
                        f"{timing['completed_jobs']}/{timing['selected_jobs']} completed in "
                        f"{timing['batch_duration_ms']} ms; concurrency "
                        f"{timing['maximum_observed_concurrency']}/{timing['configured_concurrency']}"
                    )
    return 0 if result["passed"] == result["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
