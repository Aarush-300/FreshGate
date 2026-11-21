# FreshGate

FreshGate checks the parts of an AI answer that can go out of date. Give it a paragraph about a software release, a subscription price and a company executive. It extracts the claims, selects the ones that depend on current facts, looks for web evidence, and returns a verdict and freshness score for each checked claim. A reader can open the sources, inspect a suggested correction, and enroll a claim for another check later.

There are two main ways to use it. **The FreshGate page** lets a person investigate an answer or document. **The native Bifrost plugin** checks the assistant's answer as it passes through the gateway, so an application can receive the completion and its verification in one response.

## What distinguishes FreshGate

| Capability | What you can do with it |
| --- | --- |
| Claim-level evidence | See which sentence about a release is contradicted even when the pricing claim in the same answer is confirmed. Each checked claim carries its own verdict, score, evidence and verification method. |
| Evidence policies | Require independent sources, prefer an official domain, set an evidence age limit for pricing, and preview the rule against sample evidence before applying it. Policy revisions scope cache reuse. |
| Ongoing monitoring | Save a claim from a verification with its baseline evidence. Watchtower rechecks it and can raise an alert when the verdict changes, the score crosses a threshold, or the sources change materially. |
| Evidence Replay | Follow the same claim across checks using its fingerprint. Compare two saved snapshots to find the changed verdict, score, correction or source. Export the latest record for use outside the dashboard. |
| Gateway integration | Read freshness metadata from a Bifrost completion, or use the OpenAI-compatible proxy when a native plugin is impractical. The proxy can deliver streaming tokens while verification runs after the stream. |

## Run the FreshGate page locally

Use Python 3.11 and Node.js 22 with npm to match the container build. The default development configuration uses SQLite for one local account. The page needs a configured AI provider and a Tavily search key for live verification. Bifrost is optional for this workflow.

Create a local configuration if this is your first run. Keep an existing `.env` when updating the project.

**Windows PowerShell**

```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
.\run.ps1
```

The launcher prepares the Python environment, installs dashboard dependencies, builds the page and prints the address. It starts looking for a free local port at **8127**. Use the address it prints for the page and for any clients connecting to that server.

**Linux or macOS**

```bash
test -f .env || cp .env.example .env
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
npm ci --prefix dashboard
npm run build --prefix dashboard
python -m uvicorn server.app:app --host 127.0.0.1 --port 8000
```

Open [the local page](http://127.0.0.1:8000). For frontend development, run `npm run dev` in `dashboard/`. Vite's default API target is port 8127. Set `FRESHGATE_API_ORIGIN=http://127.0.0.1:8000` in the Vite process environment when using the manual backend command above.

### Walk through an answer

1. **Register and open Profile.** Choose a provider and model, enter the provider credentials and save a Tavily key. The browser workflow uses the signed-in user's settings. The server-level keys described below serve gateway traffic.
2. **Open Verify and select “Try an example.”** The supplied paragraph contains claims about a Python release, company leadership and model pricing. You can also paste your own answer or upload a TXT, Markdown, JSON, CSV, PDF or DOCX file. The default upload limit is 10 MiB.
3. **Inspect one claim.** Read the verdict, score, explanation, source snippets and any suggested correction. Follow the source link and check whether its date and scope match the claim. Results depend on the evidence available at check time.
4. **Use Policy Studio** to specify which sources and categories matter for your application. Select the saved policy in Verify and run the answer again. Shadow mode reports what would fail, warn mode records warnings, and enforce mode applies evidence eligibility requirements.
5. **Choose Monitor claim**, open **Watchtower** and use **Check now** to run a recheck. Scheduled rechecks continue while the backend scheduler is running. An optional email digest summarizes saved results and active alerts on its own schedule.
6. **Open Replay** from the monitored claim. After two snapshots exist, choose an earlier and a later record and compare them. The export controls provide JSON, XML, CSV and a readable HTML report.

**System** shows readiness and operational metrics. **Benchmarks** displays the packaged evaluation data. **Audit log** lets an operator with the configured admin key inspect verification activity, including the originating route and correlation ID.

## How to read a verification

Extraction identifies factual statements and normalizes categories such as pricing, versions, leadership and law. Triage selects volatile claims. For each selected claim, the pipeline checks for an eligible cached result or retrieves evidence through Tavily and evaluates it. The result records the verification method so a reader can distinguish an LLM judgment from a fallback.

| Verdict | How to interpret it |
| --- | --- |
| `confirmed` | The retrieved evidence supports the claim. |
| `contradicted` | The retrieved evidence conflicts with the claim. Look for a correction and check the cited source. |
| `partially_supported` | The evidence supports part of the statement or qualifies its scope. |
| `unverifiable` | The available evidence is insufficient to settle the claim. |

The **0–100 freshness score** combines the verdict, publication recency, source count and source authority. The scorer caps contradicted claims at 10 and unverifiable claims at 50. A score is an evidence summary, not a calibrated probability. The response's `summary.stale` counts contradicted claims. Read `summary.checked`, `summary.status` and the per-claim results alongside the average. An empty check can have a null average, and an extraction failure has its own status.

## Native Bifrost plugin

The Go plugin runs inside a compatible Bifrost HTTP gateway. Its post-LLM hook takes the text of the first choice in a completed, non-streaming chat response and sends `{"answer":"…"}` to FreshGate's dedicated `POST /api/plugin/verify` endpoint. The service uses its server-side provider credentials and records the check with audit source `plugin`.

In synchronous mode, the HTTP post-hook adds a top-level `freshgate` report alongside the provider's completion. Clients can read `freshgate.claims` for individual sources and corrections, or use `X-FreshGate-Status`, `X-FreshGate-Score` and `X-FreshGate-Stale-Count` for a summary. `X-FreshGate-Plugin: active` identifies the installed transport hook. Check the status and score headers to distinguish a completed verification from plugin presence alone.

### Build and load

1. Start FreshGate and set `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `LLM_MODEL` and `TAVILY_API_KEY` for its verification service. Set `FRESHGATE_API_KEY` to a shared secret and restart the service. Configure Bifrost's own chat provider as well.
2. Build the plugin on the target OS and architecture with cgo enabled. The module pins Bifrost core **v1.15.1** and specifies **Go 1.27.3**. The Bifrost host must support Go dynamic plugins and share the toolchain, build settings and common dependency versions. On Windows, build and run the Linux gateway and plugin together under WSL2, or use the proxy below.
3. From the repository root, build the shared object.

```bash
cd plugin
go test ./...
CGO_ENABLED=1 go build -buildmode=plugin -o freshgate.so .
```

4. Put the shared object where the Bifrost process can read it. Add the following entry to the gateway's `config.json`, merging it into the existing `plugins` array. Replace the path with the actual path **inside the gateway runtime**.

```json
{
  "plugins": [
    {
      "enabled": true,
      "name": "freshgate",
      "path": "/opt/bifrost/plugins/freshgate.so",
      "config": {
        "freshgate_url": "http://127.0.0.1:8000/api/plugin/verify",
        "timeout_sec": 8,
        "fail_open": true,
        "async_mode": false
      }
    }
  ]
}
```

5. Set `FRESHGATE_API_KEY` in the Bifrost process environment to the same secret. Restart Bifrost and send a non-streaming request to **Bifrost's port** using a model configured there. Add your Bifrost authentication header if the gateway requires one.

```bash
curl -i http://127.0.0.1:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"openai/gpt-4o-mini","messages":[{"role":"user","content":"State the current stable Python release."}],"stream":false}'
```

The plugin defaults to a four-second timeout, synchronous checks and fail-open delivery. Increase the timeout to accommodate your search and judging latency. Fail-open returns the original completion on a verifier transport error. With `fail_open: false`, a synchronous verifier transport failure produces a gateway error. Async mode starts verification in the background and returns the completion without an inline report. Streaming chunks, tool-only responses and additional choices are outside the native text check.

Plugin environment overrides are `FRESHGATE_URL` (the **full verification endpoint**), `FRESHGATE_API_KEY`, `FRESHGATE_TIMEOUT_SEC`, `FRESHGATE_FAIL_OPEN` and `FRESHGATE_ASYNC_MODE`. If FreshGate runs on another port or in another container, use its reachable service address. The manifest and hook implementation live in [plugin/](plugin/).

## OpenAI-compatible proxy

Point a chat client's base URL at `http://127.0.0.1:8000/v1` and set `BIFROST_UPSTREAM_URL` on FreshGate to your Bifrost address. FreshGate accepts `POST /v1/chat/completions` and the alias `POST /proxy/v1/chat/completions`. Use `X-API-Key` for the FreshGate integration key when configured. This path works with an ordinary Bifrost HTTP deployment. Choose a single integration path for a request to avoid checking the same completion twice.

| Proxy mode | What the client receives |
| --- | --- |
| `shadow` or async | The upstream completion immediately. Verification runs in the background. |
| `warn` | The upstream JSON plus verification headers. |
| `enforce` | The completion plus a `freshgate` result. With `FRESHGATE_BLOCK_STALE=true`, a contradicted claim or an average below `FRESHGATE_MIN_SCORE` can produce HTTP 422. |
| Streaming | Server-sent events as the provider emits them. FreshGate collects assistant text from the first choice and schedules a check when the stream ends. Look up the result at `/api/stream/verification/{completion_id}` with a session or admin credential. These results are process-local. |

## Configuration and deployment

| Concern | Settings and behavior |
| --- | --- |
| Accounts and history | `FRESHGATE_STORE=sqlite` is a single-user setup. Use `FRESHGATE_STORE=mongo` and a reachable `MONGODB_URI` for owner-scoped accounts, policies, history and monitoring in a shared deployment. |
| Session and credentials | Set a persistent `FRESHGATE_SESSION_SECRET` for deployed instances. It signs sessions and protects saved provider keys. Use separate values for the client `FRESHGATE_API_KEY` and operator `FRESHGATE_ADMIN_KEY`. |
| Cache and audit | SQLite paths are configurable through `FRESHGATE_CACHE_DB` and `FRESHGATE_AUDIT_DB`. Redis is optional. Keep these files on durable storage, including when feature storage uses MongoDB. Audit records include verified input text. |
| Monitoring and mail | Configure scheduler polling and concurrency through the Watchtower settings. For email delivery, configure Brevo, Resend, SendGrid or SMTP and a sender, then save the recipient and digest interval in Watchtower. Sending a digest reads existing results. |
| Deployment | The Compose stack runs FreshGate, Bifrost, MongoDB and Redis. Set a persistent session secret in `.env`, then run `docker compose up --build`. FreshGate listens on 8000 and Bifrost on 8080. Compose connects them through the proxy path. |

Generate a secret with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Keep the session secret with your backups so encrypted credentials remain readable. See the [environment example](.env.example), [Compose configuration](docker-compose.yml) and [deployment guide](docs/11_deployment_and_rollback_guide.md).

## API, CLI and validation

The running service exposes its interactive API reference at `/docs`. For direct text verification, send `POST /verify` with an `answer` field and optional `policy_id`. The dashboard supplies a session. The CLI can send provider credentials from its process environment. For the CLI, `FRESHGATE_URL` is the **server base URL**. Use `--url` to override it.

```bash
python -m pip install -e .
freshgate status --url http://127.0.0.1:8000
# Export OPENAI_API_KEY and TAVILY_API_KEY in this shell before verifying.
freshgate verify --file answer.txt --format markdown
freshgate batch --file answers.json --min-score 60 --fail-on-unverifiable
```

A batch file can be a JSON array of answer strings. The CLI returns exit code 1 for failed evaluation conditions and 2 for configuration, API or transport errors. Use `--warning-only` to report evaluation failures without failing a build.

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q -m "not live"
npm ci --prefix dashboard
npm run build --prefix dashboard
```

Run the native Go checks from `plugin/`. Offline tests exercise application behavior with fixtures. A live verification also exercises the configured model, search provider and network, so its verdict and latency can differ from the fixture results.

## Troubleshooting

| Symptom | First check |
| --- | --- |
| The page loads but cannot verify an answer | Open Profile and check the selected model, provider credentials and Tavily key. The plugin and proxy use separate server credentials. |
| The native plugin shows as active with no score | Send a completed, non-streaming text choice. Check async mode, fail-open status, endpoint address and verifier timeout. |
| Bifrost rejects the shared object | Match the host's Go compiler, platform and shared dependencies. Use the HTTP proxy if the host cannot load dynamic plugins. |
| A monitored claim still shows its baseline | Inspect the next check, last error and enabled state. Use Check now to request a recheck. The backend must stay running for scheduled checks. |
| The page is missing or the Vite API is unreachable | Build `dashboard/` for FastAPI, or point the Vite development proxy at the running backend port. Use `/health/live` for liveness and `/health/ready` for storage readiness. |

The verification pipeline is in [core/](core/), the FastAPI application in [server/](server/), and the React page in [dashboard/](dashboard/). More detail is available in the [Policy Studio guide](docs/policy-studio.md), [Watchtower guide](docs/watchtower.md), [Replay guide](docs/verification-replay.md) and [CLI guide](docs/developer-cli.md).

Released under the [MIT License](LICENSE).
