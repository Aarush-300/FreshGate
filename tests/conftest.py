from __future__ import annotations

import tempfile, os
_TMP = tempfile.mkdtemp(prefix="freshgate-tests-")
os.environ["FRESHGATE_AUDIT_DB"]   = os.path.join(_TMP, "audit.db")
os.environ["FRESHGATE_CACHE_DB"]   = os.path.join(_TMP, "cache.db")
os.environ["CACHE_DB_PATH"]        = os.path.join(_TMP, "cache.db")
os.environ["FRESHGATE_FEATURE_DB"] = os.path.join(_TMP, "features.db")
os.environ["FRESHGATE_STORE"] = "sqlite"
os.environ.setdefault("FRESHGATE_SESSION_SECRET", "freshgate-test-session-secret-at-least-24-chars-long")
os.environ.setdefault("FRESHGATE_ADMIN_KEY", "test-admin-secret-key-at-least-24-chars-long")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-valid-mock-key-for-test-suite-runs")

import uuid
from pathlib import Path

import pytest

import core.mongo

try:
    import mongomock
    core.mongo._client = mongomock.MongoClient()
except ImportError:
    class MockCollection:
        def __init__(self):
            self.docs = {}

        def find_one(self, filter_dict, projection=None):
            for doc in self.docs.values():
                match = True
                for k, v in filter_dict.items():
                    if doc.get(k) != v:
                        match = False
                        break
                if match:
                    return dict(doc)
            return None

        def update_one(self, filter_dict, update_dict, upsert=False):
            doc = self.find_one(filter_dict)
            if not doc:
                if upsert:
                    new_doc = dict(filter_dict)
                    if "$set" in update_dict:
                        new_doc.update(update_dict["$set"])
                    doc_id = new_doc.get("_id", str(uuid.uuid4()))
                    self.docs[doc_id] = new_doc
            else:
                if "$set" in update_dict:
                    doc.update(update_dict["$set"])
                self.docs[doc["_id"]] = doc

            class Res:
                modified_count = 1
            return Res()

        def insert_one(self, doc):
            doc_id = doc.get("_id", str(uuid.uuid4()))
            self.docs[doc_id] = dict(doc)

            class Res:
                inserted_id = doc_id
            return Res()

        def count_documents(self, filter_dict):
            count = 0
            for doc in self.docs.values():
                match = all(doc.get(k) == v for k, v in filter_dict.items())
                if match:
                    count += 1
            return count

    class MockDB:
        def __init__(self):
            self.cols = {}

        def __getattr__(self, name):
            if name not in self.cols:
                self.cols[name] = MockCollection()
            return self.cols[name]

        def __getitem__(self, name):
            return getattr(self, name)

    class MockMongoClient:
        def __init__(self):
            self.db = MockDB()

        def get_database(self, name=None):
            return self.db

        def __getitem__(self, name):
            return self.db

        @property
        def admin(self):
            class Admin:
                def command(self, cmd):
                    return {"ok": 1}
            return Admin()

    core.mongo._client = MockMongoClient()

from core.security import rate_limiter
from core.circuit_breaker import circuit_breaker
from server.users import users_collection, serializer, encrypt_secret

TEST_USER_ID = "test_user_id"
TEST_USER_EMAIL = "test@example.com"


@pytest.fixture(autouse=True)
def isolate_global_rate_limiter():
    rate_limiter.reset()
    circuit_breaker.reset()
    try:
        from server.app import _login_attempts
        _login_attempts.clear()
    except Exception:
        pass
    yield
    rate_limiter.reset()
    circuit_breaker.reset()
    try:
        from server.app import _login_attempts
        _login_attempts.clear()
    except Exception:
        pass


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
