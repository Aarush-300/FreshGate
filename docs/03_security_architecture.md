# Credentials and data access

| Principal | Credential | Access |
| --- | --- | --- |
| Dashboard user | Signed `freshgate_session` cookie | Profile, verification, policies, monitoring, history and exports |
| Gateway integration | FreshGate client key, normally sent as `X-API-Key` | Native verification endpoint and chat proxy |
| Operator | `X-Admin-Key` | Recent audit records, cache clear and circuit-breaker reset |
| Verification provider | Provider and Tavily credentials | Outbound extraction, judgment and evidence search |

## Accounts and secrets

Account passwords use salted PBKDF2-HMAC-SHA256. Login and registration issue an HTTP-only cookie with SameSite=Lax and a 30-day lifetime. Enable secure cookies for an HTTPS deployment. Profile credentials are encrypted using a key derived from the session secret. Keep that secret stable across restarts and store it alongside protected recovery material. Changing it affects existing sessions and the ability to decrypt stored credentials. [Account implementation](../server/users.py)

For shared deployments, configure MongoDB for accounts and feature data. The local SQLite feature store is intended for one user. The public-fact cache can be shared across checks, and audit records can contain submitted text. Limit database and backup access accordingly.

## Request boundaries

Custom provider URLs are checked server-side. Production validation requires HTTPS and rejects hosts that resolve to disallowed network addresses. Development permits local model endpoints. Upload limits and allowed extensions apply before document text enters the verification pipeline. Request size, rate limits and model concurrency limits address different parts of the request lifecycle. [URL validation](../server/netguard.py) · [Request controls](../core/security.py)

Use distinct integration and admin keys. With no integration key, development can accept local gateway traffic. In production the protected integration endpoints report a configuration error when the key is missing. The gateway-to-FreshGate credential is separate from any credential Bifrost requires from a chat client.
