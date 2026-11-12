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

#### Headers
- `X-OpenAI-API-Key`: OpenAI-compatible provider key for claim extraction and judging (optional if configured on the server).
- `X-Tavily-API-Key`: Tavily key for live web search (optional if configured on the server).

Keys are used for this request only. Send them over HTTPS when deployed; do not put keys in the JSON body, query string, or application logs.

#### Request Body
```json
{
  "answer": "Python 2.7 is the current supported version of Python."
}
```

#### Response Body
Returns `VerificationResult` schema. Each checked volatile claim includes a `claim_fingerprint` and a `verification_id` that identify its history and immutable snapshot. Claims excluded from live verification may not have these fields.

#### Request Body
```json
{
  "answer": "Python 2.7 has just been released.",
  "policy_id": "optional-saved-policy-id"
}
```

When a policy is selected, the response includes a `policy_evaluation` object with the policy decision and per-claim results. Omit `policy_id` to preserve default behavior. See [Policy Studio](policy-studio.md) for the complete policy contract.

### Policy Studio

- `GET /api/policies/categories` — supported categories.
- `GET /api/policies` and `GET /api/policies/{policy_id}` — list or retrieve policies.
- `POST /api/policies` — create a policy; returns HTTP 201.
- `PUT /api/policies/{policy_id}` — replace policy settings.
- `DELETE /api/policies/{policy_id}` — delete a policy; returns HTTP 204.
- `POST /api/policies/validate` — validate proposed settings before saving.
- `POST /api/policies/{policy_id}/validate` — validate settings in the context of a saved policy.
- `POST /api/policies/{policy_id}/preview` — simulate policy evaluation against supplied sample claims without live search or LLM calls.
- `POST /api/policies/{policy_id}/duplicate` — create a copy; returns HTTP 201.

Policy preview request example:

```json
{
  "claims": [
    {
      "id": "claim-1",
      "text": "The current release is 3.13",
      "category": "version",
      "freshness_score": 92,
      "sources": [
        {
          "url": "https://docs.python.org/3/",
          "snippet": "Current documentation",
          "published": "2026-11-10",
          "authority_score": 0.95
        }
      ]
    }
  ]
}
```

### Watchtower

- `POST /api/watchtower/monitor` — register a volatile claim, interval, optional policy, and optional verified baseline.
- `GET /api/watchtower/claims` and `GET /api/watchtower/claims/{id}` — list monitored claims/overview or retrieve claim details/history.
- `PATCH /api/watchtower/claims/{id}` and `DELETE /api/watchtower/claims/{id}` — update monitoring state/interval/policy or remove the claim.
- `POST /api/watchtower/claims/{id}/check` — request a controlled immediate recheck; a current job lease returns HTTP 409.
- `GET /api/watchtower/alerts` and `GET /api/watchtower/alerts/{id}` — list or retrieve alerts.
- `POST /api/watchtower/alerts/{id}/acknowledge` — acknowledge an alert.

Monitoring intervals are specified in seconds and must be between one hour and 30 days. Scheduled checks use server-configured provider credentials and never reuse keys sent to `/verify`. `FRESHGATE_WATCHTOWER_*` settings bound claim count, concurrency, polling, lease duration, and retries. See [Watchtower operations](watchtower.md).

### Verification History and Replay

- `GET /api/history/claims/{fingerprint}?limit=100&offset=0` — paginated immutable snapshots for a SHA-256 claim fingerprint.
- `GET /api/history/verifications/{verification_id}` — retrieve one immutable snapshot by the ID returned on a verified claim.
- `GET /api/replay/{fingerprint}?limit=100&offset=0` — chronological snapshot page and the latest comparison.
- `GET /api/replay/{fingerprint}/compare?from={verification_id}&to={verification_id}` — compare two snapshots of that claim.
- `GET /api/replay/{fingerprint}/timeline?limit=100&offset=0` — paginated verification and Watchtower alert events.

Replay endpoints are read-only. They compare verdict, score, authority/recency/agreement dimensions, sources and snippets, corrections, and applied policy. See [Verification Replay](verification-replay.md).

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
