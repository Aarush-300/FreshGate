"""MongoDB backed accounts, sessions, per-user settings, and Watchtower data."""
from __future__ import annotations

import hashlib
import hmac
<<<<<<< HEAD
import logging
=======
>>>>>>> 60d76df5e69c81f6f854d8a8b8e6cd8999a981ab
import os
import secrets
import time
import uuid
import base64
from typing import Any

from fastapi import HTTPException, Request, status
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError
from cryptography.fernet import Fernet
<<<<<<< HEAD
from dotenv import load_dotenv

from core.mongo import get_db

logger = logging.getLogger("freshgate.users")
load_dotenv()


def users_collection():
    return get_db()["users"]
=======

_client: MongoClient | None = None


def users_collection():
    global _client
    uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
    if _client is None:
        _client = MongoClient(uri, serverSelectionTimeoutMS=2500)
    return _client[os.getenv("MONGODB_DATABASE", "freshgate")]["users"]
>>>>>>> 60d76df5e69c81f6f854d8a8b8e6cd8999a981ab


def serializer():
    secret = os.getenv("FRESHGATE_SESSION_SECRET") or os.getenv("FRESHGATE_ADMIN_KEY") or ""
    if len(secret) < 24:
        raise HTTPException(503, "Configure FRESHGATE_SESSION_SECRET with at least 24 characters.")
    return URLSafeTimedSerializer(secret, salt="freshgate-session")


def _fernet() -> Fernet:
    secret = os.getenv("FRESHGATE_SESSION_SECRET") or os.getenv("FRESHGATE_ADMIN_KEY") or ""
    if len(secret) < 24:
        raise HTTPException(503, "Configure FRESHGATE_SESSION_SECRET with at least 24 characters.")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode() if value else ""


def decrypt_secret(value: str) -> str:
    if not value:
        return ""
<<<<<<< HEAD
    if not value.startswith("gAAAA"):  # legacy plaintext (pre-encryption installs)
        return value
    try:
        return _fernet().decrypt(value.encode()).decode()
    except Exception:
        logger.warning(
            "Stored secret could not be decrypted (session secret changed?). Ask the user to re-enter it."
        )
        return ""
=======
    try:
        return _fernet().decrypt(value.encode()).decode()
    except Exception:
        # Existing installations may have stored credentials before at-rest encryption.
        return value
>>>>>>> 60d76df5e69c81f6f854d8a8b8e6cd8999a981ab


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


<<<<<<< HEAD
def optional_current_user(request: Request) -> dict[str, Any] | None:
    token = request.cookies.get("freshgate_session")
    if not token:
        return None
    try:
        user_id = serializer().loads(token, max_age=60 * 60 * 24 * 30)
        return users_collection().find_one({"_id": user_id}, {"password_hash": 0})
    except (BadSignature, PyMongoError):
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
=======
def current_user(request: Request) -> dict[str, Any]:
    token = request.cookies.get("freshgate_session")
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Please log in.")
    try:
        user_id = serializer().loads(token, max_age=60 * 60 * 24 * 30)
        user = users_collection().find_one({"_id": user_id}, {"password_hash": 0})
    except (BadSignature, PyMongoError) as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session is invalid or expired.") from exc
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session is invalid or expired.")
    return user


def public_settings(user: dict[str, Any]) -> dict[str, Any]:
    settings = user.get("settings", {})
    return {"provider": settings.get("provider", "openai"), "model": settings.get("model", ""),
            "base_url": settings.get("base_url", ""), "has_api_key": bool(settings.get("api_key")),
            "has_tavily_key": bool(settings.get("tavily_key"))}
>>>>>>> 60d76df5e69c81f6f854d8a8b8e6cd8999a981ab


def public_claim(claim: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in claim.items() if k != "_id"}

