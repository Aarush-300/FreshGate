"""Shared MongoDB client + index setup."""
from __future__ import annotations
import logging, os, threading
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.database import Database

logger = logging.getLogger("freshgate.mongo")
_client: MongoClient | None = None
_lock = threading.Lock()

def get_client() -> MongoClient:
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                uri = (os.getenv("MONGODB_URI") or "").strip() or "mongodb://localhost:27017"
                _client = MongoClient(uri, serverSelectionTimeoutMS=4000, appname="freshgate")
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
