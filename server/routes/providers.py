"""Metadata for OpenAI-compatible provider choices in the dashboard."""

from fastapi import APIRouter

router = APIRouter(prefix="/api/providers", tags=["providers"])

PROVIDER_CATALOG = [
    {
        "id": "openai",
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
        "models": ["gpt-4.1-mini", "gpt-4.1-nano", "gpt-4o-mini"],
        "credential_hint": "Use an OpenAI API key.",
    },
    {
        "id": "groq",
        "name": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "openai/gpt-oss-120b",
        "models": ["openai/gpt-oss-120b", "openai/gpt-oss-20b"],
        "credential_hint": "Use a Groq API key. Model availability depends on your Groq account.",
    },
    {
        "id": "gemini",
        "name": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "default_model": "gemini-3.8-flash",
        "models": ["gemini-3.8-flash"],
        "credential_hint": "Use a Gemini API key.",
    },
    {
        "id": "ollama",
        "name": "Ollama",
        "base_url": "http://localhost:11434/v1",
        "default_model": "gemma4:31b",
        "models": ["gemma4:31b"],
        "credential_hint": "For local Ollama, a non-empty value is required by this form; local Ollama ignores the bearer token.",
    },
    {
        "id": "openrouter",
        "name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "openai/gpt-4.1-mini",
        "models": ["openai/gpt-4.1-mini"],
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
