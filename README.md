# FreshGate

**Catch stale facts in LLM answers before your users do.**

LLMs state facts that have quietly changed: a CEO who left, an API price that went up, a library version that is no longer current. FreshGate takes any LLM answer, extracts the time-sensitive claims, re-checks each one against live web sources, and returns a verdict and a freshness score per claim. It works as a standalone verifier (dashboard, REST API, CLI) and as a drop-in verification layer in front of the [Bifrost](https://github.com/maximhq/bifrost) AI gateway.

Built for a hackathon, covering two tracks:

- **Track 01, Stale-answer detector:** claim extraction, live re-verification, per-claim freshness score.
- **Track 04, Bifrost gateway extension:** an OpenAI-compatible proxy with `shadow` / `warn` / `enforce` modes, response headers, circuit breaker with fail-open, and a native Go plugin.

---

## 🚀 Strategic Enhancements (GeekRoom AI Hackathon)

FreshGate now includes seven major capability extensions that dramatically expand its verification intelligence and operational maturity:

### Phase 1: Temporal Freshness Scoring Engine
- **Multiple decay curves**: Per-claim-type freshness decay using `linear`, `exponential`, or `step` functions.
- **Category half-life defaults**: Pricing (1h), versions (6h), laws (7d), leadership (24h), etc.
- **Predictive indicators**: Flip probability, recommended check interval, and temporal stability tags per claim.

### Phase 2: Multi-Source Evidence Triangulation
- **Specialized search clients**: Wikipedia (general knowledge), GitHub (releases & vulnerabilities), Crossref (academic papers).
- **Source type authority multipliers**: Government (1.0), Academic (0.95), API (0.9), Document (0.85), Web (1.0).
- **Cross-source consensus**: Computes agreement scores with diversity bonuses for multi-type evidence.

### Phase 3: Predictive Claim Decomposition
- **Volatility & evidence gap scoring**: Forward-looking claims tagged with risk metadata.
- **Future pattern detection**: Identifies "will release", "expected Q3", "scheduled for" announcements.
- **File upload extraction**: In-memory text extraction from PDF, DOCX, TXT, MD, JSON, CSV (10 MB limit).

### Phase 4: Proactive Watchtower Monitoring
- **Priority scheduling**: Claims sorted by flip probability descending; high-risk checked first.
- **Optimal intervals**: Dynamic check intervals based on volatility + evidence freshness.

### Phase 5: Executive Summary & Knowledge Base Export
- **Executive summary**: Verdict distribution, score trends, timeline visualizations.
- **Temporal trends**: Half-life estimation from score decay curves.
- **ISO 25964 knowledge base**: JSON, XML, CSV exports for compliance.
- **Full HTML report**: Standalone printable report with evidence citations.

### Phase 6: Watchtower Email Digests
- **SMTP delivery**: HTML digest reports with claim summaries, alerts, and predictive indicators.
- **Configurable schedules**: Cron expressions or interval dropdowns.
- **Dashboard UI**: Email schedule panel with test send capability.

### Phase 7: File Upload Verification
- **Multipart & JSON endpoints**: `POST /verify/upload` accepts documents + answer text.
- **In-memory processing**: Files read, extracted, and discarded without disk persistence.
- **Drag-and-drop UI**: File attachment zone in Verify view with size/type validation.

---

## Contents

- [How it works](#how-it-works)
- [Features](#features)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Using FreshGate](#using-freshgate)
- [Gateway proxy mode](#gateway-proxy-mode)
- [Audit log](#audit-log)
- [CLI](#cli)
- [Go plugin for Bifrost](#go-plugin-for-bifrost)
- [Deploying](#deploying)
- [Testing and benchmarks](#testing-and-benchmarks)
- [Security notes](#security-notes)
- [Known limitations](#known-limitations)
- [Project layout](#project-layout)
- [License](#license)

---

## How it works

```
 LLM answer text
       │
       ▼
┌──────────────┐   ┌──────────┐   ┌───────────────┐   ┌──────────────┐
│ 1. Extract   │──▶│ 2. Triage│──▶│ 3. Cache      │──▶│ 4. Verify    │
│ atomic claims│   │ volatile │   │ lookup        │   │ Tavily search│
│ (LLM or      │   │ vs       │   │ (SQLite/Redis,│   │ + LLM judge  │
│  heuristics) │   │ evergreen│   │  per-category │   │ (single-     │
└──────────────┘   └──────────┘   │  TTL)         │   │  flight)     │
                                  └───────────────┘   └──────┬───────┘
                                                             ▼
                              ┌──────────────────────────────────────┐
                              │ 5. Score + verdict per claim         │
                              │    history snapshot + audit record   │
                              └──────────────────────────────────────┘
```

1. **Extract** the factual claims in the answer (an OpenAI-compatible LLM, with a heuristic fallback).
2. **Triage** claims into volatile categories (pricing, versions, leadership, law, ...) and skip evergreen facts.
3. **Cache** lookup with per-category TTLs. Identical in-flight claims are coalesced so one search serves many requests.
4. **Verify** each remaining claim with Tavily web search plus an LLM judge that weighs source authority, recency and agreement.
5. **Score** each claim (0 to 100) with a verdict: `confirmed`, `contradicted`, `partially_supported` or `unverifiable`. Every result is saved as an immutable history snapshot and written to the audit log.

---

## Features

| Area | What you get |
| --- | --- |
| **Verify** | Paste an answer, get per-claim verdicts, scores, corrections and cited sources. "Try an example" button for demos. **File upload** for PDF/DOCX/TXT/MD/JSON (up to 10 MB). |
| **Policy Studio** | Per-user policies: which categories to check, allowed/blocked/preferred domains, minimum source count and authority, evidence age limits, jurisdiction, cache TTLs, and `shadow`/`warn`/`enforce` mode. |
| **Watchtower** | Monitor important claims on a schedule. Alerts fire when a verdict, score or the evidence changes. Leased jobs with retry and backoff. **Predictive scheduling** checks high-risk claims first. **Email digests** with cron/interval scheduling. |
| **Replay** | Immutable per-claim history. Compare snapshots over time (verdict, score, evidence). **Executive summary & ISO 25964 export** (JSON, XML, CSV, HTML report). |
| **Audit log** | Append-only record of every verification (dashboard, proxy, streaming, plugin, Watchtower). Admin-key protected. |
| **Gateway proxy** | OpenAI-compatible `/v1/chat/completions` in front of Bifrost, with `X-FreshGate-*` headers, streaming support and fail-open behavior. |
| **Resilience** | Per-service circuit breakers, single-flight coalescing, rate limiting (Redis with in-memory fallback), request size limits, security headers. |
| **Multi-user** | Accounts, signed session cookies, per-user provider/model/keys (encrypted at rest), per-user data isolation in MongoDB. |
| **Providers** | OpenAI, Groq, Gemini, OpenRouter, Ollama and any custom OpenAI-compatible endpoint. |
| **CLI** | `freshgate verify`, `batch`, `status`, `benchmark` with CI-friendly exit codes and `text`/`json`/`markdown` output. |
| **Dashboard** | React 19 + Vite + TypeScript, light and dark themes. **Email schedule config**, **file upload zone**, **predictive indicators**, **export buttons**. |

Claim categories: pricing, software/model versions, law, leadership, product availability, cybersecurity/CVEs, financial metrics, government policy, public health, company info, scientific findings.

---

## Quick start

### Prerequisites

- Python 3.11+
- Node.js 22+ and npm
- MongoDB (local `mongodb://localhost:27017` or a free MongoDB Atlas cluster)
- Your own LLM provider key and a [Tavily](https://tavily.com) key (entered in the dashboard Profile page)

### Run locally

```bash
git clone https://github.com/<your-username>/freshgate.git
cd freshgate

python -m venv .venv
source .venv/bin/activate          # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

cp .env.example .env               # then edit .env (see below)

cd dashboard && npm ci && npm run build && cd ..

uvicorn server.app:app --reload --host 127.0.0.1 --port 8000
```

Open <http://localhost:8000>, register an account, then open **Profile** and choose a provider, enter its API key and your Tavily key. After that, the **Verify** page works.

Windows users can run `./run.ps1`, which creates the virtualenv, installs dependencies, builds the dashboard and starts the server.

**Minimum `.env`** (generate real secrets, do not reuse examples):

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"   # run twice
```

```
MONGODB_URI=mongodb://localhost:27017
FRESHGATE_SESSION_SECRET=<random string, 32+ chars>
FRESHGATE_ADMIN_KEY=<random string, 24+ chars>
```

### Frontend development

```bash
cd dashboard
FRESHGATE_API_ORIGIN=http://127.0.0.1:8000 npm run dev
```

Vite proxies `/api`, `/health`, `/verify` and `/metrics` to the backend.

### Docker Compose (full stack)

Starts FreshGate, MongoDB 7, Redis and Bifrost:

```bash
export FRESHGATE_SESSION_SECRET=$(python -c "import secrets;print(secrets.token_urlsafe(32))")
export FRESHGATE_ADMIN_KEY=$(python -c "import secrets;print(secrets.token_urlsafe(32))")
docker compose up --build
```

FreshGate is at <http://localhost:8000>, Bifrost at <http://localhost:8080>.

---

## Configuration

All settings are environment variables (see `.env.example` for the full list).

| Variable | Default | Purpose |
| --- | --- | --- |
| `MONGODB_URI`, `MONGODB_DATABASE` | `mongodb://localhost:27017`, `freshgate` | Accounts, policies, Watchtower, history |
| `FRESHGATE_SESSION_SECRET` | (required) | Signs session cookies and encrypts stored keys. 32+ random chars. Changing it invalidates sessions and stored keys. |
| `FRESHGATE_ADMIN_KEY` | (unset) | Protects admin routes (audit log, cache clear, circuit-breaker reset). Unset means those routes return 403. |
| `FRESHGATE_API_KEY` | (unset) | Client key for the proxy and plugin routes. Required in production. |
| `FRESHGATE_ENV` | `development` | Set `production` to force HTTPS-only provider URLs and secure cookies. |
| `FRESHGATE_MODE` | `enforce` | Proxy mode: `shadow`, `warn`, `enforce` |
| `FRESHGATE_BLOCK_STALE` | `false` | In `enforce` mode, return HTTP 422 for stale responses |
| `FRESHGATE_MIN_SCORE` | `0` | Minimum average score before blocking |
| `FRESHGATE_FAIL_OPEN` | `true` | Pass the response through if verification fails or times out |
| `FRESHGATE_TIMEOUT` | `8.0` | Overall verification timeout (seconds) |
| `BIFROST_UPSTREAM_URL` | `http://localhost:8080` | Upstream gateway for the proxy |
| `OPENAI_API_KEY`, `TAVILY_API_KEY`, `OPENAI_BASE_URL`, `LLM_MODEL` | | Server-side keys, used **only** by the proxy, plugin route and Watchtower. Dashboard users use their own Profile keys. |
| `FRESHGATE_CACHE_DB`, `FRESHGATE_AUDIT_DB`, `FRESHGATE_FEATURE_DB` | `freshgate_*.db` | SQLite files. Put these on persistent storage in production. |
| `REDIS_URL` | | Optional shared cache and rate limiter |
| `FRESHGATE_RATE_LIMIT_PER_MINUTE` | `60` | Per-client limit on verification endpoints |
| `FRESHGATE_MAX_BODY_SIZE_BYTES` | `1048576` | Request body limit |
| `FRESHGATE_WATCHTOWER_*` | | Enable, poll interval, concurrency, claim cap, lease, retries |
| `FRESHGATE_SMTP_HOST`, `FRESHGATE_SMTP_PORT`, `FRESHGATE_SMTP_USER`, `FRESHGATE_SMTP_PASSWORD`, `FRESHGATE_SMTP_FROM`, `FRESHGATE_SMTP_TLS` | | SMTP config for Watchtower email digests |
| `FRESHGATE_ADMIN_EMAIL` | | Default recipient for Watchtower email reports |
| `FRESHGATE_ALLOWED_UPLOAD_TYPES`, `FRESHGATE_MAX_UPLOAD_SIZE_BYTES` | `.pdf,.txt,.md,.json,.docx`, `10485760` | File upload validation |
| `FRESHGATE_ENABLE_WIKIPEDIA_SEARCH`, `FRESHGATE_ENABLE_GITHUB_SEARCH`, `FRESHGATE_ENABLE_ACADEMIC_SEARCH` | `false` | Enable multi-source evidence search |
| `FRESHGATE_DEFAULT_DEGRADATION_CURVE` | `exponential` | Temporal decay curve default |
| `FRESHGATE_MIN_TEMPORAL_SCORE` | `0` | Floor for temporal decay factor |

---

## Using FreshGate

### Dashboard

| Page | Purpose |
| --- | --- |
| **Verify** | Check an answer (text or uploaded document), optionally against a policy |
| **System / Benchmarks** | Health, provider status, circuit breakers, cache stats, benchmark results |
| **Audit log** | Admin-only verification trail |
| **Policy Studio** | Create and simulate policies |
| **Watchtower** | Monitored claims, alerts, **email schedule**, **predictive indicators** |
| **Replay** | Claim history, **export buttons** (JSON/XML/CSV/HTML report) |
| **Profile** | Provider, model, API keys |

### REST API

```bash
# Logged-in browser session, or send provider keys per request:
curl -X POST http://localhost:8000/verify \
  -H "Content-Type: application/json" \
  -H "X-LLM-Provider: openai" \
  -H "X-LLM-API-Key: $OPENAI_API_KEY" \
  -H "X-Tavily-API-Key: $TAVILY_API_KEY" \
  -d '{"answer": "The latest stable Python release is 3.11."}'
```

| Endpoint | Auth | Description |
| --- | --- | --- |
| `POST /verify` | session or request keys | Verify answer text |
| `POST /verify/upload` | session or request keys | Verify answer text with document upload |
| `POST /v1/chat/completions`, `POST /proxy/v1/chat/completions` | upstream auth | Verifying proxy (see below) |
| `POST /api/plugin/verify` | `X-API-Key` | Endpoint used by the Go plugin |
| `GET /api/stream/verification/{id}` | session or admin | Result of an async stream verification |
| `/api/policies`, `/api/watchtower/*`, `/api/history/*`, `/api/replay/*` | session | Policy Studio, Watchtower, Replay |
| `GET /api/audits/recent` | `X-Admin-Key` | Audit log (`limit` 1-500, `offset`, `source`) |
| `/api/export/claim/{fingerprint}/*` | session | Executive summary, temporal trends, knowledge base (JSON/XML/CSV), full HTML report |
| `GET /api/watchtower/email/schedule`, `POST /api/watchtower/email/schedule` | session | Email schedule config (cron/interval) |
| `POST /api/watchtower/email/test` | session | Send test watchtower digest |
| `GET /metrics` | session or admin | Prometheus metrics |
| `GET /health/live`, `GET /health/ready` | none | Liveness and readiness probes |
| `POST /api/cache/clear`, `POST /api/circuit-breaker/reset` | `X-Admin-Key` | Operations |

Full reference: [`docs/13_api_reference.md`](docs/13_api_reference.md). Interactive docs at `/docs` when the server is running.

---

## Gateway proxy mode

Point your OpenAI SDK at FreshGate instead of Bifrost. FreshGate forwards the request, verifies the completion, and annotates the response.

```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:8000/v1", api_key="<your key>")
```

| Mode | Behavior |
| --- | --- |
| `shadow` | Returns the upstream response immediately; verifies in the background and records an audit entry |
| `warn` | Verifies inline; adds `X-FreshGate-*` headers, body unchanged |
| `enforce` | Verifies inline; adds a `freshgate` object to the body; with `FRESHGATE_BLOCK_STALE=true` returns HTTP 422 on stale claims or low scores |

Select a mode per request with the `X-FreshGate-Mode` header or `?freshgate_mode=`.

Response headers: `X-FreshGate-Score`, `-Stale-Count`, `-Total-Claims`, `-Checked-Claims`, `-Latency-Ms`, `-Status`, `-Mode`. If verification times out or the circuit breaker is open, FreshGate **fails open** (the original response is returned with `X-FreshGate-Status: fail-open`).

Streaming (`"stream": true`) responses are passed through untouched. The full text is verified after the stream ends and the result is available at `/api/stream/verification/{completion_id}`.

---

## Audit log

Every verification path writes an append-only record: dashboard `verify`, `proxy`, `proxy_stream`, `proxy_async` (shadow), `plugin` and `watchtower`. Records are never overwritten.

Open **Audit log** in the dashboard, enter the admin key, and click **Load records**. The key is sent only with that request and is not stored. Records include source, request id, claim counts, score, latency, and the input text and claims (expandable).

Audit records live in the SQLite file at `FRESHGATE_AUDIT_DB`. On hosts with ephemeral disks (for example Render's free plan) the file resets on redeploy; the page shows a notice in that case.

---

## CLI

```bash
pip install -e .                 # installs the `freshgate` command
export FRESHGATE_URL=http://127.0.0.1:8000

freshgate status
freshgate verify --text "Python 3.13 is the current stable release." --min-score 70
freshgate batch --file answers.json --format markdown
freshgate benchmark --suite unit
```

Useful flags: `--policy-id`, `--min-score`, `--fail-on-unverifiable`, `--warning-only`, `--format text|json|markdown`, `--url`, `--timeout`, `--retries`. The CLI exits non-zero when thresholds fail, so it fits CI pipelines. Provider keys are read from `FRESHGATE_OPENAI_API_KEY` / `OPENAI_API_KEY` and `FRESHGATE_TAVILY_API_KEY` / `TAVILY_API_KEY`. See [`docs/developer-cli.md`](docs/developer-cli.md).

---

## Go plugin for Bifrost

`plugin/freshgate_plugin.go` is a native Bifrost dynamic plugin. It sends completed responses to FreshGate's `/api/plugin/verify` and decorates them with freshness headers, failing open on errors.

```bash
cd plugin
go build -buildmode=plugin -o freshgate.so freshgate_plugin.go
```

Configure `freshgate_url` in `plugin.json` and supply the key through the `FRESHGATE_API_KEY` environment variable. Plugins must be built with the same Go toolchain and Bifrost version as the host (Linux/macOS only). See [`plugin/README.md`](plugin/README.md).

---

## Deploying

### Render (Blueprint)

1. Create a MongoDB Atlas cluster and copy the connection string. Allow Render's egress IPs in Atlas network access.
2. Push the repo to GitHub.
3. In Render choose **New + > Blueprint**, select the repo. `render.yaml` provisions the Docker web service, generates `FRESHGATE_SESSION_SECRET` and `FRESHGATE_ADMIN_KEY`, and prompts for `MONGODB_URI`.
4. Open the deployed URL, register, and complete **Profile**.

Caveats for the free plan: the instance sleeps when idle (Watchtower pauses while asleep) and local SQLite files (cache, audit) are on ephemeral storage and reset on redeploy. For durable audit data use a paid plan with a disk mounted at `/data` and point the three `FRESHGATE_*_DB` paths there.

Set `FRESHGATE_API_KEY` before exposing the proxy or plugin routes publicly, because they spend server-side provider credits.

More: [`docs/11_deployment_and_rollback_guide.md`](docs/11_deployment_and_rollback_guide.md), [`docs/10_production_operations_guide.md`](docs/10_production_operations_guide.md).

---

## Testing and benchmarks

```bash
pip install -r requirements-dev.txt
python -m pytest -q                      # unit and integration tests (mongomock, no network)
cd dashboard && npm run build            # type-check and build the UI
python bench/evaluate.py                 # offline benchmark
python bench/evaluate_features.py        # policy / Watchtower / Replay scenarios
```

The checked-in report (`bench/results.md`) covers 520 labelled claims. Read it with these caveats:

- Latency figures (about 2 ms median) come from the offline harness using cached evidence. They are not live search plus LLM latency, which is typically seconds.
- Recall for stale-claim detection is 88.1%, below the 90% target in the report itself, and the **leadership** category is the weakest (stale claims often go undetected). Precision looks perfect partly because the labelled set has few tricky evergreen controls.
- Re-run with `--live` for numbers against real providers (this calls paid APIs).

---

## Security notes

- Provider and Tavily keys are encrypted at rest with a key derived from `FRESHGATE_SESSION_SECRET`.
- Sessions are signed, `HttpOnly`, `SameSite=Lax` cookies; `Secure` in production.
- Admin routes use constant-time key comparison and are disabled when no admin key is configured.
- Provider base URLs are validated against SSRF (HTTPS only and no private/loopback addresses in production).
- Request size limits, per-client rate limits, strict CSP and standard security headers are on by default.
- **Never commit `.env` or any `*.db` file.** If a key was ever committed, rotate it.
- Do not run behind a proxy with `--forwarded-allow-ips='*'` unless the proxy strips client-supplied `X-Forwarded-For`.

See [`docs/03_security_architecture.md`](docs/03_security_architecture.md) and [`docs/04_threat_model.md`](docs/04_threat_model.md).

---

## Known limitations

- Verification quality depends on Tavily's results and the LLM judge; it reduces stale answers but does not guarantee correctness.
- Audit storage is SQLite only. A PostgreSQL backend is not implemented.
- The claim cache is shared across users (it caches public-fact verdicts, not user data).
- Audit records store the verified text. Treat the audit file as sensitive.
- The Go plugin must be compiled for your exact Bifrost version.

---

## Project layout

```
core/        Verification engine: extractor, triage, verifier, scorer, cache, circuit breaker,
             audit store, policies, history/replay, Watchtower, security, config
server/      FastAPI app, routes (policies, watchtower, replay, providers, profile), auth, SSRF guard
dashboard/   React + Vite + TypeScript UI (built into dashboard/dist and served at /)
cli/         `freshgate` command-line client
plugin/      Native Bifrost Go plugin
bench/       Benchmark datasets, harness and reports
tests/       pytest suite
docs/        Architecture, security, operations, API reference, feature guides
```

---

## License

MIT. See [`LICENSE`](LICENSE).
