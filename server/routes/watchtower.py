"""Watchtower monitoring and alert APIs."""

from __future__ import annotations

import sqlite3
import asyncio
import time
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.categories import category_registry
from core.persistence import get_feature_store
from core.policies import get_policy
from core.watchtower import WatchtowerService
from core.models import Source

router = APIRouter(prefix="/api/watchtower", tags=["watchtower"])
watchtower_service = WatchtowerService()


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
async def monitor_claim(request: MonitorRequest):
    store = get_feature_store()
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
        row = await asyncio.to_thread(store.save_monitored_claim, {
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
        }, watchtower_service.config.watchtower_max_monitored_claims)
    except OverflowError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Could not register monitored claim") from exc
    return row


@router.get("/claims")
async def list_claims(limit: int = Query(default=100, ge=1, le=1000), offset: int = Query(default=0, ge=0)):
    store = get_feature_store()
    rows = await asyncio.to_thread(store.list_monitored_claims, limit, offset)
    alerts = await asyncio.to_thread(store.list_alerts, 1000, 0, True)
    return {
        "claims": rows,
        "limit": limit,
        "offset": offset,
        "overview": {
            "total": await asyncio.to_thread(store.count_monitored_claims),
            "healthy": sum(1 for row in rows if row["enabled"] and row["last_error"] is None),
            "changed": sum(1 for alert in alerts if alert["monitored_claim_id"]),
            "pending": sum(1 for row in rows if row["enabled"] and row["next_check_at"] <= time.time()),
            "failed": sum(1 for row in rows if row["last_error"] is not None),
        },
    }


@router.get("/claims/{claim_id}")
async def get_claim(claim_id: str):
    store = get_feature_store()
    row = await asyncio.to_thread(store.get_monitored_claim, claim_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Monitored claim not found")
    row["history"] = await asyncio.to_thread(store.list_snapshots, row["claim_fingerprint"], 20, 0)
    row["alerts"] = [alert for alert in await asyncio.to_thread(store.list_alerts, 100, 0, False)
                     if alert["monitored_claim_id"] == claim_id]
    return row


@router.patch("/claims/{claim_id}")
async def patch_claim(claim_id: str, request: MonitorPatch):
    updates = request.model_dump(exclude_unset=True)
    if "policy_id" in updates and updates["policy_id"] is not None:
        policy = await asyncio.to_thread(get_policy, updates["policy_id"])
        row = await asyncio.to_thread(get_feature_store().get_monitored_claim, claim_id)
        if policy is None or not policy.enabled:
            raise HTTPException(status_code=422, detail="Policy does not exist or is disabled")
        if row and row["category"] not in policy.categories:
            raise HTTPException(status_code=422, detail="Policy does not include this claim category")
    row = await asyncio.to_thread(get_feature_store().update_monitored_claim, claim_id, updates)
    if row is None:
        raise HTTPException(status_code=404, detail="Monitored claim not found")
    return row


@router.delete("/claims/{claim_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_claim(claim_id: str):
    deleted = await asyncio.to_thread(get_feature_store().delete_monitored_claim, claim_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Monitored claim not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/claims/{claim_id}/check")
async def check_claim(claim_id: str):
    try:
        outcome = await watchtower_service.check_now(claim_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if outcome is None:
        raise HTTPException(status_code=404, detail="Monitored claim not found")
    return outcome


@router.get("/alerts")
async def list_alerts(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    unacknowledged_only: bool = False,
):
    rows = await asyncio.to_thread(
        get_feature_store().list_alerts, limit, offset, unacknowledged_only
    )
    return {"alerts": rows, "limit": limit, "offset": offset}


@router.get("/alerts/{alert_id}")
async def get_alert(alert_id: str):
    row = await asyncio.to_thread(get_feature_store().get_alert, alert_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    return row


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(alert_id: str):
    changed = await asyncio.to_thread(get_feature_store().acknowledge_alert, alert_id)
    if not changed:
        raise HTTPException(status_code=404, detail="Alert not found")
    return await asyncio.to_thread(get_feature_store().get_alert, alert_id)
