# FreshGate: Bug-Fix Implementation Plan (for Antigravity / Gemini Flash)

## How to use this file

Paste this into Antigravity:

```
Read FRESHGATE_FIX_PLAN.md in the repo root completely. Execute ONE phase at a time, in order.
Before editing any file, open it and confirm the code I quote still exists. If it differs, tell me and stop.
After each phase run the verification commands listed in that phase. Fix failures before moving on.
Do not delete or skip existing tests. Never print, log, or commit any key. Never edit .env.
Do not add dependencies unless a phase says so. Do not refactor anything not listed.
At the end report: files changed per phase, test output, and anything you could not finish.
```

Rules for the agent:
- Small, surgical edits. One concern per commit.
- Files use CRLF line endings. Preserve them (do not rewrite whole files).
- Run `python -m pytest -q` after each backend phase and `cd dashboard && npm run build` after each frontend phase.
- Python deps must be installed first: `pip install -r requirements-dev.txt`.

---

## Phase 0. Stop the bleeding (secrets and test pollution)

### 0.1 Secrets
The shipped `.env` contains real-looking keys (`TAVILY_API_KEY=tvly-...`, `OPENAI_API_KEY=gsk_...`, a MongoDB URI, session secret). **The human must rotate all of them.** The agent must not touch `.env`.
- Verify `.env` and `*.db` are in `.gitignore` and `.dockerignore` (they are). Verify `git ls-files | grep -E "\.env$|\.db$|\.so$"` returns nothing. If it returns files, run `git rm --cached` on them.
- Remove committed binaries from git if tracked: `plugin/freshgate.so` (35 MB), `bench/eval_cache_*.db`, `__pycache__/`.

### 0.2 Placeholder admin key
`.env` and `.env.example` set `FRESHGATE_ADMIN_KEY=your_admin_secret_token_here`. That string is public (it is in the example file), it is 28 chars so it passes the 24-char check in `server/users.py`, and `serializer()` / `_fernet()` fall back to the admin key as the session secret. Anyone who knows the example can call every admin route and forge login cookies.

Edit `core/security.py`, `get_configured_admin_key()`:
```python
_PLACEHOLDER_ADMIN = {"your_admin_secret_token_here", "changeme", "admin", "test"}

def get_configured_admin_key() -> Optional[str]:
    key = os.getenv("FRESHGATE_ADMIN_KEY", "").strip()
    if not key or key.lower() in _PLACEHOLDER_ADMIN or key.lower().startswith("your_"):
        return None   # treated as "not configured" -> admin routes return 403
    return key
```
Edit `server/users.py`: in both `serializer()` and `_fernet()` remove the `or os.getenv("FRESHGATE_ADMIN_KEY")` fallback. Session secret must come only from `FRESHGATE_SESSION_SECRET`.
Edit `.env.example`: change the value to empty (`FRESHGATE_ADMIN_KEY=`) with a comment `# generate with: python -c "import secrets;print(secrets.token_urlsafe(32))"`.

### 0.3 Tests write into the real databases
The shipped `freshgate_audit.db` has 7 rows and all 7 are test fixtures (`chatcmpl-123`, `test-audit-rec-1`, `chatcmpl-warn-1`, ...). `tests/conftest.py` never redirects the SQLite paths, so every `pytest` run pollutes the developer's real audit, cache and feature DBs.

Edit `tests/conftest.py`. At the very top, **before** any `core`/`server` import, add:
```python
import tempfile, os
_TMP = tempfile.mkdtemp(prefix="freshgate-tests-")
os.environ["FRESHGATE_AUDIT_DB"]   = os.path.join(_TMP, "audit.db")
os.environ["FRESHGATE_CACHE_DB"]   = os.path.join(_TMP, "cache.db")
os.environ["CACHE_DB_PATH"]        = os.path.join(_TMP, "cache.db")
os.environ["FRESHGATE_FEATURE_DB"] = os.path.join(_TMP, "features.db")
```
Also fix the admin key mismatch: `conftest.py` sets `FRESHGATE_ADMIN_KEY=test-admin-secret-key-at-least-24-chars-long` but `tests/test_infrastructure_phase6.py` patches it to `test-admin-secret`. After 0.2 this still works because the patch only needs to be non-placeholder. Leave it.

**Verify:** delete local `*.db`, run `python -m pytest -q`, then confirm no `freshgate_*.db` appeared in the repo root: `ls freshgate_*.db` must fail.

---

## Phase 1. Fix the Audit log (the reported bug)

### Root causes (all confirmed in code)

| # | Cause | Where |
|---|-------|-------|
| A1 | **The dashboard's Verify button never writes an audit record.** `POST /verify` calls `pipeline.verify()` and returns. Only the OpenAI-compatible proxy writes audits. | `server/app.py` `verify_answer()` |
| A2 | Even in the proxy, audits are written **only** for non-streaming, non-async/shadow, circuit-closed, successful verifications. Shadow/async (`run_async_verification`), streaming (`run_streaming_verification`), fail-open and enforce-block paths write nothing. Render is deployed with `FRESHGATE_MODE=shadow`, so production writes zero audits. | `server/app.py` lines ~343-388, 684-687 |
| A3 | Frontend table columns `request_id`, `summary`, `details` do not exist in the API payload. The API returns `correlation_id, total_claims, stale_claims, avg_score, claims, input_text, latency_ms`. Three of five columns are blank even when records exist. | `dashboard/src/App.tsx` `AuditView` |
| A4 | Empty admin key sends no header, backend returns 401, and `api.ts` treats any 401 outside `/api/auth/` as "session expired", so the user is **logged out** just for leaving the key blank. | `dashboard/src/api.ts` + `AuditView.load` |
| A5 | The key field is cleared in `finally`, even on failure, forcing retyping after every error. Also the field is wrapped in a form whose submit button has no `type`. | `AuditView.load` |
| A6 | Audit rows use `INSERT OR REPLACE` keyed on the upstream completion id. A repeated id **overwrites** the earlier record, so the log is not immutable as the UI and docs claim. Reproduced: second write with the same id replaced `input_text`. | `core/audit.py` `record_audit` |
| A7 | `limit` is unbounded. `limit=-1` returns every row (SQLite `LIMIT -1`). No pagination, no owner field. | `server/app.py` `get_recent_audits` |
| A8 | `render.yaml` puts the audit DB at `/tmp/freshgate_audit.db` (wiped on every deploy/restart; Render free has no disk). | `render.yaml` |
| A9 | Sync SQLite calls with a global `threading.Lock` run inside `async def` handlers, blocking the event loop. | `core/audit.py`, `server/app.py` |

### 1.1 Make the store append-only and queryable (`core/audit.py`)
1. Change `INSERT OR REPLACE` to `INSERT`. On `sqlite3.IntegrityError` (duplicate id), insert again with id `f"{audit_id}:{uuid.uuid4().hex[:8]}"`. Never overwrite.
2. Add columns via a safe migration in `_init_db` (use `PRAGMA table_info(verification_audit)`; `ALTER TABLE ... ADD COLUMN` only if missing):
   - `owner_id TEXT`, `source TEXT` (one of `verify`, `proxy`, `proxy_stream`, `proxy_async`, `watchtower`), `mode TEXT`.
   - Add `CREATE INDEX IF NOT EXISTS idx_audit_owner ON verification_audit(owner_id, created_at)`.
3. Extend `record_audit(...)` with optional kwargs `owner_id: Optional[str] = None, source: str = "proxy", mode: Optional[str] = None` in both `AuditStoreBase` and `SQLiteAuditStore`. Keep every existing positional/keyword argument so existing tests pass.
4. Extend `get_recent_audits(limit=50, offset=0, owner_id=None, source=None)`. Clamp inside: `limit = max(1, min(int(limit), 500))`, `offset = max(0, int(offset))`. Return the new fields too. Add `count_audits()`.
5. Replace the two `Failed to ...` `logger.error` calls so they use `logger.exception`.
6. Add async wrappers so callers never block the loop:
```python
import asyncio
async def arecord_audit(self, **kw): await asyncio.to_thread(self.record_audit, **kw)
async def aget_recent_audits(self, **kw): return await asyncio.to_thread(self.get_recent_audits, **kw)
```
(Put them on `AuditStoreBase` as concrete methods.)

### 1.2 One helper that every code path calls (`server/app.py`)
Add near the top of the file (after `logger`):
```python
async def write_audit(result: VerificationResult, *, text: str, source: str, mode: Optional[str],
                      latency_ms: int, corr_id: Optional[str], audit_id: Optional[str] = None,
                      owner_id: Optional[str] = None) -> None:
    try:
        await audit_store.arecord_audit(
            audit_id=audit_id or f"audit-{uuid.uuid4().hex}",
            correlation_id=corr_id or "req-unknown",
            input_text=text,
            claims_data=[c.model_dump() for c in result.claims],
            total_claims=result.summary.total,
            stale_claims=result.summary.stale,
            avg_score=result.summary.avg_score,
            latency_ms=latency_ms,
            status=str(result.summary.status or "verified"),
            owner_id=owner_id, source=source, mode=mode,
        )
    except Exception:
        logger.exception("audit write failed")   # audit must never break a request
```

### 1.3 Call it from every path
- **`verify_answer()`** (A1, the main fix): wrap the `await req_pipeline.verify(...)` call. Capture `t0 = time.perf_counter()` before, then
  ```python
  result = await req_pipeline.verify(...)
  await write_audit(result, text=req.answer, source="verify", mode=None,
                    latency_ms=int((time.perf_counter()-t0)*1000),
                    corr_id=getattr(request.state, "correlation_id", None),
                    owner_id=(user["_id"] if user else None))
  return result
  ```
  Keep the existing `except`/`finally`.
- **`run_async_verification(text)`**: make it return the result and call `write_audit(..., source="proxy_async")`. Pass `corr_id` and `mode` in as parameters from the caller.
- **`run_streaming_verification(completion_id, text)`**: after success call `write_audit(..., source="proxy_stream", audit_id=completion_id)`.
- **Non-streaming proxy** (existing `background_tasks.add_task(audit_store.record_audit, ...)`): replace with `background_tasks.add_task(write_audit, verification, text=assistant_content, source="proxy", mode=active_mode, latency_ms=latency_ms, corr_id=corr_id, audit_id=None)`. Do **not** reuse `upstream_data["id"]` as the audit id (A6); store it instead as part of `audit_id` prefix: `f"{upstream_id}:{uuid4().hex[:8]}"`.
- **Enforce-block branch**: the audit task is added before the 422 return, so this already works once the above is in place. Add a test.
- **Watchtower** (`core/watchtower.py` `_check_leased`): after a successful check, record with `source="watchtower"`, `owner_id=row["owner_id"]`. Use the same helper by moving `write_audit` into a small module `core/audit_helpers.py` to avoid a circular import with `server/app.py`.

### 1.4 Endpoint (`server/app.py`)
```python
from fastapi import Query
@app.get("/api/audits/recent", dependencies=[Depends(verify_admin_key)])
async def get_recent_audits(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
                            source: Optional[str] = Query(None, max_length=32)):
    rows = await audit_store.aget_recent_audits(limit=limit, offset=offset, source=source)
    return {"audits": rows, "limit": limit, "offset": offset, "total": await asyncio.to_thread(audit_store.count_audits)}
```

### 1.5 Dashboard (`dashboard/src/api.ts`, `dashboard/src/App.tsx`, `dashboard/src/types.ts`)
1. `api.ts`: change the 401 rule so it only fires the unauthorized handler when `init.headers` has no `X-Admin-Key`:
   ```ts
   const isAdminCall = !!(init.headers && ("X-Admin-Key" in (init.headers as Record<string,string>)));
   if (response.status === 401 && !path.startsWith("/api/auth/") && !isAdminCall) onUnauthorized?.();
   ```
   Also make the empty-key case explicit in `AuditView.load`: if `!key.trim()` set the error `"Enter the admin key to load audit records."` and return without calling the API.
2. `types.ts`: add
   ```ts
   export type AuditRecord = {
     id: string; correlation_id: string; created_at: number; input_text: string;
     claims: Claim[]; total_claims: number; stale_claims: number; avg_score: number | null;
     latency_ms: number; status: string; source?: string | null; mode?: string | null; owner_id?: string | null;
   };
   ```
3. `AuditView`: type state as `AuditRecord[]`. Replace the columns with real fields:
   - `created_at` (Recorded), `source` (Source), `status` (StatusMark), `correlation_id` (Request, truncate to 8 chars with full value in `title`), `total_claims` + `stale_claims` rendered as `"{total} claims, {stale} stale"` (Summary), `avg_score` (Score), `latency_ms` (Latency, `ms`), and a `details` cell that shows `input_text` and `claims` inside the existing `<details>` block.
   - Do NOT clear the key on failure: move `setKey("")` into the `try` block after success.
   - Add `type="submit"` to the button.
   - Replace the empty state text with `"No audit records yet. Run a verification from the Verify tab, then reload."`.
   - Keep `status-*` classes working for `verified`, `completed`, `partially_verified`, `completed_with_warnings`, `verification_failed`, `extraction_failed`, `no_claims_detected`. Check the `StatusMark` tone map covers each.
4. Add "Refresh" behavior: after a successful `Verify` in the Verify view nothing needs to change because the audit list is pulled on demand.

### 1.6 Deployment config (A8)
- `render.yaml`: the free plan has no disk, so the audit log cannot be durable there. Pick one:
  1. (preferred) Upgrade to `plan: starter`, add a `disk:` block mounted at `/data`, and set `FRESHGATE_AUDIT_DB=/data/freshgate_audit.db` (and cache/feature DBs the same way).
  2. Keep free plan but add a visible note in the Audit page: `"Audit records are stored on ephemeral storage on this deployment and reset on redeploy."` driven by a new field `persistent: bool` returned by `/api/audits/recent` (`persistent = not db_path.startswith("/tmp")`).
- Also change `FRESHGATE_MODE` default in `render.yaml` from `shadow` to `warn` (shadow skips response headers; audits are now written in all modes, so this is about demo clarity only).
- Make `CACHE_DB_PATH` vs `FRESHGATE_CACHE_DB` consistent. `grep -rn "CACHE_DB" core server render.yaml docker-compose.yml .env.example` and keep only the name `core/cache.py` actually reads.

### 1.7 Tests to add (`tests/test_audit_log.py`)
Use `httpx.AsyncClient(transport=ASGITransport(app=app))`, the `admin_headers` and `auth_cookie` fixtures, and patch `pipeline.verify` with an `AsyncMock` returning a valid `VerificationResult` (copy the pattern from `tests/test_server_api.py`).
1. `POST /verify` then `GET /api/audits/recent` returns a row with `source == "verify"`.
2. Shadow-mode proxy request produces a row with `source == "proxy_async"`.
3. Two records with the same `audit_id` both exist (append-only).
4. `limit=0`, `limit=-1`, `limit=100000` return HTTP 422.
5. No admin key returns 401; wrong key returns 403; correct key returns 200.
6. Audit write failure (monkeypatch `audit_store.record_audit` to raise) does not fail `/verify`.

**Verify (acceptance):** start the app (`uvicorn server.app:app --port 8000`), log in, run a verification in the UI, open Audit log, enter the admin key, click Load records. A row appears with populated Source, Request, Summary, Score, Latency columns, and expanding Inspect shows the text and claims. Leaving the key blank shows the inline message and does **not** log you out.

---

## Phase 2. Backend bugs found during the review

Each item: file, problem, fix, test.

**2.1 SSRF via request headers** (`server/app.py` `verify_answer`, anonymous branch). `X-LLM-Base-URL` / `req.base_url` is passed to `build_request_pipeline()` with no validation. `validate_base_url` is imported at the top of `app.py` but never called there. Fix: wrap `header_base_url` in `validate_base_url(...)` (catch `ValueError` and raise `HTTPException(400, str(e))`). Also validate inside `build_user_pipeline` for stored URLs. Test: a request with `X-LLM-Base-URL: http://169.254.169.254` under `FRESHGATE_ENV=production` returns 400.

**2.2 Admin-key comparison crashes on non-ASCII** (`core/security.py` `verify_admin_key` and `verify_api_key`). `hmac.compare_digest(str, str)` raises `TypeError` for non-ASCII input (reproduced), giving a 500 on `X-Admin-Key: é`. Fix: compare bytes: `hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))`. Test: non-ASCII header returns 403.

**2.3 Version-fragile Starlette constants** (`server/app.py` `status.HTTP_422_UNPROCESSABLE_CONTENT`; `core/security.py` `status.HTTP_413_CONTENT_TOO_LARGE`). These names exist only in newer Starlette releases and `requirements.txt` is unpinned (`fastapi>=0.110.0`). On an older Starlette the enforce-block line raises `AttributeError` inside the broad `except (asyncio.TimeoutError, Exception)`, which **silently fails open and never blocks**. Fix: use integer literals `422` and `413`, and pin versions in `requirements.txt` (`fastapi==<version from pip freeze>`, `starlette`, `uvicorn`, `httpx`, `pydantic`, `pymongo`; also add `mongomock` note that it is dev-only). Test: enforce-mode block returns 422.

**2.4 Over-broad `except` hides enforcement errors** (`server/app.py` ~line 767). Policy-block construction and header building live inside the same `try` as verification. Fix: narrow the `try` to just the `asyncio.wait_for(pipeline.verify(...))` call; build headers and the block response after it.

**2.5 Streaming SSE parser drops tokens** (`server/app.py` `event_generator`). It splits each network chunk by lines and `json.loads` each `data:` line. A JSON object split across two chunks raises, is swallowed by `except Exception: pass`, and that token text is lost, so the text sent to verification is incomplete. Fix: keep a `buffer` string, append decoded chunk (use an incremental decoder `codecs.getincrementaldecoder("utf-8")()` so multi-byte characters do not break), split on `"\n"`, keep the last partial line in the buffer, and parse only complete lines. Test: feed a stream where one `data:` line is split mid-JSON; assert the accumulated text is complete.

**2.6 Body-size limit is bypassable** (`core/security.py` `RequestBodySizeLimitMiddleware`). It only checks `Content-Length`; chunked requests skip it. Fix: when the header is absent, read the stream with a counter and return 413 once it exceeds the limit (implement as pure ASGI middleware rather than `BaseHTTPMiddleware`, because the latter also breaks streaming cancellation). Test: chunked body over the limit returns 413.

**2.7 CPU-heavy password hashing on the event loop** (`server/app.py` `register`, `login`). PBKDF2 with 310,000 iterations runs synchronously inside `async def`, stalling every other request (~100-300 ms each). Fix: `await asyncio.to_thread(password_hash, ...)` and `await asyncio.to_thread(check_password, ...)`. Also run a dummy `check_password` when the user does not exist so login timing does not reveal which emails are registered.

**2.8 Login throttle is trivially bypassed and per-process** (`server/app.py`). The key is `ip:email`, so rotating emails resets it, and the dict is not shared across workers. Fix: add a second counter keyed on IP alone (limit 30 per 15 min). Note Redis option in docs; do not add new deps.

**2.9 Rate limiter issues** (`core/security.py`). (a) `DistributedRateLimiter` adds a ZSET entry before checking, so rejected requests still consume budget and `retry_after` is a constant. (b) `redis.Redis.from_url` has no socket timeouts, and a hung Redis blocks import and every request. (c) Sync Redis calls are made inside async middleware. Fix: pass `socket_timeout=0.2, socket_connect_timeout=0.5`, check count before `zadd`, compute `retry_after` from the oldest entry, and fall back to the local limiter on any Redis error (already partially done).

**2.10 Test-detection hack in production code** (`server/app.py` `verify_answer`: `getattr(pipeline.verify, "__class__").__name__ in ("AsyncMock", ...)`). Production code should not know about mocks. Fix: replace with an injectable `get_request_pipeline` dependency (`Depends`) that tests override via `app.dependency_overrides`. Update the affected tests (`test_server_api.py`, `test_proxy.py`, `test_streaming_gateway.py`) accordingly and report which tests you changed.

**2.11 Single-flight key leaks across users** (`core/pipeline.py` line ~262). The key is `f"{scope or 'default'}:{type}:{text}"`. With `scope=None` (Watchtower, proxy) two owners with different credentials/policies share one in-flight verification. Fix: include `owner`/`policy.id` in the key (`f"{scope}:{policy.id if policy else '-'}:{c.type}:{...}"`) and pass `owner_id` into `verify()` from Watchtower.

**2.12 Cache is shared across users** (`server/pipeline_factory.py` `cache=base.cache`). User B can receive a result produced with user A's Tavily key and policy. Acceptable for public facts; add an `owner`-agnostic note to docs, or add `owner_id` to the cache key if per-user isolation is required. Decide with the human before changing.

**2.13 Cookie/session edge case** (`server/users.py` `optional_current_user`). `serializer()` can raise `HTTPException(503)` which is not caught by `except (BadSignature, PyMongoError)`, so an anonymous call with a stale cookie returns 503. Fix: catch `HTTPException` there and return `None`.

**2.14 `/api/stream/verification/{id}` is unauthenticated** and results live in a process-global dict. Fix: require `Depends(require_user_or_admin)` and cap by TTL (drop entries older than 15 minutes on write).

**2.15 Go plugin cannot authenticate** (`plugin/freshgate_plugin.go`). It POSTs to `/verify`, but `/verify` returns 401 for anonymous callers without provider/Tavily keys, so the plugin silently fails open and never verifies anything. Fix: add `api_key` / `admin_key` config, send `X-LLM-API-Key` and `X-Tavily-API-Key` (or add a dedicated server-side `/api/plugin/verify` guarded by `FRESHGATE_API_KEY`). Document the chosen route in `plugin/README.md`. Rebuild the `.so` is out of scope; do not commit it.

**2.16 Docker/compose hygiene** (`docker-compose.yml`, `Dockerfile`). (a) default session secret `dev-secret-change-me-...` is committed; require the variable (`${FRESHGATE_SESSION_SECRET:?set it}`). (b) Mongo and Redis publish ports `27017`/`6379` to the host with no auth; remove the `ports:` entries. (c) `bifrost:latest` is unpinned. (d) the obsolete `version:` key. (e) `FRESHGATE_API_KEY` is not passed through. (f) `COPY bench/` ships 20 MB of benchmark data into the image; drop it or exclude `*.db` explicitly.

---

## Phase 3. Frontend bugs

**3.1** Audit view (done in Phase 1.5).

**3.2 CSP vs inline styles.** `SecurityHeadersMiddleware` sends `style-src 'self'` (no `'unsafe-inline'`), which blocks style attributes. `App.tsx` has a `style={{ ... }}` usage (grep `style={{`). Either convert it to a CSS class or CSS variable set via a class, or accept `style-src 'self' 'unsafe-inline'`. Prefer removing the inline style. Check the browser console for `Refused to apply inline style`.

**3.3 Empty `catch {}` blocks** (`App.tsx` lines ~279, ~333, ~633). Swallowed errors hide real failures. Replace with `catch (cause) { console.error(cause); setError(...) }` where a user-visible surface exists, and add a comment where ignoring is intentional.

**3.4 Index keys** (`App.tsx` ~line 1701, `key={index}` on source records). Use a stable key (`row.url ?? row.id ?? index`).

**3.5 Stale build artefact.** `dashboard/dist/` is committed and is also in `.gitignore`. Remove it from git (`git rm -r --cached dashboard/dist`) and let the Dockerfile build it. After Phase 1.5 run `npm run build` and confirm `dist/` is regenerated.

**3.6 Type safety.** `RecordValue` is used for audits, snapshots and alerts. After adding `AuditRecord`, run `npx tsc --noEmit` and fix any new errors.

**Verify:** `cd dashboard && npm ci && npm run build` succeeds with no TS errors; open every sidebar page in light and dark mode and check the browser console is clean.

---

## Phase 4. Docs and repo consistency

- `docs/13_api_reference.md`: document `/api/audits/recent` params (`limit` 1-500, `offset`, `source`), the new fields, and that records are append-only.
- `docs/04_threat_model.md` claims an "append-only" audit DB. After 1.1 this is true; add that there is no tamper-evidence (optional follow-up: store `prev_hash` = SHA-256 of the previous row to make it chained).
- `docs/10_production_operations_guide.md` and `docs/11_deployment_and_rollback_guide.md` say audit data lives on a persistent `/data` disk while `render.yaml` uses `/tmp`. Make them agree with whichever option you picked in 1.6.
- `README.md`: add one paragraph "Where audit records come from" listing the five `source` values.

---

## Final checklist (agent must report each as pass/fail)

1. `python -m pytest -q` is green, and no `freshgate_*.db` is created in the repo root by the run.
2. `cd dashboard && npm run build` is green.
3. Verify tab run produces an audit row (Phase 1 acceptance test).
4. Proxy request in `shadow`, `warn`, `enforce` and streaming mode each produce an audit row.
5. Audit page with blank key shows an inline message and keeps the session.
6. Audit page with wrong key shows "invalid admin token"; with correct key lists rows with all columns populated.
7. `curl -H "X-Admin-Key: é" localhost:8000/api/audits/recent` returns 403, not 500.
8. `curl "localhost:8000/api/audits/recent?limit=-1" -H "X-Admin-Key: <key>"` returns 422.
9. Setting `FRESHGATE_ADMIN_KEY=your_admin_secret_token_here` makes admin routes return 403.
10. No secrets appear in `git diff`, logs, or `docker-compose.yml`.

## Notes on how this review was done

This was a static review plus targeted execution of the stdlib-only audit store. The Python dependencies are not installed in my environment, so I could not run the full test suite or the dashboard build. Items A1-A9, 2.2 and the column mismatch were confirmed directly in code (2.2, A6 and A7 were also reproduced by running it). Items 2.3 (Starlette constant names), 2.15 (plugin auth) and 3.2 (CSP) depend on the installed library versions or on runtime behavior, so the agent should confirm each with the quoted grep or a quick repro before changing code.
