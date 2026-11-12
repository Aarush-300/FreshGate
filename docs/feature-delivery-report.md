# FreshGate feature delivery report

## Delivered workflow

Policy Studio creates validated policies and applies them to `/verify`. Verified claim snapshots share the feature store with Watchtower. Watchtower jobs use persistent leases, bounded concurrency, retry state, and alert transitions. Replay compares snapshots and joins matching Watchtower alert events. The Developer CLI uses FreshGate's public HTTP API.

The deterministic end-to-end test is `tests/test_feature_integration.py`. A runnable server demo is `scripts/demo_feature_workflow.py`; it creates a demo policy and monitoring record and requires a running server with provider credentials configured for scheduled checks.

## Storage and migration

`core/persistence.py` manages SQLite schema version 2 in `FRESHGATE_FEATURE_DB` (default `freshgate_features.db`). It contains `policies`, `monitored_claims`, `verification_snapshots`, and `watchtower_alerts`. Version 2 adds monitoring retry state, last successful checks, expiring job leases, and deduplicated unresolved alerts. Verification snapshots reject SQL `UPDATE` and `DELETE` operations. SQLite WAL with `synchronous=NORMAL` lowers write latency; sudden host failure may lose the latest committed transaction.

SQLite is suitable for local and single-host use. Mount the feature DB on durable storage and back it up in production. The free Render disk is ephemeral; multi-host scheduling and managed PostgreSQL are not implemented. Watchtower uses only server-configured provider credentials; dashboard request keys are never persisted.

## API additions

- Policies: `/api/policies` and `/api/policies/{policy_id}` support CRUD, validation, duplication, categories, and policy simulation.
- Watchtower: `/api/watchtower/monitor`, `/api/watchtower/claims`, `/api/watchtower/claims/{claim_id}`, and `/api/watchtower/alerts` support registration, status, checks, and acknowledgement.
- History/Replay: `/api/history/claims/{fingerprint}`, `/api/history/verifications/{verification_id}`, `/api/replay/{fingerprint}`, `/api/replay/{fingerprint}/compare`, and `/api/replay/{fingerprint}/timeline` are read-only history and comparison endpoints.

Full request and response details are in [the API reference](13_api_reference.md), [Policy Studio guide](policy-studio.md), [Watchtower guide](watchtower.md), and [Replay guide](verification-replay.md).

## CLI and demonstration

```bash
python -m pip install -e .
freshgate verify --file answer.txt --min-score 70 --format json
freshgate batch --file answers.json --min-score 70 --format markdown
freshgate status
freshgate benchmark --suite unit
python bench/evaluate_features.py
python scripts/demo_feature_workflow.py
```

CLI setup, output, CI conditions, environment variables, and exit codes are in [the CLI guide](developer-cli.md). The feature benchmark uses synthetic data and no providers. The original labelled dataset has not been rewritten.

## Quality and performance results

- Offline suite: **165 passed, 1 deselected** (`-m "not live"`), with one Starlette/httpx deprecation warning.
- Deterministic feature scenarios: **10/10 passed**. A synthetic Watchtower batch ran three jobs concurrently at its configured limit of three; the last observed batch duration was 116.85 ms.
- Existing 50-item unit benchmark: 100% precision, 90% recall, 94.7% F1, 0% false positives, 10% false negatives, 87.3% source-authority accuracy, 21.82 ms cold p50, 37.26 ms cold p95, 12.51 ms warm mean, and 100% warm cache hits. These local mock-mode figures include immutable snapshot writes and are not production guarantees.
- Existing `/verify`, proxy fail-open, streaming, health, metrics, security, database migration, and dashboard checks ran in the offline suite.

Run `python bench/evaluate.py --suite unit` for normal and warm verification latency/cache measurements. Run `python bench/evaluate_features.py` for policy decisions, Watchtower transitions/concurrency, Replay comparison, and CLI report checks. Provider and HTTP network latency depends on the deployment and was not measured in these synthetic feature microbenchmarks.

## Known environment limits

- Live-provider tests were deselected; no external provider calls were made for QA.
- The native Bifrost Go plugin was not built because Go is not installed. Existing proxy fallback and fail-open tests passed.
- A Docker deployment smoke test was not run because Docker is not installed in this environment.
- Browser screenshot automation was unavailable. Dashboard navigation, API links, safe DOM rendering, and responsive CSS are checked statically, and JavaScript syntax passed `node --check`.
- Editable package installation was not tested because the environment lacks the declared `setuptools>=68` build dependency and network installation is unavailable. `python -m cli --help` works.

## New files

- `bench/evaluate_features.py`
- `cli/__init__.py`, `cli/__main__.py`, `cli/client.py`, `cli/main.py`, `cli/report.py`
- `core/claim_identity.py`, `core/history.py`, `core/persistence.py`, `core/policies.py`, `core/policy_models.py`, `core/replay.py`, `core/watchtower.py`
- `docs/dashboard.md`, `docs/developer-cli.md`, `docs/feature-architecture.md`, `docs/feature-delivery-report.md`, `docs/feature-implementation-audit.md`, `docs/policy-studio.md`, `docs/verification-replay.md`, `docs/watchtower.md`
- `pyproject.toml`
- `scripts/demo_feature_workflow.py`
- `server/routes/__init__.py`, `server/routes/policies.py`, `server/routes/replay.py`, `server/routes/watchtower.py`
- `tests/test_cli.py`, `tests/test_dashboard_integration.py`, `tests/test_feature_benchmark.py`, `tests/test_feature_integration.py`, `tests/test_feature_persistence.py`, `tests/test_policies.py`, `tests/test_replay.py`, `tests/test_watchtower.py`

## Modified files

- `.env.example`, `README.md`, `SPEC.md`, `bench/evaluate.py`
- `core/cache.py`, `core/categories.py`, `core/config.py`, `core/models.py`, `core/pipeline.py`, `core/verifier.py`
- `dashboard/app.js`, `dashboard/index.html`, `dashboard/style.css`, `docker-compose.yml`
- `docs/02_architecture_document.md`, `docs/10_production_operations_guide.md`, `docs/11_deployment_and_rollback_guide.md`, `docs/13_api_reference.md`
- `server/app.py`
- `tests/conftest.py`, `tests/test_benchmark_phase5.py`, `tests/test_regression_issue.py`, `tests/test_scorer_cache_pipeline.py`, `tests/test_scoring_cache_phase3.py`, `tests/test_server_api.py`, `tests/test_v2_resilience.py`
