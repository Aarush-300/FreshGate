# FreshGate

FreshGate is a FastAPI gateway and dashboard that extracts time-sensitive claims from an answer, checks them against current web evidence, and reports a structured freshness result. It can also proxy OpenAI-compatible chat completions through Bifrost.

**Live demo:** [freshgate-0cid.onrender.com](https://freshgate-0cid.onrender.com/)

## Run locally

Requirements: Python 3.11 or newer.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn server.app:app --reload --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. For live verification, enter an OpenAI API key and a Tavily API key in the dashboard. The browser sends them only with that verification request, and FreshGate clears the fields when the request finishes. Keys are not saved in browser storage or server configuration. Use HTTPS when accessing a deployed instance.

For API clients, `POST /verify` accepts `X-OpenAI-API-Key` and `X-Tavily-API-Key` request headers. Self-hosters may optionally provide `OPENAI_API_KEY` and `TAVILY_API_KEY` as environment variables for clients that do not send per-request keys. Never commit real credentials or place them in frontend code.

## Deploy a free demo

The included [`render.yaml`](render.yaml) configures a Render free web service. Connect this GitHub repository to Render and create a Blueprint from the repository. The service uses the included Dockerfile, sets the required port and health check, and generates an admin key for the audit view. The app listens on Render's `PORT` value and keeps its cache on local SQLite by default.

Render's free web services sleep after 15 minutes without traffic and can take about a minute to wake. Their filesystem is temporary, so the local SQLite cache and audit history can disappear after restart, sleep, or redeploy. Treat the free deployment as a demo, not production storage. Provider calls can incur charges on the API accounts whose keys users enter. Review the provider pricing and apply usage limits before sharing a public instance. To keep data and availability for production, use paid compute with persistent/managed storage and configure an appropriate authentication and usage policy.

## Docker

```sh
docker compose up --build
```

Open `http://localhost:8000`. The Compose setup runs FreshGate with Redis and Bifrost for the optional OpenAI-compatible gateway proxy. The dashboard's direct live verification accepts user-supplied keys.

## Operations

- Health: `/health/live` and `/health/ready`
- Prometheus metrics: `/metrics`
- API reference: `/docs`
- Dashboard: `/`
- Admin audit access: set `FRESHGATE_ADMIN_KEY`; the audit screen sends it per request and does not save it.

Install test-only dependencies with `python -m pip install -r requirements-dev.txt`. Run offline checks with `python -m pytest -m "not live"`. Run the benchmark suite with `python bench/evaluate.py --suite all`.

## Project map

- `server/`: FastAPI routes, middleware, and app lifecycle
- `core/`: verification pipeline, provider clients, cache, audit, security, and metrics
- `dashboard/`: static browser UI served by FastAPI
- `bench/`: labelled datasets, runner, and reports
- `docs/`: architecture, security, operations, API, and deployment guides

## License

MIT. See [LICENSE](LICENSE).
