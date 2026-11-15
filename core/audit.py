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
import asyncio
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
        owner_id: Optional[str] = None,
        source: str = "proxy",
        mode: Optional[str] = None,
    ):
        pass

    @abstractmethod
    def get_recent_audits(
        self,
        limit: int = 50,
        offset: int = 0,
        owner_id: Optional[str] = None,
        source: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def count_audits(
        self,
        owner_id: Optional[str] = None,
        source: Optional[str] = None,
    ) -> int:
        pass

    async def arecord_audit(self, **kw):
        await asyncio.to_thread(self.record_audit, **kw)

    async def aget_recent_audits(self, **kw):
        return await asyncio.to_thread(self.get_recent_audits, **kw)

    async def acount_audits(self, **kw):
        return await asyncio.to_thread(self.count_audits, **kw)


class SQLiteAuditStore(AuditStoreBase):
    """SQLite-backed audit store with WAL mode and schema auto-creation."""

    def __init__(self, db_path: str = "freshgate_audit.db"):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        with self._lock:
            conn = None
            try:
                conn = sqlite3.connect(self.db_path)
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA busy_timeout=5000;")
                with conn:
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
                            status TEXT,
                            owner_id TEXT,
                            source TEXT,
                            mode TEXT
                        );
                    """)
                    # Safe migration for existing tables missing new columns
                    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(verification_audit)").fetchall()}
                    for col in ("owner_id", "source", "mode"):
                        if col not in existing_cols:
                            conn.execute(f"ALTER TABLE verification_audit ADD COLUMN {col} TEXT;")
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_created ON verification_audit(created_at);")
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_corr ON verification_audit(correlation_id);")
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_owner ON verification_audit(owner_id, created_at);")
            finally:
                if conn:
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
        owner_id: Optional[str] = None,
        source: str = "proxy",
        mode: Optional[str] = None,
    ):
        with self._lock:
            conn = None
            try:
                conn = sqlite3.connect(self.db_path, timeout=5.0)
                now = time.time()
                query = """
                    INSERT INTO verification_audit
                    (id, correlation_id, created_at, input_text, claims_json, total_claims, stale_claims, avg_score, latency_ms, status, owner_id, source, mode)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """
                params = (
                    audit_id,
                    correlation_id,
                    now,
                    input_text[:5000],
                    json.dumps(claims_data),
                    total_claims,
                    stale_claims,
                    avg_score,
                    latency_ms,
                    status,
                    owner_id,
                    source,
                    mode,
                )
                try:
                    with conn:
                        conn.execute(query, params)
                except sqlite3.IntegrityError:
                    # Append-only retry with randomized suffix if audit_id collision occurs
                    new_id = f"{audit_id}:{uuid.uuid4().hex[:8]}"
                    params = (new_id,) + params[1:]
                    with conn:
                        conn.execute(query, params)
            except Exception as e:
                logger.exception("Failed to write to verification audit store: %s", e)
            finally:
                if conn:
                    conn.close()

    def count_audits(
        self,
        owner_id: Optional[str] = None,
        source: Optional[str] = None,
    ) -> int:
        with self._lock:
            conn = None
            try:
                conn = sqlite3.connect(self.db_path, timeout=5.0)
                cursor = conn.cursor()
                query = "SELECT COUNT(*) FROM verification_audit WHERE 1=1"
                params: List[Any] = []
                if owner_id is not None:
                    query += " AND owner_id = ?"
                    params.append(owner_id)
                if source is not None:
                    query += " AND source = ?"
                    params.append(source)
                cursor.execute(query, tuple(params))
                row = cursor.fetchone()
                return int(row[0]) if row else 0
            except Exception as e:
                logger.exception("Failed to count audit records: %s", e)
                return 0
            finally:
                if conn:
                    conn.close()

    def get_recent_audits(
        self,
        limit: int = 50,
        offset: int = 0,
        owner_id: Optional[str] = None,
        source: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        with self._lock:
            conn = None
            try:
                conn = sqlite3.connect(self.db_path, timeout=5.0)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                query = "SELECT * FROM verification_audit WHERE 1=1"
                params: List[Any] = []
                if owner_id is not None:
                    query += " AND owner_id = ?"
                    params.append(owner_id)
                if source is not None:
                    query += " AND source = ?"
                    params.append(source)
                query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
                params.extend([limit, offset])
                cursor.execute(query, tuple(params))
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
                        "owner_id": r["owner_id"] if "owner_id" in r.keys() else None,
                        "source": r["source"] if "source" in r.keys() else None,
                        "mode": r["mode"] if "mode" in r.keys() else None,
                    })
                return results
            except Exception as e:
                logger.exception("Failed to read from audit store: %s", e)
                return []
            finally:
                if conn:
                    conn.close()


def get_audit_store() -> AuditStoreBase:
    """Return the SQLite audit store; PostgreSQL persistence is not implemented yet."""
    pg_dsn = os.getenv("FRESHGATE_POSTGRES_DSN") or os.getenv("DATABASE_URL")
    if pg_dsn:
        logger.warning("PostgreSQL audit storage is not implemented; using SQLite. Configure FRESHGATE_AUDIT_DB for the active store.")
    db_file = os.getenv("FRESHGATE_AUDIT_DB", "freshgate_audit.db")
    return SQLiteAuditStore(db_path=db_file)


audit_store = get_audit_store()


audit_store = get_audit_store()
