"""Metadata for OpenAI-compatible provider choices in the dashboard."""
from __future__ import annotations

from typing import Any, Optional
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

router = APIRouter(prefix="/api/providers", tags=["providers"])


def _p(
    id: str,
    name: str,
    base_url: str,
    *,
    editable: bool = False,
    offline: bool = False,
    key_required: bool = True,
    default_model: str = "",
    models: Optional[list[str]] = None,
    hint: str = "",
) -> dict[str, Any]:
    return {
        "id": id,
        "name": name,
        "base_url": base_url,
        "editable_base_url": editable,
        "offline": offline,
        "key_required": key_required,
        "default_model": default_model,
        "models": models or [],
        "credential_hint": hint,
    }


PROVIDER_CATALOG = [
    _p(
        "openai",
        "OpenAI",
        "https://api.openai.com/v1",
        hint="OpenAI API usage is billed. Click Load models to pick one your key can use.",
    ),
    _p(
        "groq",
        "Groq",
        "https://api.groq.com/openai/v1",
        hint="Free tier available. Click Load models to see current models.",
    ),
    _p(
        "gemini",
        "Google Gemini",
        "https://generativelanguage.googleapis.com/v1beta/openai",
        default_model="gemini-3.6-flash",
        models=["gemini-3.6-flash"],
        hint="Gemini API key from Google AI Studio (free tier available).",
    ),
    _p(
        "openrouter",
        "OpenRouter",
        "https://openrouter.ai/api/v1",
        default_model="openrouter/free",
        models=["openrouter/free"],
        hint="OpenRouter API key.",
    ),
    _p("mistral", "Mistral", "https://api.mistral.ai/v1", hint="Mistral API key."),
    _p("deepseek", "DeepSeek", "https://api.deepseek.com/v1", hint="DeepSeek API key."),
    _p("together", "Together AI", "https://api.together.xyz/v1", hint="Together API key."),
    _p("xai", "xAI (Grok)", "https://api.x.ai/v1", hint="xAI API key."),
    _p(
        "ollama",
        "Ollama (local / offline)",
        "http://localhost:11434/v1",
        editable=True,
        offline=True,
        key_required=False,
        hint="Offline provider: API key is optional. Your server must be able to reach this URL (a deployed FreshGate cannot reach localhost on your PC; use a tunnel URL or run FreshGate locally).",
    ),
    _p(
        "lmstudio",
        "LM Studio (local / offline)",
        "http://localhost:1234/v1",
        editable=True,
        offline=True,
        key_required=False,
        hint="Offline provider: API key is optional. Same reachability note as Ollama.",
    ),
    _p(
        "custom",
        "Custom OpenAI-compatible",
        "",
        editable=True,
        key_required=False,
        hint="Any server that implements OpenAI chat completions and /models (vLLM, LiteLLM, llama.cpp...). API key optional. Only use endpoints you trust.",
    ),
]

SUPPORTED_PROVIDER_IDS = {p["id"] for p in PROVIDER_CATALOG}


def get_provider(provider_id: str) -> Optional[dict[str, Any]]:
    return next((p for p in PROVIDER_CATALOG if p["id"] == provider_id), None)


@router.get("")
async def list_providers():
    """Return provider defaults and model suggestions without exposing credentials."""
    return {"providers": PROVIDER_CATALOG}


class LegacyModelListRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str | None = None


@router.post("/{provider_id}/models")
async def legacy_list_provider_models(
    provider_id: str,
    request: LegacyModelListRequest,
    req: Request,
):
    from server.routes.profile import fetch_models, ModelsBody
    from server.users import current_user
    user = current_user(req)
    models_body = ModelsBody(
        provider=provider_id,
        base_url=request.base_url or "",
        api_key="",
    )
    return await fetch_models(models_body, user)
