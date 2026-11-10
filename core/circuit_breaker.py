"""Circuit Breaker Pattern for FreshGate (Phase 8).

Protects LLM Gateway uptime by automatically bypassing verification if Tavily
or LLM Judge error rates exceed configurable thresholds, self-healing via half-open probes.
"""

import time
import threading
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

STATE_CLOSED = "closed"
STATE_OPEN = "open"
STATE_HALF_OPEN = "half_open"


class CircuitBreaker:
    """Thread-safe circuit breaker preventing cascading failures in downstream services."""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout_seconds: float = 30.0,
        half_open_probes: int = 2,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds
        self.half_open_probes = half_open_probes

        self._lock = threading.Lock()
        self._state = STATE_CLOSED
        self._consecutive_failures = 0
        self._last_state_change = time.time()
        self._half_open_successes = 0

    def can_execute(self) -> bool:
        """Check if request is permitted to proceed with verification."""
        with self._lock:
            now = time.time()
            if self._state == STATE_OPEN:
                if now - self._last_state_change >= self.recovery_timeout_seconds:
                    logger.info("Circuit breaker recovery timeout reached; transitioning from OPEN to HALF_OPEN")
                    self._state = STATE_HALF_OPEN
                    self._last_state_change = now
                    self._half_open_successes = 0
                    return True
                return False
            return True

    def record_success(self):
        """Record successful verification execution."""
        with self._lock:
            if self._state == STATE_HALF_OPEN:
                self._half_open_successes += 1
                if self._half_open_successes >= self.half_open_probes:
                    logger.info("Circuit breaker probe succeeded; resetting state to CLOSED")
                    self._state = STATE_CLOSED
                    self._consecutive_failures = 0
                    self._last_state_change = time.time()
            elif self._state == STATE_CLOSED:
                self._consecutive_failures = 0

    def record_failure(self):
        """Record failed or timed-out verification execution."""
        with self._lock:
            self._consecutive_failures += 1
            now = time.time()
            if self._state == STATE_HALF_OPEN:
                logger.warning("Probe failed during HALF_OPEN state; reopening circuit breaker")
                self._state = STATE_OPEN
                self._last_state_change = now
            elif self._state == STATE_CLOSED and self._consecutive_failures >= self.failure_threshold:
                logger.error(
                    "Failure threshold reached (%d failures); tripping circuit breaker to OPEN",
                    self._consecutive_failures,
                )
                self._state = STATE_OPEN
                self._last_state_change = now

    def reset(self):
        """Manually force reset to CLOSED."""
        with self._lock:
            self._state = STATE_CLOSED
            self._consecutive_failures = 0
            self._last_state_change = time.time()

    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "state": self._state,
                "consecutive_failures": self._consecutive_failures,
                "failure_threshold": self.failure_threshold,
                "recovery_timeout_sec": self.recovery_timeout_seconds,
            }


circuit_breaker = CircuitBreaker()
