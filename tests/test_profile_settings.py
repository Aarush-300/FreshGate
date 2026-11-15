"""Tests for Phase 3/11 profile settings, security guards, and Watchtower isolation."""
import os
import time
from unittest.mock import patch, AsyncMock, MagicMock
import pytest
from httpx import AsyncClient, ASGITransport
from server.app import app
from server.users import (
    users_collection,
    serializer,
    encrypt_secret,
    decrypt_secret,
    load_credentials,
)
from server.netguard import validate_base_url


@pytest.mark.asyncio
async def test_saving_blank_tavily_when_none_saved_returns_422():
    user_id = "user_no_tavily"
    users_collection().update_one(
        {"_id": user_id},
        {"$set": {"_id": user_id, "email": "no_tavily@example.com", "settings": {}}},
        upsert=True,
    )
    cookie = {"freshgate_session": serializer().dumps(user_id)}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=cookie) as ac:
        resp = await ac.put(
            "/api/profile/settings",
            json={
                "provider": "openai",
                "model": "gpt-4o-mini",
                "api_key": "sk-new-valid-key",
                "tavily_key": "",
            },
        )
        assert resp.status_code == 422
        assert "Tavily search API key is required" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_offline_provider_with_no_key_succeeds_200():
    user_id = "user_offline"
    users_collection().update_one(
        {"_id": user_id},
        {
            "$set": {
                "_id": user_id,
                "email": "offline@example.com",
                "settings": {"tavily_key": encrypt_secret("tvly-saved-123")},
            }
        },
        upsert=True,
    )
    cookie = {"freshgate_session": serializer().dumps(user_id)}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=cookie) as ac:
        resp = await ac.put(
            "/api/profile/settings",
            json={
                "provider": "ollama",
                "base_url": "http://localhost:11434",
                "model": "llama3.2:latest",
                "api_key": "",
                "tavily_key": "",  # keeps saved tavily_key
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["provider"] == "ollama"
        assert data["model"] == "llama3.2:latest"
        assert data["base_url"] == "http://localhost:11434"
        assert data["is_complete"] is True
        assert data["has_tavily_key"] is True


@pytest.mark.asyncio
async def test_provider_switch_drops_old_key():
    user_id = "user_switch_provider"
    users_collection().update_one(
        {"_id": user_id},
        {
            "$set": {
                "_id": user_id,
                "email": "switch@example.com",
                "settings": {
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "api_key": encrypt_secret("sk-openai-key"),
                    "tavily_key": encrypt_secret("tvly-key"),
                    "is_complete": True,
                },
            }
        },
        upsert=True,
    )
    cookie = {"freshgate_session": serializer().dumps(user_id)}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=cookie) as ac:
        # Switching to Groq with blank key must reject because Groq requires a key
        resp = await ac.put(
            "/api/profile/settings",
            json={
                "provider": "groq",
                "model": "llama-3.3-70b-versatile",
                "api_key": "",
                "tavily_key": "",
            },
        )
        assert resp.status_code == 422
        assert "Enter the API key for Groq" in resp.json()["detail"]

        # Switching to offline Ollama with blank key drops the OpenAI key and succeeds
        resp2 = await ac.put(
            "/api/profile/settings",
            json={
                "provider": "ollama",
                "base_url": "http://localhost:11434",
                "model": "llama3",
                "api_key": "",
                "tavily_key": "",
            },
        )
        assert resp2.status_code == 200
        data = resp2.json()
        assert data["provider"] == "ollama"
        assert data["has_api_key"] is False
        assert data["api_key_hint"] == ""


@pytest.mark.asyncio
async def test_profile_models_parses_all_three_shapes():
    cookie = {"freshgate_session": serializer().dumps("test_user_id")}

    # Shape 1: {"data": [{"id": "gpt-4o"}, {"id": "models/gemini-2.0-flash"}]}
    mock_resp1 = MagicMock()
    mock_resp1.status_code = 200
    mock_resp1.json.return_value = {
        "data": [{"id": "gpt-4o"}, {"id": "models/gemini-2.0-flash"}, {"id": "gpt-3.5-turbo"}]
    }

    # Shape 2: {"models": [{"name": "llama3:latest"}, {"id": "mistral:latest"}]}
    mock_resp2 = MagicMock()
    mock_resp2.status_code = 200
    mock_resp2.json.return_value = {
        "models": [{"name": "llama3:latest"}, {"id": "mistral:latest"}]
    }

    # Shape 3: top-level JSON list
    mock_resp3 = MagicMock()
    mock_resp3.status_code = 200
    mock_resp3.json.return_value = [
        {"id": "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo"},
        {"id": "mistralai/Mixtral-8x7B-Instruct-v0.1"},
    ]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=cookie) as ac:
        with patch("server.routes.profile.httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            # Test Shape 1
            mock_get.return_value = mock_resp1
            r1 = await ac.post("/api/profile/models", json={"provider": "openai", "api_key": "sk-mock"})
            assert r1.status_code == 200
            # Gemini models/ prefix stripped and deduplicated/sorted
            assert "gemini-2.0-flash" in r1.json()["models"]
            assert "gpt-4o" in r1.json()["models"]

            # Test Shape 2 (Ollama)
            mock_get.return_value = mock_resp2
            r2 = await ac.post("/api/profile/models", json={"provider": "ollama", "base_url": "http://localhost:11434"})
            assert r2.status_code == 200
            assert "llama3:latest" in r2.json()["models"]
            assert "mistral:latest" in r2.json()["models"]

            # Test Shape 3 (Together / list)
            mock_get.return_value = mock_resp3
            r3 = await ac.post("/api/profile/models", json={"provider": "together", "api_key": "sk-mock"})
            assert r3.status_code == 200
            assert "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo" in r3.json()["models"]


def test_validate_base_url_ssrf_production():
    # In production, loopback/private/cloud-metadata IPs must raise ValueError
    with patch.dict(os.environ, {"FRESHGATE_ENV": "production"}):
        with pytest.raises(ValueError, match="Production servers only accept https"):
            validate_base_url("http://169.254.169.254/latest/meta-data/")

        with pytest.raises(ValueError, match="cannot reach local or private addresses"):
            validate_base_url("https://169.254.169.254/latest/meta-data/")

        with pytest.raises(ValueError, match="cannot reach local or private addresses"):
            validate_base_url("https://127.0.0.1:11434")

        with pytest.raises(ValueError, match="cannot reach local or private addresses"):
            validate_base_url("https://10.0.0.1:8000")

    # In development, loopback is allowed
    with patch.dict(os.environ, {"FRESHGATE_ENV": "development"}):
        res = validate_base_url("http://localhost:11434")
        assert res == "http://localhost:11434"


def test_decrypt_secret_garbage_returns_empty():
    assert decrypt_secret("gAAAA-garbage-not-fernet-token") == ""
    assert decrypt_secret("") == ""
    assert decrypt_secret(None) == ""


@pytest.mark.asyncio
async def test_verify_without_session_or_headers_returns_401():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/verify", json={"answer": "The moon orbits the earth."})
        assert resp.status_code == 401
        assert "Please log in" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_verify_with_incomplete_profile_returns_400():
    user_id = "user_incomplete_profile"
    users_collection().update_one(
        {"_id": user_id},
        {"$set": {"_id": user_id, "email": "inc@example.com", "settings": {}}},
        upsert=True,
    )
    cookie = {"freshgate_session": serializer().dumps(user_id)}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=cookie) as ac:
        resp = await ac.post("/verify", json={"answer": "The moon orbits the earth."})
        assert resp.status_code == 400
        assert "Finish your Profile first" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_watchtower_claims_without_session_returns_401():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/watchtower/claims")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_user_isolation_for_watchtower_claims():
    from core.mongo_store import MongoFeatureStore, _ANY
    import core.mongo
    import core.persistence

    mongo_store = MongoFeatureStore(owner_id=_ANY, db=core.mongo.get_db())
    with patch("server.routes.watchtower.get_feature_store", return_value=mongo_store), \
         patch("core.persistence.get_feature_store", return_value=mongo_store):
        user_a = "user_alpha"
        user_b = "user_beta"

        users_collection().update_one(
            {"_id": user_a},
            {"$set": {"_id": user_a, "email": "a@example.com", "settings": {"is_complete": True}}},
            upsert=True,
        )
        users_collection().update_one(
            {"_id": user_b},
            {"$set": {"_id": user_b, "email": "b@example.com", "settings": {"is_complete": True}}},
            upsert=True,
        )

        cookie_a = {"freshgate_session": serializer().dumps(user_a)}
        cookie_b = {"freshgate_session": serializer().dumps(user_b)}

        # User A monitors a claim
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=cookie_a) as ac_a:
            add_resp = await ac_a.post(
                "/api/watchtower/monitor",
                json={
                    "claim_text": "Sam Altman is the CEO of OpenAI.",
                    "category": "leadership",
                    "monitoring_interval": 3600,
                },
            )
            assert add_resp.status_code in (200, 201)
            claim_id = add_resp.json()["id"]

            # User A can read it
            get_a = await ac_a.get(f"/api/watchtower/claims/{claim_id}")
            assert get_a.status_code == 200

        # User B cannot read, check, patch, or delete User A's claim
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=cookie_b) as ac_b:
            get_b = await ac_b.get(f"/api/watchtower/claims/{claim_id}")
            assert get_b.status_code == 404

            chk_b = await ac_b.post(f"/api/watchtower/claims/{claim_id}/check")
            assert chk_b.status_code == 404

            patch_b = await ac_b.patch(f"/api/watchtower/claims/{claim_id}", json={"enabled": False})
            assert patch_b.status_code == 404

            del_b = await ac_b.delete(f"/api/watchtower/claims/{claim_id}")
            assert del_b.status_code == 404

        # User A can delete it
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=cookie_a) as ac_a:
            del_a = await ac_a.delete(f"/api/watchtower/claims/{claim_id}")
            assert del_a.status_code == 204


@pytest.mark.asyncio
async def test_auth_login_rate_limiting():
    # 10 failed login attempts from same IP/email
    # 11th should return 429
    unique_email = f"ratelimit_{int(time.time())}@example.com"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        for _ in range(10):
            r = await ac.post(
                "/api/auth/login",
                json={"email": unique_email, "password": "WrongPassword123!"},
            )
            assert r.status_code == 401

        # 11th attempt
        r11 = await ac.post(
            "/api/auth/login",
            json={"email": unique_email, "password": "WrongPassword123!"},
        )
        assert r11.status_code == 429
        assert "Too many attempts" in r11.json()["detail"]
