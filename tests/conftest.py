from __future__ import annotations

import os
os.environ["FRESHGATE_STORE"] = "sqlite"
os.environ.setdefault("FRESHGATE_SESSION_SECRET", "freshgate-test-session-secret-at-least-24-chars-long")
os.environ.setdefault("FRESHGATE_ADMIN_KEY", "test-admin-secret-key-at-least-24-chars-long")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-valid-mock-key-for-test-suite-runs")

import uuid
from pathlib import Path

import mongomock
import pytest

import core.mongo
core.mongo._client = mongomock.MongoClient()

from core.security import rate_limiter
from server.users import users_collection, serializer, encrypt_secret

TEST_USER_ID = "test_user_id"
TEST_USER_EMAIL = "test@example.com"


@pytest.fixture(autouse=True)
def isolate_global_rate_limiter():
    rate_limiter.reset()
    yield
    rate_limiter.reset()


@pytest.fixture(autouse=True)
def ensure_default_test_user():
    users_collection().update_one(
        {"_id": TEST_USER_ID},
        {
            "$set": {
                "_id": TEST_USER_ID,
                "email": TEST_USER_EMAIL,
                "settings": {
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "api_key": encrypt_secret("sk-test"),
                    "tavily_key": encrypt_secret("tvly-test"),
                    "is_complete": True,
                },
            }
        },
        upsert=True,
    )
    yield


@pytest.fixture
def auth_cookie():
    return {"freshgate_session": serializer().dumps(TEST_USER_ID)}


@pytest.fixture
def admin_headers():
    return {"X-Admin-Key": os.environ["FRESHGATE_ADMIN_KEY"]}


@pytest.fixture
def workspace_tmp_path():
    """Temporary test directory under the checkout for restricted Windows runners."""
    path = Path(__file__).resolve().parents[1] / f".freshgate-test-{uuid.uuid4().hex}"
    path.mkdir()
    try:
        yield path
    finally:
        for child in path.iterdir():
            if child.is_file():
                child.unlink()
        path.rmdir()
