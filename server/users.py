"""MongoDB backed accounts, sessions, per-user settings, and Watchtower data."""
from __future__ import annotations

import hashlib
import hmac
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

_client: MongoClient | None = None


def users_collection():
    global _client
    uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
    if _client is None:
        _client = MongoClient(uri, serverSelectionTimeoutMS=2500)
    return _client[os.getenv("MONGODB_DATABASE", "freshgate")]["users"]


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
    try:
        return _fernet().decrypt(value.encode()).decode()
    except Exception:
        # Existing installations may have stored credentials before at-rest encryption.
        return value


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


def public_claim(claim: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in claim.items() if k != "_id"}

