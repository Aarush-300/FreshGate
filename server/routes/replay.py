"""Read-only verification history and Replay APIs."""

from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter, HTTPException, Query

from core.history import get_claim_history, get_verification_history
from core.persistence import get_feature_store
from core.replay import compare_by_id, replay_claim, replay_timeline

history_router = APIRouter(prefix="/api/history", tags=["verification-history"])
replay_router = APIRouter(prefix="/api/replay", tags=["verification-replay"])


def _validate_fingerprint(fingerprint: str) -> None:
    if not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
        raise HTTPException(status_code=422, detail="Claim fingerprint must be a 64-character SHA-256 value")


@history_router.get("/claims/{fingerprint}")
async def history_for_claim(
    fingerprint: str,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    _validate_fingerprint(fingerprint)
    result = await asyncio.to_thread(
        get_claim_history, fingerprint, limit=limit, offset=offset, store=get_feature_store()
    )
    return result


@history_router.get("/verifications/{verification_id}")
async def history_for_verification(verification_id: str):
    snapshot = await asyncio.to_thread(get_verification_history, verification_id, get_feature_store())
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Verification snapshot not found")
    return snapshot


@replay_router.get("/{fingerprint}/compare")
async def compare_verifications(
    fingerprint: str,
    from_id: str = Query(alias="from", min_length=1, max_length=80),
    to_id: str = Query(alias="to", min_length=1, max_length=80),
):
    _validate_fingerprint(fingerprint)
    result = await asyncio.to_thread(
        compare_by_id, fingerprint, from_id, to_id, get_feature_store()
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Snapshots were not found for this claim")
    return result


@replay_router.get("/{fingerprint}/timeline")
async def replay_claim_timeline(
    fingerprint: str,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    _validate_fingerprint(fingerprint)
    result = await asyncio.to_thread(
        replay_timeline, fingerprint, limit=limit, offset=offset, store=get_feature_store()
    )
    if result["total"] == 0:
        raise HTTPException(status_code=404, detail="No history found for this claim")
    return result


@replay_router.get("/{fingerprint}")
async def replay_claim_history(
    fingerprint: str,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    _validate_fingerprint(fingerprint)
    result = await asyncio.to_thread(
        replay_claim, fingerprint, limit=limit, offset=offset, store=get_feature_store()
    )
    if result["total"] == 0:
        raise HTTPException(status_code=404, detail="No history found for this claim")
    return result
