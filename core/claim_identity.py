"""Stable, context-aware identities for factual claims."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Iterable
from typing import Optional


def normalize_claim_text(text: str) -> str:
    """Normalize case and whitespace while retaining all factual detail.

    Punctuation, numbers, dates, versions, and currency symbols are retained so
    claims that differ in a meaningful value do not collapse to one identity.
    """
    if not isinstance(text, str):
        raise TypeError("claim text must be a string")
    normalized = unicodedata.normalize("NFKC", text)
    return " ".join(normalized.casefold().split())


def _normalize_context(value: Optional[str]) -> str:
    return normalize_claim_text(value or "")


def _normalize_entities(entities: Optional[str | Iterable[str]]) -> list[str]:
    if entities is None:
        return []
    values = [entities] if isinstance(entities, str) else entities
    normalized = {_normalize_context(value) for value in values if value and value.strip()}
    return sorted(normalized)


def claim_fingerprint(
    text: str,
    category: str = "other",
    *,
    entities: Optional[str | Iterable[str]] = None,
    jurisdiction: Optional[str] = None,
) -> str:
    """Return a deterministic SHA-256 identity for claim text and context."""
    normalized_text = normalize_claim_text(text)
    if not normalized_text:
        raise ValueError("claim text must not be empty")
    normalized_category = _normalize_context(category)
    if not normalized_category:
        raise ValueError("claim category must not be empty")

    identity = {
        "category": normalized_category,
        "entities": _normalize_entities(entities),
        "jurisdiction": _normalize_context(jurisdiction),
        "text": normalized_text,
    }
    canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
