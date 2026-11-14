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
