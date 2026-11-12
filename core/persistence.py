"""Durable SQLite storage for policies, monitoring, history, and alerts.

This store is deliberately separate from the expiring claim cache. Verification
snapshots are append-only and survive cache refreshes and expiration.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from core.claim_identity import claim_fingerprint
from core.config import get_config

SCHEMA_VERSION = 2


class FeatureStoreError(Exception):
    """Base error for feature-store operations."""


class SQLiteFeatureStore:
    """Small transaction-safe SQLite repository for FreshGate feature data."""

    def __init__(self, db_path: Optional[str] = None):
        configured_path = db_path or get_config().feature_db_path
        self.db_path = str(configured_path)
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0, isolation_level="DEFERRED")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        # WAL + NORMAL keeps the store consistent through process crashes while
        # avoiding a full fsync for every small immutable snapshot write.
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        db_parent = Path(self.db_path).expanduser().parent
        if str(db_parent) not in ("", "."):
            db_parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._connection() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version > SCHEMA_VERSION:
                raise FeatureStoreError(
                    f"Feature database schema {version} is newer than supported schema {SCHEMA_VERSION}"
                )
            if version < 1:
                self._migrate_to_v1(conn)
                version = 1
            if version < 2:
                self._migrate_to_v2(conn)
                version = 2
            conn.execute(f"PRAGMA user_version = {version}")

    @staticmethod
    def _migrate_to_v1(conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS policies (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                configuration_json TEXT NOT NULL DEFAULT '{}',
                enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS monitored_claims (
                id TEXT PRIMARY KEY,
                claim_fingerprint TEXT NOT NULL,
                claim_text TEXT NOT NULL,
                category TEXT NOT NULL,
                policy_id TEXT REFERENCES policies(id) ON DELETE SET NULL,
                monitoring_interval INTEGER NOT NULL CHECK (monitoring_interval > 0),
                next_check_at REAL,
                last_checked_at REAL,
                last_verdict TEXT,
                enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                created_at REAL NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS verification_snapshots (
                id TEXT PRIMARY KEY,
                verification_id TEXT NOT NULL UNIQUE,
                claim_fingerprint TEXT NOT NULL,
                claim_text TEXT NOT NULL,
                category TEXT NOT NULL,
                verdict TEXT,
                freshness_score INTEGER CHECK (freshness_score IS NULL OR freshness_score BETWEEN 0 AND 100),
                sources_json TEXT NOT NULL DEFAULT '[]',
                policy_id TEXT,
                checked_at REAL NOT NULL,
                cached INTEGER NOT NULL DEFAULT 0 CHECK (cached IN (0, 1)),
                verification_duration REAL,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS watchtower_alerts (
                id TEXT PRIMARY KEY,
                monitored_claim_id TEXT REFERENCES monitored_claims(id) ON DELETE SET NULL,
                previous_verdict TEXT,
                current_verdict TEXT,
                previous_score INTEGER,
                current_score INTEGER,
                reason TEXT NOT NULL,
                old_evidence_json TEXT NOT NULL DEFAULT '[]',
                new_evidence_json TEXT NOT NULL DEFAULT '[]',
                created_at REAL NOT NULL,
                acknowledged_at REAL,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_monitored_claims_due
                ON monitored_claims(enabled, next_check_at);
            CREATE INDEX IF NOT EXISTS idx_monitored_claims_fingerprint
                ON monitored_claims(claim_fingerprint);
            CREATE INDEX IF NOT EXISTS idx_snapshots_fingerprint_checked
                ON verification_snapshots(claim_fingerprint, checked_at DESC);
            CREATE INDEX IF NOT EXISTS idx_alerts_created
                ON watchtower_alerts(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_alerts_unacknowledged
                ON watchtower_alerts(acknowledged_at, created_at DESC);

            CREATE TRIGGER IF NOT EXISTS verification_snapshots_no_update
            BEFORE UPDATE ON verification_snapshots
            BEGIN
                SELECT RAISE(ABORT, 'verification snapshots are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS verification_snapshots_no_delete
            BEFORE DELETE ON verification_snapshots
            BEGIN
                SELECT RAISE(ABORT, 'verification snapshots are immutable');
            END;
            """
        )

    @staticmethod
    def _migrate_to_v2(conn: sqlite3.Connection) -> None:
        conn.execute("BEGIN IMMEDIATE")
        try:
            monitored = {row[1] for row in conn.execute("PRAGMA table_info(monitored_claims)")}
            for name, definition in (
                ("retry_count", "INTEGER NOT NULL DEFAULT 0"),
                ("last_error", "TEXT"),
                ("last_score", "INTEGER"),
                ("last_successful_at", "REAL"),
                ("lease_token", "TEXT"),
                ("lease_until", "REAL"),
            ):
                if name not in monitored:
                    conn.execute(f"ALTER TABLE monitored_claims ADD COLUMN {name} {definition}")
            alerts = {row[1] for row in conn.execute("PRAGMA table_info(watchtower_alerts)")}
            if "transition_key" not in alerts:
                conn.execute("ALTER TABLE watchtower_alerts ADD COLUMN transition_key TEXT")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_alerts_unresolved_transition "
                "ON watchtower_alerts(monitored_claim_id, transition_key) "
                "WHERE acknowledged_at IS NULL AND transition_key IS NOT NULL"
            )
            conn.execute("PRAGMA user_version = 2")
        except Exception:
            conn.rollback()
            raise

    @staticmethod
    def _json(value: Any) -> str:
        try:
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"value must be JSON serializable: {exc}") from exc

    @staticmethod
    def _row_json(row: Optional[sqlite3.Row], json_columns: tuple[str, ...] = ()) -> Optional[dict[str, Any]]:
        if row is None:
            return None
        result = dict(row)
        for column in json_columns:
            result[column.removesuffix("_json")] = json.loads(result.pop(column))
        for column in ("enabled", "cached"):
            if column in result and result[column] is not None:
                result[column] = bool(result[column])
        return result

    @staticmethod
    def _new_id(value: Optional[str]) -> str:
        return value or str(uuid.uuid4())

    def create_policy(self, policy: dict[str, Any]) -> dict[str, Any]:
        name = str(policy.get("name", "")).strip()
        if not name:
            raise ValueError("policy name is required")
        now = float(policy.get("created_at", time.time()))
        updated = float(policy.get("updated_at", now))
        record = {
            "id": self._new_id(policy.get("id")),
            "name": name,
            "description": str(policy.get("description", "")),
            "configuration": policy.get("configuration", {}),
            "enabled": bool(policy.get("enabled", True)),
            "created_at": now,
            "updated_at": updated,
        }
        with self._lock, self._connection() as conn:
            conn.execute(
                "INSERT INTO policies (id, name, description, configuration_json, enabled, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (record["id"], record["name"], record["description"], self._json(record["configuration"]),
                 int(record["enabled"]), now, updated),
            )
        return record

    def get_policy(self, policy_id: str) -> Optional[dict[str, Any]]:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM policies WHERE id = ?", (policy_id,)).fetchone()
        return self._row_json(row, ("configuration_json",))

    def list_policies(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        _validate_page(limit, offset)
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM policies ORDER BY created_at DESC, id LIMIT ? OFFSET ?", (limit, offset)
            ).fetchall()
        return [self._row_json(row, ("configuration_json",)) for row in rows]  # type: ignore[misc]

    def update_policy(self, policy_id: str, updates: dict[str, Any]) -> Optional[dict[str, Any]]:
        current = self.get_policy(policy_id)
        if current is None:
            return None
        name = str(updates.get("name", current["name"])).strip()
        if not name:
            raise ValueError("policy name is required")
        config = updates.get("configuration", current["configuration"])
        description = str(updates.get("description", current["description"]))
        enabled = bool(updates.get("enabled", current["enabled"]))
        updated_at = float(updates.get("updated_at", time.time()))
        with self._lock, self._connection() as conn:
            conn.execute(
                "UPDATE policies SET name = ?, description = ?, configuration_json = ?, enabled = ?, updated_at = ? "
                "WHERE id = ?",
                (name, description, self._json(config), int(enabled), updated_at, policy_id),
            )
        return self.get_policy(policy_id)

    def delete_policy(self, policy_id: str) -> bool:
        with self._lock, self._connection() as conn:
            cursor = conn.execute("DELETE FROM policies WHERE id = ?", (policy_id,))
            return cursor.rowcount > 0

    def save_monitored_claim(self, claim: dict[str, Any], max_claims: Optional[int] = None) -> dict[str, Any]:
        text = str(claim.get("claim_text", "")).strip()
        category = str(claim.get("category", "other")).strip()
        interval = int(claim.get("monitoring_interval", 0))
        if not text or not category or interval <= 0:
            raise ValueError("claim_text, category, and a positive monitoring_interval are required")
        fingerprint = claim.get("claim_fingerprint") or claim_fingerprint(
            text, category, entities=claim.get("entities"), jurisdiction=claim.get("jurisdiction")
        )
        record = {
            "id": self._new_id(claim.get("id")),
            "claim_fingerprint": fingerprint,
            "claim_text": text,
            "category": category,
            "policy_id": claim.get("policy_id"),
            "monitoring_interval": interval,
            "next_check_at": claim.get("next_check_at", time.time() + interval),
            "last_checked_at": claim.get("last_checked_at"),
            "last_successful_at": claim.get("last_successful_at"),
            "last_verdict": claim.get("last_verdict"),
            "last_score": claim.get("last_score"),
            "retry_count": int(claim.get("retry_count", 0)),
            "last_error": claim.get("last_error"),
            "lease_token": None,
            "lease_until": None,
            "enabled": bool(claim.get("enabled", True)),
            "created_at": float(claim.get("created_at", time.time())),
            "metadata": claim.get("metadata", {}),
        }
        with self._lock, self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if max_claims is not None:
                count = int(conn.execute("SELECT COUNT(*) FROM monitored_claims").fetchone()[0])
                if count >= max_claims:
                    raise OverflowError("Watchtower monitoring capacity has been reached")
            conn.execute(
                "INSERT INTO monitored_claims (id, claim_fingerprint, claim_text, category, policy_id, "
                "monitoring_interval, next_check_at, last_checked_at, last_successful_at, last_verdict, last_score, "
                "retry_count, last_error, enabled, created_at, metadata_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (record["id"], fingerprint, text, category, record["policy_id"], interval,
                 record["next_check_at"], record["last_checked_at"], record["last_successful_at"],
                 record["last_verdict"], record["last_score"],
                 record["retry_count"], record["last_error"],
                 int(record["enabled"]), record["created_at"], self._json(record["metadata"])),
            )
        return record

    def get_monitored_claim(self, claim_id: str) -> Optional[dict[str, Any]]:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM monitored_claims WHERE id = ?", (claim_id,)).fetchone()
        return self._row_json(row, ("metadata_json",))

    def list_monitored_claims(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        _validate_page(limit, offset)
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM monitored_claims ORDER BY created_at DESC, id LIMIT ? OFFSET ?", (limit, offset)
            ).fetchall()
        return [self._row_json(row, ("metadata_json",)) for row in rows]  # type: ignore[misc]

    def count_monitored_claims(self) -> int:
        with self._connection() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM monitored_claims").fetchone()[0])

    def update_monitored_claim(self, claim_id: str, updates: dict[str, Any]) -> Optional[dict[str, Any]]:
        current = self.get_monitored_claim(claim_id)
        if current is None:
            return None
        interval = int(updates.get("monitoring_interval", current["monitoring_interval"]))
        if interval <= 0:
            raise ValueError("monitoring_interval must be positive")
        enabled = bool(updates.get("enabled", current["enabled"]))
        next_check = updates.get("next_check_at", current["next_check_at"])
        if enabled and not current["enabled"] and "next_check_at" not in updates:
            next_check = time.time()
        with self._lock, self._connection() as conn:
            conn.execute(
                "UPDATE monitored_claims SET enabled = ?, monitoring_interval = ?, policy_id = ?, next_check_at = ? "
                "WHERE id = ?",
                (int(enabled), interval, updates.get("policy_id", current["policy_id"]), next_check, claim_id),
            )
        return self.get_monitored_claim(claim_id)

    def delete_monitored_claim(self, claim_id: str) -> bool:
        with self._lock, self._connection() as conn:
            cursor = conn.execute("DELETE FROM monitored_claims WHERE id = ?", (claim_id,))
            return cursor.rowcount > 0

    def claim_due_monitored_claims(
        self, limit: int, lease_seconds: int, now: Optional[float] = None
    ) -> list[dict[str, Any]]:
        """Atomically lease due rows so multiple app instances cannot run them twice."""
        now = float(now if now is not None else time.time())
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT id FROM monitored_claims WHERE enabled = 1 AND next_check_at <= ? "
                "AND (lease_until IS NULL OR lease_until <= ?) ORDER BY next_check_at, id LIMIT ?",
                (now, now, limit),
            ).fetchall()
            claimed = []
            for row in rows:
                token = str(uuid.uuid4())
                cursor = conn.execute(
                    "UPDATE monitored_claims SET lease_token = ?, lease_until = ? WHERE id = ? "
                    "AND (lease_until IS NULL OR lease_until <= ?)",
                    (token, now + lease_seconds, row["id"], now),
                )
                if cursor.rowcount:
                    claimed.append(conn.execute(
                        "SELECT * FROM monitored_claims WHERE id = ?", (row["id"],)
                    ).fetchone())
            return [self._row_json(row, ("metadata_json",)) for row in claimed]  # type: ignore[misc]

    def claim_monitored_claim(
        self, claim_id: str, lease_seconds: int, now: Optional[float] = None, force: bool = False
    ) -> Optional[dict[str, Any]]:
        now = float(now if now is not None else time.time())
        token = str(uuid.uuid4())
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM monitored_claims WHERE id = ? AND enabled = 1 "
                "AND (lease_until IS NULL OR lease_until <= ?) "
                "AND (? = 1 OR next_check_at <= ?)",
                (claim_id, now, int(force), now),
            ).fetchone()
            if row is None:
                return None
            cursor = conn.execute(
                "UPDATE monitored_claims SET lease_token = ?, lease_until = ? WHERE id = ? "
                "AND (lease_until IS NULL OR lease_until <= ?)",
                (token, now + lease_seconds, claim_id, now),
            )
            if not cursor.rowcount:
                return None
            row = conn.execute("SELECT * FROM monitored_claims WHERE id = ?", (claim_id,)).fetchone()
            return self._row_json(row, ("metadata_json",))

    def finish_monitored_claim(
        self,
        claim_id: str,
        lease_token: str,
        *,
        checked_at: float,
        next_check_at: float,
        successful_at: Optional[float] = None,
        verdict: Optional[str] = None,
        score: Optional[int] = None,
        metadata: Optional[dict[str, Any]] = None,
        error: Optional[str] = None,
        retry_count: int = 0,
    ) -> bool:
        with self._lock, self._connection() as conn:
            cursor = conn.execute(
                "UPDATE monitored_claims SET last_checked_at = ?, last_successful_at = COALESCE(?, last_successful_at), "
                "next_check_at = ?, last_verdict = COALESCE(?, last_verdict), "
                "last_score = CASE WHEN ? IS NOT NULL THEN ? ELSE last_score END, metadata_json = COALESCE(?, metadata_json), "
                "last_error = ?, retry_count = ?, lease_token = NULL, lease_until = NULL "
                "WHERE id = ? AND lease_token = ?",
                (checked_at, successful_at, next_check_at, verdict, verdict, score,
                 self._json(metadata) if metadata is not None else None,
                 error, retry_count, claim_id, lease_token),
            )
            return cursor.rowcount > 0

    def save_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        text = str(snapshot.get("claim_text", "")).strip()
        category = str(snapshot.get("category", "other")).strip()
        if not text or not category:
            raise ValueError("claim_text and category are required")
        score = snapshot.get("freshness_score")
        if score is not None and not 0 <= int(score) <= 100:
            raise ValueError("freshness_score must be between 0 and 100")
        record = {
            "id": self._new_id(snapshot.get("id")),
            "verification_id": self._new_id(snapshot.get("verification_id")),
            "claim_fingerprint": snapshot.get("claim_fingerprint") or claim_fingerprint(
                text, category, entities=snapshot.get("entities"), jurisdiction=snapshot.get("jurisdiction")
            ),
            "claim_text": text,
            "category": category,
            "verdict": snapshot.get("verdict"),
            "freshness_score": int(score) if score is not None else None,
            "sources": snapshot.get("sources", []),
            "policy_id": snapshot.get("policy_id"),
            "checked_at": float(snapshot.get("checked_at", time.time())),
            "cached": bool(snapshot.get("cached", False)),
            "verification_duration": snapshot.get("verification_duration"),
            "metadata": snapshot.get("metadata", {}),
        }
        with self._lock, self._connection() as conn:
            conn.execute(
                "INSERT INTO verification_snapshots (id, verification_id, claim_fingerprint, claim_text, category, "
                "verdict, freshness_score, sources_json, policy_id, checked_at, cached, verification_duration, metadata_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (record["id"], record["verification_id"], record["claim_fingerprint"], text, category,
                 record["verdict"], record["freshness_score"], self._json(record["sources"]), record["policy_id"],
                 record["checked_at"], int(record["cached"]), record["verification_duration"],
                 self._json(record["metadata"])),
            )
        return record

    def get_snapshot(self, snapshot_id: str) -> Optional[dict[str, Any]]:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM verification_snapshots WHERE id = ?", (snapshot_id,)).fetchone()
        return self._row_json(row, ("sources_json", "metadata_json"))

    def get_snapshot_by_verification_id(self, verification_id: str) -> Optional[dict[str, Any]]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM verification_snapshots WHERE verification_id = ?", (verification_id,)
            ).fetchone()
        return self._row_json(row, ("sources_json", "metadata_json"))

    def list_snapshots(
        self, fingerprint: str, limit: int = 100, offset: int = 0, newest_first: bool = True
    ) -> list[dict[str, Any]]:
        _validate_page(limit, offset)
        direction = "DESC" if newest_first else "ASC"
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM verification_snapshots WHERE claim_fingerprint = ? "
                f"ORDER BY checked_at {direction}, id {direction} LIMIT ? OFFSET ?", (fingerprint, limit, offset)
            ).fetchall()
        return [self._row_json(row, ("sources_json", "metadata_json")) for row in rows]  # type: ignore[misc]

    def count_snapshots(self, fingerprint: str) -> int:
        with self._connection() as conn:
            return int(conn.execute(
                "SELECT COUNT(*) FROM verification_snapshots WHERE claim_fingerprint = ?", (fingerprint,)
            ).fetchone()[0])

    def save_alert(self, alert: dict[str, Any]) -> dict[str, Any]:
        reason = str(alert.get("reason", "")).strip()
        if not reason:
            raise ValueError("alert reason is required")
        record = {
            "id": self._new_id(alert.get("id")),
            "monitored_claim_id": alert.get("monitored_claim_id"),
            "previous_verdict": alert.get("previous_verdict"),
            "current_verdict": alert.get("current_verdict"),
            "previous_score": alert.get("previous_score"),
            "current_score": alert.get("current_score"),
            "reason": reason,
            "old_evidence": alert.get("old_evidence", []),
            "new_evidence": alert.get("new_evidence", []),
            "created_at": float(alert.get("created_at", time.time())),
            "acknowledged_at": alert.get("acknowledged_at"),
            "metadata": alert.get("metadata", {}),
            "transition_key": alert.get("transition_key"),
        }
        with self._lock, self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if record["transition_key"] is not None and record["acknowledged_at"] is None:
                existing = conn.execute(
                    "SELECT * FROM watchtower_alerts WHERE monitored_claim_id = ? AND transition_key = ? "
                    "AND acknowledged_at IS NULL",
                    (record["monitored_claim_id"], record["transition_key"]),
                ).fetchone()
                if existing is not None:
                    return self._row_json(existing, ("old_evidence_json", "new_evidence_json", "metadata_json"))
            conn.execute(
                "INSERT INTO watchtower_alerts (id, monitored_claim_id, previous_verdict, current_verdict, "
                "previous_score, current_score, reason, old_evidence_json, new_evidence_json, created_at, "
                "acknowledged_at, metadata_json, transition_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (record["id"], record["monitored_claim_id"], record["previous_verdict"], record["current_verdict"],
                 record["previous_score"], record["current_score"], reason, self._json(record["old_evidence"]),
                 self._json(record["new_evidence"]), record["created_at"], record["acknowledged_at"],
                 self._json(record["metadata"]), record["transition_key"]),
            )
        return record

    def get_alert(self, alert_id: str) -> Optional[dict[str, Any]]:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM watchtower_alerts WHERE id = ?", (alert_id,)).fetchone()
        return self._row_json(row, ("old_evidence_json", "new_evidence_json", "metadata_json"))

    def list_alerts(self, limit: int = 100, offset: int = 0, unacknowledged_only: bool = False) -> list[dict[str, Any]]:
        _validate_page(limit, offset)
        where = "WHERE acknowledged_at IS NULL" if unacknowledged_only else ""
        with self._connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM watchtower_alerts {where} ORDER BY created_at DESC, id LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [self._row_json(row, ("old_evidence_json", "new_evidence_json", "metadata_json")) for row in rows]  # type: ignore[misc]

    def acknowledge_alert(self, alert_id: str, acknowledged_at: Optional[float] = None) -> bool:
        with self._lock, self._connection() as conn:
            cursor = conn.execute(
                "UPDATE watchtower_alerts SET acknowledged_at = COALESCE(acknowledged_at, ?) WHERE id = ?",
                (acknowledged_at if acknowledged_at is not None else time.time(), alert_id),
            )
            return cursor.rowcount > 0


def _validate_page(limit: int, offset: int) -> None:
    if not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    if not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be zero or greater")


_store: Optional[SQLiteFeatureStore] = None
_store_lock = threading.Lock()


def get_feature_store() -> SQLiteFeatureStore:
    """Return the process-wide feature store, initializing it on first use."""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = SQLiteFeatureStore()
    return _store


def reset_feature_store(store: Optional[SQLiteFeatureStore] = None) -> None:
    """Replace the process store; intended for tests and controlled startup wiring."""
    global _store
    with _store_lock:
        _store = store
