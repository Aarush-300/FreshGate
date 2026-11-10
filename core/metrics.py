"""Prometheus Metrics Collector for FreshGate (Phase 6).

Tracks gateway operational metrics:
- freshgate_requests_total
- freshgate_claims_extracted_total
- freshgate_claims_checked_total
- freshgate_claims_stale_detected_total
- freshgate_cache_hits_total
- freshgate_cache_misses_total
- freshgate_search_api_failures_total
- freshgate_verification_latency_seconds_sum
- freshgate_verification_latency_seconds_count
"""

import threading
import time
from typing import Dict, Any


class MetricsCollector:
    """Thread-safe Prometheus metrics collector for gateway observability."""

    def __init__(self):
        self._lock = threading.Lock()
        self.requests_total = 0
        self.claims_extracted_total = 0
        self.claims_checked_total = 0
        self.claims_stale_detected_total = 0
        self.cache_hits_total = 0
        self.cache_misses_total = 0
        self.search_api_failures_total = 0
        self.latency_seconds_sum = 0.0
        self.latency_seconds_count = 0

    def inc_requests(self, count: int = 1):
        with self._lock:
            self.requests_total += count

    def inc_claims_extracted(self, count: int = 1):
        with self._lock:
            self.claims_extracted_total += count

    def inc_claims_checked(self, count: int = 1):
        with self._lock:
            self.claims_checked_total += count

    def inc_claims_stale(self, count: int = 1):
        with self._lock:
            self.claims_stale_detected_total += count

    def inc_cache_hit(self, count: int = 1):
        with self._lock:
            self.cache_hits_total += count

    def inc_cache_miss(self, count: int = 1):
        with self._lock:
            self.cache_misses_total += count

    def inc_search_failure(self, count: int = 1):
        with self._lock:
            self.search_api_failures_total += count

    def observe_latency(self, seconds: float):
        with self._lock:
            self.latency_seconds_sum += seconds
            self.latency_seconds_count += 1

    def to_prometheus_format(self) -> str:
        with self._lock:
            lines = [
                "# HELP freshgate_requests_total Total number of chat completion proxy requests",
                "# TYPE freshgate_requests_total counter",
                f"freshgate_requests_total {self.requests_total}",
                "",
                "# HELP freshgate_claims_extracted_total Total atomic factual claims extracted",
                "# TYPE freshgate_claims_extracted_total counter",
                f"freshgate_claims_extracted_total {self.claims_extracted_total}",
                "",
                "# HELP freshgate_claims_checked_total Total claims checked against web evidence",
                "# TYPE freshgate_claims_checked_total counter",
                f"freshgate_claims_checked_total {self.claims_checked_total}",
                "",
                "# HELP freshgate_claims_stale_detected_total Total outdated/contradicted claims flagged",
                "# TYPE freshgate_claims_stale_detected_total counter",
                f"freshgate_claims_stale_detected_total {self.claims_stale_detected_total}",
                "",
                "# HELP freshgate_cache_hits_total Total verification cache hits",
                "# TYPE freshgate_cache_hits_total counter",
                f"freshgate_cache_hits_total {self.cache_hits_total}",
                "",
                "# HELP freshgate_cache_misses_total Total verification cache misses",
                "# TYPE freshgate_cache_misses_total counter",
                f"freshgate_cache_misses_total {self.cache_misses_total}",
                "",
                "# HELP freshgate_search_api_failures_total Total Tavily or search provider failures",
                "# TYPE freshgate_search_api_failures_total counter",
                f"freshgate_search_api_failures_total {self.search_api_failures_total}",
                "",
                "# HELP freshgate_verification_latency_seconds Verification latency in seconds",
                "# TYPE freshgate_verification_latency_seconds summary",
                f"freshgate_verification_latency_seconds_sum {self.latency_seconds_sum:.4f}",
                f"freshgate_verification_latency_seconds_count {self.latency_seconds_count}",
            ]
            return "\n".join(lines) + "\n"

    def get_summary(self) -> Dict[str, Any]:
        with self._lock:
            avg_latency_ms = (
                (self.latency_seconds_sum / self.latency_seconds_count * 1000)
                if self.latency_seconds_count > 0
                else 0.0
            )
            total_cache = self.cache_hits_total + self.cache_misses_total
            hit_rate = (self.cache_hits_total / total_cache * 100) if total_cache > 0 else 0.0
            return {
                "requests_total": self.requests_total,
                "claims_extracted": self.claims_extracted_total,
                "claims_checked": self.claims_checked_total,
                "claims_stale": self.claims_stale_detected_total,
                "cache_hits": self.cache_hits_total,
                "cache_misses": self.cache_misses_total,
                "cache_hit_rate_pct": round(hit_rate, 1),
                "search_failures": self.search_api_failures_total,
                "avg_latency_ms": round(avg_latency_ms, 2),
            }


metrics = MetricsCollector()
