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
<<<<<<< HEAD
from server.pipeline_factory import pipeline_for_owner
from server.users import current_user

logger = logging.getLogger("freshgate.watchtower")
=======
from core.models import Source
from server.users import current_user, users_collection
from fastapi import Depends
import uuid
>>>>>>> 60d76df5e69c81f6f854d8a8b8e6cd8999a981ab

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
<<<<<<< HEAD
async def monitor_claim(request: MonitorRequest, req: Request):
    user, store = _scoped(req)
=======
async def monitor_claim(request: MonitorRequest, user: dict = Depends(current_user)):
    store = get_feature_store()
>>>>>>> 60d76df5e69c81f6f854d8a8b8e6cd8999a981ab
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
<<<<<<< HEAD
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

=======
    row = {
            "id": uuid.uuid4().hex,
            "claim_text": request.claim_text,
            "category": request.category,
            "monitoring_interval": request.monitoring_interval,
            "policy_id": request.policy_id,
            "enabled": request.enabled,
            "claim_fingerprint": __import__("hashlib").sha256(request.claim_text.strip().lower().encode()).hexdigest(),
            "last_error": None,
            "last_verdict": baseline.verdict if baseline else None,
            "last_score": baseline.score if baseline else None,
            "last_checked_at": now if baseline else None,
            "last_successful_at": now if baseline and baseline.verdict != "unverifiable" else None,
            "next_check_at": now + request.monitoring_interval,
            "metadata": metadata,
            "entities": request.entities,
            "jurisdiction": jurisdiction,
        }
    users_collection().update_one({"_id": user["_id"]}, {"$push": {"claims": row}})
>>>>>>> 60d76df5e69c81f6f854d8a8b8e6cd8999a981ab
    return row


@router.get("/claims")
<<<<<<< HEAD
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
=======
async def list_claims(user: dict = Depends(current_user), limit: int = Query(default=100, ge=1, le=1000), offset: int = Query(default=0, ge=0)):
    rows = user.get("claims", [])[offset:offset+limit]
    alerts = user.get("alerts", [])
>>>>>>> 60d76df5e69c81f6f854d8a8b8e6cd8999a981ab
    return {
        "claims": rows,
        "limit": limit,
        "offset": offset,
        "overview": {
<<<<<<< HEAD
            "total": total_count,
            "healthy": sum(1 for row in rows if row.get("enabled") and row.get("last_error") is None),
            "changed": sum(1 for alert in alerts if alert.get("monitored_claim_id")),
            "pending": sum(1 for row in rows if row.get("enabled") and (row.get("next_check_at") or 0) <= now),
=======
            "total": len(user.get("claims", [])),
            "healthy": sum(1 for row in rows if row.get("enabled") and row.get("last_error") is None),
            "changed": sum(1 for alert in alerts if alert.get("monitored_claim_id")),
            "pending": sum(1 for row in rows if row.get("enabled") and row.get("next_check_at", 0) <= time.time()),
>>>>>>> 60d76df5e69c81f6f854d8a8b8e6cd8999a981ab
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
