# FreshGate: Monitoring & Incident Response Runbook

## 1. Key Metrics & Alert Thresholds

Scraped from `GET /metrics`:

| Metric Name | Type | Warning Threshold | Critical Threshold | Action Required |
| :--- | :--- | :--- | :--- | :--- |
| `freshgate_search_api_failures_total` | Counter | $> 5 \text{ / min}$ | $> 20 \text{ / min}$ | Check Tavily API quota & network route |
| `freshgate_verification_latency_seconds_sum` | Summary | Avg $> 2.5\text{s}$ | Avg $> 3.8\text{s}$ | Check external LLM latency; scale workers |
| `freshgate_cache_hits_total` | Counter | Hit Rate $< 70\%$ | Hit Rate $< 50\%$ | Inspect cache expiration & Redis connectivity |
| `freshgate_requests_total` | Counter | Rate drop $> 50\%$ | Zero requests | Check gateway upstream connectivity |

---

## 2. Incident Playbooks

### Playbook A: High Fail-Open Rate (`X-FreshGate-Status: fail-open`)
**Symptoms:** Logs show `FreshGate verification failed/timed out, failing open`. Responses succeed, but headers show `fail-open`.
1. **Identify Cause:**
   - Check Tavily status: verify whether search queries are failing or timing out.
   - Check LLM Judge status: verify whether OpenAI or custom judge endpoint is throttled or responding slowly.
2. **Mitigation:**
   - If search API is down, FreshGate's fail-open design guarantees the original LLM answer reaches users without interruption.
   - If temporary network latency increased, raise `FRESHGATE_TIMEOUT=6.0` to permit deeper evidence retrieval.

### Playbook B: Circuit Breaker Tripped (`X-FreshGate-Circuit-Breaker: open`)
**Symptoms:** Verification is completely bypassed; requests emit `X-FreshGate-Reason: circuit_breaker_tripped`.
1. **Diagnosis:** Check `GET /api/deployment/status`. Examine `consecutive_failures`.
2. **Recovery:**
   - Wait 30 seconds for automatic `HALF_OPEN` self-healing probes.
   - Once downstream APIs recover, probe calls will automatically reset the circuit to `CLOSED`.
   - To force an immediate manual reset:
     ```bash
     curl -X POST http://localhost:8000/api/circuit-breaker/reset \
       -H "X-Admin-Key: <FRESHGATE_ADMIN_KEY>"
     ```

### Playbook C: Database Lock or High Cache Latency (SQLite)
**Symptoms:** `GET /health/ready` returns HTTP 503 or logs report `database is locked`.
1. **Diagnosis:** High concurrency writes exceeding single-threaded SQLite lock duration.
2. **Mitigation:**
   - Verify that WAL mode is active: `PRAGMA journal_mode=WAL;`.
   - Verify `PRAGMA busy_timeout=5000;`.
   - For high-throughput distributed loads, transition to Redis by setting `REDIS_URL=redis://redis:6379/0`.

---

## 3. Investigating Incidents via Structured Logs

All logs are formatted as structured JSON with correlation IDs:
```json
{
  "timestamp": "2026-11-10T23:50:49Z",
  "level": "INFO",
  "logger": "freshgate",
  "message": "Async verification complete in 18.2ms. Stale=1, AvgScore=10",
  "correlation_id": "4b6c3e72-8f91-4d1a-b3e2-8921cf892011"
}
```
Query specific requests across gateway logs:
```bash
docker logs freshgate-verifier | grep "4b6c3e72-8f91-4d1a-b3e2-8921cf892011"
```
