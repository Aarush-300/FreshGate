"""Metadata for OpenAI-compatible provider choices in the dashboard."""

from fastapi import APIRouter, Header, HTTPException
import httpx
from pydantic import BaseModel, ConfigDict

router = APIRouter(prefix="/api/providers", tags=["providers"])

PROVIDER_CATALOG = [
    {
        "id": "openai",
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "default_model": "",
        "models": [],
        "credential_hint": "Use an OpenAI API key. OpenAI API usage is billed; load your account's available models or enter a model ID.",
    },
    {
        "id": "groq",
        "name": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "",
        "models": [],
        "credential_hint": "Use a Groq API key. Load the models available to your account to choose one within its current plan limits.",
    },
    {
        "id": "gemini",
        "name": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "default_model": "gemini-3.6-flash",
        "models": ["gemini-3.6-flash"],
        "credential_hint": "Use a Gemini API key. Gemini 3.6 Flash has a free API tier, subject to Google's rate limits.",
    },
    {
        "id": "ollama",
        "name": "Ollama",
        "base_url": "http://localhost:11434/v1",
        "default_model": "",
        "models": [],
        "credential_hint": "API key is optional for local Ollama. Remote Ollama endpoints may require authentication.",
    },
    {
        "id": "openrouter",
        "name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "openrouter/free",
        "models": ["openrouter/free"],
        "credential_hint": "Use an OpenRouter API key.",
    },
    {
        "id": "custom",
        "name": "Custom OpenAI-compatible",
        "base_url": "",
        "default_model": "",
        "models": [],
        "credential_hint": "Only send a key to an endpoint you trust. This must implement OpenAI-compatible chat completions.",
    },
]

SUPPORTED_PROVIDER_IDS = {provider["id"] for provider in PROVIDER_CATALOG}


@router.get("")
async def list_providers():
    """Return provider defaults and model suggestions without exposing credentials."""
    return {"providers": PROVIDER_CATALOG}


class ModelListRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str | None = None


@router.post("/{provider_id}/models")
async def list_provider_models(
    provider_id: str,
    request: ModelListRequest,
    api_key: str | None = Header(default=None, alias="X-LLM-API-Key"),
):
    """Fetch current model IDs using a request-scoped key; credentials are never persisted."""
    provider = next((item for item in PROVIDER_CATALOG if item["id"] == provider_id), None)
    if provider is None or provider_id == "custom":
        raise HTTPException(status_code=400, detail="Live model listing is not supported for this provider.")
    if provider_id != "ollama" and (not api_key or not api_key.strip()):
        raise HTTPException(status_code=400, detail="Enter the provider API key before loading models.")

    if provider_id == "ollama":
        base_url = (request.base_url or provider["base_url"]).rstrip("/")
        if not base_url.endswith("/v1"):
            raise HTTPException(status_code=400, detail="Ollama base URL must end in /v1 to load local models.")
        url = f"{base_url[:-3]}/api/tags"
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    else:
        url = f"{provider['base_url'].rstrip('/')}/models"
        headers = {"Authorization": f"Bearer {api_key.strip()}"}

    try:
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=False) as client:
            response = await client.get(url, headers=headers)
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail="The provider could not return its model list. Check the API key and provider access.") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Could not load models from the selected provider.") from exc

    if provider_id == "ollama":
        models = [item.get("name") for item in payload.get("models", []) if isinstance(item, dict) and item.get("name")]
    else:
        models = [item.get("id") for item in payload.get("data", []) if isinstance(item, dict) and item.get("id")]
    return {"models": sorted(set(models), key=str.casefold)}
