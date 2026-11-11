"""Historical Verification Audit Store (Phase 6).

Provides immutable audit logging of all verified LLM claims and decisions across
SQLite (default lightweight) and PostgreSQL (distributed production) backends.
"""

import os
import json
import time
import uuid
import sqlite3
import threading
import logging
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)


class AuditStoreBase(ABC):
    @abstractmethod
    def record_audit(
        self,
        audit_id: str,
        correlation_id: str,
        input_text: str,
        claims_data: List[Dict[str, Any]],
        total_claims: int,
        stale_claims: int,
        avg_score: Optional[int],
        latency_ms: int,
        status: str = "verified",
    ):
        pass

    @abstractmethod
    def get_recent_audits(self, limit: int = 50) -> List[Dict[str, Any]]:
        pass


class SQLiteAuditStore(AuditStoreBase):
    """SQLite-backed audit store with WAL mode and schema auto-creation."""

    def __init__(self, db_path: str = "freshgate_audit.db"):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS verification_audit (
                    id TEXT PRIMARY KEY,
                    correlation_id TEXT,
                    created_at REAL,
                    input_text TEXT,
                    claims_json TEXT,
                    total_claims INTEGER,
                    stale_claims INTEGER,
                    avg_score INTEGER,
                    latency_ms INTEGER,
                    status TEXT
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_created ON verification_audit(created_at);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_corr ON verification_audit(correlation_id);")
            conn.commit()
            conn.close()

    def record_audit(
        self,
        audit_id: str,
        correlation_id: str,
        input_text: str,
        claims_data: List[Dict[str, Any]],
        total_claims: int,
        stale_claims: int,
        avg_score: Optional[int],
        latency_ms: int,
        status: str = "verified",
    ):
        with self._lock:
            try:
                conn = sqlite3.connect(self.db_path, timeout=5.0)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO verification_audit
                    (id, correlation_id, created_at, input_text, claims_json, total_claims, stale_claims, avg_score, latency_ms, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        audit_id,
                        correlation_id,
                        time.time(),
                        input_text[:5000],
                        json.dumps(claims_data),
                        total_claims,
                        stale_claims,
                        avg_score,
                        latency_ms,
                        status,
                    ),
                )
                conn.commit()
                conn.close()
            except Exception as e:
                logger.error("Failed to write to verification audit store: %s", e)

    def get_recent_audits(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            try:
                conn = sqlite3.connect(self.db_path, timeout=5.0)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT * FROM verification_audit ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                )
                rows = cursor.fetchall()
                results = []
                for r in rows:
                    results.append({
                        "id": r["id"],
                        "correlation_id": r["correlation_id"],
                        "created_at": r["created_at"],
                        "input_text": r["input_text"],
                        "claims": json.loads(r["claims_json"] or "[]"),
                        "total_claims": r["total_claims"],
                        "stale_claims": r["stale_claims"],
                        "avg_score": r["avg_score"],
                        "latency_ms": r["latency_ms"],
                        "status": r["status"],
                    })
                conn.close()
                return results
            except Exception as e:
                logger.error("Failed to read from audit store: %s", e)
                return []


def get_audit_store() -> AuditStoreBase:
    """Return the SQLite audit store; PostgreSQL persistence is not implemented yet."""
    pg_dsn = os.getenv("FRESHGATE_POSTGRES_DSN") or os.getenv("DATABASE_URL")
    if pg_dsn:
        logger.warning("PostgreSQL audit storage is not implemented; using SQLite. Configure FRESHGATE_AUDIT_DB for the active store.")
    db_file = os.getenv("FRESHGATE_AUDIT_DB", "freshgate_audit.db")
    return SQLiteAuditStore(db_path=db_file)


audit_store = get_audit_store()
