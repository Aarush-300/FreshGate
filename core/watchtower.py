"""Persistent scheduled reverification for monitored claims."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Any, Callable, Awaitable, Optional

from core.config import FreshGateConfig, get_config
from core.models import Claim, VerificationResult, Source
from core.history import save_claim_snapshot
from core.persistence import SQLiteFeatureStore, get_feature_store
from core.pipeline import FreshGatePipeline
from core.policies import get_policy
from core.audit_helpers import write_audit
from core.scorer import compute_temporal_decay

logger = logging.getLogger(__name__)


class WatchtowerService:
    def __init__(
        self,
        store: Optional[SQLiteFeatureStore] = None,
        pipeline: Optional[FreshGatePipeline] = None,
        config: Optional[FreshGateConfig] = None,
        pipeline_resolver: Optional[Callable[[Optional[str]], Awaitable[FreshGatePipeline]]] = None,
    ):
        self._store = store
        self.pipeline = pipeline
        self._owns_pipeline = pipeline is None
        self._resolver = pipeline_resolver
        self.config = config or get_config()
        self._semaphore = asyncio.Semaphore(self.config.watchtower_max_concurrency)
        self._run_lock = asyncio.Lock()
        self._task: Optional[asyncio.Task] = None
        self._stopping = asyncio.Event()
        self._email_service = None

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
                try:
                    await client.aclose()
                except (RuntimeError, Exception) as exc:
                    logger.debug("Error during watchtower pipeline client teardown: %s", exc)
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

            # Phase 4: Priority queue - sort by flip probability descending
            claims_for_sorting = []
            for row in rows:
                claim = Claim(
                    id=row["id"],
                    text=row["claim_text"],
                    type=row["category"],
                    volatility_score=row.get("volatility_score", 0.5),
                    evidence_gap=row.get("evidence_gap", 0.5),
                    freshness_half_life_seconds=row.get("freshness_half_life_seconds"),
                    degradation_curve=row.get("degradation_curve", "exponential"),
                    last_verified_at=row.get("last_verified_at"),
                )
                claims_for_sorting.append((claim, row))

            # Sort by flip probability (highest first)
            claims_for_sorting.sort(key=lambda x: self.calculate_flip_probability(x[0]), reverse=True)
            sorted_rows = [row for _, row in claims_for_sorting]

            return list(await asyncio.gather(*(self._check_leased(row) for row in sorted_rows)))

    async def check_now(self, claim_id: str, owner_id: Optional[str] = None) -> Optional[dict[str, Any]]:
        target_store = (
            self.store.for_owner(owner_id)
            if (owner_id and hasattr(self.store, "for_owner"))
            else self.store
        )
        row = await asyncio.to_thread(
            target_store.claim_monitored_claim,
            claim_id,
            self.config.watchtower_lease_seconds,
            None,
            True,
        )
        if row is None:
            current = await asyncio.to_thread(target_store.get_monitored_claim, claim_id)
            if current is None:
                return None
            raise RuntimeError("A check is already running or monitoring is disabled")
        return await self._check_leased(row)

    async def _get_pipeline(self, owner_id: Optional[str] = None) -> tuple[FreshGatePipeline, bool]:
        if self.pipeline is not None:
            return self.pipeline, False
        if self._resolver is not None:
            return await self._resolver(owner_id), True
        if not (self.config.is_openai_configured and self.config.is_tavily_configured):
            raise RuntimeError("Scheduled checks require server-configured OPENAI_API_KEY and TAVILY_API_KEY")
        self.pipeline = FreshGatePipeline()
        return self.pipeline, False

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
        owner_id = row.get("owner_id")
        store = (
            self.store.for_owner(owner_id)
            if hasattr(self.store, "for_owner")
            else self.store
        )
        active_pipeline = None
        is_ephemeral = False
        try:
            async with self._semaphore:
                policy = (
                    await asyncio.to_thread(get_policy, row["policy_id"], store=store)
                    if row.get("policy_id")
                    else None
                )
                if row.get("policy_id") and (policy is None or not policy.enabled):
                    raise RuntimeError("The associated policy no longer exists or is disabled")
                active_pipeline, is_ephemeral = await self._get_pipeline(owner_id)
                result = await active_pipeline.verify(row["claim_text"], policy=policy, owner_id=owner_id)
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
            if previous_verdict and previous_verdict != "unverifiable" and current_verdict == "unverifiable":
                reason = "Repeated checks could not find sufficient evidence"
            elif previous_verdict == "unverifiable" and current_verdict == "unverifiable":
                reason = None

            snapshot = await asyncio.to_thread(
                store.get_snapshot_by_verification_id, target.verification_id
            ) if target.verification_id else None
            if snapshot is None:
                snapshot = await asyncio.to_thread(
                    save_claim_snapshot,
                    target,
                    policy=policy,
                    duration_seconds=time.perf_counter() - started,
                    fingerprint=row["claim_fingerprint"],
                    metadata={"watchtower_claim_id": row["id"]},
                    store=store,
                )
            verification_id = snapshot["verification_id"]
            if reason:
                transition_key = hashlib.sha256(
                    f"{previous_verdict}:{current_verdict}:{reason}".encode("utf-8")
                ).hexdigest()
                alert_store = store
                await asyncio.to_thread(alert_store.save_alert, {
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
            latency_ms = int((time.perf_counter() - started) * 1000)
            await write_audit(
                result,
                text=row["claim_text"],
                source="watchtower",
                mode=None,
                latency_ms=latency_ms,
                corr_id=None,
                owner_id=owner_id,
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
            msg = str(exc)
            if msg.startswith("Add your AI provider"):
                delay = int(row["monitoring_interval"])
            else:
                retry_count += 1
                delay = self._retry_delay(retry_count, int(row["monitoring_interval"]))
            await asyncio.to_thread(
                self.store.finish_monitored_claim,
                row["id"], token, checked_at=checked_at, next_check_at=checked_at + delay,
                error=msg, retry_count=retry_count,
            )
            logger.warning("Watchtower check failed for %s: %s", row["id"], exc)
            return {"claim_id": row["id"], "status": "failed", "error": msg, "retry_count": retry_count}
        finally:
            if is_ephemeral and active_pipeline and hasattr(active_pipeline, "verifier") and hasattr(active_pipeline.verifier, "tavily_client"):
                await active_pipeline.verifier.tavily_client.aclose()

    # Phase 4: Predictive Monitoring Methods
    def predict_optimal_check_interval(self, claim: Claim) -> int:
        """
        Predict optimal check interval based on claim volatility, evidence freshness, and half-life.

        Returns interval in seconds.
        """
        if claim.volatility_score > 0.7:
            half_life = claim.freshness_half_life_seconds or 86400
            return min(half_life, 3600)  # Max 1 hour for high volatility
        return claim.freshness_half_life_seconds or 86400  # Default 24 hours

    def calculate_flip_probability(self, claim: Claim) -> float:
        """
        Calculate probability that claim's verdict will flip on next verification.

        Based on:
        - volatility_score: inherent volatility of the claim type (0-1)
        - evidence_gap: confidence in evidence coverage (0-1, lower is better)
        - temporal decay: how fresh the evidence is (0-1, lower is better)
        """
        from core.scorer import compute_temporal_decay

        volatility = max(0.0, min(1.0, claim.volatility_score))
        evidence_confidence = 1.0 - max(0.0, min(1.0, claim.evidence_gap))
        temporal_freshness = compute_temporal_decay(claim)

        # Probability increases with volatility, decreases with evidence coverage and freshness
        base_prob = volatility * (1.0 - evidence_confidence) * (1.0 - temporal_freshness)

        # Scale to 0-1 range
        return max(0.0, min(1.0, base_prob))

    def get_priority_queue_order(self, claims: list[Claim]) -> list[Claim]:
        """
        Sort claims by flip probability descending for priority checking.

        High-probability claims are checked first within concurrency limit.
        """
        return sorted(claims, key=lambda c: self.calculate_flip_probability(c), reverse=True)

    # Phase 6: Email Integration
    async def _get_email_service(self):
        """Get or create email service instance."""
        if self._email_service is None:
            from core.email import get_email_service
            self._email_service = get_email_service(self.config)
        return self._email_service

    async def send_scheduled_report(self, owner_id: Optional[str] = None) -> bool:
        """
        Generate and send scheduled watchtower report via email.

        Returns True if email sent successfully.
        """
        email_service = await self._get_email_service()
        if not email_service._is_configured():
            logger.warning("Email not configured, skipping scheduled report")
            return False

        # Get all monitored claims for this owner
        store = self.store.for_owner(owner_id) if (owner_id and hasattr(self.store, "for_owner")) else self.store
        monitored = await asyncio.to_thread(store.list_monitored_claims, owner_id)

        if not monitored:
            logger.info("No monitored claims for scheduled report")
            return True

        # Get latest verification for each claim
        claims = []
        alerts = []
        for row in monitored:
            # Get latest snapshot
            snapshot = await asyncio.to_thread(store.get_latest_snapshot, row["claim_fingerprint"])
            if snapshot:
                # Reconstruct claim from snapshot
                claim = Claim(
                    id=row["id"],
                    text=snapshot["claim_text"],
                    type=snapshot["category"],
                    verdict=snapshot["verdict"],
                    score=snapshot["freshness_score"],
                    sources=[Source(**s) for s in snapshot.get("sources", [])],
                    last_verified_at=snapshot.get("checked_at"),
                    volatility_score=row.get("volatility_score", 0.5),
                    evidence_gap=row.get("evidence_gap", 0.5),
                    predicted_flip_probability=row.get("predicted_flip_probability"),
                    recommended_check_interval=row.get("recommended_check_interval"),
                )
                claims.append(claim)

            # Get recent alerts
            claim_alerts = await asyncio.to_thread(store.get_alerts_for_claim, row["id"], limit=5)
            for alert in claim_alerts:
                alerts.append({
                    "title": f"Verdict Change: {alert['previous_verdict']} → {alert['current_verdict']}",
                    "message": alert["reason"],
                })

        # Send report to admin email
        if self.config.admin_email:
            return await email_service.send_watchtower_report(
                to=[self.config.admin_email],
                claims=claims,
                alerts=alerts,
            )
        return False


class _InsufficientEvidence(Exception):
    def __init__(self, message: str, retry_count: int):
        super().__init__(message)
        self.retry_count = retry_count
