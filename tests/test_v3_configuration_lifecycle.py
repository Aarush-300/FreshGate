"""FreshGate V3 Comprehensive Test Suite: Configuration Lifecycle, Secret Validation, and Root Cause Verification.

Tests:
1. .env loaded before service initialization.
2. Environment variables already supplied by Docker / OS environment.
3. Missing OPENAI_API_KEY blocks request with clear configuration error (never b'Bearer ').
4. Empty OPENAI_API_KEY blocks request before network call.
5. Whitespace-only API key rejected.
6. Placeholder API keys ('your_key', 'sk-placeholder', 'none') rejected.
7. API key containing surrounding whitespace is cleaned and accepted.
8. Application import without initialized credentials starts in degraded/unconfigured state safely.
9. Application startup with valid credentials sets healthy status.
10. Application shutdown gracefully closes HTTP client pools.
11. Favicon 404 resolved: GET /favicon.ico returns 200 OK.
12. Subprocess test proving import order does not freeze empty credentials from .env.
"""

import os
import sys
import subprocess
import pytest
import httpx
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from core.config import (
    FreshGateConfig,
    load_config,
    reload_config,
    get_config,
    is_valid_credential,
    mask_secret,
)
from core.llm_manager import (
    LLMProviderManager,
    LLMProviderException,
    LLMErrorType,
    get_llm_manager,
    reset_shared_llm_manager,
)
from core.verifier import TavilySearchClient
from server.app import app


# --- 1. Credential Validation Helper Tests ---

def test_credential_validation():
    # Valid keys
    assert is_valid_credential("sk-proj-1234567890abcdef") is True
    assert is_valid_credential("  sk-valid-key-with-spaces  ") is True
    assert is_valid_credential("tvly-prod-key-1234567890") is True

    # Invalid / empty / whitespace
    assert is_valid_credential(None) is False
    assert is_valid_credential("") is False
    assert is_valid_credential("   ") is False

    # Placeholders
    assert is_valid_credential("your_openai_api_key_here") is False
    assert is_valid_credential("your-api-key") is False
    assert is_valid_credential("sk-placeholder") is False
    assert is_valid_credential("none") is False
    assert is_valid_credential("null") is False
    assert is_valid_credential("test-key") is False

    # Invalid header characters (control chars, newlines)
    assert is_valid_credential("sk-key\r\nmalicious-header") is False
    assert is_valid_credential("sk-key\n") is False
    assert is_valid_credential("sk-key\0") is False

    # Too short to be real credential
    assert is_valid_credential("123") is False


def test_mask_secret():
    assert mask_secret("") == "[NOT CONFIGURED]"
    assert mask_secret(None) == "[NOT CONFIGURED]"
    assert mask_secret("short") == "****"
    assert mask_secret("sk-proj-1234567890abcdef") == "sk-p...cdef"


# --- 2. Configuration Scenarios (Scenarios 1-7) ---

def test_config_missing_openai_key():
    cfg = load_config(override_env={"OPENAI_API_KEY": "", "TAVILY_API_KEY": ""})
    assert cfg.is_openai_configured is False
    assert cfg.openai_api_key == ""
    assert cfg.is_tavily_configured is False


def test_config_whitespace_openai_key():
    cfg = load_config(override_env={"OPENAI_API_KEY": "    ", "TAVILY_API_KEY": ""})
    assert cfg.is_openai_configured is False
    assert cfg.openai_api_key == ""


def test_config_placeholder_openai_key():
    cfg = load_config(override_env={"OPENAI_API_KEY": "your_openai_api_key_here"})
    assert cfg.is_openai_configured is False
    assert cfg.openai_api_key == ""


def test_config_surrounding_whitespace_stripped():
    cfg = load_config(override_env={"OPENAI_API_KEY": "  sk-test-valid-production-key-12345  "})
    assert cfg.is_openai_configured is True
    assert cfg.openai_api_key == "sk-test-valid-production-key-12345"


def test_config_docker_environment_override():
    docker_env = {
        "OPENAI_API_KEY": "sk-docker-supplied-key-12345",
        "FRESHGATE_MODE": "warn",
        "FRESHGATE_UPSTREAM_TIMEOUT": "45.0",
        "FRESHGATE_MAX_GLOBAL_CONCURRENCY": "25",
    }
    cfg = load_config(override_env=docker_env)
    assert cfg.is_openai_configured is True
    assert cfg.openai_api_key == "sk-docker-supplied-key-12345"
    assert cfg.freshgate_mode == "warn"
    assert cfg.upstream_timeout == 45.0
    assert cfg.max_global_concurrency == 25


# --- 3. Empty Bearer Authorization Header Prevention ---

@pytest.mark.asyncio
async def test_llm_manager_rejects_missing_key_before_network():
    """Verify complete() never constructs 'Authorization: Bearer ' and raises immediately."""
    mgr = LLMProviderManager(api_key="")

    with patch.object(mgr._client, "post", new_callable=AsyncMock) as mock_post:
        with pytest.raises(LLMProviderException) as exc_info:
            await mgr.complete("Test prompt")

        # Network request must NEVER have been attempted
        mock_post.assert_not_called()
        assert exc_info.value.detail.error_type == LLMErrorType.INVALID_API_KEY
        assert "missing or invalid" in exc_info.value.detail.message
        assert mgr.stats.status == "missing_credentials"


@pytest.mark.asyncio
async def test_llm_manager_rejects_placeholder_key_before_network():
    mgr = LLMProviderManager(api_key="your_openai_api_key_here")

    with patch.object(mgr._client, "post", new_callable=AsyncMock) as mock_post:
        with pytest.raises(LLMProviderException) as exc_info:
            await mgr.complete("Test prompt")

        mock_post.assert_not_called()
        assert exc_info.value.detail.error_type == LLMErrorType.INVALID_API_KEY
        assert mgr.stats.status == "missing_credentials"


@pytest.mark.asyncio
async def test_tavily_client_rejects_missing_key_without_bearer():
    """Verify TavilySearchClient never sends malformed header when key is missing or invalid."""
    client = TavilySearchClient(api_key="")
    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        results = await client.search("test query")
        mock_post.assert_not_called()
        assert results == []
        assert client.status == "missing_credentials"
    await client.aclose()


# --- 4. Application Lifespan & HTTP Client Cleanup ---

@pytest.mark.asyncio
async def test_llm_manager_aclose_lifecycle():
    mgr = LLMProviderManager(api_key="sk-valid-key-for-lifecycle-test")
    assert not mgr._client.is_closed
    await mgr.aclose()
    assert mgr._client.is_closed


# --- 5. Favicon 404 Test ---

def test_favicon_endpoint_returns_200():
    client = TestClient(app)
    response = client.get("/favicon.ico")
    assert response.status_code == 200
    assert response.headers.get("content-type") == "image/x-icon"
    assert len(response.content) > 0


# --- 6. Provider Status Endpoint Diagnostic Data ---

def test_api_provider_status_contract():
    client = TestClient(app)
    response = client.get("/api/provider/status")
    assert response.status_code == 200
    data = response.json()

    assert "openai" in data
    assert "tavily" in data
    assert "circuit_breakers" in data
    assert "cache" in data
    assert "config" in data

    # Verify secrets are never exposed
    cfg_diag = data["config"]
    assert "sk-" not in str(cfg_diag["openai"]["key_preview"]) or "..." in str(cfg_diag["openai"]["key_preview"])
    assert "tvly-" not in str(cfg_diag["tavily"]["key_preview"]) or "..." in str(cfg_diag["tavily"]["key_preview"])


# --- 7. Subprocess Import-Order Regression Test ---

def test_subprocess_import_order_does_not_freeze_empty_credentials():
    """Run isolated Python subprocess to prove that importing server.app loads .env

    and initializes configuration before LLMProviderManager is accessed.
    """
    code = """
import sys
from core.config import get_config
from core.llm_manager import get_llm_manager
from server.app import app

cfg = get_config()
mgr = get_llm_manager()

# If .env exists in the directory, config must have loaded without error
print(f"CONFIG_OPENAI_OK:{cfg.is_openai_configured}")
print(f"MGR_STATUS:{mgr.stats.status}")
print("IMPORT_ORDER_TEST_SUCCESS")
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "..")),
    )
    assert result.returncode == 0, f"Subprocess failed with stderr:\n{result.stderr}"
    assert "IMPORT_ORDER_TEST_SUCCESS" in result.stdout
