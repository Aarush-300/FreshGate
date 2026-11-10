"""Multi-Service Circuit Breaker Pattern for FreshGate (V2).

Protects LLM Gateway uptime by maintaining isolated circuit breakers for:
- LLM extraction
- LLM judging
- Tavily search
- Cache storage

Supports CLOSED, OPEN, and HALF_OPEN states with automatic recovery probes,
and prevents cascading failure storms across downstream APIs.
"""

import time
import threading
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("freshgate.circuit_breaker")

STATE_CLOSED = "closed"
STATE_OPEN = "open"
STATE_HALF_OPEN = "half_open"


class ServiceCircuitBreaker:
    """Individual circuit breaker for an isolated downstream service."""

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout_seconds: float = 30.0,
        half_open_probes: int = 2,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds
        self.half_open_probes = half_open_probes

        self._lock = threading.Lock()
        self._state = STATE_CLOSED
        self._consecutive_failures = 0
        self._last_state_change = time.time()
        self._half_open_successes = 0
        self._is_quota_exhausted = False

    def can_execute(self) -> bool:
        """Check if request is permitted to proceed for this specific service."""
        with self._lock:
            if self._is_quota_exhausted:
                # Quota exhaustion requires billing intervention
                return False

            now = time.time()
            if self._state == STATE_OPEN:
                if now - self._last_state_change >= self.recovery_timeout_seconds:
                    logger.info("[%s] Circuit breaker recovery timeout reached; transitioning from OPEN to HALF_OPEN", self.name)
                    self._state = STATE_HALF_OPEN
                    self._last_state_change = now
                    self._half_open_successes = 0
                    return True
                return False
            return True

    def record_success(self):
        """Record successful execution on this service."""
        with self._lock:
            self._is_quota_exhausted = False
            if self._state == STATE_HALF_OPEN:
                self._half_open_successes += 1
                if self._half_open_successes >= self.half_open_probes:
                    logger.info("[%s] Circuit breaker probe succeeded; resetting state to CLOSED", self.name)
                    self._state = STATE_CLOSED
                    self._consecutive_failures = 0
                    self._last_state_change = time.time()
            elif self._state == STATE_CLOSED:
                self._consecutive_failures = 0

    def record_failure(self, is_quota_error: bool = False):
        """Record failed execution or timeout on this service."""
        with self._lock:
            self._consecutive_failures += 1
            now = time.time()

            if is_quota_error:
                self._is_quota_exhausted = True
                self._state = STATE_OPEN
                self._last_state_change = now
                logger.error("[%s] Permanent quota exhaustion detected; immediately tripping circuit breaker to OPEN", self.name)
                return

            if self._state == STATE_HALF_OPEN:
                logger.warning("[%s] Probe failed during HALF_OPEN state; reopening circuit breaker", self.name)
                self._state = STATE_OPEN
                self._last_state_change = now
            elif self._state == STATE_CLOSED and self._consecutive_failures >= self.failure_threshold:
                logger.error(
                    "[%s] Failure threshold reached (%d failures); tripping circuit breaker to OPEN",
                    self.name,
                    self._consecutive_failures,
                )
                self._state = STATE_OPEN
                self._last_state_change = now

    def reset(self):
        """Manually force reset to CLOSED."""
        with self._lock:
            self._state = STATE_CLOSED
            self._consecutive_failures = 0
            self._is_quota_exhausted = False
            self._last_state_change = time.time()

    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "name": self.name,
                "state": self._state,
                "consecutive_failures": self._consecutive_failures,
                "failure_threshold": self.failure_threshold,
                "recovery_timeout_sec": self.recovery_timeout_seconds,
                "quota_exhausted": self._is_quota_exhausted,
            }


class MultiServiceCircuitBreaker:
    """Composite circuit breaker managing isolated breakers for all gateway subsystems.

    Preserves 100% backward compatibility with CircuitBreaker while adding per-service isolation.
    """

    def __init__(self, failure_threshold: int = 5, recovery_timeout_seconds: float = 30.0):
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds
        self.services: Dict[str, ServiceCircuitBreaker] = {
            "gateway": ServiceCircuitBreaker("gateway", failure_threshold, recovery_timeout_seconds),
            "llm_extraction": ServiceCircuitBreaker("llm_extraction", failure_threshold, recovery_timeout_seconds),
            "llm_judging": ServiceCircuitBreaker("llm_judging", failure_threshold, recovery_timeout_seconds),
            "tavily_search": ServiceCircuitBreaker("tavily_search", failure_threshold, recovery_timeout_seconds),
            "cache_storage": ServiceCircuitBreaker("cache_storage", failure_threshold, recovery_timeout_seconds),
        }

    def can_execute(self, service: str = "gateway") -> bool:
        breaker = self.services.get(service) or self.services["gateway"]
        # If overall gateway or the specific service is OPEN, block
        if not self.services["gateway"].can_execute():
            return False
        return breaker.can_execute()

    def record_success(self, service: str = "gateway"):
        if service in self.services:
            self.services[service].record_success()
        self.services["gateway"].record_success()

    def record_failure(self, service: str = "gateway", is_quota_error: bool = False):
        if service in self.services:
            self.services[service].record_failure(is_quota_error=is_quota_error)
        self.services["gateway"].record_failure(is_quota_error=is_quota_error)

    def reset(self, service: Optional[str] = None):
        if service and service in self.services:
            self.services[service].reset()
        else:
            for b in self.services.values():
                b.reset()

    def reset_all(self):
        self.reset()

    def get_state(self, service: str = "gateway") -> Dict[str, Any]:
        breaker = self.services.get(service) or self.services["gateway"]
        return breaker.get_state()

    def get_all_states(self) -> Dict[str, Any]:
        return {name: b.get_state() for name, b in self.services.items()}


# Global singleton instance
circuit_breaker = MultiServiceCircuitBreaker()
CircuitBreaker = MultiServiceCircuitBreaker
