"""Audit helper module to write verification records without circular imports."""
from __future__ import annotations

import uuid
import logging
from typing import Optional

from core.audit import audit_store
from core.models import VerificationResult

logger = logging.getLogger("freshgate.audit")


async def write_audit(
    result: VerificationResult,
    *,
    text: str,
    source: str,
    mode: Optional[str] = None,
    latency_ms: int = 0,
    corr_id: Optional[str] = None,
    audit_id: Optional[str] = None,
    owner_id: Optional[str] = None,
) -> None:
    try:
        claims_data = [c.model_dump() for c in result.claims] if hasattr(result, "claims") else []
        total = getattr(getattr(result, "summary", None), "total", len(claims_data))
        stale = getattr(getattr(result, "summary", None), "stale", 0)
        avg_score = getattr(getattr(result, "summary", None), "avg_score", None)
        status_val = str(getattr(getattr(result, "summary", None), "status", None) or getattr(result, "status", None) or "verified")

        await audit_store.arecord_audit(
            audit_id=audit_id or f"audit-{uuid.uuid4().hex}",
            correlation_id=corr_id or "req-unknown",
            input_text=text,
            claims_data=claims_data,
            total_claims=total,
            stale_claims=stale,
            avg_score=avg_score,
            latency_ms=latency_ms,
            status=status_val,
            owner_id=owner_id,
            source=source,
            mode=mode,
        )
    except Exception:
        logger.exception("audit write failed")  # audit must never break a request


async def write_audit_failure(
    text: str,
    *,
    source: str,
    mode: Optional[str] = None,
    reason: str = "fail-open",
    corr_id: Optional[str] = None,
    owner_id: Optional[str] = None,
    latency_ms: int = 0,
) -> None:
    try:
        status_val = "fail-open" if ("fail" in reason.lower() or "timeout" in reason.lower()) else "error"
        effective_mode = mode or reason
        await audit_store.arecord_audit(
            audit_id=f"audit-{uuid.uuid4().hex}",
            correlation_id=corr_id or "req-unknown",
            input_text=text,
            claims_data=[],
            total_claims=0,
            stale_claims=0,
            avg_score=None,
            latency_ms=latency_ms,
            status=status_val,
            owner_id=owner_id,
            source=source,
            mode=effective_mode,
        )
    except Exception:
        logger.exception("audit failure write failed")
