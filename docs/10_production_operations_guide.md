# Operating FreshGate

Run the FastAPI process continuously if monitoring and email schedules are enabled. The dashboard can show an earlier successful observation after a provider failure. Check the time of the last successful verification alongside process health.

| Startup check | What success establishes |
| --- | --- |
| `GET /health/live` | The HTTP process answers its liveness route. |
| `GET /health/ready` | Storage readiness checks succeeded, including MongoDB when required and a cache statistics read. |
| Load the page and sign in | The built assets, account store and session cookie work through the public origin. |
| Verify a short volatile claim | The selected credential path can perform extraction and retrieval. Inspect status, method and sources. |
| Send a non-streaming chat request | The configured Bifrost model and selected integration return the expected response. |

## Configuration and persistence

Profile settings fund dashboard checks. Configure server provider and search credentials for the plugin and proxy. Set `FRESHGATE_MODE`, `FRESHGATE_BLOCK_STALE` and `FRESHGATE_MIN_SCORE` for the expected delivery behavior. Check deployment overrides. The application default is enforce with stale blocking off, while the supplied Render configuration selects warn.

Inventory accounts, feature history, audits, the session secret and cache paths. Losing feature storage can erase policies, enrolled claims and Replay history. Restore a backup into a separate deployment with the corresponding session secret to check that saved Profiles still work.

## Routine checks

Review provider failures, cache activity and latency over a consistent time window. Inspect overdue monitors, last errors and digest delivery. Check available disk space and growth of snapshots and audits. Administrative cache clear and circuit reset change runtime state. Investigate the underlying problem before using either. [Incident runbook](12_monitoring_and_incident_response_runbook.md)
