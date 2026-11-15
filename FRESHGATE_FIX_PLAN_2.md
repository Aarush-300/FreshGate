# FreshGate: Fix Plan #2 (post-review of the first round)

Round 1 (audit log, SSRF, admin-key handling, rate limiter, pins) was applied and mostly lands correctly. This round covers
(a) mistakes and gaps in the round-1 fixes, and (b) problems in modules that round 1 never touched.

## How to use

Paste into Antigravity:

```
Read FRESHGATE_FIX_PLAN_2.md completely. Execute ONE phase at a time, in order.
Before editing a file, open it and confirm the code I quote still exists. If it differs, tell me and stop.
After each phase run its verification commands and fix failures before moving on.
Do not delete or skip existing tests. Never print, log, or commit any key. Never edit .env.
Do not add dependencies. Do not refactor anything not listed. Files use CRLF: preserve line endings.
At the end report: files changed per phase, test output, anything unfinished.
```

Always: `pip install -r requirements-dev.txt` first; `python -m pytest -q` after backend phases; `cd dashboard && npm run build` after frontend phases.

---

## Phase 1. Security holes in the round-1 fixes (do first)

### 1.1 Unknown-user login still has a timing oracle (`server/app.py` `login`)
The "dummy hash" is `"pbkdf2:sha256:310000$dummy$000..."`. `check_password` does `stored.split(".", 1)`, there is no `"."`, so it raises `ValueError`, is caught, and returns `False` **without hashing**. Unknown emails are therefore still much faster than known ones.
Fix: in `server/users.py` add `DUMMY_PASSWORD_HASH = password_hash(secrets.token_urlsafe(16))` at module level (computed once at import). In `login`, use it: `await asyncio.to_thread(check_password, body.password, DUMMY_PASSWORD_HASH)`.
Test: `check_password("x", DUMMY_PASSWORD_HASH)` returns `False` and takes > 10 ms (it really hashed).

### 1.2 `/api/plugin/verify` is open when `FRESHGATE_API_KEY` is unset, and not rate-limited
`verify_api_key` deliberately passes everything when no key is configured ("open local dev mode"). `/api/plugin/verify` and `/v1/chat/completions` both spend the **server's** `OPENAI_API_KEY` / `TAVILY_API_KEY`. On Render `FRESHGATE_API_KEY` is not set, so anyone on the internet can burn those credits. `RateLimitMiddleware` only covers paths starting with `/verify`, `/proxy`, `/v1/chat/completions`, so the plugin route is unthrottled.
Fix:
1. In `core/security.py` add `require_api_key_strict(request)`: if `FRESHGATE_ENV=production` and no API key is configured, raise `HTTPException(503, "FRESHGATE_API_KEY must be set for this endpoint")`; otherwise behave like `verify_api_key`.
2. Use it as the dependency on `/api/plugin/verify`, `/proxy/v1/chat/completions` and `/v1/chat/completions`. Keep dev mode open outside production.
3. Add `"/api/plugin"` to the rate-limited prefixes in `RateLimitMiddleware`.
4. Add `FRESHGATE_API_KEY` to `render.yaml` with `generateValue: true`.
Tests: production env + no key returns 503; wrong key 403; right key 200; 61st request in a minute returns 429.

### 1.3 `plugin/plugin.json` still points to the old route
`plugin.json` has `"freshgate_url": "http://127.0.0.1:8000/verify"` and no `api_key`. Config values override the Go default, so the plugin still calls the old, unauthenticated route and silently fails open.
Fix: set `"freshgate_url": "http://127.0.0.1:8000/api/plugin/verify"` and add `"api_key": ""`. Mention in `plugin/README.md` that the key should come from the `FRESHGATE_API_KEY` env var, not the json file.

### 1.4 Rate-limit identity can be forged (`core/security.py` `RateLimitMiddleware`, `Dockerfile`)
- `client_id = f"token:{token}"` uses any token the caller sends **without validating it**. Rotating a fake `X-API-Key` per request gives a fresh bucket each time, so the limit is bypassed.
- `Dockerfile` runs `--proxy-headers --forwarded-allow-ips='*'`, so any client can spoof `X-Forwarded-For` and pick its own IP. This also defeats the IP-based login throttle.
Fix:
1. Always key on IP: `client_id = f"ip:{client_ip}"`. If a token is present **and** valid (`hmac.compare_digest` against the configured key), add a second, more generous bucket keyed on a hash of the token; never create buckets for invalid tokens.
2. Dockerfile: replace `'*'` with `${FORWARDED_ALLOW_IPS:-127.0.0.1}` and set `FORWARDED_ALLOW_IPS=*` only in `render.yaml` (Render terminates TLS at its own proxy).
3. Add a periodic sweep to `SlidingWindowRateLimiter`: on every 1000th call delete keys whose newest timestamp is older than the window. Today idle client ids are never evicted, so memory grows with the number of distinct IPs.
Tests: invalid rotating tokens still hit 429 after the limit; spoofed `X-Forwarded-For` is ignored when the peer is not trusted.

### 1.5 Placeholder secrets in `.env.example` pass validation
`FRESHGATE_SESSION_SECRET=replace_with_a_random_secret_at_least_32_characters` is longer than the 24-char minimum, so anyone who copies the example verbatim gets a **publicly known** session-signing secret (forged login cookies). `FRESHGATE_API_KEY=optional_client_api_key_here` is the same problem.
Fix:
1. In `server/users.py`, reject placeholders in `serializer()` and `_fernet()`: lowercase value starts with `replace`, `your_`, `changeme`, `dev-secret`, or equals `secret`. Raise the existing 503.
2. In `core/security.py` apply the same placeholder rule to `get_configured_api_key()` (treat as unset).
3. `.env.example`: leave `FRESHGATE_SESSION_SECRET=` and `FRESHGATE_API_KEY=` empty, and move all inline `# ...` comments onto their own line (`FRESHGATE_ADMIN_KEY=  # ...` relies on parser-specific inline-comment handling).
4. `docker-compose.yml` already requires the secret (`:?`); keep that.

### 1.6 Proxy and plugin routes accept unlimited unauthenticated use of upstream
Covered by 1.2. Additionally forward only needed headers upstream: today `forward_to_upstream` copies **every** client header (cookies, `X-Admin-Key`, `X-FreshGate-*`) to Bifrost. Allow-list: `authorization`, `content-type`, `accept`, `x-request-id`, and any `x-bf-*` headers Bifrost documents. Test: `Cookie` and `X-Admin-Key` are not forwarded.

**Verify phase 1:** `python -m pytest -q`; manual: `curl -X POST localhost:8000/api/plugin/verify -d '{"answer":"x"}'` with `FRESHGATE_ENV=production` and no key returns 503.

---

## Phase 2. Streaming and proxy correctness

### 2.1 One bad SSE line drops the rest of the chunk (`server/app.py` `event_generator`)
The new buffering fixed split JSON, but the whole `for line in lines:` loop still sits inside one `try/except Exception: pass`. A single non-JSON `data:` line (keep-alive, provider error object) aborts parsing for every remaining line in that chunk, so tokens are silently lost.
Fix: extract a helper and call it per line with its own try/except:
```python
def _consume_sse_line(line: str, state: dict) -> None:
    line = line.strip()
    if not line.startswith("data:"):
        return
    data = line[5:].strip()
    if not data or data == "[DONE]":
        return
    try:
        parsed = json.loads(data)
    except ValueError:
        return
    if not state.get("completion_id") and parsed.get("id"):
        state["completion_id"] = parsed["id"]
    choices = parsed.get("choices") or []
    if choices and isinstance(choices, list):
        piece = (choices[0].get("delta") or {}).get("content")
        if piece and state["chars"] < 50000:
            state["chunks"].append(piece)
            state["chars"] += len(piece)
```
Use it in both the main loop and the final-buffer flush (this removes the ~20 duplicated lines in the `finally`). Note the code requires `"data: "` with a space; the SSE spec allows `data:` without one, which the helper above handles.
Test: stream with `data: {bad`, then a good chunk in the same network read; assert the good token is kept.

### 2.2 No audit for fail-open, timeout, circuit-open, and failed `/verify`
Audits are written only on success. The compliance log therefore hides exactly the incidents that matter. Add a small `write_audit_failure(text, source, mode, reason, corr_id, owner_id)` in `core/audit_helpers.py` that stores an empty-claims record with `status="fail-open"` or `"error"` and the reason in `mode`/`status`. Call it from the proxy `except` branch, the circuit-open branch, and the `verify_answer` exception path.
Test: force `pipeline.verify` to raise; a `fail-open` audit row exists.

### 2.3 Audit store polish (`core/audit.py`)
1. Delete the duplicated `audit_store = get_audit_store()` at the bottom of the file (it builds two stores).
2. `json.dumps(claims_data)` needs `default=str`; one non-serializable value currently loses the whole record (only a log line).
3. Docstring says "SQLite and PostgreSQL backends"; PostgreSQL is not implemented. Fix the wording.
4. The dashboard shows `String(value ?? "proxy")` for the Source column, so old rows with `NULL` source are mislabeled "proxy". Show `"legacy"`.
5. `input_text` is stored in plaintext (truncated to 5000 chars) for every user. Add `FRESHGATE_AUDIT_REDACT_INPUT=true` option to store only a SHA-256 + first 120 chars. Document it.

### 2.4 Circuit breaker rarely trips and over-admits (`core/circuit_breaker.py`, `server/app.py`)
- `pipeline.verify()` catches provider errors and returns results with status `verification_failed` / `extraction_failed`; the proxy then calls `circuit_breaker.record_success()`, so a dead provider never opens the breaker.
  Fix: after `verify`, if `verification.summary.status in ("verification_failed","extraction_failed")` call `record_failure()` instead of `record_success()`.
- In `HALF_OPEN`, `can_execute()` returns `True` for every concurrent request, so `half_open_probes` does not limit anything (thundering herd). Track `_probes_in_flight` and only admit `half_open_probes` at a time.
Tests for both.

### 2.5 `verify_answer` still contains test-only branching
`elif pipeline is not _shared_pipeline_instance:` exists purely so tests that patch the module-level `pipeline` keep working, and the policy-loading block is copied three times.
Fix: extract `def _load_policy(req, user)` and call it once after the pipeline is chosen. Remove the `pipeline is not _shared_pipeline_instance` branch and update the few tests that patch `server.app.pipeline` to use `app.dependency_overrides[get_request_pipeline]` (as `test_server_api.py` already does). Report the tests you changed.

### 2.6 Watchtower double-runs verification on `TypeError` (`core/watchtower.py`)
```python
try: result = await active_pipeline.verify(..., owner_id=owner_id)
except TypeError: result = await active_pipeline.verify(...)   # runs the whole check again
```
Any `TypeError` raised deep inside verification (a real bug) triggers a second full, billable run and hides the first traceback. Remove the fallback; make test doubles accept `**kwargs`.

### 2.7 Body-size middleware is convoluted (`core/security.py` `RequestBodySizeLimitMiddleware`)
It raises `HTTPException` inside `receive`, drops all downstream sends while `exceeded`, then sends a 413 after the app returns. Any handler that wraps `request.body()` in `except Exception` can continue with a truncated body before the 413 goes out. Simplify: for requests without a `Content-Length`, read the body messages up front into a buffer (stopping once the limit is exceeded). If the limit is exceeded, send the 413 immediately and never call the app; otherwise replay the buffered messages to the app through a replacement `receive`. Tests: chunked oversize body returns 413 on `/verify`, `/v1/chat/completions` and `/api/auth/register`; a legitimate chunked body under the limit works.

**Verify phase 2:** `python -m pytest -q`; stream a mixed good/bad SSE sample through the test client.

---

## Phase 3. Concurrency and storage bugs in untouched modules

### 3.1 Single-flight cancellation bug (`core/cache.py` `SingleFlightCoalescer.do`)
- Followers do `return await fut`. Cancelling a follower (client disconnect, `asyncio.wait_for` timeout in the proxy, `FRESHGATE_TIMEOUT=8s`) **cancels the shared future**, so every other follower fails with `CancelledError` and the leader's result is thrown away.
- If the leader is cancelled, `fut.set_exception(CancelledError)` hands a cancellation to unrelated followers; `CancelledError` is a `BaseException`, escapes `asyncio.gather`'s `except Exception` in `pipeline.py`, and kills the follower request instead of failing open.
- If nobody awaits the future, asyncio logs "Future exception was never retrieved".
Fix:
```python
if not is_leader:
    return await asyncio.shield(fut)
try:
    result = await coroutine_func(*args, **kwargs)
    if not fut.done(): fut.set_result(result)
    return result
except asyncio.CancelledError:
    if not fut.done(): fut.set_exception(RuntimeError("coalesced verification was cancelled"))
    fut.exception()  # mark retrieved
    raise
except Exception as e:
    if not fut.done(): fut.set_exception(e)
    fut.exception()
    raise
finally:
    async with self._lock: self._in_flight.pop(key, None)
```
Tests: cancel one follower, assert the other still gets the result; cancel the leader, assert followers get `RuntimeError`, not `CancelledError`.

### 3.2 Blocking SQLite/Redis calls inside `async` code
`pipeline.verify()` calls `self.cache.get(...)` and `self.cache.set(...)` synchronously (SQLite behind a `threading.Lock`, or Redis). Each call blocks the event loop and every other request. Round 1 fixed this for audits only.
Fix: in `core/pipeline.py` wrap all cache calls: `await asyncio.to_thread(self.cache.get, ...)` and `await asyncio.to_thread(self.cache.set, verified, ...)`. Same for `health_ready()` (`pipeline.cache.get_stats()`), `/api/provider/status`, `/api/cache/clear`. Keep signatures unchanged.

### 3.3 Expired cache rows are never purged
`ClaimCache` has a method that deletes `expires_at <= now`, but nothing calls it (no scheduler, no startup hook). `freshgate_cache.db` grows forever.
Fix: add a task in `lifespan` that calls it every 30 minutes and once at startup; cancel it on shutdown. Test: insert an expired row, call the sweep, assert it is gone.

### 3.4 Mongo indexes are created once, with no retry (`server/app.py` `lifespan`, `core/mongo.py`)
If MongoDB is not ready at boot (docker-compose uses `condition: service_started`, which does not wait for readiness; Atlas can also blip), `ensure_indexes` logs an error and is never retried. The unique `users.email` index then never exists, and `register` relies on `DuplicateKeyError` for uniqueness, so duplicate accounts become possible.
Fix:
1. `lifespan`: run `ensure_indexes` in a background task with exponential backoff (1, 2, 4 ... 30 s) until success; log once per failure.
2. `register`: before insert, `find_one({"email": email})` and return 409 if it exists (defense in depth).
3. `docker-compose.yml`: give `mongo` a healthcheck (`mongosh --quiet --eval "db.adminCommand('ping')"`) and set `depends_on.mongo.condition: service_healthy`.
4. `/health/ready` should report `"indexes": true/false`.
Test: `ensure_indexes` fails twice then succeeds; the task ends after success.

### 3.5 Login throttle memory growth (`server/app.py`)
`_login_attempts` keys (`ip:email`, `ip:<ip>`) are only pruned when the same key logs in again, so it grows with attacker-chosen emails. Add a sweep: on each call drop keys with no timestamps newer than 15 minutes when the dict exceeds 10,000 entries.

**Verify phase 3:** `python -m pytest -q`; load test: 50 concurrent identical `/verify` requests with one cancelled midway; the others succeed.

---

## Phase 4. Frontend

1. **Audit view pagination.** The API already returns `total`, `limit`, `offset`, but the UI only shows the first 50 and labels the table with the filtered count (`{filtered.length} records`), which is misleading. Store `total`; show `"{filtered.length} of {total}"`; add a "Load more" button that requests `?offset=<loaded>` (append results) and a Source filter dropdown (`verify, proxy, proxy_stream, proxy_async, watchtower, plugin`) that passes `source=`. Keep the admin key in memory only; keep it available for "Load more" until the user leaves the page (clear on unmount, not after the first request), otherwise paging requires retyping the key.
2. **Missing CSS.** `.audit-detail-body` is used in `AuditView` but is not defined in `styles.css`. Add it (padding, `max-height: 320px; overflow: auto` for the `pre`, `word-break: break-word` for the input paragraph) using existing color tokens for both themes.
3. **Source label:** legacy `NULL` source shows `"legacy"`.
4. **Status tones.** `tone()` now handles `verified`. Also add `fail-open`, `error` (bad), `no-claims-detected` (good) so the new audit rows are colored correctly.
5. Run `cd dashboard && npx tsc --noEmit` and fix any type errors from the `row as AuditRecord` casts (use a small type guard instead if needed).

**Verify phase 4:** `npm run build`; seed 120 audit rows; confirm "Load more" appends rows and the count reads `100 of 120` then `120 of 120`.

---

## Phase 5. Dependencies, Docker, deploy

1. **Verify the new pins actually install.** `requirements.txt` now pins `fastapi==0.163.0`, `starlette==1.10.0`, `uvicorn==0.54.0`, `pydantic==2.14.3`, `pymongo[srv]==4.20.0`, `cryptography==50.0.2`, `python-dotenv==1.2.4`. I could not confirm these versions exist or are mutually compatible. In a clean venv run `pip install -r requirements.txt && pip check`. If resolution fails: remove the explicit `starlette==` line (FastAPI pins its own compatible range) and pin to versions `pip index versions <pkg>` actually lists. Also pin `redis` and the dev requirements (`pytest`, `pytest-asyncio`, `mongomock`). Confirm the Dockerfile's Python (`3.11.9`) satisfies every pinned package's `Requires-Python`.
2. **Verify the Bifrost image tag.** `docker pull maximhq/bifrost:v1.3.1`. If it does not exist, use a tag that does (check Docker Hub) and keep it pinned.
3. **Compose:** `FRESHGATE_STORE=mongo` and the Dockerfile `VOLUME /data` are fine; remove the unused `CACHE_DB_PATH` mention in `.env.example` if `core/config.py` prefers `FRESHGATE_CACHE_DB` (it reads both; keep only one in docs).
4. **Render:** still `plan: free` with the audit DB in `/tmp`. That is acceptable only with the UI notice added in round 1; the README must say audit data resets on redeploy, and Watchtower sleeps when the free instance spins down. If persistence matters for the demo, move to `starter` plus a `disk` mounted at `/data` and point the three SQLite paths there.
5. **`COPY bench/`** in the Dockerfile ships ~20 MB of benchmark data and caches (`*.db` are only excluded by `.dockerignore`; confirm). Drop the `COPY bench/` line if `/api/benchmark` only needs `bench/results.md` and `dataset_production.json`; copy just those two files.

---

## Phase 6. Tests to add (`tests/test_round2.py`)

One test per fix above, at minimum: 1.1, 1.2 (503/403/200/429), 1.4 (rotating fake tokens), 1.5 (placeholder secrets rejected), 1.6 (header allow-list), 2.1, 2.2, 2.4 (both), 3.1 (both), 3.3, 3.4, 3.5. Use the `admin_headers`/`auth_cookie` fixtures and `app.dependency_overrides[get_request_pipeline]`.

## Final checklist (report pass/fail for each)

1. `python -m pytest -q` is green; no `freshgate_*.db` appears in the repo root afterwards.
2. `pip install -r requirements.txt && pip check` is clean in a fresh venv.
3. `cd dashboard && npm ci && npm run build` is green.
4. Production env + no `FRESHGATE_API_KEY`: `/api/plugin/verify` and `/v1/chat/completions` return 503.
5. Rotating fake `X-API-Key` values still hit 429.
6. Unknown-email login takes about as long as a wrong-password login for a known email.
7. Cancelling one of two coalesced requests does not fail the other.
8. Stop Mongo, start FreshGate, start Mongo: indexes are created without a restart.
9. Audit page: filter by source, load more past 50, rows for fail-open are visible.
10. No secrets in `git diff`, `.env.example`, `docker-compose.yml` or logs.

## Honest limits of this review

This was static review plus running stdlib-only snippets. The Python dependencies and `node_modules` are not available in my environment, so I could not run the test suite or the dashboard build, and I could not verify the package versions or the Bifrost image tag. Items 1.1, 3.1 and 3.4 follow directly from reading the code; confirm each with the quoted repro before changing it.
