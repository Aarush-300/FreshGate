# Diagnose verification and monitoring incidents

Start with the affected entry point, timestamp, HTTP status and correlation ID. Establish whether the request used a signed-in Profile, direct provider headers or server-level credentials. A working browser check and a failing gateway request may use different accounts.

| Symptom | Inspect | Recovery signal |
| --- | --- | --- |
| Liveness succeeds but readiness returns 503 | Configured MongoDB connection, cache backend, writable volume and server logs | Readiness returns 200 and a saved user can open history. |
| Proxy returns 502 | Bifrost address as seen from FastAPI, upstream availability and selected model | A non-streaming request reaches the configured gateway. |
| Completion arrives without a score | Mode, plugin status, tool-only output, timeout and fail-open reason | A synchronous text completion includes a result and a score or an explicitly unavailable average. |
| Fewer claims have evidence | Extraction status, search failures, policy exclusions and missing source dates | A representative claim has traceable evidence or a specific explanation for insufficient evidence. |
| Rechecks are overdue | Scheduler uptime, enabled state, owner Profile, leases, retries and backlog | A check updates the successful timestamp and next due time. |
| Reports stop arriving | Sender, recipient, interval, next due time and transport settings | A test message reaches the recipient and a due digest advances the schedule. |

Use a session or admin key for `/metrics`. Inspect changes in `freshgate_search_api_failures_total`, cache hits and misses, and checked claims over the same window. Divide the change in `freshgate_verification_latency_seconds_sum` by the change in `freshgate_verification_latency_seconds_count` for mean observed latency when the count increased. Process counters can restart with the service.

Use operator credentials for `GET /api/audits/recent`. Filter by source, limit and offset. Correlate the audit outcome with the client-facing mode and avoid copying submitted text into incident notes. Confirm the provider has recovered before resetting an open circuit. For a single disputed claim, inspect its stored evidence before considering a shared cache clear. [Operations](10_production_operations_guide.md)
