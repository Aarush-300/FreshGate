"""Persistent scheduled reverification for monitored claims."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Any, Optional

from core.config import FreshGateConfig, get_config
from core.models import Claim, VerificationResult
from core.history import save_claim_snapshot
from core.persistence import SQLiteFeatureStore, get_feature_store
from core.pipeline import FreshGatePipeline
from core.policies import get_policy

logger = logging.getLogger(__name__)


class WatchtowerService:
    def __init__(
        self,
        store: Optional[SQLiteFeatureStore] = None,
        pipeline: Optional[FreshGatePipeline] = None,
        config: Optional[FreshGateConfig] = None,
    ):
        self._store = store
        self.pipeline = pipeline
        self._owns_pipeline = pipeline is None
        self.config = config or get_config()
        self._semaphore = asyncio.Semaphore(self.config.watchtower_max_concurrency)
        self._run_lock = asyncio.Lock()
        self._task: Optional[asyncio.Task] = None
        self._stopping = asyncio.Event()

    @property
    def store(self) -> SQLiteFeatureStore:
        return self._store or get_feature_store()

    async def start(self) -> None:
        if not self.config.watchtower_enabled or (self._task and not self._task.done()):
            return
        self._stopping.clear()
        # Expired leases are eligible for pickup immediately after a restart.
        self._task = asyncio.create_task(self._scheduler_loop(), name="freshgate-watchtower")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._owns_pipeline and self.pipeline is not None:
            client = getattr(getattr(self.pipeline, "verifier", None), "tavily_client", None)
            if client is not None and hasattr(client, "aclose"):
                await client.aclose()
            self.pipeline = None

    async def _scheduler_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.run_due_checks()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Watchtower scheduler iteration failed")
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self.config.watchtower_poll_seconds)
            except asyncio.TimeoutError:
                pass

    async def run_due_checks(self) -> list[dict[str, Any]]:
        if self._run_lock.locked():
            return []
        async with self._run_lock:
            rows = await asyncio.to_thread(
                self.store.claim_due_monitored_claims,
                self.config.watchtower_max_concurrency,
                self.config.watchtower_lease_seconds,
            )
            if not rows:
                return []
            return list(await asyncio.gather(*(self._check_leased(row) for row in rows)))

    async def check_now(self, claim_id: str) -> Optional[dict[str, Any]]:
        row = await asyncio.to_thread(
            self.store.claim_monitored_claim,
            claim_id,
            self.config.watchtower_lease_seconds,
            None,
            True,
        )
        if row is None:
            current = await asyncio.to_thread(self.store.get_monitored_claim, claim_id)
            if current is None:
                return None
            raise RuntimeError("A check is already running or monitoring is disabled")
        return await self._check_leased(row)

    async def _get_pipeline(self) -> FreshGatePipeline:
        if self.pipeline is not None:
            return self.pipeline
        if not self.config.is_openai_configured or not self.config.is_tavily_configured:
            raise RuntimeError("Scheduled checks require server-configured OPENAI_API_KEY and TAVILY_API_KEY")
        if self.pipeline is None:
            self.pipeline = FreshGatePipeline()
        return self.pipeline

    @staticmethod
    def _transient_failure(result: VerificationResult) -> Optional[str]:
        for provider, state in (result.provider_health or {}).items():
            if state in {"missing_credentials", "rate_limited", "unavailable", "error", "degraded"}:
                return f"{provider} provider is {state}"
        for claim in result.claims:
            explanation = (claim.explanation or "").lower()
            markers = ("timed out", "timeout", "rate limit", "rate-limited", "service was unreachable", "network error", "provider unavailable")
            if any(marker in explanation for marker in markers):
                return claim.explanation
        if result.status in {"verification_failed", "extraction_failed"}:
            return result.error or result.status
        return None

    @staticmethod
    def _evidence(claim: Claim) -> list[dict[str, Any]]:
        return [source.model_dump() for source in claim.sources]

    def _retry_delay(self, retry_count: int, interval: int) -> int:
        if retry_count >= self.config.watchtower_max_retries:
            return interval
        return min(interval, self.config.watchtower_retry_base_seconds * (2 ** max(0, retry_count - 1)))

    @staticmethod
    def _transition_reason(previous: Optional[str], current: str, old_score: Optional[int], new_score: Optional[int], threshold: int, old_sources: list[dict], new_sources: list[dict]) -> Optional[str]:
        if previous == "confirmed" and current in {"contradicted", "partially_supported"}:
            return f"Verdict changed from {previous} to {current}"
        if old_score is not None and old_score >= threshold and new_score is not None and new_score < threshold:
            return f"Freshness score dropped below the configured threshold ({threshold})"
        old_urls = {source.get("url") for source in old_sources if source.get("url")}
        new_urls = {source.get("url") for source in new_sources if source.get("url")}
        if old_urls and new_urls:
            changed = len(old_urls ^ new_urls) / max(len(old_urls | new_urls), 1)
            if changed >= 0.5:
                return "Evidence sources changed materially"
        return None

    async def _check_leased(self, row: dict[str, Any]) -> dict[str, Any]:
        token = row.get("lease_token")
        started = time.perf_counter()
        checked_at = time.time()
        retry_count = int(row.get("retry_count", 0))
        metadata = dict(row.get("metadata") or {})
        try:
            async with self._semaphore:
                policy = await asyncio.to_thread(get_policy, row["policy_id"], store=self.store) if row.get("policy_id") else None
                if row.get("policy_id") and (policy is None or not policy.enabled):
                    raise RuntimeError("The associated policy no longer exists or is disabled")
                active_pipeline = await self._get_pipeline()
                result = await active_pipeline.verify(row["claim_text"], policy=policy)
            failure = self._transient_failure(result)
            target = next((claim for claim in result.claims if claim.type == row["category"]), None)
            if target is None and result.claims:
                target = result.claims[0]
            if failure:
                raise RuntimeError(failure)
            if target is None:
                raise RuntimeError("Verification did not produce a claim for monitoring")

            new_evidence = self._evidence(target)
            old_evidence = metadata.get("last_evidence", [])
            threshold = int(metadata.get("alert_score_threshold", 60))
            current_verdict = target.verdict or "unverifiable"
            if current_verdict == "unverifiable":
                retry_count += 1
                if retry_count < self.config.watchtower_max_retries:
                    raise _InsufficientEvidence(target.explanation or "Evidence was inconclusive", retry_count)

            previous_verdict = row.get("last_verdict")
            previous_score = row.get("last_score")
            reason = self._transition_reason(
                previous_verdict, current_verdict, previous_score, target.score, threshold, old_evidence, new_evidence
            )
            if previous_verdict and current_verdict == "unverifiable":
                reason = "Repeated checks could not find sufficient evidence"
            snapshot = await asyncio.to_thread(
                self.store.get_snapshot_by_verification_id, target.verification_id
            ) if target.verification_id else None
            if snapshot is None:
                snapshot = await asyncio.to_thread(
                    save_claim_snapshot,
                    target,
                    policy=policy,
                    duration_seconds=time.perf_counter() - started,
                    fingerprint=row["claim_fingerprint"],
                    metadata={"watchtower_claim_id": row["id"]},
                    store=self.store,
                )
            verification_id = snapshot["verification_id"]
            if reason:
                transition_key = hashlib.sha256(
                    f"{previous_verdict}:{current_verdict}:{reason}".encode("utf-8")
                ).hexdigest()
                await asyncio.to_thread(self.store.save_alert, {
                    "monitored_claim_id": row["id"],
                    "previous_verdict": previous_verdict,
                    "current_verdict": current_verdict,
                    "previous_score": previous_score,
                    "current_score": target.score,
                    "reason": reason,
                    "old_evidence": old_evidence,
                    "new_evidence": new_evidence,
                    "created_at": checked_at,
                    "transition_key": transition_key,
                    "metadata": {"claim_fingerprint": row["claim_fingerprint"], "verification_id": verification_id},
                })
            metadata["last_evidence"] = new_evidence
            metadata["last_policy_evaluation"] = result.policy_evaluation
            await asyncio.to_thread(
                self.store.finish_monitored_claim,
                row["id"], token, checked_at=checked_at,
                successful_at=checked_at,
                next_check_at=checked_at + int(row["monitoring_interval"]),
                verdict=current_verdict, score=target.score, metadata=metadata, retry_count=0,
            )
            return {"claim_id": row["id"], "status": "completed", "verdict": current_verdict, "alert": bool(reason)}
        except _InsufficientEvidence as exc:
            delay = self._retry_delay(exc.retry_count, int(row["monitoring_interval"]))
            await asyncio.to_thread(
                self.store.finish_monitored_claim,
                row["id"], token, checked_at=checked_at, next_check_at=checked_at + delay,
                error=str(exc), retry_count=exc.retry_count,
            )
            return {"claim_id": row["id"], "status": "retrying", "error": str(exc), "retry_count": exc.retry_count}
        except Exception as exc:
            retry_count += 1
            delay = self._retry_delay(retry_count, int(row["monitoring_interval"]))
            await asyncio.to_thread(
                self.store.finish_monitored_claim,
                row["id"], token, checked_at=checked_at, next_check_at=checked_at + delay,
                error=str(exc), retry_count=retry_count,
            )
            logger.warning("Watchtower check failed for %s: %s", row["id"], exc)
            return {"claim_id": row["id"], "status": "failed", "error": str(exc), "retry_count": retry_count}


class _InsufficientEvidence(Exception):
    def __init__(self, message: str, retry_count: int):
        super().__init__(message)
        self.retry_count = retry_count
