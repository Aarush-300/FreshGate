# FreshGate: Production Operations Guide

## 1. Quickstart & Service Launch

### 1.1 Local / Development Mode
```bash
# Create virtual environment and install dependencies
python -m venv .venv
.\.venv\Scripts\activate   # On Windows (or source .venv/bin/activate on Linux)
pip install -r requirements.txt

# Start FreshGate Universal Proxy on port 8000
uvicorn server.app:app --host 0.0.0.0 --port 8000 --workers 4
```

### 1.2 Production Docker Deployment
```bash
# Launch with Docker Compose (includes Bifrost, Redis, and persistent volumes)
docker-compose up -d --build

# Verify healthy startup
docker-compose ps
curl -s http://localhost:8000/health/ready
```

---

## 2. Configuration Reference

All behavior is controlled via environment variables:

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `BIFROST_UPSTREAM_URL` | `http://127.0.0.1:8080` | URL of upstream Bifrost or LLM gateway |
| `TAVILY_API_KEY` | *(None)* | Optional server-side fallback. Otherwise supply `X-Tavily-API-Key` for each `/verify` request. |
| `OPENAI_API_KEY` | *(None)* | Optional server-side fallback. Otherwise supply `X-OpenAI-API-Key` for each `/verify` request. |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | Custom endpoint for judge (Ollama, vLLM, etc.) |
| `LLM_MODEL` | `gpt-4o-mini` | Default model for atomic extraction and verification judging |
| `FRESHGATE_TIMEOUT` | `4.0` | Gateway verification fail-open timeout in seconds |
| `FRESHGATE_FAIL_OPEN` | `true` | Guarantee that original LLM answers pass through on any error |
| `FRESHGATE_MODE` | `enforce` | Gateway mode: `shadow`, `warn`, or `enforce` |
| `FRESHGATE_BLOCK_STALE` | `false` | When true, returns HTTP 422 if outdated claims are detected |
| `FRESHGATE_MIN_SCORE` | `0` | Minimum freshness score (0 - 100) before triggering policy action |
| `FRESHGATE_MAX_BODY_SIZE_BYTES` | `1048576` | Maximum allowed request payload in bytes (1 MB) |
| `FRESHGATE_RATE_LIMIT_PER_MINUTE` | `60` | IP-based request rate limit per minute |
| `FRESHGATE_API_KEY` | *(None)* | Client API key for protected gateway proxy endpoints |
| `FRESHGATE_ADMIN_KEY` | *(None)* | Administrative token for cache clearing and breaker resets |
| `REDIS_URL` | *(None)* | Redis connection string (`redis://redis:6379/0`) for distributed cache |
| `FRESHGATE_CACHE_DB` | `freshgate_cache.db` | File path for SQLite claim cache |
| `FRESHGATE_AUDIT_DB` | `freshgate_audit.db` | File path for historical verification audit store; local SQLite needs persistent storage on the host. |

---

## 3. Operational Health Checks

- **Liveness Probe:** `GET /health/live`
  - Returns `{"status": "alive"}` immediately (HTTP 200).
- **Readiness Probe:** `GET /health/ready`
  - Queries database and cache status (`SELECT 1` / table read).
  - Returns HTTP 200 `{"status": "ready", "database": "connected", "cache": "connected"}` or HTTP 503 if backend is unavailable.
- **Prometheus Metrics:** `GET /metrics`
  - Scraped at standard 15s intervals by Prometheus collectors.

---

## 4. Maintenance Operations

### Clearing Cache (Zero-Downtime)
```bash
curl -X POST http://localhost:8000/api/cache/clear \
  -H "X-Admin-Key: <FRESHGATE_ADMIN_KEY>"
```

### Resetting Circuit Breaker
```bash
curl -X POST http://localhost:8000/api/circuit-breaker/reset \
  -H "X-Admin-Key: <FRESHGATE_ADMIN_KEY>"
```
