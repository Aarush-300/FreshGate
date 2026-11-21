"""MongoDB backed accounts, sessions, per-user settings, and Watchtower data."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import time
import uuid
from pathlib import Path
from threading import Lock
from types import SimpleNamespace
from typing import Any, Optional

from fastapi import HTTPException, Request, status
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pymongo.errors import DuplicateKeyError, PyMongoError
from cryptography.fernet import Fernet
from dotenv import load_dotenv

from core.mongo import get_db, uses_mongo_accounts

logger = logging.getLogger("freshgate.users")
load_dotenv()

_SQLITE_USERS_LOCK = Lock()
_SQLITE_USERS_SCHEMA_READY: set[str] = set()
_LOCAL_SESSION_SECRET_PATH = Path(__file__).resolve().parents[1] / ".freshgate_session_secret"


class SQLiteUserLimitError(Exception):
    """Raised when a local SQLite install tries to create a second account."""


class SQLiteUsersCollection:
    """SQLite-backed single-user account collection for local development."""

    def __init__(self, db_path: str):
        self.db_path = str(Path(db_path).expanduser().resolve())
        with _SQLITE_USERS_LOCK:
            if self.db_path not in _SQLITE_USERS_SCHEMA_READY:
                parent = Path(self.db_path).parent
                if str(parent) not in ("", "."):
                    parent.mkdir(parents=True, exist_ok=True)
                with sqlite3.connect(self.db_path, timeout=10) as conn:
                    conn.execute(
                        "CREATE TABLE IF NOT EXISTS users ("
                        "id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, "
                        "password_hash TEXT NOT NULL DEFAULT '', settings_json TEXT NOT NULL DEFAULT '{}', "
                        "metadata_json TEXT NOT NULL DEFAULT '{}', created_at REAL)"
                    )
                _SQLITE_USERS_SCHEMA_READY.add(self.db_path)

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 10000")
        return conn

    @staticmethod
    def _document(row: Optional[sqlite3.Row]) -> Optional[dict[str, Any]]:
        if row is None:
            return None
        doc = {
            "_id": row["id"],
            "email": row["email"],
            "password_hash": row["password_hash"],
            "settings": json.loads(row["settings_json"] or "{}"),
            "metadata": json.loads(row["metadata_json"] or "{}"),
            "created_at": row["created_at"],
        }
        return doc

    def find_one(self, query: dict[str, Any], projection: Optional[dict[str, int]] = None):
        field_map = {"_id": "id", "email": "email"}
        clauses, values = [], []
        for key in ("_id", "email"):
            if key in query:
                clauses.append(f"{field_map[key]} = ?")
                values.append(query[key])
        if not clauses:
            return None
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT * FROM users WHERE {' AND '.join(clauses)} LIMIT 1", values
            ).fetchone()
        doc = self._document(row)
        if doc is not None and projection:
            for key, include in projection.items():
                if include == 0:
                    doc.pop(key, None)
        return doc

    def single_account(self):
        """Resolve local claims only when the database has exactly one owner."""
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM users LIMIT 2").fetchall()
        return self._document(rows[0]) if len(rows) == 1 else None

    def count_documents(self, query: Optional[dict[str, Any]] = None) -> int:
        query = query or {}
        field_map = {"_id": "id", "email": "email"}
        clauses, values = [], []
        for key in ("_id", "email"):
            if key in query:
                clauses.append(f"{field_map[key]} = ?")
                values.append(query[key])
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            row = conn.execute(f"SELECT COUNT(*) FROM users{where}", values).fetchone()
        return int(row[0])

    def insert_one(self, document: dict[str, Any]):
        user_id = str(document.get("_id") or uuid.uuid4().hex)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            count = int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])
            if count:
                raise SQLiteUserLimitError(
                    "Local SQLite mode supports a single account. Configure MongoDB and set FRESHGATE_STORE=mongo for multiple users."
                )
            conn.execute(
                "INSERT INTO users (id, email, password_hash, settings_json, metadata_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, document["email"], document["password_hash"],
                 json.dumps(document.get("settings", {})), json.dumps(document.get("metadata", {})),
                 document.get("created_at")),
            )
        return SimpleNamespace(inserted_id=user_id)

    def update_one(self, query: dict[str, Any], update: dict[str, Any], upsert: bool = False):
        current = self.find_one(query)
        fields = update.get("$set", {})
        if current is None:
            if not upsert:
                return SimpleNamespace(modified_count=0, matched_count=0, upserted_id=None)
            current = {**query, **fields}
            current.setdefault("_id", current.get("id") or uuid.uuid4().hex)
            current.setdefault("email", "")
            current.setdefault("password_hash", "")
            current.setdefault("settings", {})
            current.setdefault("metadata", {})
            current.setdefault("created_at", time.time())
            if current.get("email"):
                self.insert_one(current)
                return SimpleNamespace(modified_count=0, matched_count=0, upserted_id=current["_id"])
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO users (id, email, password_hash, settings_json, metadata_json, created_at) "
                    "VALUES (?, '', ?, ?, ?, ?)",
                    (current["_id"], current["password_hash"], json.dumps(current.get("settings", {})),
                     json.dumps(current.get("metadata", {})), current.get("created_at")),
                )
            return SimpleNamespace(modified_count=0, matched_count=0, upserted_id=current["_id"])

        for key, value in fields.items():
            if key.startswith("metadata."):
                nested_key = key.split(".", 1)[1]
                current.setdefault("metadata", {})[nested_key] = value
            elif key == "$setOnInsert":
                continue
            else:
                current[key] = value
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET email = ?, password_hash = ?, settings_json = ?, "
                "metadata_json = ?, created_at = ? WHERE id = ?",
                (current["email"], current["password_hash"], json.dumps(current.get("settings", {})),
                 json.dumps(current.get("metadata", {})), current.get("created_at"), current["_id"]),
            )
        return SimpleNamespace(modified_count=1, matched_count=1, upserted_id=None)


def users_collection():
    if not uses_mongo_accounts():
        db_path = os.getenv("FRESHGATE_USERS_DB") or os.getenv("FRESHGATE_FEATURE_DB", "freshgate_features.db")
        return SQLiteUsersCollection(db_path)
    return get_db()["users"]


def _is_placeholder_secret(val: str) -> bool:
    v = (val or "").strip().lower()
    return (
        v.startswith("replace")
        or v.startswith("your_")
        or v.startswith("changeme")
        or v.startswith("dev-secret")
        or v.startswith("optional")
        or v == "secret"
    )


def _session_secret() -> str:
    secret = (os.getenv("FRESHGATE_SESSION_SECRET") or "").strip()
    if secret:
        if len(secret) >= 24 and not _is_placeholder_secret(secret):
            return secret
        raise HTTPException(503, "Configure FRESHGATE_SESSION_SECRET with at least 24 characters.")

    # Never silently create or use a development secret in production, where
    # multiple workers must share one explicitly configured signing key.
    if os.getenv("FRESHGATE_ENV", "development").strip().lower() == "production":
        raise HTTPException(503, "Configure FRESHGATE_SESSION_SECRET with at least 24 characters.")

    try:
        if _LOCAL_SESSION_SECRET_PATH.exists():
            secret = _LOCAL_SESSION_SECRET_PATH.read_text(encoding="utf-8").strip()
        else:
            secret = secrets.token_urlsafe(48)
            with _LOCAL_SESSION_SECRET_PATH.open("x", encoding="utf-8") as secret_file:
                secret_file.write(secret)
            try:
                _LOCAL_SESSION_SECRET_PATH.chmod(0o600)
            except OSError:
                pass
    except FileExistsError:
        secret = _LOCAL_SESSION_SECRET_PATH.read_text(encoding="utf-8").strip()
    except OSError as exc:
        logger.error("Could not load or create local session secret: %s", exc)
        raise HTTPException(
            503,
            "Could not create the local session secret. Set FRESHGATE_SESSION_SECRET in .env and restart FreshGate.",
        ) from exc

    if len(secret) < 24 or _is_placeholder_secret(secret):
        raise HTTPException(503, "Configure FRESHGATE_SESSION_SECRET with at least 24 characters.")
    os.environ.setdefault("FRESHGATE_SESSION_SECRET", secret)
    return secret


def serializer():
    return URLSafeTimedSerializer(_session_secret(), salt="freshgate-session")


def _fernet() -> Fernet:
    secret = _session_secret()
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode() if value else ""


def decrypt_secret(value: str) -> str:
    if not value:
        return ""
    if not value.startswith("gAAAA"):  # legacy plaintext (pre-encryption installs)
        return value
    try:
        return _fernet().decrypt(value.encode()).decode()
    except Exception:
        logger.warning(
            "Stored secret could not be decrypted (session secret changed?). Ask the user to re-enter it."
        )
        return ""


def password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return salt.hex() + "." + digest.hex()


def check_password(password: str, stored: str) -> bool:
    try:
        salt_hex, digest = stored.split(".", 1)
        return hmac.compare_digest(password_hash(password, bytes.fromhex(salt_hex)).split(".", 1)[1], digest)
    except (ValueError, TypeError):
        return False


DUMMY_PASSWORD_HASH = password_hash(secrets.token_urlsafe(16))


def optional_current_user(request: Request) -> dict[str, Any] | None:
    token = request.cookies.get("freshgate_session")
    if not token:
        return None
    try:
        user_id = serializer().loads(token, max_age=60 * 60 * 24 * 30)
        return users_collection().find_one({"_id": user_id}, {"password_hash": 0})
    except (BadSignature, PyMongoError, HTTPException):
        return None


def current_user(request: Request) -> dict[str, Any]:
    user = optional_current_user(request)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Please log in.")
    return user


def _hint(enc: str) -> str:
    plain = decrypt_secret(enc) if enc else ""
    return ("••••" + plain[-4:]) if len(plain) >= 8 else ("••••" if plain else "")


def effective_base_url(settings: dict) -> str:
    from server.routes.providers import get_provider

    p = get_provider(settings.get("provider", "openai"))
    if p is None:
        return ""
    return (
        (settings.get("base_url") or p["base_url"])
        if p["editable_base_url"]
        else p["base_url"]
    )


def settings_complete(settings: dict) -> bool:
    from server.routes.providers import get_provider

    p = get_provider(settings.get("provider", ""))
    if not p or not settings.get("model") or not effective_base_url(settings):
        return False
    if p["key_required"] and not settings.get("api_key"):
        return False
    return bool(settings.get("tavily_key"))


def public_settings(user_or_dict: Any) -> dict[str, Any]:
    s = user_or_dict.get("settings", user_or_dict) or {}
    return {
        "provider": s.get("provider", "openai"),
        "model": s.get("model", ""),
        "base_url": effective_base_url({**s, "provider": s.get("provider", "openai")}),
        "has_api_key": bool(s.get("api_key")),
        "api_key_hint": _hint(s.get("api_key", "")),
        "has_tavily_key": bool(s.get("tavily_key")),
        "tavily_key_hint": _hint(s.get("tavily_key", "")),
        "is_complete": settings_complete({**s, "provider": s.get("provider", "openai")}),
    }


def load_credentials(user: dict | None) -> dict[str, Any] | None:
    if not user:
        return None
    settings = user.get("settings", {}) or {}
    prov_id = settings.get("provider", "openai")
    if not settings_complete({**settings, "provider": prov_id}):
        return None
    from server.routes.providers import get_provider

    p = get_provider(prov_id)
    if not p:
        return None
    return {
        "provider": prov_id,
        "base_url": effective_base_url(settings),
        "model": settings.get("model", ""),
        "api_key": decrypt_secret(settings.get("api_key", "")),
        "tavily_key": decrypt_secret(settings.get("tavily_key", "")),
        "keyless_ok": bool(not p["key_required"]),
    }


def public_claim(claim: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in claim.items() if k != "_id"}

