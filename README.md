# FreshGate

FreshGate is a FastAPI gateway that checks time-sensitive claims in AI responses against current evidence. It includes a React dashboard for verification, service health, benchmarks, audit access, policy management, Watchtower monitoring, and read-only Replay.

## Features

- Verify answer text with current web evidence through supported OpenAI-compatible providers.
- Proxy OpenAI-compatible chat completions through the FreshGate gateway.
- Manage freshness and source policies; monitor claims and review verification history.
- Inspect health, Prometheus metrics, benchmark data, and protected audit records.
- Run locally with SQLite or deploy with persistent SQLite storage.

Provider keys entered in the dashboard apply only to the current verification request and are not stored in browser storage. Scheduled Watchtower checks need server-side `OPENAI_API_KEY` and `TAVILY_API_KEY` values.

## Requirements

- Python 3.11 or newer
- Node.js 22 or newer and npm (needed to build the dashboard)
- Docker (optional, for container deployment or Docker Compose)

## Run locally

### Windows PowerShell

The launcher creates a virtual environment and builds the dashboard if needed, then starts the API. It uses port 8127 by default and tries another port if that port is busy.

```powershell
.\run.ps1
```

To install and run manually:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Push-Location dashboard
npm ci
npm run build
Pop-Location
.\.venv\Scripts\python.exe -m uvicorn server.app:app --reload --host 127.0.0.1 --port 8000
```

### macOS and Linux

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
cd dashboard && npm ci && npm run build && cd ..
python -m uvicorn server.app:app --reload --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. For dashboard development, start the API and run `npm run dev` inside `dashboard`. The Vite proxy targets `http://127.0.0.1:8000` by default; set `FRESHGATE_API_ORIGIN` to use another API address.

Copy `.env.example` to `.env` to configure local credentials or options. Keep `.env` private. For direct dashboard verification, choose a provider and enter its API key in the dashboard. See [dashboard usage](docs/dashboard.md) for provider behavior and [the API reference](docs/13_api_reference.md) for endpoints.

## Deploy to Render

The repository includes a Render Blueprint at [`render.yaml`](render.yaml). It builds the dashboard and API from the root [`Dockerfile`](Dockerfile), listens on Render's `PORT`, checks `/health/ready`, and mounts a 1 GB persistent disk at `/data` for the cache, audit, and feature databases.

1. Push this repository to GitHub.
2. In Render, create a new **Blueprint** and connect the GitHub repository.
3. Review the Blueprint resources and confirm creation. The configured `0.5c-512mb` web compute plan and persistent disk are paid resources; a free web service cannot use this persistent disk.
4. Wait for the first deploy to finish, then open the `onrender.com` URL shown in the Render dashboard.
5. Optionally configure `OPENAI_API_KEY` and `TAVILY_API_KEY` in the Render service environment for scheduled Watchtower checks. The Blueprint generates `FRESHGATE_ADMIN_KEY` for protected audit access.

The persistent disk keeps SQLite data across service restarts and deploys, but it is attached to one service instance. Back up important data. Review [Render's Blueprint reference](https://render.com/docs/blueprint-spec) and [compute plans](https://render.com/docs/compute-plans) before provisioning because plan availability and pricing can change.

For direct verification, users can supply provider credentials with each request. If you configure server-side provider keys, protect the public service with an appropriate access and usage policy. Never put secrets in the repository or frontend source.

## Docker Compose

```sh
docker compose up --build
```

The Compose file also starts Redis and Bifrost for the optional gateway proxy. Local data is stored in Docker volumes. Copy `.env.example` to `.env` and set credentials as needed.

## 1. Bifrost setup and usage

FreshGate integrates with Bifrost in two ways:

- **Native plugin:** runs inside a dynamically linked Linux or macOS Bifrost build. Go shared-object plugins are not supported by Go on Windows. Windows users can use WSL2 with Ubuntu, or use the proxy option below.
- **FreshGate proxy:** cross-platform option that routes OpenAI-compatible chat requests through FreshGate to Bifrost. It does not load the native plugin.

### Native plugin prerequisites

- Linux or macOS (on Windows, install Ubuntu with `wsl --install -d Ubuntu`; Docker Desktop's `docker-desktop` distro is not a development shell).
- Go **1.27.3**, matching the Bifrost source checkout used for the gateway binary.
- `make`, a C compiler and C development headers (Linux: `build-essential libc6-dev`), plus Node.js/npm for Bifrost's UI build.
- A dynamic Bifrost source checkout. The prebuilt `maximhq/bifrost` Docker image is not the dynamic binary built below.
- A running FreshGate API, Bifrost provider credentials, and a Bifrost virtual key permitted to use that provider.

### Build Bifrost and the plugin from the same source

The Bifrost source used during FreshGate integration is intentionally not tracked in this repository (the `vendor/` directory is ignored). Reproduce the tested build by cloning the matching upstream commit. Its Go code is split across modules; use a local Go workspace so source modules stay aligned. In Linux/macOS terminal (or Ubuntu WSL), from the FreshGate repository root:

```sh
git clone https://github.com/maximhq/bifrost.git vendor/bifrost
git -C vendor/bifrost checkout f7e77e57b50745999190a3f9b6753244a222c240
cd vendor/bifrost
go version                         # must be go1.27.3
test -f go.work || go work init ./cli ./core ./framework ./transports
go work use ./plugins/logging
make build DYNAMIC=1 LOCAL=1
```

If the build reports missing local packages from another `plugins/<name>` module, add that module with `go work use ./plugins/<name>` and rebuild. On WSL, use Linux Node/npm (`which npm` should point into `/usr/bin` or another Linux path), and reinstall `ui/node_modules` with Linux `npm ci` if it was previously installed by Windows npm. The build needs CGO headers; on Ubuntu run `sudo apt install -y make build-essential libc6-dev`.

Build the plugin using the same Go workspace and build flags as Bifrost (from the Bifrost checkout):

```sh
cd ../../plugin
GOWORK="$(realpath ../vendor/bifrost/go.work)" CGO_ENABLED=1 \
  go build -a -trimpath -buildmode=plugin -o freshgate.so freshgate_plugin.go
```

The plugin module files in `plugin/` pin the local Bifrost core module. Keep the Bifrost binary and `.so` built with the same Go toolchain, source checkout, architecture, CGO environment, and compatible build flags. Go plugins are highly sensitive to mismatches.

### Run FreshGate and configure Bifrost

Run FreshGate separately. For Docker Compose, from the repository root:

```sh
docker compose up -d --build freshgate
```

For local Python, install project dependencies and run the API; the Windows `run.ps1` launcher defaults to port `8127`. The plugin must be able to reach `/verify` from the Bifrost process. On WSL accessing a Windows-hosted FreshGate, obtain the host address with `ip route | awk '/default/ {print $3}'` and test `curl http://<host-ip>:8000/health/ready`. Do not assume `127.0.0.1` crosses the Windows/WSL boundary.

Create an app directory and `config.json`. Use the reachable FreshGate URL and absolute path to the plugin file:

```json
{
  "$schema": "https://www.getbifrost.ai/schema",
  "plugins": [{
    "enabled": true,
    "name": "freshgate",
    "path": "/absolute/path/to/plugin/freshgate.so",
    "config": {
      "freshgate_url": "http://127.0.0.1:8000/verify",
      "timeout_sec": 8,
      "fail_open": true,
      "async_mode": false
    }
  }]
}
```

On first launch, configure a private `setup_token` in this config (or `BIFROST_SETUP_TOKEN` in the process environment), start Bifrost, visit its dashboard, and create the admin account. Configure the provider in Bifrost and issue a virtual key whose permissions allow that provider. Keep setup tokens, provider keys, and virtual keys out of source control and screenshots.

Launch the dynamic binary, pointing it at the app directory (not with a `-config` flag):

```sh
cd vendor/bifrost
./tmp/bifrost-http -app-dir /absolute/path/to/bifrost-data -host 0.0.0.0 -port 8080
```

Check startup logs for `plugin status: freshgate - active`. Send a non-streaming chat completion directly to `http://localhost:8080/v1/chat/completions` with model/provider allowed by the virtual key and header `x-bf-vk`. Successful FreshGate execution appears under `extra_fields.raw_response.freshgate`; the transport hook adds `X-FreshGate-Plugin: active`. Verify the FreshGate `/verify` request in service logs too. A loaded plugin alone does not prove verification succeeded.

### Windows usage options

The native `.so` plugin cannot load in a Windows Bifrost binary. To exercise the native plugin on Windows hardware, build and run both dynamic Bifrost and the plugin in Ubuntu WSL2 using the steps above; run FreshGate in Docker or make its API reachable from WSL. Alternatively, point clients at the cross-platform FreshGate proxy (`http://127.0.0.1:8127/proxy/v1` for the local launcher, or port `8000` for Compose); this tests the proxy integration, not the native plugin.

## 2. Tavily setup and usage

FreshGate uses Tavily for live web evidence searches. Create a Tavily API key in your Tavily account and set it as `TAVILY_API_KEY` in the root `.env` for Docker Compose, or in the environment of the FreshGate process. Restart/recreate FreshGate after changing environment variables. Do not commit `.env` or expose the key in screenshots.

The verification endpoint is `POST /verify`. Example (PowerShell):

```powershell
$body = @{ answer = "The current CEO of Microsoft is Satya Nadella." } | ConvertTo-Json
Invoke-RestMethod -Uri "https://freshgate-0cid.onrender.com/verify" `
  -Method Post -ContentType "application/json" -Body $body
```

The deployed service URL is [https://freshgate-0cid.onrender.com/](https://freshgate-0cid.onrender.com/). Check `GET https://freshgate-0cid.onrender.com/health/ready` before a demo. Live verification also needs an OpenAI-compatible extraction/judging provider configured through `OPENAI_API_KEY`, `OPENAI_BASE_URL`, and `LLM_MODEL`; Tavily alone supplies search evidence, not the LLM extraction step. The hosted deployment may sleep, change availability, or require credentials; for judging, provide a local Docker Compose path and your own credentials rather than depending only on the public URL.

## Checks

Install the development dependencies and run the Python suite:

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Build the dashboard:

```sh
cd dashboard
npm ci
npm run build
```

Operational endpoints are `/health/live`, `/health/ready`, `/metrics`, and `/docs`.

## Project layout

- `server/`: FastAPI application and routes
- `core/`: verification pipeline, providers, cache, audit, security, and metrics
- `dashboard/`: React, TypeScript, and Vite dashboard
- `bench/`: benchmark datasets and runners
- `docs/`: architecture, API, operations, and feature guides
- `plugin/`: Bifrost plugin source

## License

MIT. See [LICENSE](LICENSE).
