"""Policy Studio API endpoints."""

from __future__ import annotations

import sqlite3
from fastapi import APIRouter, HTTPException, Response, status

from core.persistence import get_feature_store
from core.policies import create_policy, evaluate_policy_preview, get_policy, list_policies, update_policy
from core.policy_models import PolicyConfig, PolicyCreateRequest, PolicyPreviewRequest
from core.categories import category_registry

router = APIRouter(prefix="/api/policies", tags=["policies"])


def _validate_candidate(values: dict) -> tuple[PolicyConfig, list[dict]]:
    candidate = PolicyConfig.model_validate(values)
    warnings = []
    if candidate.preferred_domains and candidate.allowed_domains:
        unused = sorted(set(candidate.preferred_domains) - set(candidate.allowed_domains))
        if unused:
            warnings.append({
                "code": "preferred_domain_not_allowlisted",
                "domains": unused,
                "message": "Preferred domains outside the allowlist will not be eligible evidence.",
            })
    return candidate, warnings


@router.get("/categories")
async def get_policy_categories():
    return {
        "categories": [
            {"name": name, "volatile": category_registry.get(name).is_volatile}
            for name in category_registry.get_all_categories()
        ]
    }


@router.get("")
async def get_policies(limit: int = 100, offset: int = 0):
    try:
        values = list_policies(limit=limit, offset=offset)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"policies": [policy.model_dump() for policy in values], "limit": limit, "offset": offset}


@router.post("", status_code=status.HTTP_201_CREATED)
async def post_policy(request: PolicyCreateRequest):
    try:
        policy = create_policy(request.model_dump())
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Policy ID already exists") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return policy.model_dump()


@router.post("/validate")
async def validate_candidate_policy(request: PolicyCreateRequest):
    try:
        candidate, warnings = _validate_candidate(request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"valid": True, "policy": candidate.model_dump(), "warnings": warnings}


@router.get("/{policy_id}")
async def get_policy_by_id(policy_id: str):
    policy = get_policy(policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Policy not found")
    return policy.model_dump()


@router.put("/{policy_id}")
async def put_policy(policy_id: str, request: PolicyCreateRequest):
    try:
        policy = update_policy(policy_id, request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if policy is None:
        raise HTTPException(status_code=404, detail="Policy not found")
    return policy.model_dump()


@router.delete("/{policy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_policy(policy_id: str):
    if not get_feature_store().delete_policy(policy_id):
        raise HTTPException(status_code=404, detail="Policy not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{policy_id}/validate")
async def validate_policy(policy_id: str, request: PolicyCreateRequest):
    current = get_policy(policy_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Policy not found")
    try:
        candidate, warnings = _validate_candidate({**request.model_dump(), "id": policy_id})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"valid": True, "policy": candidate.model_dump(), "warnings": warnings}


@router.post("/{policy_id}/preview")
async def preview_policy(policy_id: str, request: PolicyPreviewRequest):
    policy = get_policy(policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Policy not found")
    return evaluate_policy_preview(policy, request.claims)


@router.post("/{policy_id}/duplicate", status_code=status.HTTP_201_CREATED)
async def duplicate_policy(policy_id: str):
    policy = get_policy(policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Policy not found")
    clone = policy.model_dump(exclude={"id", "created_at", "updated_at"})
    clone["name"] = f"{policy.name} copy"
    try:
        return create_policy(clone).model_dump()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Could not duplicate policy") from exc
