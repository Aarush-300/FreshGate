# Watchtower

Watchtower rechecks monitored volatile claims independently of synchronous chat and `/verify` requests. The dashboard can register a claim directly from a successful live verification, or API clients can register a claim and optionally provide its verified baseline.

## Scheduling and provider credentials

Monitoring intervals are seconds, from one hour to 30 days. The service bounds active registrations (`FRESHGATE_WATCHTOWER_MAX_CLAIMS`), concurrent reverifications, retry delay, lease duration, and scheduler polling. Defaults and environment variables are listed in `.env.example`.

Scheduled checks use only server-configured `OPENAI_API_KEY` and `TAVILY_API_KEY`. Keys submitted in request headers for `/verify` are request-scoped and are never saved or reused by Watchtower. Without server credentials, manual and scheduled jobs are recorded as failed and retried; configure the server credentials before enabling scheduled checks.

The scheduler starts and stops with FastAPI. SQLite leases are acquired transactionally, preventing two workers from running a check for the same claim. Expired leases become eligible again after a process crash or restart. This design supports multiple workers sharing the same SQLite database on a single host; it is not a substitute for a distributed database in multi-host deployments. Keep the feature database on durable storage.

## Change detection

Watchtower stores immutable verification snapshots and creates persistent alerts for confirmed-to-contradicted/partially-supported transitions, a score falling below the configured threshold, materially changed source sets, and repeated inconclusive evidence. A policy selected at registration is passed to the existing verification pipeline on every check.

Provider errors, missing credentials, timeouts, rate limits, and failed extraction are recorded as failed attempts and retried with bounded exponential backoff. They do not change the saved verdict or generate stale-information alerts. Repeated inconclusive evidence is retried before Watchtower reports loss of evidence. Unresolved duplicate transitions share one alert; acknowledging an alert allows a later recurrence to create a new alert.

## API and dashboard

Use the `/api/watchtower` endpoints listed in the [API reference](13_api_reference.md). The dashboard displays monitoring counts, claim state, check controls, evidence links, Replay links, and acknowledgement actions. A claim must have a successful verification baseline for change detection to compare it against; registering without a baseline sets one after the first successful check.

## Persistence and access

Monitoring rows, leases, snapshots, and alerts are stored in the shared `FRESHGATE_FEATURE_DB` SQLite database. Back it up with the policy data. PostgreSQL support and multi-tenant access control are not included in this phase. Protect a deployed instance at its ingress boundary and apply provider usage limits.
