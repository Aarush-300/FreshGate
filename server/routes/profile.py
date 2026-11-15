"""User profile settings and provider model listing."""
from __future__ import annotations

import logging
import time
from typing import Any, List

from fastapi import APIRouter, HTTPException, Request, status
import httpx
from pydantic import BaseModel, ConfigDict, Field

from core.config import is_valid_credential
from server.netguard import validate_base_url
from server.routes.providers import SUPPORTED_PROVIDER_IDS, get_provider
from server.users import (
    current_user,
    decrypt_secret,
    encrypt_secret,
    public_settings,
    users_collection,
)

logger = logging.getLogger("freshgate.profile")

router = APIRouter(prefix="/api/profile", tags=["profile"])


class SettingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(max_length=40)
    model: str = Field(min_length=1, max_length=120)
    base_url: str = Field(default="", max_length=500)  # only used for editable providers
    api_key: str = Field(default="", max_length=500)  # blank = keep saved
    tavily_key: str = Field(default="", max_length=500)  # blank = keep saved
    clear_api_key: bool = False


class ModelsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    base_url: str = ""
    api_key: str = ""  # blank -> use the saved key IF the saved provider matches


@router.get("/settings")
async def get_profile_settings(req: Request):
    user = current_user(req)
    return public_settings(user)


@router.put("/settings")
async def update_profile_settings(body: SettingsBody, req: Request):
    user = current_user(req)
    saved = user.get("settings", {}) or {}

    # 1. provider must be in SUPPORTED_PROVIDER_IDS
    if body.provider not in SUPPORTED_PROVIDER_IDS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported provider: {body.provider}",
        )
    prov = get_provider(body.provider)
    if not prov:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provider configuration not found.",
        )

    # 2. Base URL: if editable, validate; if not, ignore and store ""
    if prov["editable_base_url"]:
        if not body.base_url or not body.base_url.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Base URL is required for {prov['name']}.",
            )
        try:
            stored_base_url = validate_base_url(body.base_url)
        except ValueError as err:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(err),
            )
    else:
        stored_base_url = ""

    # 3. model is trimmed, non-empty
    model = body.model.strip()
    if not model:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Model is required.",
        )

    # 4. API key handling
    provider_changed = saved.get("provider") != body.provider
    new_api_key = body.api_key.strip()

    if body.clear_api_key:
        final_encrypted_api_key = ""
    elif new_api_key:
        if not is_valid_credential(new_api_key):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid API key format for {prov['name']}.",
            )
        final_encrypted_api_key = encrypt_secret(new_api_key)
    else:
        # blank -> keep old key only if provider didn't change
        if provider_changed:
            final_encrypted_api_key = ""
        else:
            final_encrypted_api_key = saved.get("api_key", "")

    if prov["key_required"] and not final_encrypted_api_key:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Enter the API key for {prov['name']}.",
        )

    # 5. Tavily key handling
    new_tavily = body.tavily_key.strip()
    if new_tavily:
        if not is_valid_credential(new_tavily):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid Tavily API key format.",
            )
        final_encrypted_tavily = encrypt_secret(new_tavily)
    else:
        final_encrypted_tavily = saved.get("tavily_key", "")

    if not final_encrypted_tavily:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A Tavily search API key is required.",
        )

    # 6. Save in Mongo
    new_settings = {
        "provider": body.provider,
        "model": model,
        "base_url": stored_base_url,
        "api_key": final_encrypted_api_key,
        "tavily_key": final_encrypted_tavily,
        "updated_at": time.time(),
    }
    users_collection().update_one(
        {"_id": user["_id"]},
        {"$set": {"settings": new_settings}},
    )

    user["settings"] = new_settings
    return public_settings(user)


async def fetch_models(body: ModelsBody, user: dict[str, Any]) -> dict[str, Any]:
    # 1. provider check
    prov = get_provider(body.provider)
    if not prov:
        raise HTTPException(status_code=400, detail="Unknown provider.")

    # 2. base url
    if prov["editable_base_url"]:
        try:
            base = validate_base_url(body.base_url or prov["base_url"])
        except ValueError as err:
            raise HTTPException(status_code=400, detail=str(err))
    else:
        base = prov["base_url"]

    # 3. key resolution
    saved = user.get("settings", {}) or {}
    key = body.api_key.strip()
    if not key and saved.get("provider") == body.provider:
        key = decrypt_secret(saved.get("api_key", ""))

    if prov["key_required"] and not key:
        raise HTTPException(
            status_code=400,
            detail="Enter your API key (or save it first), then load models.",
        )

    # 4. URLs to try
    urls_to_try: list[str] = []
    if prov["id"] == "ollama":
        trimmed = base[:-3] if base.endswith("/v1") else base
        urls_to_try.append(f"{trimmed}/api/tags")
        urls_to_try.append(f"{base}/models")
    else:
        urls_to_try.append(f"{base}/models")

    headers = {"Authorization": f"Bearer {key}"} if key else {}

    data = None
    last_exc = None

    async with httpx.AsyncClient(timeout=12.0, follow_redirects=False) as client:
        for url in urls_to_try:
            try:
                resp = await client.get(url, headers=headers)
                if resp.status_code in (401, 403):
                    raise HTTPException(
                        status_code=400, detail="The provider rejected this API key."
                    )
                if resp.status_code == 404:
                    if len(urls_to_try) > 1 and url == urls_to_try[0]:
                        continue
                    raise HTTPException(
                        status_code=400,
                        detail="This provider did not return a model list. Type the model ID manually.",
                    )
                resp.raise_for_status()
                data = resp.json()
                break
            except httpx.ConnectError:
                raise HTTPException(status_code=502, detail=f"Could not reach {base}.")
            except httpx.ConnectTimeout:
                raise HTTPException(status_code=502, detail=f"Could not reach {base}.")
            except HTTPException:
                raise
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                if exc.response.status_code in (401, 403):
                    raise HTTPException(
                        status_code=400, detail="The provider rejected this API key."
                    )
                if exc.response.status_code == 404:
                    if len(urls_to_try) > 1 and url == urls_to_try[0]:
                        continue
                    raise HTTPException(
                        status_code=400,
                        detail="This provider did not return a model list. Type the model ID manually.",
                    )
                raise HTTPException(
                    status_code=400, detail=f"Provider error: {exc.response.status_code}"
                )
            except Exception as exc:
                last_exc = exc
                logger.error("Error fetching models from %s: %s", url, exc)

    if data is None:
        if last_exc:
            raise HTTPException(
                status_code=502, detail=f"Could not reach {base}."
            )
        raise HTTPException(
            status_code=400,
            detail="This provider did not return a model list. Type the model ID manually.",
        )

    # 6. Parse all three response shapes:
    # {"data": [{"id": ...}]}, {"models": [{"name"|"id": ...}]}, or top-level JSON list
    raw_models: list[str] = []
    if isinstance(data, dict):
        if "data" in data and isinstance(data["data"], list):
            for item in data["data"]:
                if isinstance(item, dict) and "id" in item:
                    raw_models.append(str(item["id"]))
                elif isinstance(item, str):
                    raw_models.append(item)
        elif "models" in data and isinstance(data["models"], list):
            for item in data["models"]:
                if isinstance(item, dict):
                    m_id = item.get("name") or item.get("id")
                    if m_id:
                        raw_models.append(str(m_id))
                elif isinstance(item, str):
                    raw_models.append(item)
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and "id" in item:
                raw_models.append(str(item["id"]))
            elif isinstance(item, str):
                raw_models.append(item)

    # Strip leading models/ from ids (Gemini)
    cleaned: list[str] = []
    seen = set()
    for m in raw_models:
        clean = m.removeprefix("models/").strip()
        if clean and clean.lower() not in seen:
            seen.add(clean.lower())
            cleaned.append(clean)

    cleaned.sort(key=lambda s: s.lower())
    cleaned = cleaned[:500]

    return {"models": cleaned, "base_url": base}


@router.post("/models")
async def list_models(body: ModelsBody, req: Request):
    user = current_user(req)
    return await fetch_models(body, user)
