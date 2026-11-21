# HTTP API reference

Use the origin of your running FreshGate server. FastAPI serves interactive schemas at `/docs` and the machine-readable contract at `/openapi.json`. The tables below list each operation published in that contract. Query columns list declared query parameters. Some handlers also read headers, form fields or gateway options directly from the request.

## Verify one answer

Send a JSON body with an `answer` of 1–50,000 characters. Include a saved `policy_id` to apply an evidence policy. This example uses process-supplied provider credentials. Use your own FreshGate origin when handling credentials.

```bash
curl -sS http://127.0.0.1:8000/verify \
  -H 'Content-Type: application/json' \
  -H "X-OpenAI-API-Key: ${OPENAI_API_KEY}" \
  -H "X-Tavily-API-Key: ${TAVILY_API_KEY}" \
  -d '{"answer":"The current stable release of Python is version 3.10."}'
```

Read `status` and `summary.checked` before using the average. Each checked claim can carry its own `verdict`, `score`, `sources`, `correction` and `cached` indicator. `summary.stale` counts contradictions. `summary.avg_score` may be null. The upload route accepts multipart form data with a `file` and optional `answer` text. Profile verification, gateway integration and operator administration use different credentials. [Security architecture](03_security_architecture.md)

## Handle HTTP outcomes

| Status | Typical interpretation |
| --- | --- |
| 401 / 403 | Missing authentication, invalid credentials or an operation requiring the admin key. |
| 404 / 409 | A lookup missed, a selected policy is disabled, or a monitoring operation conflicts with current state. Stream lookup also returns 404 while the result is pending. |
| 413 / 422 | Input exceeds a size limit, validation failed, or a blocking gateway rule rejected a completion. Inspect the response body. |
| 429 | A rate or enrollment limit was reached. Check the route and error detail. |
| 502 / 503 | Bifrost was unreachable, a required service was unavailable, or an integration is misconfigured. Check readiness and gateway configuration. |

For an OpenAI-compatible request, the proxy can take `X-FreshGate-Mode` or the `freshgate_mode` query option. Behavior also depends on streaming and the configured delivery settings. For native plugin calls, send the integration key via `X-API-Key` and point the plugin at the full verification endpoint. [Gateway guide](bifrost-notes.md)

## Verification and chat

Direct verification accepts a session or request-specific provider credentials. Gateway routes use the integration key when configured and require it in production. Stream lookup requires a session or admin key.

| Method | Path | Operation | Query |
| --- | --- | --- | --- |
| POST | `/verify` | Verify Answer | — |
| POST | `/verify/upload` | Verify Upload | — |
| POST | `/v1/chat/completions` | Proxy Chat Completions | — |
| POST | `/proxy/v1/chat/completions` | Proxy Chat Completions | — |
| GET | `/api/stream/verification/{completion_id}` | Get Stream Verification | — |
| POST | `/api/plugin/verify` | Plugin Verify | — |

## Accounts and providers

Registration and login accept email and password and issue a session cookie. The provider catalog is public. Profile settings and model discovery use the signed-in account. Saving a blank key can retain the current provider's stored key.

| Method | Path | Operation | Query |
| --- | --- | --- | --- |
| GET | `/api/providers` | List Providers | — |
| POST | `/api/providers/{provider_id}/models` | Legacy List Provider Models | — |
| GET | `/api/profile/settings` | Get Profile Settings | — |
| PUT | `/api/profile/settings` | Update Profile Settings | — |
| POST | `/api/profile/models` | List Models | — |
| POST | `/api/auth/register` | Register | — |
| POST | `/api/auth/login` | Login | — |
| POST | `/api/auth/logout` | Logout | — |
| GET | `/api/auth/me` | Me | — |

## Policies

The category registry is public. The remaining routes use the session's feature store. Creation returns 201 and successful deletion returns 204. Preview accepts sample claims and sources.

| Method | Path | Operation | Query |
| --- | --- | --- | --- |
| GET | `/api/policies/categories` | Get Policy Categories | — |
| GET | `/api/policies` | Get Policies | `limit`, `offset` |
| POST | `/api/policies` | Post Policy | — |
| POST | `/api/policies/validate` | Validate Candidate Policy | — |
| GET | `/api/policies/{policy_id}` | Get Policy By Id | — |
| PUT | `/api/policies/{policy_id}` | Put Policy | — |
| DELETE | `/api/policies/{policy_id}` | Delete Policy | — |
| POST | `/api/policies/{policy_id}/validate` | Validate Policy | — |
| POST | `/api/policies/{policy_id}/preview` | Preview Policy | — |
| POST | `/api/policies/{policy_id}/duplicate` | Duplicate Policy | — |

## Monitoring and delivery

These routes require a signed-in user. Use the enrollment ID for a recheck. A successful HTTP call to the check endpoint can still report a failed or retrying attempt in its body. Acknowledgment changes alert review state.

| Method | Path | Operation | Query |
| --- | --- | --- | --- |
| POST | `/api/watchtower/monitor` | Monitor Claim | — |
| GET | `/api/watchtower/claims` | List Claims | `limit`, `offset` |
| GET | `/api/watchtower/claims/{claim_id}` | Get Claim | — |
| PATCH | `/api/watchtower/claims/{claim_id}` | Patch Claim | — |
| DELETE | `/api/watchtower/claims/{claim_id}` | Delete Claim | — |
| POST | `/api/watchtower/claims/{claim_id}/check` | Check Claim | — |
| GET | `/api/watchtower/alerts` | List Alerts | `limit`, `offset`, `unacknowledged_only` |
| GET | `/api/watchtower/alerts/{alert_id}` | Get Alert | — |
| POST | `/api/watchtower/alerts/{alert_id}/acknowledge` | Acknowledge Alert | — |
| GET | `/api/watchtower/email/schedule` | Get Email Schedule | — |
| POST | `/api/watchtower/email/schedule` | Set Email Schedule | — |
| DELETE | `/api/watchtower/email/schedule` | Delete Email Schedule | — |
| POST | `/api/watchtower/email/schedule/stop` | Stop Email Schedule | — |
| POST | `/api/watchtower/email/test` | Send Test Email | — |

## History, Replay and exports

Use a signed-in session. Replay takes a 64-character lowercase hexadecimal fingerprint. A comparison requires the from and to query parameters. Download options and available formats are described in the [Replay guide](verification-replay.md).

| Method | Path | Operation | Query |
| --- | --- | --- | --- |
| GET | `/api/history/claims/{fingerprint}` | History For Claim | `limit`, `offset` |
| GET | `/api/history/verifications/{verification_id}` | History For Verification | — |
| GET | `/api/replay/{fingerprint}/compare` | Compare Verifications | `from`, `to` |
| GET | `/api/replay/{fingerprint}/timeline` | Replay Claim Timeline | `limit`, `offset` |
| GET | `/api/replay/{fingerprint}` | Replay Claim History | `limit`, `offset` |
| GET | `/api/export/claim/{fingerprint}/executive-summary` | Get Executive Summary | `limit` |
| GET | `/api/export/claim/{fingerprint}/temporal-trends` | Get Temporal Trends | `limit` |
| GET | `/api/export/claim/{fingerprint}/prediction-accuracy` | Get Prediction Accuracy | — |
| GET | `/api/export/claim/{fingerprint}/knowledge-base` | Get Knowledge Base | `format`, `download` |
| GET | `/api/export/claim/{fingerprint}/full-report` | Get Full Report | `format`, `download` |

## Service and operator routes

Health probes and the dashboard are public. Metrics, provider and deployment status, benchmark results and stream lookup accept a user session or operator credential. Recent audits, cache clear and circuit reset require the admin key. The service-worker routes serve compatibility responses.

| Method | Path | Operation | Query |
| --- | --- | --- | --- |
| GET | `/health` | Health Check | — |
| GET | `/health/live` | Health Live | — |
| GET | `/health/ready` | Health Ready | — |
| GET | `/metrics` | Get Metrics | — |
| GET | `/api/audits/recent` | Get Recent Audits | `limit`, `offset`, `source` |
| GET | `/api/provider/status` | Get Provider Status | — |
| GET | `/api/deployment/status` | Get Deployment Status | — |
| POST | `/api/circuit-breaker/reset` | Reset Circuit Breaker | — |
| GET | `/api/benchmark` | Get Benchmark Results | — |
| POST | `/api/cache/clear` | Clear Cache | — |
| GET | `/` | Get Dashboard | — |
| GET | `/service-worker.js` | Service Worker Noop | — |
| GET | `/sw.js` | Service Worker Noop | — |
