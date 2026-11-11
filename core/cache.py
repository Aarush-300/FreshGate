"""FreshGate Multi-Backend Caching Engine.

Supports:
- Structured, versioned cache keys with category and jurisdiction isolation.
- Fast, short-lived TTL for unverifiable claims.
- Concurrency-safe SQLite with Write-Ahead Logging (WAL) and busy timeout.
- Distributed Redis caching via a unified ClaimCacheBase interface.
- Invalidation and runtime performance statistics.
"""

import os
import json
import sqlite3
import hashlib
import time
import threading
import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any
from core.models import Claim, Source
from core.categories import category_registry
from core.config import get_config

CACHE_KEY_VERSION = "v2"
UNVERIFIABLE_SHORT_TTL_SECONDS = 300  # 5 minutes for unverified items
logger = logging.getLogger(__name__)


def build_structured_cache_key(
    claim_text: str,
    category: str = "other",
    jurisdiction: str = "global",
    entity: str = "general",
) -> str:
    """Build deterministic, versioned, jurisdiction-isolated cache key."""
    norm_text = " ".join(claim_text.strip().lower().split())
    text_hash = hashlib.sha256(norm_text.encode("utf-8")).hexdigest()[:16]
    clean_cat = category.strip().lower()
    clean_jur = jurisdiction.strip().lower()
    clean_ent = entity.strip().lower()
    return f"{CACHE_KEY_VERSION}:{clean_cat}:{clean_jur}:{clean_ent}:{text_hash}"


class ClaimCacheBase(ABC):
    """Abstract Base Class for FreshGate cache providers."""

    @abstractmethod
    def get(
        self,
        claim_text: str,
        category: str = "other",
        jurisdiction: str = "global",
        entity: str = "general",
    ) -> Optional[Claim]:
        pass

    @abstractmethod
    def set(
        self,
        claim: Claim,
        jurisdiction: str = "global",
        entity: Optional[str] = None,
        ttl_seconds: Optional[int] = None,
    ):
        pass

    @abstractmethod
    def invalidate(self, category: Optional[str] = None, jurisdiction: Optional[str] = None):
        pass

    @abstractmethod
    def get_stats(self) -> Dict[str, Any]:
        pass

    def stats(self) -> Dict[str, Any]:
        return self.get_stats()

    @abstractmethod
    def clear(self):
        pass

    def cleanup_expired(self) -> int:
        """Prune expired entries and return number of deleted records."""
        return 0


class SQLiteClaimCache(ClaimCacheBase):
    """Hardened, thread-safe SQLite cache with WAL mode and structured keys."""

    def __init__(self, db_path: str = "freshgate_cache.db"):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # Enable Write-Ahead Logging (WAL) and busy timeout for high concurrency
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def _init_db(self):
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='claim_cache'")
            table_exists = bool(cursor.fetchone())

            if not table_exists:
                conn.execute(
                    """
                    CREATE TABLE claim_cache (
                        cache_key TEXT PRIMARY KEY,
                        category TEXT,
                        jurisdiction TEXT,
                        entity TEXT,
                        claim_text TEXT,
                        verdict TEXT,
                        score INTEGER,
                        confidence REAL,
                        explanation TEXT,
                        correction TEXT,
                        sources_json TEXT,
                        claim_json TEXT,
                        created_at REAL,
                        expires_at REAL
                    )
                    """
                )
            else:
                # Automated schema migration for existing databases
                cursor = conn.execute("PRAGMA table_info(claim_cache)")
                columns = {row["name"] for row in cursor.fetchall()}

                if "cache_key" not in columns and "claim_hash" in columns:
                    conn.execute("ALTER TABLE claim_cache RENAME COLUMN claim_hash TO cache_key")
                if "category" not in columns and "claim_type" in columns:
                    conn.execute("ALTER TABLE claim_cache RENAME COLUMN claim_type TO category")
                elif "category" not in columns:
                    conn.execute("ALTER TABLE claim_cache ADD COLUMN category TEXT DEFAULT 'other'")

                if "jurisdiction" not in columns:
                    conn.execute("ALTER TABLE claim_cache ADD COLUMN jurisdiction TEXT DEFAULT 'global'")
                if "entity" not in columns:
                    conn.execute("ALTER TABLE claim_cache ADD COLUMN entity TEXT DEFAULT 'general'")
                if "confidence" not in columns:
                    conn.execute("ALTER TABLE claim_cache ADD COLUMN confidence REAL DEFAULT 0.8")
                if "explanation" not in columns:
                    conn.execute("ALTER TABLE claim_cache ADD COLUMN explanation TEXT")
                if "claim_json" not in columns:
                    conn.execute("ALTER TABLE claim_cache ADD COLUMN claim_json TEXT")

            conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_expires ON claim_cache(expires_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_category ON claim_cache(category)")
            conn.commit()

    def get(
        self,
        claim_text: str,
        category: str = "other",
        jurisdiction: str = "global",
        entity: str = "general",
    ) -> Optional[Claim]:
        key = build_structured_cache_key(claim_text, category, jurisdiction, entity)
        now = time.time()

        with self._lock, self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM claim_cache WHERE cache_key = ? AND expires_at > ?",
                (key, now),
            )
            row = cursor.fetchone()
            if not row:
                self._misses += 1
                return None

            self._hits += 1
            sources_raw = json.loads(row["sources_json"] or "[]")
            sources = [Source(**s) for s in sources_raw]

            if row["claim_json"]:
                try:
                    cached_claim = Claim.model_validate_json(row["claim_json"])
                    cached_claim.id = key.split(":")[-1]
                    cached_claim.cached = True
                    return cached_claim
                except (ValueError, TypeError):
                    logger.warning("Ignoring malformed cached claim payload for key %s", key)

            return Claim(
                id=key.split(":")[-1],
                text=row["claim_text"],
                type=row["category"],
                verdict=row["verdict"],
                score=row["score"],
                confidence=row["confidence"] or 0.8,
                explanation=row["explanation"],
                sources=sources,
                cached=True,
                correction=row["correction"],
            )

    def set(
        self,
        claim: Claim,
        jurisdiction: str = "global",
        entity: Optional[str] = None,
        ttl_seconds: Optional[int] = None,
    ):
        target_entity = entity or (claim.entities[0] if claim.entities else "general")
        key = build_structured_cache_key(claim.text, claim.type, jurisdiction, target_entity)
        now = time.time()

        if ttl_seconds is None:
            # Unverifiable items get short TTL so they can be re-evaluated promptly
            if claim.verdict == "unverifiable":
                ttl_seconds = UNVERIFIABLE_SHORT_TTL_SECONDS
            else:
                cat_def = category_registry.get(claim.type)
                ttl_seconds = cat_def.default_ttl_seconds if cat_def else 86400

        expires_at = now + ttl_seconds
        sources_json = json.dumps([s.model_dump() for s in claim.sources])

        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO claim_cache 
                (cache_key, category, jurisdiction, entity, claim_text, verdict, score, confidence,
                 explanation, correction, sources_json, claim_json, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key,
                    claim.type,
                    jurisdiction.lower(),
                    target_entity.lower(),
                    claim.text,
                    claim.verdict,
                    claim.score,
                    claim.confidence,
                    claim.explanation,
                    claim.correction,
                    sources_json,
                    claim.model_dump_json(),
                    now,
                    expires_at,
                ),
            )
            conn.commit()

    def invalidate(self, category: Optional[str] = None, jurisdiction: Optional[str] = None):
        with self._lock, self._get_connection() as conn:
            if category and jurisdiction:
                conn.execute(
                    "DELETE FROM claim_cache WHERE category = ? AND jurisdiction = ?",
                    (category.lower(), jurisdiction.lower()),
                )
            elif category:
                conn.execute("DELETE FROM claim_cache WHERE category = ?", (category.lower(),))
            elif jurisdiction:
                conn.execute("DELETE FROM claim_cache WHERE jurisdiction = ?", (jurisdiction.lower(),))
            else:
                conn.execute("DELETE FROM claim_cache")
            conn.commit()

    def get_stats(self) -> Dict[str, Any]:
        total_ops = self._hits + self._misses
        hit_rate = round(self._hits / total_ops, 3) if total_ops > 0 else 0.0

        with self._lock, self._get_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) as count FROM claim_cache WHERE expires_at > ?", (time.time(),))
            active_count = cursor.fetchone()["count"]

        return {
            "backend": "sqlite",
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": hit_rate,
            "total_entries": active_count,
        }

    def clear(self):
        with self._lock, self._get_connection() as conn:
            conn.execute("DELETE FROM claim_cache")
            conn.commit()

    def cleanup_expired(self) -> int:
        """Prune expired entries and return number of deleted records."""
        now = time.time()
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM claim_cache WHERE expires_at <= ?", (now,))
            deleted = cursor.rowcount
            conn.commit()
            return deleted


class RedisClaimCache(ClaimCacheBase):
    """Distributed Redis cache implementation with native key expiration."""

    def __init__(self, redis_url: str):
        self.redis_url = redis_url
        self._hits = 0
        self._misses = 0
        try:
            import redis
            self.client = redis.Redis.from_url(redis_url, decode_responses=True)
            self.client.ping()
            self.available = True
        except Exception:
            self.client = None
            self.available = False

    def get(
        self,
        claim_text: str,
        category: str = "other",
        jurisdiction: str = "global",
        entity: str = "general",
    ) -> Optional[Claim]:
        if not self.available or not self.client:
            self._misses += 1
            return None

        key = build_structured_cache_key(claim_text, category, jurisdiction, entity)
        try:
            raw = self.client.get(key)
        except Exception as exc:
            logger.warning("Redis cache read failed; bypassing cache: %s", exc)
            self.available = False
            self._misses += 1
            return None
        if not raw:
            self._misses += 1
            return None

        try:
            data = json.loads(raw)
            cached_claim = Claim.model_validate(data.get("claim", data))
            cached_claim.id = key.split(":")[-1]
            cached_claim.cached = True
            self._hits += 1
            return cached_claim
        except (ValueError, TypeError, KeyError) as exc:
            logger.warning("Ignoring malformed Redis cache payload: %s", exc)
            self._misses += 1
            return None

    def set(
        self,
        claim: Claim,
        jurisdiction: str = "global",
        entity: Optional[str] = None,
        ttl_seconds: Optional[int] = None,
    ):
        if not self.available or not self.client:
            return

        target_entity = entity or (claim.entities[0] if claim.entities else "general")
        key = build_structured_cache_key(claim.text, claim.type, jurisdiction, target_entity)

        if ttl_seconds is None:
            if claim.verdict == "unverifiable":
                ttl_seconds = UNVERIFIABLE_SHORT_TTL_SECONDS
            else:
                cat_def = category_registry.get(claim.type)
                ttl_seconds = cat_def.default_ttl_seconds if cat_def else 86400

        payload = {"claim": claim.model_dump()}
        try:
            self.client.setex(key, ttl_seconds, json.dumps(payload))
        except Exception as exc:
            logger.warning("Redis cache write failed; bypassing cache: %s", exc)
            self.available = False

    def invalidate(self, category: Optional[str] = None, jurisdiction: Optional[str] = None):
        if not self.available or not self.client:
            return
        pattern = f"{CACHE_KEY_VERSION}:{category.lower() if category else '*'}:{jurisdiction.lower() if jurisdiction else '*'}:*"
        try:
            keys = list(self.client.scan_iter(match=pattern, count=500))
            if keys:
                self.client.delete(*keys)
        except Exception as exc:
            logger.warning("Redis cache invalidation failed: %s", exc)
            self.available = False

    def get_stats(self) -> Dict[str, Any]:
        total_ops = self._hits + self._misses
        hit_rate = round(self._hits / total_ops, 3) if total_ops > 0 else 0.0
        try:
            keys_count = sum(1 for _ in self.client.scan_iter(match=f"{CACHE_KEY_VERSION}:*", count=500)) if (self.available and self.client) else 0
        except Exception as exc:
            logger.warning("Redis cache stats failed: %s", exc)
            self.available = False
            keys_count = 0

        return {
            "backend": "redis",
            "available": self.available,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": hit_rate,
            "total_entries": keys_count,
        }

    def clear(self):
        if self.available and self.client:
            try:
                keys = list(self.client.scan_iter(match=f"{CACHE_KEY_VERSION}:*", count=500))
                if keys:
                    self.client.delete(*keys)
            except Exception as exc:
                logger.warning("Redis cache clear failed: %s", exc)
                self.available = False


def get_cache() -> ClaimCacheBase:
    """Build the configured cache, falling back to SQLite if Redis is unavailable."""
    config = get_config()
    redis_url = config.redis_url
    if redis_url:
        redis_cache = RedisClaimCache(redis_url)
        if redis_cache.available:
            return redis_cache
        logger.warning("Redis cache unavailable; falling back to SQLite at %s", config.cache_db_path)
    return SQLiteClaimCache(db_path=config.cache_db_path)


class SingleFlightCoalescer:
    """Single-Flight request coalescer preventing thundering-herd duplicate external API calls."""

    def __init__(self):
        self._in_flight: Dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()

    async def do(self, key: str, coroutine_func, *args, **kwargs) -> Any:
        async with self._lock:
            if key in self._in_flight:
                fut = self._in_flight[key]
                # Await in-flight verification
                return await fut

            loop = asyncio.get_running_loop()
            fut = loop.create_future()
            self._in_flight[key] = fut

        try:
            result = await coroutine_func(*args, **kwargs)
            if not fut.done():
                fut.set_result(result)
            return result
        except Exception as e:
            if not fut.done():
                fut.set_exception(e)
            raise e
        finally:
            async with self._lock:
                self._in_flight.pop(key, None)


single_flight = SingleFlightCoalescer()
