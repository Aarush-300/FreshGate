# FreshGate: Deployment and Rollback Guide

## 1. Phased Deployment Strategy

To eliminate operational risk during real-world gateway adoption, FreshGate provides a 4-stage controlled rollout:

```
[ Phase 1: Shadow Mode ] ──► [ Phase 2: Warning Mode ] ──► [ Phase 3: Enforce Mode ] ──► [ Phase 4: Active Policy ]
  (Zero delay/mutation)        (Headers-only metadata)        (Enriched JSON payload)        (Rejection of Stale Claims)
```

### Stage 1: Shadow Mode (`FRESHGATE_MODE=shadow`)
- **Behavior:** The universal proxy forwards requests to Bifrost/LLM and returns the exact upstream answer immediately. Verification runs strictly in background tasks.
- **Purpose:** Collect real-world metrics, warm cache entries, evaluate latency impact, and establish error baselines with zero client disruption.
- **Verification:** Monitor Prometheus `freshgate_claims_extracted_total` and logs for correlation IDs.

### Stage 2: Warning-Only Mode (`FRESHGATE_MODE=warn`)
- **Behavior:** Verification runs inline. FreshGate attaches `X-FreshGate-Score`, `X-FreshGate-Stale-Count`, and `X-FreshGate-Latency-Ms` headers to HTTP responses, but **does not mutate the response JSON body**.
- **Purpose:** Client applications can inspect headers to validate scoring thresholds without parsing new JSON fields.

### Stage 3: Enforce Mode (`FRESHGATE_MODE=enforce`)
- **Behavior:** Full production mode. The response JSON is enriched with the `"freshgate"` metadata block containing claims, citations, verdicts, and corrections.

### Stage 4: Policy Enforcement (`FRESHGATE_BLOCK_STALE=true`)
- **Behavior:** If a response contains confirmed stale claims or its score falls below `FRESHGATE_MIN_SCORE`, the gateway blocks the hallucinated response with `HTTP 422 Unprocessable Content`.

### Feature database durability

Policies, monitoring state, alerts, and Replay snapshots use FRESHGATE_FEATURE_DB (default freshgate_features.db). Mount this file on durable storage and include it in backups. The checked-in Render Blueprint uses a paid 0.5c-512mb web service with a 1 GB disk mounted at /data; its cache, audit, and feature SQLite files are configured there so their data survives service restarts and redeploys. Render free web services do not support persistent disks. Scheduled Watchtower checks also require server-side OPENAI_API_KEY and TAVILY_API_KEY; dashboard request keys are never reused by background jobs.

The SQLite feature store uses WAL mode with `synchronous=NORMAL` to keep snapshot writes consistent across process crashes while reducing per-write latency. As with SQLite's NORMAL synchronous mode generally, a sudden host or power failure may lose the most recent committed transaction; use managed durable storage and backups for production history.

---

## 2. Instant Rollback Procedures

If issues arise during production deployment, three rollback options are available depending on severity:

### Option A: Immediate Upstream DNS / Port Bypass (0s downtime)
Switch client routing directly to Bifrost core:
- Change target endpoint from `http://freshgate:8000/v1/chat/completions` directly to `http://bifrost:8080/v1/chat/completions`.
- Because FreshGate is an OpenAI-compatible proxy, client applications require zero code changes.

### Option B: Runtime Dynamic Bypass via Environment
If client endpoints cannot be immediately changed, set FreshGate to bypass mode:
```bash
# In docker-compose.yml or environment:
FRESHGATE_MODE=shadow
FRESHGATE_TIMEOUT=0.01  # Immediate fail-open
```
Reload containers:
```bash
docker-compose up -d freshgate
```
FreshGate immediately passes every response through untouched in $< 1\text{ ms}$.

### Option C: Container Rollback
```bash
# Graceful stop
docker-compose stop freshgate

# Re-route traffic or restart previous container tag
docker-compose up -d --no-deps bifrost
```
