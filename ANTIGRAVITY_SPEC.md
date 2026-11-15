# FreshGate: Antigravity Prompt + Implementation Spec

How to use this file:
1. Copy **PART A** (the short prompt) into Antigravity's chat.
2. Put this whole file in your repo root as `ANTIGRAVITY_SPEC.md` (PART A tells the model to read it). If you'd rather paste everything, paste Parts A and B together.
3. Before you start: **rotate every key in your `.env`** (Tavily, OpenAI/Groq, MongoDB, admin key). Your zip contained the real `.env`. Treat those keys as leaked.

---

# PART A: PROMPT TO PASTE INTO ANTIGRAVITY

```
You are working on FreshGate, a FastAPI (Python 3.11) + React/Vite/TypeScript project in this repo.
It is being submitted to a hackathon. It must be deployed on Render and pushed to GitHub.

Read ANTIGRAVITY_SPEC.md in the repo root completely before doing anything. It is your source of truth.
It lists every bug I found, the exact files and functions to change, code snippets, the dark-mode color
palette, and the deployment steps.

Rules:
1. FIRST produce an Implementation Plan artifact that mirrors the phases in the spec (Phase 0 to Phase 11),
   with a checklist per phase. Do not write code until the plan exists.
2. Then execute ONE phase at a time, in order. After each phase run:
   `python -m pytest -q` and, for frontend phases, `cd dashboard && npm run build`.
   Fix failures before moving on. Do not delete or skip existing tests. Update a test only if the
   spec says the behavior it asserts is intentionally changing, and say so in your summary.
3. Use the installed "impeccable" skill for every UI phase (Phases 7, 8, 9). Read DESIGN.md and PRODUCT.md first.
   If it exposes audit / critique / normalize / polish / harden style commands, run them after finishing the
   UI phases and apply the findings that do not contradict the spec. The color palette in Phase 8 is
   mandatory and overrides any palette the skill suggests.
4. Do not invent features outside the spec. Do not add dependencies except those the spec lists.
5. Never print, log, commit, or hard-code any API key. Never edit `.env`.
6. When done, give me: (a) a list of files changed per phase, (b) test results, (c) the manual test
   checklist from Phase 11 with each item marked pass/fail, (d) anything you could not finish and why.
```

---

# PART B: THE SPEC

## 0. Project map (what exists today)

| Area | Files |
|---|---|
| FastAPI app, auth, profile, `/verify`, gateway proxy | `server/app.py` (820 lines) |
| Mongo users, sessions, Fernet encryption helpers | `server/users.py` |
| Provider catalog + model listing | `server/routes/providers.py` |
| Watchtower API | `server/routes/watchtower.py` |
| Policy API | `server/routes/policies.py` |
| History/Replay API | `server/routes/replay.py` |
| SQLite store (policies, monitored claims, snapshots, alerts) | `core/persistence.py` (`SQLiteFeatureStore`, `get_feature_store()`) |
| Scheduler | `core/watchtower.py` (`WatchtowerService`) |
| Pipeline | `core/pipeline.py`, `core/history.py`, `core/policies.py`, `core/replay.py` |
| LLM calls | `core/llm.py`, `core/llm_manager.py` |
| Dashboard | `dashboard/src/App.tsx` (1091 lines), `styles.css`, `api.ts`, `types.ts` |
| Deploy | `Dockerfile`, `render.yaml`, `docker-compose.yml`, `.gitignore`, `.dockerignore`, `.env.example` |

## 1. Root causes found (read this before coding)

These are real bugs in the current code. Each is fixed in a phase below.

**Backend / data**
- **B1. Watchtower claims live in local SQLite, and Mongo only gets a mirror copy.** `monitor_claim()` in `server/routes/watchtower.py` saves to SQLite and then `$push`es a copy into `users.claims`. `list_claims()` reads the SQLite rows again. On Render's free tier SQLite is wiped on every deploy and restart. Fix: Mongo becomes the source of truth. (Phases 1, 2, 5)
- **B2. Watchtower ignores the user's saved provider config.** `core/watchtower.py::_get_pipeline()` builds `FreshGatePipeline()` from **server env** `OPENAI_API_KEY` and `LLM_MODEL` (default `gpt-4o-mini`). That is exactly the error in your screenshot: `model_not_found: gpt-4o-mini`. Scheduled checks must use the **claim owner's** saved settings. (Phase 4)
- **B3. No per-user isolation at all.** Policies, monitored claims, alerts, history snapshots and replay are global SQLite tables with no owner column. Every route in `policies.py`, `replay.py` and `watchtower.py` (except one fallback branch) is unauthenticated, so anyone, logged in or not, can read, edit and delete everyone's data. `GET /api/watchtower/claims` for a logged-out visitor returns all claims. `DELETE /api/watchtower/claims/{id}` has no ownership check. (Phases 2, 5, 6)
- **B4. `/verify` ignores saved profile settings in a subtle way.** In `verify_answer()`: `user_settings.get("has_api_key")` is read from the raw Mongo settings dict, which has no such key (it exists only in `public_settings()`). So `effective_provider` is never taken from the profile. It also lets **logged-out callers use your server's OPENAI_API_KEY** via fallback (`llm_manager.complete`: `if effective_key is None: effective_key = get_config().openai_api_key`). That burns your money. (Phase 4)
- **B5. SSRF.** The user-supplied `base_url` is used by the server to make HTTP requests with no private-IP check. On Render someone can point it at internal addresses. (Phase 3)
- **B6. `decrypt_secret()` returns the ciphertext on failure** (`except Exception: return value`). If `FRESHGATE_SESSION_SECRET` ever changes, the encrypted blob is sent to the provider as if it were the API key. (Phase 3)
- **B7. "Load models" needs the key typed in again.** `POST /api/providers/{id}/models` requires an `X-LLM-API-Key` header even when the user already saved a key. It also ignores the user's `base_url` for non-Ollama providers and refuses `custom`. (Phase 3)
- **B8. Public endpoints leak server info.** `/api/provider/status` (returns config and masked key previews), `/api/deployment/status`, `/api/benchmark`, `/metrics` need no login. (Phase 6)
- **B9. Rate limiter sees only Render's proxy IP**, so all users share one bucket. Uvicorn must run with `--proxy-headers`. There is no brute-force protection on login. (Phases 6, 10)
- **B10. Nonsense alert in your screenshot:** `unverifiable → unverifiable`. In `core/watchtower.py::_check_leased`, the line `if previous_verdict and current_verdict == "unverifiable": reason = "Repeated checks..."` fires even when the previous verdict was also `unverifiable`. (Phase 5)
- **B11. `register()` calls `create_index` on every signup**, email is not validated, and `users.claims` and `users.alerts` arrays become dead. (Phases 1, 3)
- **B12. Readiness probe** `/health/ready` only checks SQLite cache, not Mongo. (Phase 10)

**Frontend**
- **F1. Provider section is duplicated** inside `VerifyView` (`<fieldset className="provider-config">`). The Verify page asks for key, base URL and model again. (Phase 7)
- **F2. `ProfileView` has no "Load models" button** and the Model field is a plain text input. Base URL is a free-typed field so users must google it. Tavily is not marked required. (Phase 7)
- **F3. Profile `useEffect` has `[provider, baseUrl, model]` as dependencies and re-fetches `/api/providers` on every keystroke.** (Phase 7)
- **F4. Editing Base URL on Verify wipes the typed API key** (`if (llmKey) setLlmKey("")`). (Phase 7, removed with the section)
- **F5. No delete button for Watchtower claims** (the API exists, the UI doesn't), and the Mongo mirror isn't cleaned on delete. (Phases 5, 7)
- **F6. After "Sign out" the previous user's `claims`, `alerts`, `policies` stay in React state**, so the next login on the same tab briefly shows them. The 45-second `setInterval` keeps polling while logged out. A 401 from any API call doesn't send the user to the login screen. (Phase 7)
- **F7. Watchtower row shows a stale score (22) next to a failed status.** (Phase 7)
- **F8. Layout bugs visible in your screenshots:** (a) the "Base URL" input is pushed to the bottom of its grid cell, so the label floats far above it, because `.provider-grid` stretches items to the height of the tall Model cell (select + input stacked); (b) the helper line "Use an OpenAI API key…" collides with the "Tavily search API key" label (no spacing); (c) the model select shows "Other…" with an empty second input, which looks broken. (Phase 9)
- **F9. Dark mode is ugly because of 183 hard-coded `light-dark(#light, #dark)` pairs in `styles.css`**, and the dark halves are muddy warm browns (`#2e291d`, `#342f25`, `#38342c`, `#3c3830`, `#413d36`...). Placeholders are `#c5c4bf`, almost as bright as real text. (Phase 8)

**Deploy**
- **D1. `render.yaml`:** `plan: 0.5c-512mb` is not a valid Render plan; the persistent `disk` requires a paid plan and will not be needed once data is in Mongo; `MONGODB_URI`, `MONGODB_DATABASE`, `FRESHGATE_SESSION_SECRET`, `COOKIE_SECURE`, `FRESHGATE_ENV` are missing, so the app will 503 on register/login in production. (Phase 10)
- **D2. Render free web services sleep after ~15 min idle**, which stops the in-process Watchtower scheduler. (Phase 10 documents the fix.)
- **D3. Repo hygiene:** the zip contains `.env` with real keys, ~40 `.db` files, `plugin/freshgate.so`, and `__pycache__`. `.gitignore` already covers most of this, but nothing verifies it. (Phase 0)

---

## Phase 0: Repo safety and hygiene

1. Do **not** open or print `.env`. Only edit `.env.example`.
2. Run `git status` and `git ls-files`. If any of these are tracked, run `git rm -r --cached <path>` for each: `.env`, `*.db`, `*.so`, `__pycache__/`, `dashboard/dist/`, `node_modules/`, `.agent*/`, `.gemini/`, `.impeccable/`, `bench/eval_cache_*.db`. If there is no git repo yet, run `git init -b main`.
3. Append to `.gitignore` (keep the existing lines):
   ```
   # Render / editors
   .render/
   .vscode/
   .idea/
   .DS_Store
   # Generated bench caches
   bench/eval_cache_*.db
   ```
4. Add to `.dockerignore`: `ANTIGRAVITY_SPEC.md`, `.agent`, `.agents`, `.taste-skill`.
5. Create `.github/workflows/ci.yml` (so the GitHub repo shows a green badge). It runs on push and PR to `main`: set up Python 3.11, `pip install -r requirements.txt -r requirements-dev.txt`, `python -m pytest -q`; set up Node 22, `cd dashboard && npm ci && npm run build`.
6. Rewrite `.env.example` (keep every existing variable; **add** the ones below with safe placeholders; never real values):
   ```
   FRESHGATE_ENV=development            # set to "production" on Render
   COOKIE_SECURE=false                  # set to "true" on Render (HTTPS)
   FRESHGATE_SESSION_SECRET=replace_with_a_random_secret_at_least_32_characters
   MONGODB_URI=mongodb+srv://USER:PASSWORD@CLUSTER.mongodb.net/?retryWrites=true&w=majority
   MONGODB_DATABASE=freshgate
   FRESHGATE_STORE=mongo                # "mongo" (default) or "sqlite" (tests only)
   ```
   Also add a comment above `OPENAI_API_KEY`/`TAVILY_API_KEY`: "Server-level keys are used ONLY by the gateway proxy (/v1/chat/completions). Dashboard users always use the keys saved in their own Profile."

---

## Phase 1: Mongo foundation

**Goal:** one shared Mongo client and one place that creates indexes.

1. In `requirements.txt` change `pymongo>=4.7.0` to `pymongo[srv]>=4.7.0` (needed for `mongodb+srv://` Atlas URIs). Add nothing else.
2. Create `core/mongo.py`:
   ```python
   """Shared MongoDB client + index setup."""
   from __future__ import annotations
   import logging, os, threading
   from pymongo import ASCENDING, DESCENDING, MongoClient
   from pymongo.database import Database

   logger = logging.getLogger("freshgate.mongo")
   _client: MongoClient | None = None
   _lock = threading.Lock()

   def get_client() -> MongoClient:
       global _client
       if _client is None:
           with _lock:
               if _client is None:
                   uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
                   _client = MongoClient(uri, serverSelectionTimeoutMS=4000, appname="freshgate")
       return _client

   def get_db() -> Database:
       return get_client()[os.getenv("MONGODB_DATABASE", "freshgate")]

   def ping() -> bool:
       try:
           get_client().admin.command("ping")
           return True
       except Exception as exc:  # noqa: BLE001
           logger.error("Mongo ping failed: %s", exc)
           return False

   def ensure_indexes() -> None:
       db = get_db()
       db.users.create_index("email", unique=True)
       db.policies.create_index([("owner_id", ASCENDING), ("id", ASCENDING)], unique=True)
       db.policies.create_index([("owner_id", ASCENDING), ("created_at", DESCENDING)])
       db.monitored_claims.create_index([("owner_id", ASCENDING), ("id", ASCENDING)], unique=True)
       db.monitored_claims.create_index([("owner_id", ASCENDING), ("created_at", DESCENDING)])
       db.monitored_claims.create_index([("enabled", ASCENDING), ("next_check_at", ASCENDING), ("lease_until", ASCENDING)])
       db.verification_snapshots.create_index("verification_id", unique=True)
       db.verification_snapshots.create_index([("owner_id", ASCENDING), ("claim_fingerprint", ASCENDING), ("checked_at", DESCENDING)])
       db.watchtower_alerts.create_index([("owner_id", ASCENDING), ("acknowledged_at", ASCENDING), ("created_at", DESCENDING)])
       db.watchtower_alerts.create_index([("monitored_claim_id", ASCENDING), ("transition_key", ASCENDING)])
   ```
3. In `server/users.py`: delete the private `_client` and make `users_collection()` return `get_db()["users"]` (import from `core.mongo`). Keep every other function name unchanged (tests import them).
4. In `server/app.py` `lifespan()`: before `watchtower_service.start()` add
   ```python
   try:
       await asyncio.to_thread(ensure_indexes)
   except Exception as exc:
       logger.error("MongoDB index setup failed: %s", exc)
   ```
   (the app must still boot if Mongo is down; auth routes already return a clean 500/503).
5. In `register()` remove the `users_collection().create_index("email", unique=True)` line. Change the inserted document to
   `{"_id": uid, "email": email, "password_hash": ..., "settings": {}, "created_at": time.time()}` (no `claims` / `alerts` arrays any more).

---

## Phase 2: `MongoFeatureStore` with owner scoping (this makes every user's data separate)

**Design:** the **scoped-store trick**. Every method in the existing SQLite store becomes available in Mongo with the same name and return shape. Calling `store.for_owner(user_id)` returns a store that automatically adds `owner_id` to every read and write. Existing code in `core/policies.py`, `core/history.py`, `core/replay.py` already accepts `store=...`, so they keep working unchanged when you pass the scoped store.

1. Create `core/mongo_store.py` with class `MongoFeatureStore`. Collections: `policies`, `monitored_claims`, `verification_snapshots`, `watchtower_alerts` (all in `get_db()`). Every document has `owner_id` (string user id, or `None` for system/gateway data) and an `id` field (same uuid string the SQLite store uses). **Do not use `id` as Mongo `_id`**; let Mongo generate `_id`, and strip `_id` from everything returned.
2. Ownership sentinel:
   ```python
   _ANY = object()          # root store: sees everything (used ONLY by the scheduler)
   class MongoFeatureStore:
       def __init__(self, owner_id=_ANY, db=None):
           self.owner_id = owner_id
           self.db = db if db is not None else get_db()
       def for_owner(self, owner_id: str | None) -> "MongoFeatureStore":
           return MongoFeatureStore(owner_id=owner_id, db=self.db)
       def _scope(self, extra: dict | None = None) -> dict:
           q = {} if self.owner_id is _ANY else {"owner_id": self.owner_id}
           if extra: q.update(extra)
           return q
       @property
       def _write_owner(self):
           return None if self.owner_id is _ANY else self.owner_id
       @staticmethod
       def _clean(doc):
           if doc is None: return None
           doc = dict(doc); doc.pop("_id", None); doc.pop("owner_id", None); return doc
   ```
3. Implement **all** these public methods with **identical names, arguments and return shapes** as `SQLiteFeatureStore` in `core/persistence.py` (read that file; copy the validation rules and the returned dict keys exactly, with booleans as real `bool`, JSON columns as real dict/list under the key without the `_json` suffix, e.g. `metadata`, `sources`, `configuration`, `old_evidence`, `new_evidence`):
   `create_policy, get_policy, list_policies, update_policy, delete_policy, save_monitored_claim, get_monitored_claim, list_monitored_claims, count_monitored_claims, update_monitored_claim, delete_monitored_claim, claim_due_monitored_claims, claim_monitored_claim, finish_monitored_claim, save_snapshot, get_snapshot, get_snapshot_by_verification_id, list_snapshots, count_snapshots, save_alert, get_alert, list_alerts, acknowledge_alert`
   Reuse `_validate_page` from `core/persistence.py` (import it).
4. Every query must go through `self._scope({...})`. Every insert must set `"owner_id": self._write_owner`. No query may ever omit the scope except in the two scheduler methods below when `self.owner_id is _ANY`.
5. Tricky methods, implement exactly like this:

   **save_monitored_claim** (capacity check is per owner now):
   ```python
   if max_claims is not None and self.db.monitored_claims.count_documents(self._scope()) >= max_claims:
       raise OverflowError("Watchtower monitoring capacity has been reached")
   record = {...same keys/validation as SQLite...}   # retry_count, last_error, lease_token=None, lease_until=None, enabled, created_at, metadata
   self.db.monitored_claims.insert_one({**record, "owner_id": self._write_owner})
   return record
   ```
   Because Watchtower max claims was global (1000), make the env default per user: leave the env name `FRESHGATE_WATCHTOWER_MAX_CLAIMS` but document it as "per user" and set the default in `core/config.py` to `50` (find `watchtower_max_monitored_claims`).

   **claim_due_monitored_claims** (atomic lease, works across multiple instances; scheduler only, so it must be called on the root store; raise `RuntimeError` if `self.owner_id is not _ANY`):
   ```python
   now = float(now if now is not None else time.time())
   claimed = []
   for _ in range(limit):
       token = str(uuid.uuid4())
       doc = self.db.monitored_claims.find_one_and_update(
           {"enabled": True, "next_check_at": {"$lte": now},
            "$or": [{"lease_until": None}, {"lease_until": {"$lte": now}}]},
           {"$set": {"lease_token": token, "lease_until": now + lease_seconds}},
           sort=[("next_check_at", 1)], return_document=ReturnDocument.AFTER)
       if doc is None: break
       claimed.append(doc)   # KEEP owner_id on these rows: the scheduler needs it. Strip only _id.
   return claimed
   ```
   For these scheduler rows return the doc **with** `owner_id` included (do not call `_clean`; just `pop("_id")`).

   **claim_monitored_claim** (manual "Check now"): `find_one_and_update` with filter `self._scope({"id": claim_id, "enabled": True, "$or": [lease free]})` plus `next_check_at <= now` unless `force`. Return the doc **with** `owner_id`.

   **finish_monitored_claim**: `update_one({"id": claim_id, "lease_token": lease_token}, {"$set": {...}})` where fields mirror the SQL: `last_checked_at`, `next_check_at`, `last_error`, `retry_count`, `lease_token: None`, `lease_until: None`; set `last_successful_at` only if `successful_at is not None`; set `last_verdict` only if `verdict is not None`; set `last_score` only if `score is not None`; set `metadata` only if `metadata is not None`. Return `result.modified_count > 0`. Do NOT scope this by owner (the lease token is the authority and the scheduler runs as root), but never expose this method to routes.

   **update_monitored_claim**: same logic as SQLite (`enabled` turning on sets `next_check_at = now` unless provided).

   **delete_monitored_claim**: delete the claim **and** its alerts (`watchtower_alerts` where `monitored_claim_id == claim_id` and same owner scope). Snapshots stay (history is immutable). Return `deleted_count > 0` of the claim.

   **save_alert**: if `transition_key` is not None and `acknowledged_at` is None, first `find_one(self._scope({"monitored_claim_id": ..., "transition_key": ..., "acknowledged_at": None}))` and return it if found; else insert.

   **save_snapshot / get_snapshot_by_verification_id / list_snapshots / count_snapshots**: scoped; `verification_id` has a unique index; no update or delete methods exist (immutability).

   **list_* methods**: sort (`created_at` desc, `id`) for claims and policies, (`checked_at`, `id`) with `newest_first` for snapshots, (`created_at` desc) for alerts; use `.skip(offset).limit(limit)`; `unacknowledged_only` filters `acknowledged_at: None`.
6. Add to `SQLiteFeatureStore` (in `core/persistence.py`) only: `def for_owner(self, owner_id): return self` and make **every** public method accept and ignore nothing new. SQLite mode has **no isolation** and exists only so the old tests keep running. Log a warning at startup if `FRESHGATE_STORE=sqlite` and `FRESHGATE_ENV=production`.
7. Replace `get_feature_store()` in `core/persistence.py`:
   ```python
   def get_feature_store():
       global _store
       if _store is None:
           with _store_lock:
               if _store is None:
                   if os.getenv("FRESHGATE_STORE", "mongo").lower() == "sqlite" or os.getenv("PYTEST_CURRENT_TEST"):
                       _store = SQLiteFeatureStore()
                   else:
                       from core.mongo_store import MongoFeatureStore
                       _store = MongoFeatureStore()
       return _store
   ```
   Keep `reset_feature_store()` working. In `tests/conftest.py` set `os.environ["FRESHGATE_STORE"] = "sqlite"` at the top so the existing tests keep using SQLite.
8. Policy records and `for_owner`: `core/policies.py::get_policy(policy_id, store=None)` etc. already accept `store`. No change except updating the type hints to `Any`.
9. **Pipeline history:** `FreshGatePipeline.__init__` already takes `history_store`. Per-user pipelines (Phase 4) pass `history_store=get_feature_store().for_owner(user_id)`. The shared pipeline (gateway) keeps `history_store=None` and so writes `owner_id=None` rows that no user can see.

Write **new tests** in `tests/test_mongo_store_isolation.py` using `mongomock` (add `mongomock>=4.1` to `requirements-dev.txt` only): user A creates a policy, a claim, an alert and a snapshot; user B's scoped store sees none of them and `delete_monitored_claim(A's id)` returns `False` from B's store; the root store's `claim_due_monitored_claims` returns A's due claim with `owner_id` set; double leasing the same claim returns it only once.

---

## Phase 3: Profile settings backend (provider catalog, model loading, validation, encryption)

### 3.1 Provider catalog: rewrite `server/routes/providers.py`

The user must never have to look up a Base URL. For known providers the **server decides the URL**, and the UI shows it read-only. Replace `PROVIDER_CATALOG` with:

```python
def _p(id, name, base_url, *, editable=False, offline=False, key_required=True,
       default_model="", models=None, hint=""):
    return {"id": id, "name": name, "base_url": base_url, "editable_base_url": editable,
            "offline": offline, "key_required": key_required, "default_model": default_model,
            "models": models or [], "credential_hint": hint}

PROVIDER_CATALOG = [
  _p("openai", "OpenAI", "https://api.openai.com/v1", hint="OpenAI API usage is billed. Click Load models to pick one your key can use."),
  _p("groq", "Groq", "https://api.groq.com/openai/v1", hint="Free tier available. Click Load models to see current models."),
  _p("gemini", "Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai", default_model="gemini-3.6-flash", models=["gemini-3.6-flash"], hint="Gemini API key from Google AI Studio (free tier available)."),
  _p("openrouter", "OpenRouter", "https://openrouter.ai/api/v1", default_model="openrouter/free", models=["openrouter/free"], hint="OpenRouter API key."),
  _p("mistral", "Mistral", "https://api.mistral.ai/v1", hint="Mistral API key."),
  _p("deepseek", "DeepSeek", "https://api.deepseek.com/v1", hint="DeepSeek API key."),
  _p("together", "Together AI", "https://api.together.xyz/v1", hint="Together API key."),
  _p("xai", "xAI (Grok)", "https://api.x.ai/v1", hint="xAI API key."),
  _p("ollama", "Ollama (local / offline)", "http://localhost:11434/v1", editable=True, offline=True, key_required=False,
     hint="Offline provider: API key is optional. Your server must be able to reach this URL (a deployed FreshGate cannot reach localhost on your PC; use a tunnel URL or run FreshGate locally)."),
  _p("lmstudio", "LM Studio (local / offline)", "http://localhost:1234/v1", editable=True, offline=True, key_required=False,
     hint="Offline provider: API key is optional. Same reachability note as Ollama."),
  _p("custom", "Custom OpenAI-compatible", "", editable=True, key_required=False,
     hint="Any server that implements OpenAI chat completions and /models (vLLM, LiteLLM, llama.cpp...). API key optional. Only use endpoints you trust."),
]
SUPPORTED_PROVIDER_IDS = {p["id"] for p in PROVIDER_CATALOG}
def get_provider(provider_id): return next((p for p in PROVIDER_CATALOG if p["id"] == provider_id), None)
```
Keep `GET /api/providers` (public, no secrets) returning `{"providers": PROVIDER_CATALOG}`.

### 3.2 URL validation (SSRF guard). New file `server/netguard.py`

```python
import ipaddress, os, socket
from urllib.parse import urlparse

def is_production() -> bool:
    return os.getenv("FRESHGATE_ENV", "development").lower() == "production"

def validate_base_url(url: str, *, allow_private: bool | None = None) -> str:
    """Return a normalized URL (no trailing slash) or raise ValueError with a user-friendly message."""
    url = (url or "").strip()
    p = urlparse(url)
    if p.scheme not in {"http", "https"} or not p.netloc or not p.hostname:
        raise ValueError("Enter a full URL that starts with http:// or https://")
    if p.username or p.password:
        raise ValueError("Do not put credentials inside the URL.")
    if allow_private is None:
        allow_private = not is_production()
    if not allow_private:
        if p.scheme != "https":
            raise ValueError("Production servers only accept https:// provider URLs.")
        try:
            infos = socket.getaddrinfo(p.hostname, p.port or 443, proto=socket.IPPROTO_TCP)
        except socket.gaierror:
            raise ValueError("That host name could not be resolved.")
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
                raise ValueError("This server cannot reach local or private addresses. Use a public https URL (for example an ngrok or Cloudflare tunnel) or run FreshGate locally.")
    return url.rstrip("/")
```

### 3.3 Settings helpers. In `server/users.py`

1. Fix B6:
   ```python
   def decrypt_secret(value: str) -> str:
       if not value:
           return ""
       if not value.startswith("gAAAA"):      # legacy plaintext (pre-encryption installs)
           return value
       try:
           return _fernet().decrypt(value.encode()).decode()
       except Exception:
           logger.warning("Stored secret could not be decrypted (session secret changed?). Ask the user to re-enter it.")
           return ""
   ```
   (add `import logging; logger = logging.getLogger("freshgate.users")`).
2. Replace `public_settings()`:
   ```python
   def _hint(enc: str) -> str:
       plain = decrypt_secret(enc) if enc else ""
       return ("••••" + plain[-4:]) if len(plain) >= 8 else ("••••" if plain else "")

   def effective_base_url(settings: dict) -> str:
       from server.routes.providers import get_provider
       p = get_provider(settings.get("provider", "openai"))
       if p is None: return ""
       return (settings.get("base_url") or p["base_url"]) if p["editable_base_url"] else p["base_url"]

   def settings_complete(settings: dict) -> bool:
       from server.routes.providers import get_provider
       p = get_provider(settings.get("provider", ""))
       if not p or not settings.get("model") or not effective_base_url(settings): return False
       if p["key_required"] and not settings.get("api_key"): return False
       return bool(settings.get("tavily_key"))

   def public_settings(user_or_dict) -> dict:
       s = user_or_dict.get("settings", user_or_dict) or {}
       return {"provider": s.get("provider", "openai"), "model": s.get("model", ""),
               "base_url": effective_base_url({**s, "provider": s.get("provider", "openai")}),
               "has_api_key": bool(s.get("api_key")), "api_key_hint": _hint(s.get("api_key", "")),
               "has_tavily_key": bool(s.get("tavily_key")), "tavily_key_hint": _hint(s.get("tavily_key", "")),
               "is_complete": settings_complete({**s, "provider": s.get("provider", "openai")})}
   ```
   Update `UserSettings` in `dashboard/src/types.ts` to add `api_key_hint: string; tavily_key_hint: string; is_complete: boolean;`.
3. Add `def load_credentials(user: dict) -> dict | None` returning
   `{"provider", "base_url", "model", "api_key" (decrypted, "" if none), "tavily_key" (decrypted), "keyless_ok": bool(not provider.key_required)}` or `None` if `settings_complete` is false.

### 3.4 New file `server/routes/profile.py` (move profile logic out of `app.py`)

Delete `ProfileSettings` and `profile_settings()` from `server/app.py`, and register the new router (`app.include_router(profile_router)`).

```python
router = APIRouter(prefix="/api/profile", tags=["profile"])

class SettingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(max_length=40)
    model: str = Field(min_length=1, max_length=120)
    base_url: str = Field(default="", max_length=500)    # only used for editable providers
    api_key: str = Field(default="", max_length=500)     # blank = keep saved
    tavily_key: str = Field(default="", max_length=500)  # blank = keep saved
    clear_api_key: bool = False
```

**GET `/api/profile/settings`** requires a session (`current_user`), returns `public_settings(user)`.

**PUT `/api/profile/settings`** rules, in this order, each failure is a 422 with a clear message:
1. `provider` must be in `SUPPORTED_PROVIDER_IDS`.
2. Base URL: if provider is editable (`ollama`, `lmstudio`, `custom`) the body's `base_url` is required and passes `validate_base_url(...)` (ValueError message becomes the 422 detail). If the provider is not editable, **ignore the body's base_url and store `""`** (the server always uses the catalog URL).
3. `model` is trimmed, non-empty.
4. API key: new key = `body.api_key.strip()`. If provided, check `is_valid_credential(...)` from `core.config`. If blank: keep the old encrypted key **only if the provider did not change**; if the provider changed, drop the old key (it belonged to another provider). If `clear_api_key` is true, drop it. After this, if the provider requires a key (`key_required`) and there is no key, return 422 "Enter the API key for <provider name>." Offline providers (`key_required=False`) may have no key.
5. Tavily key: new one (validated) or the saved one. If neither exists, 422 "A Tavily search API key is required."
6. Save with `users_collection().update_one({"_id": user["_id"]}, {"$set": {"settings": {"provider", "model", "base_url", "api_key": encrypt_secret(...), "tavily_key": encrypt_secret(...), "updated_at": time.time()}}})`.
7. Return `public_settings(...)`.

**POST `/api/profile/models`** (this is the "Load models" button), requires a session:
```python
class ModelsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    base_url: str = ""
    api_key: str = ""     # blank -> use the saved key IF the saved provider matches
```
Logic:
1. `provider = get_provider(body.provider)`; 400 if unknown.
2. `base = validate_base_url(body.base_url)` if provider is editable, else `provider["base_url"]`.
3. Key: `body.api_key.strip()` or, if blank and `saved.provider == body.provider`, `decrypt_secret(saved api_key)`. If the provider requires a key and none is available: 400 "Enter your API key (or save it first), then load models."
4. Build URLs to try: Ollama → first `{base minus trailing /v1}/api/tags`, then `{base}/models`; every other provider → `{base}/models`. Headers: `Authorization: Bearer <key>` only if key non-empty.
5. `httpx.AsyncClient(timeout=12, follow_redirects=False)`. Map errors: 401/403 → 400 "The provider rejected this API key."; 404 → 400 "This provider did not return a model list. Type the model ID manually."; connection error → 502 "Could not reach <base>."
6. Parse **all three** response shapes: `{"data": [{"id": ...}]}`, `{"models": [{"name"|"id": ...}]}` (Ollama), or a top-level JSON list (Together). Strip a leading `models/` from ids (Gemini). De-duplicate, sort case-insensitively, cap at 500.
7. Return `{"models": [...], "base_url": base}`.

Keep the old `POST /api/providers/{provider_id}/models` route working by making it a thin wrapper that requires a session and calls the same helper (so old tests/clients don't break) **or** delete it and its test if no test covers it; check with grep first.

### 3.5 Registration/login hardening (in `server/app.py`)
- Validate email with a simple regex `^[^@\s]+@[^@\s]+\.[^@\s]+$` in `AuthBody` via `field_validator`.
- Add an in-memory attempt limiter keyed by `ip + email` (use `request.client.host`, which works after Phase 10's `--proxy-headers`): max 10 failed logins per 15 minutes → 429 "Too many attempts. Try again later." Clear the counter on success.
- Set cookie `secure=True` automatically when `FRESHGATE_ENV=production` even if `COOKIE_SECURE` is unset.

---

## Phase 4: `/verify`, per-user pipelines, and Watchtower using the owner's keys

### 4.1 New file `server/pipeline_factory.py`
Move `build_request_pipeline()` out of `app.py` into here, and extend it:

```python
_shared: FreshGatePipeline | None = None
def get_shared_pipeline() -> FreshGatePipeline:
    global _shared
    if _shared is None: _shared = FreshGatePipeline()
    return _shared

def build_user_pipeline(creds: dict, owner_id: str, base: FreshGatePipeline | None = None) -> FreshGatePipeline:
    base = base or get_shared_pipeline()
    mgr = get_llm_manager()
    kw = dict(api_key=creds["api_key"], base_url=creds["base_url"], model=creds["model"],
              manager=mgr, allow_keyless=creds["keyless_ok"])
    extraction = OpenAILLMClient(request_type="extraction", **kw)
    judge = OpenAILLMClient(request_type="judging", **kw)
    verifier = ClaimVerifier(tavily_client=TavilySearchClient(api_key=creds["tavily_key"]), judge_llm=judge)
    verifier._semaphore = base.verifier._semaphore
    return FreshGatePipeline(extractor=ClaimExtractor(llm_client=extraction), verifier=verifier,
                             cache=base.cache, history_store=get_feature_store().for_owner(owner_id))

async def pipeline_for_owner(owner_id: str | None) -> FreshGatePipeline:
    if not owner_id:
        raise RuntimeError("This claim has no owner, so there are no credentials to check it with.")
    user = await asyncio.to_thread(users_collection().find_one, {"_id": owner_id})
    creds = load_credentials(user) if user else None
    if creds is None:
        raise RuntimeError("Add your AI provider and Tavily key in Profile to enable checks.")
    return build_user_pipeline(creds, owner_id)
```
In `app.py` replace `pipeline = FreshGatePipeline()` with `pipeline = get_shared_pipeline()` (keep the module-level name `pipeline`; tests monkeypatch it).

### 4.2 `core/llm.py` and `core/llm_manager.py`: support keyless providers
- `OpenAILLMClient.__init__`: add `allow_keyless: bool = False`; store it; pass `allow_keyless=self.allow_keyless` into `self.manager.complete(...)`.
- `LLMProviderManager.complete(...)`: add `allow_keyless: bool = False` param. Change the validity check to
  `if not is_valid_credential(effective_key) and not is_local_ollama and not allow_keyless:`.
  Keep the `Authorization` header only when `effective_key` is non-empty (already so).
- **Remove the silent fallback to the server key for user requests:** `if effective_key is None: effective_key = get_config().openai_api_key` stays only for the shared gateway pipeline. Per-user pipelines always pass `api_key=""` (not `None`) when keyless, so the fallback never triggers.

### 4.3 Rewrite `POST /verify` in `server/app.py`
New request model: `VerifyRequest { answer: str (1..50000), policy_id: Optional[str] }`. Remove `provider`, `base_url`, `model` from the body and ignore the `X-LLM-Base-URL` / `X-LLM-Model` headers.

Logic:
1. `user = optional_current_user(request)`.
2. **Logged in:** `creds = load_credentials(user)`. If `None` → `400` with detail `"Finish your Profile first: pick a provider and model, add your API key (if required) and your Tavily key."`. Else `req_pipeline = build_user_pipeline(creds, user["_id"], base=pipeline)`.
3. **Not logged in (CLI / gateway clients):** only allowed if the request carries **both** a key header for the LLM (`X-LLM-API-Key`, `X-OpenAI-API-Key` or `X-OpenAI-Key`) **and** a Tavily header (`X-Tavily-API-Key` or `X-Tavily-Key`). Then build a request-scoped pipeline from those headers (base URL and model come from the server env `OPENAI_BASE_URL`, `LLM_MODEL`; run `validate_base_url` on them). Otherwise `401 "Please log in."`. A logged-out request must **never** fall back to server keys.
4. Policy: `get_policy(req.policy_id, store=get_feature_store().for_owner(user["_id"]))` for logged-in users; header-only clients may not use policies (400).
5. Call `req_pipeline.verify(req.answer, single_flight_scope=correlation_id, policy=policy)`; in `finally` close `req_pipeline.verifier.tavily_client` (`aclose()`) because it is request-scoped.
6. Delete dead code: the provider/`has_api_key` logic, the `llm_base_url`/`llm_model` branches and `build_request_pipeline` in `app.py`.

Update `cli/client.py` only if a test breaks (it already sends `X-OpenAI-API-Key` and `X-Tavily-API-Key`, which step 3 supports).

### 4.4 `core/watchtower.py`: use the claim owner's credentials
1. `WatchtowerService.__init__(..., pipeline_resolver: Optional[Callable[[Optional[str]], Awaitable[FreshGatePipeline]]] = None)`; store it.
2. Replace `_get_pipeline(self)` with `_get_pipeline(self, owner_id)`:
   ```python
   if self.pipeline is not None:            # tests inject a pipeline
       return self.pipeline, False
   if self._resolver is not None:
       return await self._resolver(owner_id), True     # True = ephemeral, must be closed
   # legacy single-tenant fallback
   if not (self.config.is_openai_configured and self.config.is_tavily_configured):
       raise RuntimeError("Scheduled checks require server-configured OPENAI_API_KEY and TAVILY_API_KEY")
   self.pipeline = FreshGatePipeline(); return self.pipeline, False
   ```
3. In `_check_leased`: `owner_id = row.get("owner_id")`; `store = self.store.for_owner(owner_id) if hasattr(self.store, "for_owner") else self.store`; use `store` for `get_policy`, `get_snapshot_by_verification_id` and `save_claim_snapshot(..., store=store)`. Keep using `self.store` (root) for `save_alert` and `finish_monitored_claim`, but add `"owner_id"`-aware alerts: before `self.store.save_alert(...)` do `alert_store = store`, and call `alert_store.save_alert(...)` so alerts get the right `owner_id`. After the verify call, in a `finally`, close the tavily client if the pipeline was ephemeral.
4. Fix **B10** (nonsense alert). Replace the block
   ```python
   if previous_verdict and current_verdict == "unverifiable":
       reason = "Repeated checks could not find sufficient evidence"
   ```
   with
   ```python
   if previous_verdict and previous_verdict != "unverifiable" and current_verdict == "unverifiable":
       reason = "Repeated checks could not find sufficient evidence"
   elif previous_verdict == "unverifiable" and current_verdict == "unverifiable":
       reason = None
   ```
5. A claim whose owner has an incomplete profile must fail with the friendly message from `pipeline_for_owner` (it is stored in `last_error` and shown on the card). It must not retry every 60 seconds: in the generic `except Exception` branch, if the message starts with `"Add your AI provider"`, set the next check to `checked_at + interval` (no backoff spam).
6. `check_now(self, claim_id, owner_id=None)`: when `owner_id` is given use `self.store.for_owner(owner_id)` for `claim_monitored_claim` and `get_monitored_claim`, so another user's claim id returns `None` (404).
7. In `server/routes/watchtower.py`: `watchtower_service = WatchtowerService(pipeline_resolver=pipeline_for_owner)`.

---

## Phase 5: Watchtower routes (Mongo, owner-scoped, delete)

Rewrite `server/routes/watchtower.py`. Add a helper at the top:

```python
def _scoped(req: Request):
    user = current_user(req)                 # 401 if not logged in
    return user, get_feature_store().for_owner(user["_id"])
```
Every route calls `_scoped(req)` first (add `req: Request` to routes that lack it).

- `POST /monitor`: `store.save_monitored_claim({...})` as today but with the scoped store and `max_claims=watchtower_service.config.watchtower_max_monitored_claims`. **Delete the whole "mirror to users_collection" block.** Policy lookups use the scoped store (`get_policy(request.policy_id, store=store)`).
- `GET /claims`: return rows from `store.list_monitored_claims(limit, offset)` and alerts from `store.list_alerts(1000, 0, True)`. Delete the fallback to the global list. Compute `overview` over **all** rows (not the page slice): total = `store.count_monitored_claims()`, healthy/pending/failed/changed from the returned rows. Keep the response shape the dashboard expects.
- `GET /claims/{id}`, `PATCH /claims/{id}`, `DELETE /claims/{id}`, `POST /claims/{id}/check` (call `watchtower_service.check_now(claim_id, owner_id=user["_id"])`), `GET /alerts`, `GET /alerts/{id}`, `POST /alerts/{id}/acknowledge`: all through the scoped store. Wrong owner means the row is simply not found, so return the existing 404.
- `DELETE /claims/{id}` must return 204 and the Mongo store removes the claim and its alerts (Phase 2).
- Remove the unused import of `users_collection` and `sqlite3`-specific except blocks where the Mongo store raises different errors (keep catching `OverflowError`; for duplicates catch `pymongo.errors.DuplicateKeyError` too).

Also update `server/routes/policies.py` and `server/routes/replay.py` (Phase 6).

---

## Phase 6: Lock down every other route

1. `server/routes/policies.py` and `server/routes/replay.py`: add `req: Request` to every handler, call `user = current_user(req)`, `store = get_feature_store().for_owner(user["_id"])`, and pass `store=store` to every `core.policies`, `core.history`, `core.replay` function (they all accept `store`). `delete_policy` uses `store.delete_policy`. `GET /api/policies/categories` stays public (static).
2. In `server/app.py` add a dependency `require_user_or_admin` (session cookie **or** valid admin key via the existing `verify_admin_key`) and apply it to: `/api/provider/status`, `/api/deployment/status`, `/api/benchmark`, `/metrics`. Keep `/health`, `/health/live`, `/health/ready` public.
3. `/api/provider/status`: for non-admin users, remove `config` diagnostics that reveal server key previews or server base URLs (`config.get_safe_diagnostics()` output): return `{llm, tavily, circuit_breakers, cache, metrics}` without `config`, **or** replace `config` with the signed-in user's `public_settings`. The dashboard `SystemView` reads `config`, so make `SystemView` tolerate it being absent.
4. `/api/audits/recent`, `/api/cache/clear`, `/api/circuit-breaker/reset` already use the admin key. Leave them. Add a one-line description under the Audit log page title in the UI: "Gateway audit trail (admin key required)."
5. Confirm `/proxy/v1/chat/completions` and `/v1/chat/completions` (gateway mode) keep using the shared pipeline and server env keys. That is intended for the Bifrost gateway use case. Keep them as is.

---

## Phase 7: Frontend logic (`dashboard/src/App.tsx`, `api.ts`, `types.ts`)

Use the impeccable skill for layout decisions, but follow these functional requirements exactly.

### 7.1 `api.ts`
- Add a global 401 handler:
  ```ts
  let onUnauthorized: (() => void) | null = null;
  export const setUnauthorizedHandler = (fn: (() => void) | null) => { onUnauthorized = fn; };
  // inside api(), right after `if (!response.ok) {` and before throwing:
  if (response.status === 401 && !path.startsWith("/api/auth/")) onUnauthorized?.();
  ```
- Add `export const deleteReq: RequestInit = { method: "DELETE" };`
- Add `export const putBody = (v: unknown): RequestInit => ({ method: "PUT", body: JSON.stringify(v) });`

### 7.2 `App()` shell
- Add `resetWorkspace()` that sets `claims`, `alerts`, `policies`, `categories`, `benchmark`, `provider`, `deployment`, `health`, `readiness` back to empty, `replayFingerprint` to `""`, and `globalError` to `null`.
- On sign-out: call `resetWorkspace()`, `setUser(null)`, `window.location.hash = "verify"`.
- Register `setUnauthorizedHandler(() => { resetWorkspace(); setUser(null); })` in a `useEffect`, and clear it on cleanup.
- The 45-second polling `setInterval` must only run while `user` is non-null (make it a separate `useEffect([user, refreshWorkspace])`), and `refreshWorkspace()` must return early if there is no user. Do not call `refreshWorkspace` when the tab is hidden (`document.visibilityState !== "visible"`).
- Pass `user` and an `onProfileChanged(settings)` callback so Verify can read `user.settings.is_complete`.

### 7.3 `VerifyView`: remove the whole provider section (requirement 1)
- Delete: the `<fieldset className="provider-config">…</fieldset>` block, the "saved-creds-hint" div, and every state, effect and handler used only by it: `providers`, `providerId`, `model`, `customModel`, `providerModels`, `loadingModels`, `modelNotice`, `baseUrl`, `llmKey`, `showLlmKey`, `searchKey`, `activeProvider`, `chooseProvider`, `loadModels`, plus the "Keys are sent with this request…" line.
- The verify request is now `jsonBody({ answer, ...(policyId ? { policy_id: policyId } : {}) })` with **no key headers**.
- At the top of the form, when `!user?.settings?.is_complete`, show an info card (`InlineNotice variant="info"` or a new `.setup-callout`): "Finish your Profile to start checking. Pick your AI provider and model, add your API key (if the provider needs one) and your Tavily key." with a **button** "Open Profile" that calls `window.location.hash = "profile"` (the hashchange listener already updates the section). Disable "Check response" while incomplete.
- When complete, show a compact read-only line under the textarea: `Using {provider name} · {model} · Tavily key ✓` with a small "Change" text button to Profile.
- Remove the unused imports left behind (`ProviderOption`, `Eye`, `EyeOff`, etc. only if no longer used anywhere; the build must have zero TS errors and zero unused-variable errors).
- Keep the `monitor()` flow; update its success message to "Claim added to Watchtower. Scheduled checks use the provider and Tavily key saved in your Profile."

### 7.4 `ProfileView`: rebuild (requirements 1 and 2)
State: `provider`, `baseUrl` (only meaningful for editable providers), `model`, `apiKey`, `tavilyKey`, `show*` toggles, `models: string[]`, `loadingModels`, `modelNotice`, `busy`, `error`, `notice`, `providers`.
- Load `/api/providers` **once** (`useEffect` with `[]`). Initialize `provider` from `user.settings.provider || "openai"`.
- Derive `active = providers.find(p => p.id === provider)`.
- **Base URL:** if `active.editable_base_url` show an editable `<input type="url">` with the placeholder from the catalog and helper text; otherwise show the catalog URL in a **read-only, visually muted field** with the label "Base URL (set automatically)". The user never types it for known providers.
- **API key field:** label "API key"; if `active.key_required` add a "REQUIRED" tag, else "OPTIONAL". Placeholder: `Saved ({user.settings.api_key_hint}). Leave blank to keep it.` when a key is saved for the same provider, else `Paste your {active.name} API key`. The saved pill "✓ Saved" stays.
- **Model field:** one control group: a `<select>` listing `models` (from Load models) or the catalog's `models`, plus a final option "Enter a model ID manually…". Next to it a **"Load models"** secondary button. If the "manual" option is picked or the list is empty, show a text input **inside the same cell with 8px spacing**. If `model` is saved but not in the list, keep it selected (add it as the first option) so the saved value is never lost.
- `loadModels()` calls `POST /api/profile/models` with `{ provider, base_url: active.editable_base_url ? baseUrl : "", api_key: apiKey.trim() }` (the server uses the saved key when blank and the provider is unchanged). Show `modelNotice`: "N models loaded." or the error text. Auto-click once on mount if a key is saved and the provider matches the saved one (so returning users immediately see their models), but never block rendering on it.
- **Tavily field:** mark "REQUIRED". Placeholder `Saved ({user.settings.tavily_key_hint}). Leave blank to keep it.` when saved. Add helper text "Get a free key at tavily.com".
- When the user changes provider: set `model` to `active.default_model` (or `""`), clear `models`, clear `apiKey`, set `baseUrl` to the new provider's default, clear `modelNotice`.
- Client-side validation before save (mirror server rules): model non-empty; Tavily saved or typed; key saved-for-same-provider or typed when `key_required`; editable base URL is a valid http(s) URL. Show errors with `InlineNotice`.
- Save calls `PUT /api/profile/settings` with `{ provider, model, base_url: editable ? baseUrl : "", api_key, tavily_key }`. On success call `onSaved(updated)`, clear both secret inputs, and show "Saved. Your settings are encrypted and will be here next time you log in." 
- Show a status badge at the top of the page: "Profile complete" (green) or "Profile incomplete: <what's missing>" (gold), computed from `user.settings.is_complete` and the missing fields.
- Offline providers: when `active.offline` show the `credential_hint` in an info callout (it explains the localhost-on-Render limitation).
- Persistence requirement check: after login `/api/auth/me` returns `settings` and `ProfileView` initializes from it, so everything is pre-filled next time. Verify by logging out and in again.

### 7.5 `WatchtowerView`
- Add a **Remove** button to each claim row (icon `Trash2` from lucide-react, label "Remove"), next to Pause/Resume/Replay. First click turns it into an inline confirm: `Remove this claim? [Remove] [Cancel]` (two-step, no `window.confirm`). Confirm calls `api("/api/watchtower/claims/{id}", deleteReq)`, then `onRefresh()`, then notice "Claim removed from Watchtower." Add `.danger` styling using the rust tokens.
- Show `Score —` when `claim.last_error` is set (fix F7); show the first line of the error in `.watch-error` with a "Fix in Profile" link when the error starts with "Add your AI provider".
- Hide alerts whose claim no longer exists (they are deleted server-side anyway).
- Empty state copy for no claims is already fine.

### 7.6 Other
- `SystemView`: tolerate missing `config` (`providerValue.config ?? {}` already; just ensure no crash).
- `AuditView`: add the explanatory line from Phase 6.
- Update the footer/`PRODUCT.md` line "Request-scoped provider credentials entered in the browser apply only to that request…" to: "Provider and search credentials are saved to the signed-in user's profile, encrypted at rest on the server, and are never stored in browser storage."

---

## Phase 8: Dark mode redesign (requirement 4). The palette below is mandatory

### 8.1 The problem
`styles.css` has 183 inline `light-dark(#lightHex, #darkHex)` pairs; the dark halves are warm browns like `#2e291d`, `#342f25`, `#38342c`, `#413d36`, so dark mode looks muddy and brown. The placeholder is `#c5c4bf` (too bright). Fix it by moving every color to CSS variables and giving dark mode its own cool, graphite-and-moss palette (the green accent matches the brand).

### 8.2 The palette: **"Graphite Moss"**

Dark theme tokens (all contrast ratios computed against the surface they sit on; body text ≥ 7:1, secondary text ≥ 4.5:1):

| Token | Dark value | Used for |
|---|---|---|
| `--paper` | `#0E1311` | page background |
| `--paper-deep` | `#0A0E0C` | sidebar, top bar, footer |
| `--paper-light` | `#151B18` | cards / panels |
| `--paper-raised` | `#1B2320` | inputs, selects, textarea, table row hover base |
| `--paper-hover` | `#222C28` | hover states, nav hover |
| `--rule` | `#2B3631` | borders |
| `--rule-light` | `#212A26` | dividers inside panels |
| `--ink` | `#E7ECE8` | primary text |
| `--ink-soft` | `#B2BDB6` | secondary text |
| `--ink-faint` | `#8A968F` | hints, placeholders, timestamps |
| `--green` | `#6CCB95` | accent text, links, active nav, focus ring |
| `--green-strong` | `#3E9B6B` | primary button background (text on it: `#06120B`) |
| `--green-strong-hover` | `#4DB17D` | primary button hover |
| `--green-wash` | `#163024` | success / "Saved" pill background |
| `--rust` | `#F2907F` | errors, danger text |
| `--rust-wash` | `#34201D` | error background |
| `--gold` | `#E2B75A` | warnings, "partially supported", theme icon |
| `--gold-wash` | `#2E2711` | warning background |
| `--info` | `#8AA4C4` | "unverifiable" / neutral status |
| `--info-wash` | `#1B2531` | neutral background |
| `--scroll-thumb` | `#33403A` | scrollbar |
| `--selection` | `#2C5A43` | `::selection` background |
| `--shadow` | `0 1px 0 rgb(0 0 0 / 40%), 0 8px 24px rgb(0 0 0 / 25%)` | elevated panels |

Light theme stays as it is (keep every existing light value; they are the left side of the current pairs).

### 8.3 How to implement
1. At the top of `styles.css` replace the `:root` token block with explicit light tokens **plus** the new tokens `--paper-raised`, `--paper-hover`, `--green-strong`, `--green-strong-hover`, `--info`, `--info-wash`, `--scroll-thumb`, `--selection`, `--shadow`, `--on-green` (light: `#fffdf7`, dark: `#06120B`), `--placeholder` (light `#777569`, dark `#8A968F`). Keep `color-scheme` handling.
2. Define the dark values with this exact structure so the page follows the toggle **and** the OS before the user picks:
   ```css
   :root { /* light tokens */ }
   :root[data-theme="dark"] { /* all dark tokens from the table in 8.2 */ color-scheme: dark; }
   @media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { /* same dark tokens */ } }
   ```
   (The app sets `data-theme` before paint in `useLayoutEffect`; keep that.) Do not use `light-dark()` anywhere. The goal is **zero** `light-dark(` left in the file: `grep -c "light-dark(" dashboard/src/styles.css` must print `0`.
3. Replace every inline `light-dark(#x, #y)` using this mapping (decide by the **role** of the property, not the hex):
   - `border` / `border-color` / `border-top` etc. → `var(--rule)` (inner dividers → `var(--rule-light)`)
   - panel/card `background` → `var(--paper-light)`; sidebar/topbar/footer → `var(--paper-deep)`; page → `var(--paper)`
   - inputs/selects/textarea `background` → `var(--paper-raised)`
   - hover backgrounds (`.nav-link:hover`, `.icon-button:hover`, row hover, `.text-button:hover`) → `var(--paper-hover)`
   - headings and strong text → `var(--ink)`; body secondary → `var(--ink-soft)`; faint/meta/placeholder → `var(--ink-faint)` (placeholder → `var(--placeholder)`)
   - `.primary-button` background → `var(--green-strong)`, text `var(--on-green)`, hover `var(--green-strong-hover)`; in light mode `--green-strong` = `#2e4937` and hover `#3a5b45`
   - status colors: confirmed → `--green` on `--green-wash`; contradicted/failed/danger → `--rust` on `--rust-wash`; partially_supported/warn → `--gold` on `--gold-wash`; unverifiable/pending/paused → `--info` on `--info-wash`
   - `.brand-mark` background → `var(--green-strong)`, icon `var(--on-green)`
   - scrollbars → `var(--scroll-thumb)` / `var(--paper)`
   - `rgb(... / N%)` light-dark pairs (shadows, translucent panels) → `var(--shadow)` or a token
4. Add: `::selection { background: var(--selection); color: var(--ink); }`, `:focus-visible { outline: 2px solid var(--green); outline-offset: 2px; }`, `input::placeholder, textarea::placeholder { color: var(--placeholder); opacity: 1; }`, `select option { background: var(--paper-raised); color: var(--ink); }`.
5. Status component audit: grep `StatusMark`, `tone()` in `App.tsx` and the `.status-*` / `.tone-*` classes in CSS. Make sure each verdict maps to the token pairs above, including `unverifiable` (use `--info`), and that the Watchtower pulse dot uses `--green` (enabled) / `--ink-faint` (disabled).
6. Update `DESIGN.md` with a "Dark theme: Graphite Moss" section containing the table from 8.2, and keep `PRODUCT.md` consistent.
7. Check contrast: no text may use a color with less than 4.5:1 against its background. Check these pairs explicitly: `--ink-faint` on `--paper-light`, `--green` on `--green-wash`, `--rust` on `--rust-wash`, `--gold` on `--gold-wash`, `--info` on `--info-wash`, `--on-green` on `--green-strong`. If one fails, lighten the text token by ~6% until it passes.
8. Theme switch: keep the existing toggle. Make the switch's thumb/track use `--green-strong` when on. Also add `<meta name="color-scheme" content="light dark">` and `<meta name="theme-color" ...>` (two tags with `media="(prefers-color-scheme: ...)"`, light `#f4f0e6`, dark `#0E1311`) in `dashboard/index.html`.

---

## Phase 9: UI bugs, layout, and polish checklist

Fix each; verify visually at widths 1440, 1024, 768 and 390.

1. **Base URL misalignment (F8a):** in `styles.css`, `.provider-grid { align-items: start; }` and `.field-group { align-content: start; }`. Also give the Model control group `grid-column: 1 / -1` on screens ≤ 900px.
2. **Helper text colliding with the Tavily label (F8b):** give `.provider-hint` `margin: 8px 0 16px`, and `.search-key-field` `margin-top: 4px`. Every `.field-label` gets `margin-bottom: 6px` (currently 0 because `.field-group` has `gap: 0`; set `gap: 6px` on `.field-group` instead).
3. **Model select showing "Other…" with an empty input (F8c):** handled by the new Profile model control (Phase 7.4).
4. Required-field markers: `.field-label span` with text "REQUIRED"/"OPTIONAL" uses `--ink-faint`, 9px, letter-spacing .08em, never the same color as the label.
5. Buttons: all buttons get `min-height: 38px`, `cursor: pointer`, disabled state `opacity: .55; cursor: not-allowed`. Loading buttons keep their width (no layout jump when the spinner appears).
6. Inline notices: `InlineNotice` info/success/error variants use the wash tokens; ensure the dismiss button is keyboard-accessible with `aria-label="Dismiss"`.
7. Watchtower rows: the three actions (Check now, Pause/Resume, Replay, Remove) wrap on narrow screens without overflow; claim text wraps (`overflow-wrap: anywhere`).
8. Tables (`DataTable`) scroll horizontally inside their panel at ≤ 700px instead of widening the page.
9. Sidebar bottom block: the email must truncate with ellipsis and show the full address in a `title`; "Sign out" must be a visible button with focus ring.
10. Loading and empty states: every list/panel shows a skeleton line (`LoadingLine`) while loading and an `EmptyState` when empty; never a blank card.
11. Keyboard: tab order follows visual order; the mobile menu traps focus and closes on `Escape`.
12. Motion: wrap non-essential animations in `@media (prefers-reduced-motion: no-preference)`.
13. Auth screen: password field gets a show/hide toggle; on register show the minimum length (10); disable the submit button while busy; show server errors inline.
14. Remove `console.log` and unused imports; `npm run build` must produce **zero** TypeScript errors.
15. Run the impeccable skill's audit/critique/polish on the Profile, Verify, Watchtower and Auth screens in both themes and apply fixes that don't contradict this spec.

---

## Phase 10: Deploy on Render + push to GitHub

### 10.1 `Dockerfile`
- Keep the multi-stage build.
- Change the CMD to run behind Render's proxy:
  ```dockerfile
  CMD ["sh", "-c", "uvicorn server.app:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
  ```
- Keep `/data` writable (SQLite claim cache and audit DB live there and are fine to lose on redeploy).
- Add `HEALTHCHECK` is optional; Render uses `healthCheckPath`.

### 10.2 `render.yaml` (replace the whole file)
```yaml
services:
  - type: web
    name: freshgate
    runtime: docker
    plan: free            # change to "starter" ($7/mo) for always-on Watchtower
    healthCheckPath: /health/ready
    autoDeploy: true
    envVars:
      - key: FRESHGATE_ENV
        value: production
      - key: COOKIE_SECURE
        value: "true"
      - key: FRESHGATE_SESSION_SECRET
        generateValue: true
      - key: FRESHGATE_ADMIN_KEY
        generateValue: true
      - key: MONGODB_URI
        sync: false          # paste your Atlas connection string in the Render dashboard
      - key: MONGODB_DATABASE
        value: freshgate
      - key: FRESHGATE_STORE
        value: mongo
      - key: FRESHGATE_MODE
        value: shadow
      - key: FRESHGATE_FAIL_OPEN
        value: "true"
      - key: FRESHGATE_CACHE_DB
        value: /tmp/freshgate_cache.db
      - key: FRESHGATE_AUDIT_DB
        value: /tmp/freshgate_audit.db
      - key: FRESHGATE_FEATURE_DB
        value: /tmp/freshgate_features.db
      - key: FRESHGATE_WATCHTOWER_ENABLED
        value: "true"
      - key: FRESHGATE_WATCHTOWER_MAX_CLAIMS
        value: "50"
```
No `disk` block (nothing important lives on disk any more). Do **not** add `OPENAI_API_KEY` or `TAVILY_API_KEY` to Render (users bring their own). First grep `core/` and `server/` for the actual env var names used for the cache/audit DB paths (`CACHE_DB_PATH` vs `FRESHGATE_CACHE_DB`) and make `render.yaml`, `docker-compose.yml` and `.env.example` agree.

### 10.3 Readiness
`/health/ready`: also call `core.mongo.ping()` in `asyncio.to_thread`; if Mongo is unreachable, return 503 with `"database": "mongo_unavailable"`. Keep the cache check.

### 10.4 `docker-compose.yml`
Add a `mongo:7` service and set `MONGODB_URI=mongodb://mongo:27017` for local compose runs, `FRESHGATE_SESSION_SECRET=${FRESHGATE_SESSION_SECRET:-dev-secret-change-me-at-least-32-chars}`, `FRESHGATE_STORE=mongo`.

### 10.5 `README.md`: add these sections
1. **What it is** and screenshots placeholder.
2. **Hackathon mapping** (Problem 01 Stale-answer detector, Problem 04 Bifrost extension; see Phase 11).
3. **Quick start (local):** `python -m venv .venv`, `pip install -r requirements.txt`, copy `.env.example` to `.env`, set `MONGODB_URI`, `FRESHGATE_SESSION_SECRET`, `cd dashboard && npm ci && npm run build`, `uvicorn server.app:app --reload`.
4. **Deploy on Render (step by step):**
   1. MongoDB Atlas: create a free M0 cluster, create a database user, in *Network Access* allow `0.0.0.0/0` (Render's free instances have no fixed IP), copy the `mongodb+srv://...` string.
   2. Push the repo to GitHub (10.6).
   3. Render dashboard → *New* → *Blueprint* → pick the repo (it reads `render.yaml`) → set `MONGODB_URI` → deploy.
   4. Open the Render URL, register, go to **Profile**, pick a provider, click **Load models**, paste the Tavily key, save.
   5. Free-plan note: free web services sleep after ~15 min without traffic, which pauses the Watchtower scheduler; use a free pinger (UptimeRobot / cron-job.org) hitting `https://<your-app>.onrender.com/health/live` every 10 minutes, or upgrade to Starter.
5. **Security notes:** per-user isolation, encrypted keys (Fernet derived from `FRESHGATE_SESSION_SECRET`; **changing this secret makes saved keys unreadable, users must re-enter them**), SSRF guard, offline-provider limitation on hosted deployments.
6. **Shared evidence cache note:** the claim-evidence cache is shared across users (it stores public web evidence for identical claim text and never any user credentials).

### 10.6 GitHub push (do NOT run `git push` yourself; I will, with my credentials)
1. Verify `git status` is clean of secrets: run `git ls-files | grep -E "\.env$|\.db$|\.so$|node_modules|dashboard/dist"`: must output nothing.
2. `git add -A && git commit -m "feat: per-user Mongo profile, watchtower, isolation, dark theme, Render deploy"` (one commit per phase is better; do that if easy).
3. Print the exact commands for me to run:
   ```
   git branch -M main
   git remote add origin https://github.com/<USERNAME>/freshgate.git
   git push -u origin main
   ```
4. Confirm `LICENSE` exists (it does) and the README renders.

---

## Phase 11: Hackathon alignment, final tests, and manual checklist

### 11.1 Problem 01: Stale-answer detector (what the judges look for → where it already is)
- "Takes any LLM answer, flags claims likely to have gone stale, re-verifies against live sources, **freshness score per claim**": Verify page + `core/pipeline.py`, `core/scorer.py`, Tavily live search. Make sure the claim card in the UI shows the **freshness score number, the verdict, the evidence age/date, and at least one source link**. If the score is missing (null), show "Not scored" rather than an empty cell.
- Add a **"Try an example"** button under the textarea on the Verify page that fills in a short sample answer containing 3 time-sensitive claims (a CEO of a company, a product price, a latest software version). Hard-code a neutral sample string in the front end. This makes the demo work in 10 seconds for judges.

### 11.2 Problem 04: Extend the open-source Bifrost gateway
- Already built: `plugin/` (Go plugin), `/v1/chat/completions` proxy mode with `shadow / warn / enforce`, X-FreshGate response headers, circuit breaker with fail-open, audit log.
- Add a README section **"Using FreshGate in front of Bifrost"** with a copy-paste `curl` that sends a chat completion through `/v1/chat/completions` and shows the `X-FreshGate-Score` / `X-FreshGate-Stale-Count` headers, and the three modes (`X-FreshGate-Mode: shadow|warn|enforce`). Source the exact header names from `server/app.py` (they are listed in `proxy_chat_completions`).
- Add a short `docs/demo-script.md` (a 3-minute walkthrough: register → Profile → Verify example → add stale claim to Watchtower → show alert → Replay → gateway curl).

### 11.3 Automated checks to add or keep passing
- `python -m pytest -q` all green (SQLite mode for legacy tests via `conftest.py`).
- New tests: `tests/test_mongo_store_isolation.py` (Phase 2), `tests/test_profile_settings.py` (use `mongomock` + FastAPI `TestClient` and monkeypatch `server.users.users_collection`): saving with blank Tavily and none saved → 422; offline provider with no key → 200; provider switch drops old key; `POST /api/profile/models` with a mocked `httpx` returns parsed ids for the three response shapes; `validate_base_url("http://169.254.169.254")` raises in production mode; `decrypt_secret("gAAAA-garbage")` returns `""`; `/verify` without a session and without headers → 401; `/verify` with an incomplete profile → 400 with the friendly message; `/api/watchtower/claims` without a session → 401; user B cannot read, check, patch or delete user A's claim (404).
- `cd dashboard && npm run build` has no errors.

### 11.4 Manual checklist (report pass/fail for each)
1. Register user A. Profile shows "incomplete". Verify page shows the setup callout and a disabled Check button.
2. In Profile pick OpenAI, paste key, click **Load models**: list appears; choose a model; Base URL is read-only and pre-filled; leave Tavily empty → error; add Tavily → saved. No Base URL was ever typed.
3. Sign out, sign in again: provider, model, Base URL, "✓ Saved" pills and hints are all present. Verify page says "Using OpenAI · <model> · Tavily key ✓".
4. Switch provider to Ollama: API key shows OPTIONAL and the offline hint; saving without a key works.
5. Verify page: no provider section anywhere. Click "Try an example" then Check: claims with scores and sources appear.
6. Add a claim to Watchtower from a verified claim; reload the browser; it is still there. (Mongo `monitored_claims` collection contains it with the user's `owner_id`.)
7. Click **Remove** on a claim: inline confirm, claim disappears, and it is gone after refresh and from Mongo.
8. Click **Check now**: uses the user's own model (no `gpt-4o-mini model_not_found` error). A claim checked twice with `unverifiable` does **not** create an `unverifiable → unverifiable` alert.
9. Register user B in a private window: B sees **no** claims, alerts, policies or history of A. With B's session, calling `GET /api/watchtower/claims/<A's id>` returns 404; `DELETE` returns 404.
10. Logged out, `curl /api/watchtower/claims`, `/api/policies`, `/api/provider/status`, `/metrics`, `/verify` all return 401.
11. Dark mode: no brown tint anywhere; panels, inputs and sidebar are distinct shades of graphite; green accent; placeholders dimmer than typed text; focus rings visible; status chips readable. Check Auth, Verify, System, Benchmarks, Audit, Policy Studio, Watchtower, Replay, Profile.
12. Layout: Base URL no longer floats; hint and Tavily label don't touch; nothing overflows horizontally at 390px.
13. Sign out clears the screen state; signing in as a different user never flashes the previous user's data.
14. Docker: `docker build .` succeeds. `docker run -p 8000:8000 --env-file .env.example ...` boots (it may report Mongo unavailable, but must not crash).
15. After deploying on Render: `/health/ready` is 200, registration works over HTTPS (cookie is `Secure`), and the Watchtower check works against the deployed instance.

### 11.5 Things I (the human) must do, not the model
- Rotate all keys that were in `.env`.
- Create the MongoDB Atlas cluster and put `MONGODB_URI` in Render.
- Run the `git push` commands.
- Optionally set up the UptimeRobot ping.
