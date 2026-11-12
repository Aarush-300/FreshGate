"""FreshGate Centralized Configuration Architecture (Phase 1 & 2).

Manages all environment configurations, credentials, timeouts, limits,
and operational parameters with strict validation, safe diagnostics, and secret protection.
"""

import os
import re
import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass, field
from dotenv import load_dotenv

# Ensure .env is loaded as early as possible when config is imported
load_dotenv()

logger = logging.getLogger("freshgate.config")

# Common placeholder patterns that must never be treated as valid API keys
PLACEHOLDER_KEY_PATTERNS = [
    r"^your[-_].*",
    r"^sk-placeholder.*",
    r"^placeholder.*",
    r"^none$",
    r"^null$",
    r"^test[-_]?key$",
    r"^xxx+$",
    r"^\*+$",
]


def is_valid_credential(val: Optional[str]) -> bool:
    """Validate whether an API credential is real and safe for HTTP headers.

    Checks:
    - Non-null and non-empty after trimming whitespace.
    - Not a known placeholder pattern.
    - Contains only printable ASCII characters (no \r, \n, or control chars that corrupt HTTP headers).
    """
    if not val:
        return False
    clean = val.strip()
    if not clean:
        return False

    # Check for invalid header characters
    try:
        clean.encode("ascii")
    except UnicodeEncodeError:
        return False

    if any(c in clean for c in ("\r", "\n", "\0", "\t")):
        return False

    # Check against known placeholders
    clean_lower = clean.lower()
    for pattern in PLACEHOLDER_KEY_PATTERNS:
        if re.match(pattern, clean_lower):
            return False

    # Basic length check: real OpenAI / Tavily keys are typically at least 15 characters
    if len(clean) < 8:
        return False

    return True


def mask_secret(secret: Optional[str]) -> str:
    """Safely mask a secret for logging or diagnostics without leaking credentials."""
    if not secret or not secret.strip():
        return "[NOT CONFIGURED]"
    clean = secret.strip()
    if len(clean) <= 8:
        return "****"
    return f"{clean[:4]}...{clean[-4:]}"


@dataclass
class FreshGateConfig:
    """Validated, centralized FreshGate configuration."""

    # LLM Provider Configuration (OpenAI-compatible)
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    is_openai_configured: bool = False

    # Tavily Search Configuration
    tavily_api_key: str = ""
    tavily_base_url: str = "https://api.tavily.com/search"
    is_tavily_configured: bool = False

    # Upstream AI Gateway Configuration
    bifrost_upstream_url: str = "http://127.0.0.1:8080"

    # Granular Timeouts (seconds)
    upstream_timeout: float = 60.0
    extraction_timeout: float = 4.0
    search_timeout: float = 5.0
    judging_timeout: float = 5.0
    freshgate_timeout: float = 8.0

    # Retry and Backoff Settings
    max_retries: int = 3
    max_retry_duration_seconds: float = 12.0

    # Concurrency and Queue Limits
    max_global_concurrency: int = 10
    max_extraction_concurrency: int = 5
    max_judging_concurrency: int = 5
    max_pending_queue: int = 50
    queue_timeout_seconds: float = 8.0

    # Cache and Storage
    cache_db_path: str = "freshgate_cache.db"
    feature_db_path: str = "freshgate_features.db"
    watchtower_enabled: bool = True
    watchtower_poll_seconds: int = 10
    watchtower_max_concurrency: int = 2
    watchtower_max_monitored_claims: int = 1000
    watchtower_lease_seconds: int = 900
    watchtower_max_retries: int = 3
    watchtower_retry_base_seconds: int = 60
    redis_url: Optional[str] = None

    # Security and Policy
    admin_key: str = ""
    rate_limit_per_minute: int = 60
    max_body_size_bytes: int = 1048576  # 1 MB

    # Gateway Execution Mode (shadow, warn, enforce)
    freshgate_mode: str = "enforce"
    fail_open: bool = True
    async_mode: bool = False
    min_score: int = 0
    block_stale: bool = False

    def get_safe_diagnostics(self) -> Dict[str, Any]:
        """Return diagnostic view of configuration with all secrets masked."""
        return {
            "openai": {
                "configured": self.is_openai_configured,
                "base_url": self.openai_base_url,
                "model": self.llm_model,
                "key_preview": mask_secret(self.openai_api_key) if self.is_openai_configured else "[MISSING]",
            },
            "tavily": {
                "configured": self.is_tavily_configured,
                "base_url": self.tavily_base_url,
                "key_preview": mask_secret(self.tavily_api_key) if self.is_tavily_configured else "[MISSING]",
            },
            "timeouts": {
                "upstream": self.upstream_timeout,
                "extraction": self.extraction_timeout,
                "search": self.search_timeout,
                "judging": self.judging_timeout,
                "gateway_total": self.freshgate_timeout,
            },
            "concurrency": {
                "max_global": self.max_global_concurrency,
                "max_extraction": self.max_extraction_concurrency,
                "max_judging": self.max_judging_concurrency,
                "max_queue": self.max_pending_queue,
            },
            "gateway": {
                "mode": self.freshgate_mode,
                "fail_open": self.fail_open,
                "rate_limit_per_minute": self.rate_limit_per_minute,
            },
        }


def load_config(override_env: Optional[Dict[str, str]] = None) -> FreshGateConfig:
    """Load, validate, and return the centralized FreshGate configuration.

    Reads from optional override_env, or os.environ with fallback to .env file.
    """
    env = dict(os.environ)
    if override_env:
        env.update(override_env)

    # 1. OpenAI credentials validation
    raw_openai_key = (env.get("OPENAI_API_KEY") or "").strip()
    is_openai_ok = is_valid_credential(raw_openai_key)
    openai_key = raw_openai_key if is_openai_ok else ""

    openai_base_url = (env.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    llm_model = (env.get("LLM_MODEL") or "gpt-4o-mini").strip()

    # 2. Tavily credentials validation
    raw_tavily_key = (env.get("TAVILY_API_KEY") or "").strip()
    is_tavily_ok = is_valid_credential(raw_tavily_key)
    tavily_key = raw_tavily_key if is_tavily_ok else ""
    tavily_base_url = (env.get("TAVILY_BASE_URL") or "https://api.tavily.com/search").rstrip("/")

    # 3. Upstream & Timeouts
    upstream_url = (env.get("BIFROST_UPSTREAM_URL") or "http://127.0.0.1:8080").rstrip("/")

    def _float_env(key: str, default: float) -> float:
        try:
            return float(env.get(key, str(default)))
        except (ValueError, TypeError):
            return default

    def _int_env(key: str, default: int) -> int:
        try:
            return int(env.get(key, str(default)))
        except (ValueError, TypeError):
            return default

    def _bool_env(key: str, default: bool) -> bool:
        v = env.get(key)
        if v is None:
            return default
        return v.strip().lower() in ("true", "1", "yes", "on")

    # Construct validated configuration
    cfg = FreshGateConfig(
        openai_api_key=openai_key,
        openai_base_url=openai_base_url,
        llm_model=llm_model,
        is_openai_configured=is_openai_ok,
        tavily_api_key=tavily_key,
        tavily_base_url=tavily_base_url,
        is_tavily_configured=is_tavily_ok,
        bifrost_upstream_url=upstream_url,
        upstream_timeout=_float_env("FRESHGATE_UPSTREAM_TIMEOUT", 60.0),
        extraction_timeout=_float_env("FRESHGATE_EXTRACTION_TIMEOUT", 4.0),
        search_timeout=_float_env("FRESHGATE_SEARCH_TIMEOUT", 5.0),
        judging_timeout=_float_env("FRESHGATE_JUDGING_TIMEOUT", 5.0),
        freshgate_timeout=_float_env("FRESHGATE_TIMEOUT", 8.0),
        max_retries=_int_env("FRESHGATE_MAX_RETRIES", 3),
        max_retry_duration_seconds=_float_env("FRESHGATE_MAX_RETRY_DURATION", 12.0),
        max_global_concurrency=_int_env("FRESHGATE_MAX_GLOBAL_CONCURRENCY", 10),
        max_extraction_concurrency=_int_env("FRESHGATE_MAX_EXTRACTION_CONCURRENCY", 5),
        max_judging_concurrency=_int_env("FRESHGATE_MAX_JUDGING_CONCURRENCY", 5),
        max_pending_queue=_int_env("FRESHGATE_MAX_PENDING_QUEUE", 50),
        queue_timeout_seconds=_float_env("FRESHGATE_QUEUE_TIMEOUT", 8.0),
        cache_db_path=env.get("FRESHGATE_CACHE_DB") or env.get("CACHE_DB_PATH", "freshgate_cache.db"),
        feature_db_path=env.get("FRESHGATE_FEATURE_DB", "freshgate_features.db"),
        watchtower_enabled=_bool_env("FRESHGATE_WATCHTOWER_ENABLED", True),
        watchtower_poll_seconds=max(1, min(300, _int_env("FRESHGATE_WATCHTOWER_POLL_SECONDS", 10))),
        watchtower_max_concurrency=max(1, min(10, _int_env("FRESHGATE_WATCHTOWER_MAX_CONCURRENCY", 2))),
        watchtower_max_monitored_claims=max(1, min(10000, _int_env("FRESHGATE_WATCHTOWER_MAX_CLAIMS", 1000))),
        watchtower_lease_seconds=max(60, min(3600, _int_env("FRESHGATE_WATCHTOWER_LEASE_SECONDS", 900))),
        watchtower_max_retries=max(1, min(10, _int_env("FRESHGATE_WATCHTOWER_MAX_RETRIES", 3))),
        watchtower_retry_base_seconds=max(5, min(3600, _int_env("FRESHGATE_WATCHTOWER_RETRY_BASE_SECONDS", 60))),
        redis_url=env.get("REDIS_URL"),
        admin_key=(env.get("FRESHGATE_ADMIN_KEY") or "").strip(),
        rate_limit_per_minute=_int_env("FRESHGATE_RATE_LIMIT_PER_MINUTE", 60),
        max_body_size_bytes=_int_env("FRESHGATE_MAX_BODY_SIZE_BYTES", 1048576),
        freshgate_mode=(env.get("FRESHGATE_MODE") or "enforce").strip().lower(),
        fail_open=_bool_env("FRESHGATE_FAIL_OPEN", True),
        async_mode=_bool_env("FRESHGATE_ASYNC_MODE", False),
        min_score=_int_env("FRESHGATE_MIN_SCORE", 0),
        block_stale=_bool_env("FRESHGATE_BLOCK_STALE", False),
    )

    if not is_openai_ok:
        logger.warning(
            "No server-side OPENAI_API_KEY is configured. Live requests must supply X-OpenAI-API-Key or use heuristic fallback."
        )
    if not is_tavily_ok:
        logger.warning(
            "No server-side TAVILY_API_KEY is configured. Live requests must supply X-Tavily-API-Key for web search."
        )

    return cfg


# Active global configuration instance
_active_config: Optional[FreshGateConfig] = None


def get_config() -> FreshGateConfig:
    """Return the active FreshGate configuration singleton, loading it if not yet initialized."""
    global _active_config
    if _active_config is None:
        _active_config = load_config()
    return _active_config


def reload_config(override_env: Optional[Dict[str, str]] = None) -> FreshGateConfig:
    """Reload configuration from environment (useful for testing or dynamic credential reload)."""
    global _active_config
    _active_config = load_config(override_env)
    return _active_config
