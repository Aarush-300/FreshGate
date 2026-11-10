import hashlib
import json
import sqlite3
import time
from typing import Optional, List
from core.models import Claim, Source

DEFAULT_TTLS = {
    "pricing": 3600,          # 1 hour
    "version": 21600,         # 6 hours
    "leadership": 86400,      # 24 hours
    "law": 604800,            # 7 days
}

DEFAULT_TTL = 86400           # 24 hours fallback


def normalize_claim_key(claim_text: str) -> str:
    """Compute deterministic SHA-256 hash for normalized claim text."""
    normalized = " ".join(claim_text.strip().lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class SQLiteClaimCache:
    """SQLite-backed claim verification cache with per-claim-type TTLs."""

    def __init__(self, db_path: str = "freshgate_cache.db"):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS claim_cache (
                    claim_hash TEXT PRIMARY KEY,
                    claim_type TEXT,
                    claim_text TEXT,
                    verdict TEXT,
                    score INTEGER,
                    sources_json TEXT,
                    correction TEXT,
                    created_at REAL,
                    expires_at REAL
                )
                """
            )
            conn.commit()

    def get(self, claim_text: str, claim_type: str = "other") -> Optional[Claim]:
        """Retrieve cached claim verdict if present and not expired."""
        claim_hash = normalize_claim_key(claim_text)
        now = time.time()

        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM claim_cache WHERE claim_hash = ? AND expires_at > ?",
                (claim_hash, now),
            )
            row = cursor.fetchone()
            if not row:
                return None

            sources_raw = json.loads(row["sources_json"] or "[]")
            sources = [Source(**s) for s in sources_raw]

            return Claim(
                id=claim_hash[:8],
                text=row["claim_text"],
                type=row["claim_type"],
                verdict=row["verdict"],
                score=row["score"],
                sources=sources,
                cached=True,
                correction=row["correction"],
            )

    def set(self, claim: Claim, ttl_seconds: Optional[int] = None):
        """Store verified claim in cache with appropriate TTL."""
        claim_hash = normalize_claim_key(claim.text)
        now = time.time()

        if ttl_seconds is None:
            claim_type_key = claim.type.strip().lower()
            ttl_seconds = DEFAULT_TTLS.get(claim_type_key, DEFAULT_TTL)

        expires_at = now + ttl_seconds
        sources_json = json.dumps([s.model_dump() for s in claim.sources])

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO claim_cache 
                (claim_hash, claim_type, claim_text, verdict, score, sources_json, correction, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    claim_hash,
                    claim.type,
                    claim.text,
                    claim.verdict,
                    claim.score,
                    sources_json,
                    claim.correction,
                    now,
                    expires_at,
                ),
            )
            conn.commit()

    def clear(self):
        """Clear all cached entries."""
        with self._get_connection() as conn:
            conn.execute("DELETE FROM claim_cache")
            conn.commit()
