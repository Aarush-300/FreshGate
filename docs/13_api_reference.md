# FreshGate: Complete API Reference

## 1. Chat Completion Gateway Endpoints

### `POST /v1/chat/completions` & `POST /proxy/v1/chat/completions`
Drop-in replacement for OpenAI `/v1/chat/completions`. Intercepts responses, extracts facts, validates against Tavily web evidence, and decorates metadata.

#### Headers
- `Content-Type`: `application/json` (Required)
- `Authorization`: `Bearer <token>` (Optional upstream or gateway key)
- `X-FreshGate-Mode`: `enforce` | `warn` | `shadow` (Optional override)
- `X-FreshGate-Async`: `true` | `false` (Optional override)

#### Request Body
Standard OpenAI chat completion payload:
```json
{
  "model": "gpt-4o-mini",
  "messages": [
    {"role": "user", "content": "What is the price of Netflix Standard with ads?"}
  ],
  "temperature": 0.2,
  "stream": false
}
```

#### Response (Non-Streaming Enriched)
```json
{
  "id": "chatcmpl-prod-999",
  "object": "chat.completion",
  "created": 1710000000,
  "model": "gpt-4o-mini",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Netflix Standard with ads costs $9.99 per month in the US."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 15,
    "completion_tokens": 20,
    "total_tokens": 35
  },
  "freshgate": {
    "claims": [
      {
        "id": "c1",
        "text": "Netflix Standard with ads costs $9.99 per month in the US",
        "type": "pricing",
        "verdict": "contradicted",
        "score": 10,
        "correction": "Netflix Standard with ads costs $6.99 per month in the US",
        "cached": false,
        "sources": [
          {
            "url": "https://help.netflix.com/en/node/24926",
            "snippet": "Standard with ads is $6.99/month in the United States.",
            "authority_score": 0.95,
            "is_primary": true
          }
        ]
      }
    ],
    "summary": {
      "total": 1,
      "checked": 1,
      "stale": 1,
      "avg_score": 10
    }
  }
}
```

#### Response Headers
- `X-FreshGate-Status`: `verified` | `fail-open`
- `X-FreshGate-Score`: `0` to `100`
- `X-FreshGate-Stale-Count`: Count of contradicted claims
- `X-FreshGate-Total-Claims`: Total claims extracted
- `X-FreshGate-Checked-Claims`: Volatile claims verified
- `X-FreshGate-Latency-Ms`: Middleware processing time in ms
- `X-Correlation-ID`: Unique request tracking ID

---

## 2. Direct Verification Endpoint

### `POST /verify`
Verifies arbitrary assistant text directly without forwarding to an upstream LLM.

#### Request Body
```json
{
  "answer": "Python 2.7 is the current supported version of Python."
}
```

#### Response Body
Returns `VerificationResult` schema.

### `POST /preview`
Runs the deterministic local fallback extractor without calling an LLM or web-search provider. Returns `mode: "offline_preview"`, candidate claim objects, and a message stating that the candidates are not live-verified. A no-pattern-match result is inconclusive and does not confirm freshness.

#### Request Body
```json
{
  "answer": "Python 2.7 has just been released."
}
```

---

## 3. Streaming Verification Retrieval

### `GET /api/stream/verification/{completion_id}`
Retrieves asynchronous verification results for a completed SSE stream.

#### Response
```json
{
  "status": "completed",
  "result": {
    "claims": [...],
    "summary": { "total": 1, "checked": 1, "stale": 0, "avg_score": 95 }
  },
  "duration_ms": 18.2,
  "timestamp": 1710000000.0
}
```

---

## 4. Observability & System Probes

### `GET /metrics`
Returns Prometheus-formatted metrics (`text/plain; version=0.0.4`).

### `GET /health/live`
Returns `{"status": "alive"}` (HTTP 200).

### `GET /health/ready`
Returns `{"status": "ready", "database": "connected", "cache": "connected", "upstream": "..."}`. Returns HTTP 503 if backend is unavailable.

### `GET /api/deployment/status`
Returns active mode (`enforce`, `warn`, `shadow`), policy thresholds, and circuit breaker status.

---

## 5. Administrative Endpoints

Protected by `X-Admin-Key: <FRESHGATE_ADMIN_KEY>`.

### `POST /api/cache/clear`
Clears all cached claims. Returns `{"status": "cleared"}`.

### `POST /api/circuit-breaker/reset`
Resets circuit breaker from `OPEN` to `CLOSED`. Returns `{"status": "reset", "circuit_breaker": {...}}`.

### `GET /api/audits/recent?limit=50`
Returns recent immutable verification audit records.
