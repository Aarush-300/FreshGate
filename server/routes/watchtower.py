"""Watchtower monitoring and alert APIs."""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo.errors import DuplicateKeyError

from core.categories import category_registry
from core.models import Source
from core.persistence import get_feature_store
from core.policies import get_policy
from core.watchtower import WatchtowerService
from server.pipeline_factory import pipeline_for_owner
from server.users import current_user

logger = logging.getLogger("freshgate.watchtower")

router = APIRouter(prefix="/api/watchtower", tags=["watchtower"])
watchtower_service = WatchtowerService(pipeline_resolver=pipeline_for_owner)


def _scoped(req: Request):
    user = current_user(req)
    return user, get_feature_store().for_owner(user["_id"])


class MonitoringBaseline(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: Literal["confirmed", "contradicted", "partially_supported", "unverifiable"]
    score: Optional[int] = Field(default=None, ge=0, le=100)
    sources: list[Source] = Field(default_factory=list, max_length=20)


class MonitorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim_text: str = Field(min_length=1, max_length=5000)
    category: str
    monitoring_interval: int = Field(default=86400, ge=3600, le=2_592_000)
    enabled: bool = True
    policy_id: Optional[str] = Field(default=None, min_length=1, max_length=80)
    alert_score_threshold: int = Field(default=60, ge=0, le=100)
    entities: list[str] = Field(default_factory=list, max_length=30)
    jurisdiction: Optional[str] = Field(default=None, max_length=120)
    baseline: Optional[MonitoringBaseline] = None

    # Phase 6: Email scheduling
    enable_predictive_scheduling: bool = True
    check_interval_override: Optional[int] = Field(default=None, ge=300, le=2_592_000)
    email_schedule: Optional[str] = Field(default=None, description="Cron expression or interval (e.g., '0 9 * * *' or 'daily')")

    @field_validator("claim_text")
    @classmethod
    def strip_claim_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("claim_text must not be empty")
        return value

    @field_validator("category")
    @classmethod
    def normalize_supported_category(cls, value: str) -> str:
        normalized, known = category_registry.normalize_category(value)
        if not known or normalized not in category_registry.get_all_volatile():
            raise ValueError("Watchtower can monitor supported volatile claim categories only")
        return normalized


class MonitorPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: Optional[bool] = None
    monitoring_interval: Optional[int] = Field(default=None, ge=3600, le=2_592_000)
    policy_id: Optional[str] = Field(default=None, min_length=1, max_length=80)


@router.post("/monitor", status_code=status.HTTP_201_CREATED)
async def monitor_claim(request: MonitorRequest, req: Request):
    user, store = _scoped(req)
    policy = None
    if request.policy_id:
        policy = await asyncio.to_thread(get_policy, request.policy_id, store=store)
        if policy is None or not policy.enabled:
            raise HTTPException(status_code=422, detail="Policy does not exist or is disabled")
        if request.category not in policy.categories:
            raise HTTPException(status_code=422, detail="Policy does not include this claim category")

    now = time.time()
    baseline = request.baseline
    jurisdiction = request.jurisdiction or (policy.jurisdiction if policy else None)
    metadata = {
        "entities": request.entities,
        "jurisdiction": jurisdiction,
        "alert_score_threshold": request.alert_score_threshold,
        "last_evidence": [source.model_dump() for source in baseline.sources] if baseline else [],
    }
    try:
        row = await asyncio.to_thread(
            store.save_monitored_claim,
            {
                "claim_text": request.claim_text,
                "category": request.category,
                "monitoring_interval": request.monitoring_interval,
                "policy_id": request.policy_id,
                "enabled": request.enabled,
                "last_verdict": baseline.verdict if baseline else None,
                "last_score": baseline.score if baseline else None,
                "last_checked_at": now if baseline else None,
                "last_successful_at": now if baseline and baseline.verdict != "unverifiable" else None,
                "next_check_at": now + request.monitoring_interval,
                "metadata": metadata,
                "entities": request.entities,
                "jurisdiction": jurisdiction,
            },
            watchtower_service.config.watchtower_max_monitored_claims,
        )
    except OverflowError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except (sqlite3.IntegrityError, DuplicateKeyError) as exc:
        raise HTTPException(status_code=409, detail="Could not register monitored claim") from exc
    return row


@router.get("/claims")
async def list_claims(
    req: Request,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    user, store = _scoped(req)
    rows = await asyncio.to_thread(store.list_monitored_claims, limit, offset)
    alerts = await asyncio.to_thread(store.list_alerts, 1000, 0, True)
    total_count = await asyncio.to_thread(store.count_monitored_claims)
    now = time.time()
    return {
        "claims": rows,
        "limit": limit,
        "offset": offset,
        "overview": {
            "total": total_count,
            "healthy": sum(1 for row in rows if row.get("enabled") and row.get("last_error") is None),
            "changed": sum(1 for alert in alerts if alert.get("monitored_claim_id")),
            "pending": sum(1 for row in rows if row.get("enabled") and (row.get("next_check_at") or 0) <= now),
            "failed": sum(1 for row in rows if row.get("last_error") is not None),
        },
    }


@router.get("/claims/{claim_id}")
async def get_claim(claim_id: str, req: Request):
    user, store = _scoped(req)
    row = await asyncio.to_thread(store.get_monitored_claim, claim_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Monitored claim not found")
    row["history"] = await asyncio.to_thread(store.list_snapshots, row["claim_fingerprint"], 20, 0)
    alerts = await asyncio.to_thread(store.list_alerts, 100, 0, False)
    row["alerts"] = [alert for alert in alerts if alert.get("monitored_claim_id") == claim_id]
    return row


@router.patch("/claims/{claim_id}")
async def patch_claim(claim_id: str, request: MonitorPatch, req: Request):
    user, store = _scoped(req)
    updates = request.model_dump(exclude_unset=True)
    if "policy_id" in updates and updates["policy_id"] is not None:
        policy = await asyncio.to_thread(get_policy, updates["policy_id"], store=store)
        row = await asyncio.to_thread(store.get_monitored_claim, claim_id)
        if policy is None or not policy.enabled:
            raise HTTPException(status_code=422, detail="Policy does not exist or is disabled")
        if row and row.get("category") not in policy.categories:
            raise HTTPException(status_code=422, detail="Policy does not include this claim category")
    row = await asyncio.to_thread(store.update_monitored_claim, claim_id, updates)
    if row is None:
        raise HTTPException(status_code=404, detail="Monitored claim not found")
    return row


@router.delete("/claims/{claim_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_claim(claim_id: str, req: Request):
    user, store = _scoped(req)
    deleted = await asyncio.to_thread(store.delete_monitored_claim, claim_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Monitored claim not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/claims/{claim_id}/check")
async def check_claim(claim_id: str, req: Request):
    user, store = _scoped(req)
    try:
        outcome = await watchtower_service.check_now(claim_id, owner_id=user["_id"])
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if outcome is None:
        raise HTTPException(status_code=404, detail="Monitored claim not found")
    return outcome


@router.get("/alerts")
async def list_alerts(
    req: Request,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    unacknowledged_only: bool = False,
):
    user, store = _scoped(req)
    rows = await asyncio.to_thread(
        store.list_alerts, limit, offset, unacknowledged_only
    )
    return {"alerts": rows, "limit": limit, "offset": offset}


@router.get("/alerts/{alert_id}")
async def get_alert(alert_id: str, req: Request):
    user, store = _scoped(req)
    row = await asyncio.to_thread(store.get_alert, alert_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    return row


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(alert_id: str, req: Request):
    user, store = _scoped(req)
    changed = await asyncio.to_thread(store.acknowledge_alert, alert_id)
    if not changed:
        raise HTTPException(status_code=404, detail="Alert not found")
    return await asyncio.to_thread(store.get_alert, alert_id)


# Phase 6: Watchtower Email Schedule API Endpoints
class EmailScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    cron_expression: Optional[str] = Field(default=None, description="Cron expression (e.g., '0 9 * * *' for daily at 9 AM)")
    interval_seconds: Optional[int] = Field(default=None, ge=60, le=2_592_000, description="Interval in seconds (alternative to cron)")
    custom_amount: Optional[int] = Field(default=None, ge=1, le=10000, description="Custom periodic amount")
    custom_unit: Optional[str] = Field(default=None, description="Custom periodic unit: minutes, hours, days, weeks")
    recipient_email: Optional[str] = Field(default=None, description="Override default admin email")


class EmailTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipient_email: Optional[str] = Field(default=None)


@router.post("/email/schedule")
async def set_email_schedule(request: EmailScheduleRequest, req: Request):
    """Configure scheduled email reports for watchtower."""
    user, store = _scoped(req)

    # Store email schedule in user metadata or config
    # For now, update the watchtower service config
    watchtower_service.config.admin_email = request.recipient_email or watchtower_service.config.admin_email

    interval_seconds = request.interval_seconds
    if not interval_seconds and request.custom_amount and request.custom_unit:
        unit = request.custom_unit.lower()
        multiplier = 3600
        if "minute" in unit:
            multiplier = 60
        elif "hour" in unit:
            multiplier = 3600
        elif "day" in unit:
            multiplier = 86400
        elif "week" in unit:
            multiplier = 604800
        interval_seconds = max(60, min(2_592_000, request.custom_amount * multiplier))
    elif not interval_seconds:
        cron = request.cron_expression
        if cron == "0 */6 * * *":
            interval_seconds = 21600
        elif cron == "0 */12 * * *":
            interval_seconds = 43200
        elif cron == "0 9 * * 1":
            interval_seconds = 604800
        else:
            interval_seconds = 86400

    now = time.time()
    # Store schedule in user preferences
    metadata = {
        "email_schedule": {
            "enabled": request.enabled,
            "cron_expression": request.cron_expression,
            "interval_seconds": interval_seconds,
            "custom_amount": request.custom_amount,
            "custom_unit": request.custom_unit,
            "recipient_email": request.recipient_email,
            "next_run_at": now + interval_seconds,
            "last_sent_at": None,
        }
    }

    # Update user metadata
    from core.persistence import get_feature_store
    fs = get_feature_store()
    await asyncio.to_thread(fs.update_user_metadata, user["_id"], metadata)

    return {"status": "configured", "schedule": metadata["email_schedule"]}


@router.get("/email/schedule")
async def get_email_schedule(req: Request):
    """Get current email schedule configuration."""
    user, store = _scoped(req)
    from core.persistence import get_feature_store
    fs = get_feature_store()
    metadata = await asyncio.to_thread(fs.get_user_metadata, user["_id"])
    schedule = metadata.get("email_schedule", {}) if metadata else {}
    return {"schedule": schedule}


@router.post("/email/schedule/stop")
async def stop_email_schedule(req: Request):
    """Stop/pause scheduled periodic email reports for watchtower."""
    user, store = _scoped(req)
    from core.persistence import get_feature_store
    fs = get_feature_store()
    metadata = await asyncio.to_thread(fs.get_user_metadata, user["_id"])
    schedule = metadata.get("email_schedule", {}) if metadata else {}
    schedule["enabled"] = False
    metadata["email_schedule"] = schedule
    await asyncio.to_thread(fs.update_user_metadata, user["_id"], metadata)
    return {"status": "stopped", "schedule": schedule}


@router.delete("/email/schedule")
async def delete_email_schedule(req: Request):
    """Disable/stop scheduled periodic email reports."""
    user, store = _scoped(req)
    from core.persistence import get_feature_store
    fs = get_feature_store()
    metadata = await asyncio.to_thread(fs.get_user_metadata, user["_id"])
    schedule = metadata.get("email_schedule", {}) if metadata else {}
    schedule["enabled"] = False
    metadata["email_schedule"] = schedule
    await asyncio.to_thread(fs.update_user_metadata, user["_id"], metadata)
    return {"status": "stopped", "schedule": schedule}


@router.post("/email/test")
async def send_test_email(request: EmailTestRequest, req: Request):
    """Send a test email to verify SMTP or HTTPS email configuration."""
    user, store = _scoped(req)
    recipient = request.recipient_email or watchtower_service.config.admin_email

    if not recipient:
        raise HTTPException(status_code=400, detail="No recipient email configured")

    email_service = await watchtower_service._get_email_service()
    if not email_service._is_configured():
        return {"sent": False, "configured": False, "recipient": recipient}
    result, error_msg = await email_service.send_test_email_with_error([recipient])

    return {"sent": result, "configured": True, "recipient": recipient, "error": error_msg}
