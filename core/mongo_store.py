"""MongoDB feature store implementation with per-owner scoping."""
from __future__ import annotations

import time
import uuid
from typing import Any, Optional

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.database import Database

from core.claim_identity import claim_fingerprint
from core.mongo import get_db
from core.persistence import _validate_page

_ANY = object()


class MongoFeatureStore:
    """Owner-scoped MongoDB feature store repository."""

    def __init__(self, owner_id: Any = _ANY, db: Optional[Database] = None):
        self.owner_id = owner_id
        self.db: Database = db if db is not None else get_db()

    def for_owner(self, owner_id: str | None) -> MongoFeatureStore:
        return MongoFeatureStore(owner_id=owner_id, db=self.db)

    def _scope(self, extra: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        q: dict[str, Any] = {} if self.owner_id is _ANY else {"owner_id": self.owner_id}
        if extra:
            q.update(extra)
        return q

    @property
    def _write_owner(self) -> Optional[str]:
        return None if self.owner_id is _ANY else self.owner_id

    @staticmethod
    def _clean(doc: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if doc is None:
            return None
        res = dict(doc)
        res.pop("_id", None)
        res.pop("owner_id", None)
        return res

    @staticmethod
    def _new_id(value: Optional[str]) -> str:
        return value or str(uuid.uuid4())

    # --- Policies ---

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
        self.db.policies.insert_one({**record, "owner_id": self._write_owner})
        return record

    def get_policy(self, policy_id: str) -> Optional[dict[str, Any]]:
        doc = self.db.policies.find_one(self._scope({"id": policy_id}))
        return self._clean(doc)

    def list_policies(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        _validate_page(limit, offset)
        cursor = (
            self.db.policies.find(self._scope())
            .sort([("created_at", DESCENDING), ("id", ASCENDING)])
            .skip(offset)
            .limit(limit)
        )
        return [self._clean(d) for d in cursor]  # type: ignore[misc]

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
        self.db.policies.update_one(
            self._scope({"id": policy_id}),
            {
                "$set": {
                    "name": name,
                    "description": description,
                    "configuration": config,
                    "enabled": enabled,
                    "updated_at": updated_at,
                }
            },
        )
        return self.get_policy(policy_id)

    def delete_policy(self, policy_id: str) -> bool:
        res = self.db.policies.delete_one(self._scope({"id": policy_id}))
        return res.deleted_count > 0

    # --- Monitored Claims ---

    def save_monitored_claim(
        self, claim: dict[str, Any], max_claims: Optional[int] = None
    ) -> dict[str, Any]:
        text = str(claim.get("claim_text", "")).strip()
        category = str(claim.get("category", "other")).strip()
        interval = int(claim.get("monitoring_interval", 0))
        if not text or not category or interval <= 0:
            raise ValueError("claim_text, category, and a positive monitoring_interval are required")
        if max_claims is not None and self.db.monitored_claims.count_documents(self._scope()) >= max_claims:
            raise OverflowError("Watchtower monitoring capacity has been reached")
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
            "next_check_at": float(claim.get("next_check_at", time.time() + interval)),
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
        self.db.monitored_claims.insert_one({**record, "owner_id": self._write_owner})
        return record

    def get_monitored_claim(self, claim_id: str) -> Optional[dict[str, Any]]:
        doc = self.db.monitored_claims.find_one(self._scope({"id": claim_id}))
        return self._clean(doc)

    def list_monitored_claims(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        _validate_page(limit, offset)
        cursor = (
            self.db.monitored_claims.find(self._scope())
            .sort([("created_at", DESCENDING), ("id", ASCENDING)])
            .skip(offset)
            .limit(limit)
        )
        return [self._clean(d) for d in cursor]  # type: ignore[misc]

    def count_monitored_claims(self) -> int:
        return self.db.monitored_claims.count_documents(self._scope())

    def update_monitored_claim(
        self, claim_id: str, updates: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
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
        self.db.monitored_claims.update_one(
            self._scope({"id": claim_id}),
            {
                "$set": {
                    "enabled": enabled,
                    "monitoring_interval": interval,
                    "policy_id": updates.get("policy_id", current["policy_id"]),
                    "next_check_at": next_check,
                }
            },
        )
        return self.get_monitored_claim(claim_id)

    def delete_monitored_claim(self, claim_id: str) -> bool:
        res = self.db.monitored_claims.delete_one(self._scope({"id": claim_id}))
        if res.deleted_count > 0:
            self.db.watchtower_alerts.delete_many(self._scope({"monitored_claim_id": claim_id}))
            return True
        return False

    def claim_due_monitored_claims(
        self, limit: int, lease_seconds: int, now: Optional[float] = None
    ) -> list[dict[str, Any]]:
        if self.owner_id is not _ANY:
            raise RuntimeError("claim_due_monitored_claims must be called on the root store")
        now = float(now if now is not None else time.time())
        claimed = []
        for _ in range(limit):
            token = str(uuid.uuid4())
            doc = self.db.monitored_claims.find_one_and_update(
                {
                    "enabled": True,
                    "next_check_at": {"$lte": now},
                    "$or": [{"lease_until": None}, {"lease_until": {"$lte": now}}],
                },
                {"$set": {"lease_token": token, "lease_until": now + lease_seconds}},
                sort=[("next_check_at", ASCENDING)],
                return_document=ReturnDocument.AFTER,
            )
            if doc is None:
                break
            doc = dict(doc)
            doc.pop("_id", None)
            claimed.append(doc)
        return claimed

    def claim_monitored_claim(
        self, claim_id: str, lease_seconds: int, now: Optional[float] = None, force: bool = False
    ) -> Optional[dict[str, Any]]:
        now = float(now if now is not None else time.time())
        token = str(uuid.uuid4())
        filter_q: dict[str, Any] = {
            "id": claim_id,
            "enabled": True,
            "$or": [{"lease_until": None}, {"lease_until": {"$lte": now}}],
        }
        if not force:
            filter_q["next_check_at"] = {"$lte": now}
        doc = self.db.monitored_claims.find_one_and_update(
            self._scope(filter_q),
            {"$set": {"lease_token": token, "lease_until": now + lease_seconds}},
            return_document=ReturnDocument.AFTER,
        )
        if doc is None:
            return None
        doc = dict(doc)
        doc.pop("_id", None)
        return doc

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
        set_fields: dict[str, Any] = {
            "last_checked_at": checked_at,
            "next_check_at": next_check_at,
            "last_error": error,
            "retry_count": retry_count,
            "lease_token": None,
            "lease_until": None,
        }
        if successful_at is not None:
            set_fields["last_successful_at"] = successful_at
        if verdict is not None:
            set_fields["last_verdict"] = verdict
        if score is not None:
            set_fields["last_score"] = score
        if metadata is not None:
            set_fields["metadata"] = metadata

        res = self.db.monitored_claims.update_one(
            {"id": claim_id, "lease_token": lease_token},
            {"$set": set_fields},
        )
        return res.modified_count > 0

    # --- Verification Snapshots ---

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
        self.db.verification_snapshots.insert_one({**record, "owner_id": self._write_owner})
        return record

    def get_snapshot(self, snapshot_id: str) -> Optional[dict[str, Any]]:
        doc = self.db.verification_snapshots.find_one(self._scope({"id": snapshot_id}))
        return self._clean(doc)

    def get_snapshot_by_verification_id(self, verification_id: str) -> Optional[dict[str, Any]]:
        doc = self.db.verification_snapshots.find_one(
            self._scope({"verification_id": verification_id})
        )
        return self._clean(doc)

    def list_snapshots(
        self, fingerprint: str, limit: int = 100, offset: int = 0, newest_first: bool = True
    ) -> list[dict[str, Any]]:
        _validate_page(limit, offset)
        direction = DESCENDING if newest_first else ASCENDING
        cursor = (
            self.db.verification_snapshots.find(self._scope({"claim_fingerprint": fingerprint}))
            .sort([("checked_at", direction), ("id", direction)])
            .skip(offset)
            .limit(limit)
        )
        return [self._clean(d) for d in cursor]  # type: ignore[misc]

    def count_snapshots(self, fingerprint: str) -> int:
        return self.db.verification_snapshots.count_documents(
            self._scope({"claim_fingerprint": fingerprint})
        )

    # --- Alerts ---

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
        if record["transition_key"] is not None and record["acknowledged_at"] is None:
            existing = self.db.watchtower_alerts.find_one(
                self._scope(
                    {
                        "monitored_claim_id": record["monitored_claim_id"],
                        "transition_key": record["transition_key"],
                        "acknowledged_at": None,
                    }
                )
            )
            if existing is not None:
                return self._clean(existing)
        self.db.watchtower_alerts.insert_one({**record, "owner_id": self._write_owner})
        return record

    def get_alert(self, alert_id: str) -> Optional[dict[str, Any]]:
        doc = self.db.watchtower_alerts.find_one(self._scope({"id": alert_id}))
        return self._clean(doc)

    def list_alerts(
        self, limit: int = 100, offset: int = 0, unacknowledged_only: bool = False
    ) -> list[dict[str, Any]]:
        _validate_page(limit, offset)
        q: dict[str, Any] = {"acknowledged_at": None} if unacknowledged_only else {}
        cursor = (
            self.db.watchtower_alerts.find(self._scope(q))
            .sort([("created_at", DESCENDING)])
            .skip(offset)
            .limit(limit)
        )
        return [self._clean(d) for d in cursor]  # type: ignore[misc]

    def acknowledge_alert(self, alert_id: str, acknowledged_at: Optional[float] = None) -> bool:
        doc = self.db.watchtower_alerts.find_one(self._scope({"id": alert_id}))
        if doc is None:
            return False
        if doc.get("acknowledged_at") is None:
            ack = acknowledged_at if acknowledged_at is not None else time.time()
            self.db.watchtower_alerts.update_one(
                self._scope({"id": alert_id}),
                {"$set": {"acknowledged_at": ack}},
            )
        return True

    def get_latest_snapshot(self, fingerprint: str) -> Optional[dict[str, Any]]:
        doc = self.db.verification_snapshots.find_one(
            self._scope({"claim_fingerprint": fingerprint}),
            sort=[("checked_at", DESCENDING)],
        )
        return self._clean(doc)

    def get_alerts_for_claim(self, claim_id: str, limit: int = 5) -> list[dict[str, Any]]:
        cursor = (
            self.db.watchtower_alerts.find(self._scope({"monitored_claim_id": claim_id}))
            .sort([("created_at", DESCENDING)])
            .limit(limit)
        )
        return [self._clean(d) for d in cursor]  # type: ignore[misc]

    def update_user_metadata(self, user_id: str, metadata: dict[str, Any]) -> None:
        self.db.users.update_one(
            {"_id": user_id},
            {"$set": {f"metadata.{k}": v for k, v in metadata.items()}},
            upsert=False,
        )

    def get_user_metadata(self, user_id: str) -> dict[str, Any]:
        doc = self.db.users.find_one({"_id": user_id}, {"metadata": 1})
        return doc.get("metadata", {}) if doc else {}
