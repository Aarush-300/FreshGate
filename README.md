# FreshGate

FreshGate checks time-sensitive factual claims in AI answers against web evidence. It provides a React dashboard, a FastAPI verification service, an OpenAI-compatible gateway, and Watchtower for tracking claims over time.

A verdict is evidence-based guidance, not a guarantee of correctness. Search coverage, source quality, and the configured language model affect results.

## Capabilities

| Component | Purpose |
| --- | --- |
| Verify | Extract claims from text or documents and return verdicts, freshness scores, corrections, and sources. |
| Policy Studio | Configure categories, evidence requirements, domain preferences, jurisdiction, and enforcement mode. |
| Watchtower | Enroll claims, schedule rechecks, retain the latest result, and raise change alerts. |
| Email reports | Send saved monitoring results and active alerts on a separate delivery schedule. |
| Replay | Inspect immutable verification history and compare evidence over time. |
| Audit log | Record verification activity across dashboard, gateway, plugin, and monitoring paths. |
| Gateway | Forward chat completions to Bifrost and verify generated answers. |
| CLI | Verify text, process batches, inspect status, and run benchmarks. |

Supported volatile categories include pricing, versions, leadership, law, product availability, cybersecurity, financial information, government policy, public health, company information, and scientific findings.

## Architecture and data flow

1. The extractor identifies factual claims using the configured language model, with heuristic fallback behavior.
2. Triage selects claims that need freshness checks.
3. The cache reuses eligible results according to category and policy settings.
4. The verifier retrieves evidence through Tavily and asks an OpenAI-compatible model to evaluate it.
5. The scorer assigns a freshness score and verdict: `confirmed`, `contradicted`, `partially_supported`, or `unverifiable`.
6. History snapshots and audit records retain results for later inspection.
7. Watchtower rechecks enrolled claims and emails summaries of persisted results.

The dashboard uses the credentials saved in each user's Profile. The server's Watchtower resolver also loads the monitored claim owner's Profile credentials. Gateway and plugin integrations use server configuration.

MongoDB provides owner-scoped feature storage. SQLite feature storage is intended for single-user local use: its `for_owner()` method does not isolate users. Cache and audit storage have separate SQLite paths; MongoDB does not automatically move these files into MongoDB.

## Prerequisites

- A Python installation compatible with [requirements.txt](requirements.txt); the Dockerfile defines the deployment runtime.
- Node.js and npm for building the dashboard; the Dockerfile defines the frontend build runtime.
- MongoDB for a multi-user installation and owner-aware scheduled checks.
- An LLM provider account or supported local endpoint, plus a Tavily search key.
- Optional Redis for shared caching/rate limiting and Bifrost for gateway use.

## Local setup

Run commands from the repository root. Do not overwrite an existing `.env` containing your configuration.

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
Set-Location dashboard
npm ci
npm run build
Set-Location ..
python -m uvicorn server.app:app --reload --host 127.0.0.1 --port 8000
```

Edit `.env` before starting the server. The supplied `run.ps1` can also prepare dependencies, build the dashboard, and launch the application.

### macOS / Linux

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
cd dashboard
npm ci
npm run build
cd ..
python -m uvicorn server.app:app --reload --host 127.0.0.1 --port 8000
```

Open <http://localhost:8000>, register, and configure **Profile** with a provider, model, provider key, and Tavily key. Verify a short answer before enabling monitoring.

The application loads `.env`. A file named `env` without the leading dot is not the standard configuration file. Restart the backend after changing environment configuration.

### Storage configuration

For single-user experimentation:

```dotenv
FRESHGATE_ENV=development
FRESHGATE_STORE=sqlite
FRESHGATE_USERS_DB=freshgate_features.db
FRESHGATE_FEATURE_DB=freshgate_features.db
FRESHGATE_CACHE_DB=freshgate_cache.db
FRESHGATE_AUDIT_DB=freshgate_audit.db
```

For accounts with isolated monitoring data:

```dotenv
FRESHGATE_STORE=mongo
MONGODB_URI=mongodb://localhost:27017
MONGODB_DATABASE=freshgate
```

Replace the placeholder MongoDB URI in `.env.example` with a working connection string. The local SQLite feature store does not retain owner identity for the server's per-owner pipeline resolver; use MongoDB for the full multi-user Watchtower workflow.

Generate persistent secrets with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Use separate values for `FRESHGATE_SESSION_SECRET`, `FRESHGATE_ADMIN_KEY`, and, where needed, `FRESHGATE_API_KEY`. Keep the session secret stable: it is used for sessions and stored credential encryption.

### Frontend development

Start the backend on port 8000, then run `npm run dev` inside `dashboard/`. Set `FRESHGATE_API_ORIGIN` to override the backend origin used by the Vite proxy. For example, in PowerShell:

```powershell
$env:FRESHGATE_API_ORIGIN = 'http://127.0.0.1:8000'
npm run dev
```

## Watchtower workflow

1. Verify an answer and choose **Monitor claim**, or add a claim directly in Watchtower.
2. Check that the claim appears in the monitored list. Enrollment from a verification can include its baseline verdict, score, and sources.
3. Set the monitoring interval. Registration schedules the first check after that interval; use **Check now** for an immediate check.
4. Save working provider and Tavily credentials in the owner's Profile.
5. Inspect the latest result, error, next check time, and history in Watchtower.
6. Configure email delivery on the server and save an email schedule in Watchtower.

Rechecks use leases to prevent concurrent processing of a claim, bounded concurrency, and retry backoff. Alerts cover selected verdict changes, score threshold crossings, material evidence changes, and repeated insufficient evidence. Acknowledging an alert removes it from the email's active-alert section.

### What a digest contains

Reports include all enrolled claims, including claims without history snapshots. They use a newer matching snapshot when available, otherwise the monitored claim's saved baseline/latest result and evidence. Claims awaiting a result remain visible as pending. Verification timestamps are formatted as UTC ISO timestamps.

Email delivery and claim verification run on separate schedules. A digest summarizes stored results; sending a digest does not itself run a fresh verification. Paused claims remain enrolled and can still appear with their saved results.

The scheduler runs a batch of due checks before evaluating email schedules. A large backlog can therefore leave some claims awaiting their next check when a digest is sent.

### Scheduling behavior

- `interval_seconds` supports 60 seconds through 30 days.
- Custom minute/hour/day/week values are converted to intervals.
- The recognized cron presets map to 6-hour, 12-hour, daily, or weekly intervals. This is not a general cron parser and does not promise delivery at a particular wall-clock time.
- Saving a schedule sets its next delivery relative to the save time.
- Delivery occurs on a scheduler poll after the report becomes due.
- A successful send advances `last_sent_at` and `next_run_at`; a failed send leaves the schedule due for retry.
- Pausing an email schedule stops its reports, independently of claim monitoring.

## Email configuration

The service selects delivery in this order: Brevo, Resend, SendGrid, then SMTP. Configure the intended provider and sender identity. HTTPS providers are useful when outbound SMTP is unavailable.

Example using Brevo:

```dotenv
BREVO_API_KEY=<your-provider-key>
FRESHGATE_SMTP_FROM=FreshGate Watchtower <verified-sender@example.com>
FRESHGATE_ADMIN_EMAIL=reports@example.com
```

Alternatives are `RESEND_API_KEY` or `SENDGRID_API_KEY`. Sender validation and delivery restrictions depend on your provider account.

For SMTP:

```dotenv
FRESHGATE_SMTP_HOST=smtp.example.com
FRESHGATE_SMTP_PORT=587
FRESHGATE_SMTP_USER=<smtp-username>
FRESHGATE_SMTP_PASSWORD=<smtp-password>
FRESHGATE_SMTP_FROM=reports@example.com
FRESHGATE_SMTP_TLS=true
FRESHGATE_ADMIN_EMAIL=reports@example.com
```

Port 587 uses STARTTLS; port 465 uses implicit TLS. Save a recipient on each user's email schedule. `FRESHGATE_ADMIN_EMAIL` is the fallback recipient. One user's schedule does not change another user's fallback configuration.

**Send test email** checks transport configuration with a test message; it does not send the monitored-claims digest. Wait for a scheduled report to validate digest contents.

## Configuration reference

See [.env.example](.env.example) and [core/config.py](core/config.py) for the full configuration surface.

| Setting | Purpose / default |
| --- | --- |
| `FRESHGATE_ENV` | Development or production environment. |
| `FRESHGATE_STORE` | Explicitly choose `sqlite` or `mongo`. |
| `FRESHGATE_USERS_STORE` | Optional account-store override. |
| `FRESHGATE_SESSION_SECRET` | Persistent session and credential-encryption secret. |
| `FRESHGATE_ADMIN_KEY` | Protects administrative operations. |
| `FRESHGATE_API_KEY` | Client key for protected integrations. |
| `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `LLM_MODEL` | Server LLM configuration. |
| `TAVILY_API_KEY` | Server search credential. |
| `BIFROST_UPSTREAM_URL` | Upstream gateway address. |
| `FRESHGATE_MODE` | `shadow`, `warn`, or `enforce`; default `enforce`. |
| `FRESHGATE_TIMEOUT` | Verification budget, default 8 seconds. |
| `FRESHGATE_FAIL_OPEN` | Preserve responses on verification failure, default true. |
| `FRESHGATE_BLOCK_STALE`, `FRESHGATE_MIN_SCORE` | Optional enforcement thresholds. |
| `REDIS_URL` | Optional Redis connection. |
| `FRESHGATE_RATE_LIMIT_PER_MINUTE` | Default 60. |
| `FRESHGATE_MAX_BODY_SIZE_BYTES` | General body limit, default 1 MiB. |
| `FRESHGATE_MAX_UPLOAD_SIZE_BYTES` | Document limit, default 10 MiB. |
| `FRESHGATE_WATCHTOWER_ENABLED` | Enable scheduler, default true. |
| `FRESHGATE_WATCHTOWER_POLL_SECONDS` | Default 10 seconds. |
| `FRESHGATE_WATCHTOWER_MAX_CONCURRENCY` | Default 2 checks. |
| `FRESHGATE_WATCHTOWER_MAX_CLAIMS` | Enrollment cap, default 50; Compose overrides this. |
| `FRESHGATE_WATCHTOWER_LEASE_SECONDS` | Default 900 seconds. |
| `FRESHGATE_WATCHTOWER_MAX_RETRIES` | Default 3. |
| `FRESHGATE_WATCHTOWER_RETRY_BASE_SECONDS` | Default 60 seconds. |

## API and integrations

Interactive API documentation is available at `/docs` while the backend is running. See [API reference](docs/13_api_reference.md) for additional details.

| Route | Purpose |
| --- | --- |
| `POST /verify` | Verify answer text. |
| `POST /verify/upload` | Verify an uploaded document. |
| `/api/policies` | Policy management. |
| `POST /api/watchtower/monitor` | Enroll a claim. |
| `GET /api/watchtower/claims` | List monitored claims. |
| `POST /api/watchtower/claims/{id}/check` | Run an immediate check. |
| `/api/watchtower/alerts` | Inspect alerts. |
| `GET /api/watchtower/email/schedule` | Read the current user's schedule. |
| `POST /api/watchtower/email/schedule` | Save a schedule. |
| `POST /api/watchtower/email/schedule/stop` | Pause reports. |
| `POST /api/watchtower/email/test` | Test email delivery. |
| `/api/history/*`, `/api/replay/*` | History and comparisons. |
| `/api/export/claim/{fingerprint}/*` | Claim exports and reports. |
| `GET /api/audits/recent` | Admin audit records. |
| `GET /health/live`, `GET /health/ready` | Health probes. |

Dashboard feature routes require a signed-in session. Administrative routes use `X-Admin-Key`; integration routes have their own key requirements. Do not put provider keys into URLs.

### Gateway

Point an OpenAI-compatible client at `/v1` on FreshGate and configure Bifrost as the upstream. `shadow` verifies in the background, `warn` annotates responses with freshness headers, and `enforce` can include verification details and apply configured blocking thresholds.

Streaming text is passed through and checked after completion. Retrieve its result through `/api/stream/verification/{id}` with the required authorization. See [plugin documentation](plugin/README.md) for the native Bifrost Go plugin and toolchain requirements.

### CLI

```bash
python -m pip install -e .
freshgate status
freshgate verify --text "The latest stable release is version 3.13." --min-score 70
freshgate batch --file answers.json --format markdown
freshgate benchmark --suite unit
```

Configure `FRESHGATE_URL` and provider credentials for your environment. The CLI supports score thresholds, output formats, and failure exit codes for CI. See [CLI guide](docs/developer-cli.md).

## Deployment

`docker compose up --build` starts FreshGate, MongoDB, Redis, and Bifrost. Set `FRESHGATE_SESSION_SECRET` before starting; configure integration/admin keys as appropriate. FreshGate listens on port 8000 and Bifrost on 8080. Named volumes retain data.

The Compose service uses an explicit environment list. Add your selected email provider variables to the FreshGate service's environment or an appropriate `env_file`; placing them in the host `.env` alone does not automatically forward every variable into the container.

[render.yaml](render.yaml) provides a deployment blueprint. Configure MongoDB connectivity, stable secrets, and email credentials in the hosting environment. Watchtower only runs while the backend process is awake. Use persistent storage for local cache/audit files if you need them across redeploys.

Run a single scheduler process for email delivery: report schedules currently have no distributed delivery lease, so multiple backend workers can send duplicate reports. Claim checks have separate leases.

See [deployment and rollback](docs/11_deployment_and_rollback_guide.md) and [operations](docs/10_production_operations_guide.md).

## Troubleshooting

| Symptom | Checks |
| --- | --- |
| Mail arrives but reports no claims | Confirm enrollment under the same account as the schedule. Install the report fix in this version and restart the backend; older reports skipped claims without matching snapshots. |
| Claims appear but results remain old | Check next-check time, enabled state, errors, owner Profile credentials, and whether the backend is running. Email frequency does not control verification frequency. |
| Report generation fails after a successful check | Inspect backend logs. This version converts numeric snapshot timestamps before building email claims. |
| Test email works but scheduled mail does not | Confirm the schedule is enabled, its recipient and next-run time, and `FRESHGATE_WATCHTOWER_ENABLED`. |
| No mail arrives | Check selected provider precedence, sender verification, credentials, delivery logs, and spam folder. |
| Owner/credential error in SQLite monitoring | Use MongoDB for the server's owner-aware scheduled workflow. |
| MongoDB connection failure | Replace example credentials/URI and verify network access and database permissions. |
| Dashboard is missing | Run `npm ci` and `npm run build` in `dashboard/`. |
| Virtual environment cannot find Python | Recreate it with the local Python installation; virtual environments copied from another machine are not portable. |
| Configuration changes have no effect | Edit `.env` or deployment environment variables and restart the backend. |

## Tests and validation

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m pytest tests/test_watchtower.py tests/test_mongo_store_isolation.py -q
cd dashboard
npm run build
```

Watchtower regression tests cover baselines without snapshots, pending claims, newer history results, timestamp conversion, pagination, acknowledged alerts, MongoDB owner isolation, and delivery failure scheduling. Tests mock email delivery and do not send real messages.

Benchmark tools live under `bench/`. Offline results measure the harness and fixtures; they should not be interpreted as live provider latency or general accuracy guarantees. Live runs require configured credentials and can incur provider charges.

## Security and limitations

Keep `.env`, provider credentials, session secrets, and database files out of version control. Stored provider keys are encrypted using the session secret. Audit records include verified text and should be treated as sensitive data.

Use MongoDB for multi-user feature isolation. The public-fact cache is shared, and audit persistence is SQLite-based. Preserve the session secret and back up durable data before deployment changes. See [security architecture](docs/03_security_architecture.md) and [threat model](docs/04_threat_model.md).

## Repository layout

```text
core/        Verification, configuration, storage, history, Watchtower, email
server/      FastAPI application, authentication, provider resolution, routes
dashboard/   React / TypeScript / Vite interface
cli/         Command-line HTTP client
plugin/      Native Bifrost integration
bench/       Benchmark datasets and evaluation tools
tests/       Python regression and integration tests
docs/        Architecture, API, deployment, and feature guides
```

## License

MIT. See [LICENSE](LICENSE).
