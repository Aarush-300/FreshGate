"""Shared MongoDB client + index setup."""
from __future__ import annotations
import logging, os, threading
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.database import Database

logger = logging.getLogger("freshgate.mongo")
_client: MongoClient | None = None
_lock = threading.Lock()


def uses_mongo_store() -> bool:
    """Use Mongo for feature data when configured; otherwise use local SQLite."""
    configured_store = (os.getenv("FRESHGATE_STORE") or "").strip().lower()
    if configured_store:
        return configured_store == "mongo"
    # Production uses Mongo by default; missing MONGODB_URI is a configuration
    # error, not a reason to silently store accounts in an ephemeral SQLite file.
    return os.getenv("FRESHGATE_ENV", "development").strip().lower() == "production"


def uses_mongo_accounts() -> bool:
    """Use Mongo for accounts unless local SQLite or an explicit account store is selected."""
    account_store = (os.getenv("FRESHGATE_USERS_STORE") or "").strip().lower()
    if account_store:
        return account_store == "mongo"
    return uses_mongo_store()


def mongo_is_required() -> bool:
    """Whether any configured account or feature store depends on MongoDB."""
    return uses_mongo_store() or uses_mongo_accounts()


def get_client() -> MongoClient:
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                uri = (os.getenv("MONGODB_URI") or "").strip() or "mongodb://localhost:27017"
                _client = MongoClient(
                    uri,
                    serverSelectionTimeoutMS=4000,
                    connectTimeoutMS=4000,
                    socketTimeoutMS=4000,
                    appname="freshgate",
                )
    return _client


def get_db() -> Database:
    return get_client()[os.getenv("MONGODB_DATABASE", "freshgate")]


def ping() -> bool:
    try:
        get_client().admin.command("ping")
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("Mongo ping failed: %s", exc)
        return False

def ensure_indexes() -> None:
    db = get_db()
    db.users.create_index("email", unique=True)
    db.policies.create_index([("owner_id", ASCENDING), ("id", ASCENDING)], unique=True)
    db.policies.create_index([("owner_id", ASCENDING), ("created_at", DESCENDING)])
    db.monitored_claims.create_index([("owner_id", ASCENDING), ("id", ASCENDING)], unique=True)
    db.monitored_claims.create_index([("owner_id", ASCENDING), ("created_at", DESCENDING)])
    db.monitored_claims.create_index([("enabled", ASCENDING), ("next_check_at", ASCENDING), ("lease_until", ASCENDING)])
    db.verification_snapshots.create_index("verification_id", unique=True)
    db.verification_snapshots.create_index([("owner_id", ASCENDING), ("claim_fingerprint", ASCENDING), ("checked_at", DESCENDING)])
    db.watchtower_alerts.create_index([("owner_id", ASCENDING), ("acknowledged_at", ASCENDING), ("created_at", DESCENDING)])
    db.watchtower_alerts.create_index([("monitored_claim_id", ASCENDING), ("transition_key", ASCENDING)])
