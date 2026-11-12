# Feature Implementation Audit

**Scope:** Phase 0 audit for Policy Studio, Watchtower, Verification Replay, and the Developer CLI.

## Existing architecture

- `server/app.py` owns the FastAPI application, lifespan, direct verification, OpenAI-compatible proxy routes, streaming verification retrieval, health checks, metrics, benchmark results, audit/admin endpoints, and static dashboard serving. Feature routes can be registered from separate routers without creating another application.
- `core/pipeline.py` coordinates claim extraction, volatility triage, cache lookup, verification, scoring, and the existing `VerificationResult` response. `server/app.py` also creates a request-scoped pipeline for `/verify` so per-request provider keys are used.
- `core/models.py` contains Pydantic `Claim`, `Source`, `Summary`, `ExtractionResult`, and `VerificationResult` schemas. Claims already include entities, category, verdict, score, source/evidence data, cache state, and a verification timestamp. There is no stable public fingerprint or verification ID yet.
- `core/extractor.py`, `core/triage.py`, `core/verifier.py`, `core/scorer.py`, `core/sources.py`, `core/categories.py`, and `core/cache.py` provide the extraction, volatility, evidence, scoring, source, category, and cache services to reuse.
- `core/cache.py` has a `ClaimCacheBase` with SQLite and Redis implementations. SQLite enables WAL and has schema initialization/upgrade logic. Redis is best-effort. This is an expiring cache and must not be used as immutable history.
- `core/audit.py` stores whole-request audit rows in SQLite with WAL. It serializes claims as JSON and exposes recent audits. It is not a per-claim history API; its `INSERT OR REPLACE` behavior also does not meet immutable-snapshot semantics. Although the module mentions a PostgreSQL DSN, `get_audit_store()` warns and falls back to SQLite; PostgreSQL is not implemented.
- There is no shared persistence abstraction, migration framework, `migrations/` directory, or PostgreSQL database dependency. `docker-compose.yml` currently includes Bifrost, FreshGate, and Redis with a volume for SQLite files; it does not include PostgreSQL.
- The dashboard uses static HTML, CSS, and vanilla JavaScript from `dashboard/`. Navigation and panels are defined in `dashboard/index.html`; interaction, API calls, and safe DOM rendering live in `dashboard/app.js`. It currently has Verify, System, Benchmarks, and Audit Log sections. New tabs should use these conventions. The removed claim-pattern offline preview must not be restored as a feature; Policy Studio's proposed policy simulation is a separate operation.
- The Bifrost integration consists of the proxy routes in `server/app.py` and a Go plugin in `plugin/`. `docs/bifrost-notes.md` records a usable `PostLLMHook`; `plugin/README.md` documents the Go build constraints and fallback proxy. Preserve both paths.
- `bench/` contains existing evaluation code, datasets, and reports. New CLI benchmark behavior should call the existing runner rather than create a parallel benchmark engine.

## Existing test baseline

Command attempted: `python -m pytest -m "not live" -q`.

- The system Python 3.14 environment has no `pytest` installed.
- The repository `.venv` has Python 3.14.7 and pytest. Running its offline suite reached **100 passed, 1 failed, 10 errors, 1 deselected**.
- Ten fixture errors were caused by Windows `PermissionError: [WinError 5]` while pytest created/cleaned its temporary directory under the user Temp location. A rerun with a workspace basetemp still hit a Temp cleanup permission error.
- `tests/test_server_api.py::test_post_verify_endpoint_contract` failed because the route creates a request-scoped pipeline while the test patches the process-wide pipeline; the test therefore attempted real OpenAI/Tavily network calls and returned `unverifiable` instead of the mocked contradiction. Treat the current suite as an unreliable baseline until the test isolation is repaired in a later implementation phase.
- No live provider tests were run (`-m "not live"`).

## Data flow and compatibility constraints

1. Direct `/verify` validates optional request-scoped OpenAI/Tavily keys, constructs a scoped pipeline, and returns the existing `VerificationResult` contract.
2. Proxy routes forward chat completions to Bifrost and preserve/enrich its response; streaming verification is recorded separately and is retrievable by completion ID.
3. Cache entries are keyed by category, jurisdiction, entity, and normalized text and expire by TTL. They are reusable for policy-aware cache decisions but are not a history source.
4. The audit store captures whole-request snapshots in a different SQLite database. It is useful for audit display but has no per-claim lookup, pagination contract, or mutation-proof append semantics.
5. Preserve current `/verify`, proxy, streaming, health, metrics, admin, and dashboard contracts. New API routes should be added under `/api/...` using routers and should not change existing response fields.
6. Do not store provider credentials in policy, monitoring, history, replay, or CLI report records.

## Recommended implementation shape

- Add a stable claim identity helper in `core/claim_identity.py`. Preserve numeric, date, version, currency, and entity details. Include category and optional jurisdiction/entity context in the fingerprint input. Keep the existing cache key format independent for backward compatibility.
- Add shared durable feature persistence as a focused SQLite store with append-only verification snapshots and explicit schema versioning/migrations. Keep it separate from expiring cache entries. Reuse serialized `Claim` and `Source` data where practical. PostgreSQL support is not currently practical without a new driver, connection strategy, deployment configuration, and migration work; deliver SQLite first and document the production durability limit unless a later phase establishes PostgreSQL.
- Use this store as the shared source for monitored claims, alerts, and replay history. Avoid adding separate history databases for Watchtower and Replay. Keep the existing request audit store operational.
- Implement policies as validated Pydantic configuration and deterministic evaluation services. Integrate into pipeline behavior only behind an optional selected policy so default behavior and response schema stay unchanged. Ensure policy source restrictions affect eligibility without changing evidence-based verdict semantics.
- Implement Watchtower as a lifecycle-managed worker that claims due jobs in persistent storage, limits concurrency, records retryable failures distinctly from factual verdict changes, and writes immutable snapshots through the shared history service.
- Implement Replay as read-only queries/comparisons over those snapshots.
- Implement the CLI as an HTTP client package over public FreshGate endpoints; avoid importing internal verification services.
- Add feature APIs as FastAPI routers and UI panels/modules following the current static dashboard structure. Do not reintroduce the removed offline extraction-preview button/endpoint. The plan's policy-preview API is a policy simulation and must be clearly distinguished from that removed feature.

## Proposed files by phase

| Phase | New files | Existing files to modify |
|---|---|---|
| 0 | `docs/feature-implementation-audit.md` | None |
| 1 | `core/claim_identity.py`, `core/persistence.py` (or focused `core/feature_store.py`), schema migration/version module, focused tests | `core/models.py` only if additive IDs/types are needed; config and `.env.example` only for configurable DB path; `tests/` |
| 2 | `core/policy_models.py`, `core/policies.py`, `server/routes/policies.py`, policy tests, `docs/policy-studio.md` | `server/app.py`, `core/pipeline.py`, `dashboard/index.html`, `dashboard/app.js`, `dashboard/style.css`, API docs, `SPEC.md` |
| 3 | `core/watchtower.py`, `server/routes/watchtower.py`, Watchtower tests, `docs/watchtower.md` | persistence/store, app lifecycle in `server/app.py`, config/`.env.example`, dashboard files, API/deployment docs |
| 4 | `core/history.py` if not contained in shared persistence, `core/replay.py`, history/replay routes or router, tests, `docs/verification-replay.md` | snapshot writer integration in `core/pipeline.py`, persistence/store, `server/app.py`, dashboard files, API docs |
| 5 | `cli/` package, CLI tests, `docs/developer-cli.md` | packaging metadata (none currently identified), `requirements.txt` only if needed, README |
| 6–7 | integration tests, `docs/feature-architecture.md`, demonstration script | dashboard and docs; benchmark code/datasets only where meaningful and measured |

## Dependency and deployment notes

- Runtime dependencies currently include FastAPI, Uvicorn, Pydantic, httpx, python-dotenv, Redis client, and Prometheus client. Test dependencies are in `requirements-dev.txt`. No SQLAlchemy, Alembic, PostgreSQL driver, APScheduler, or Typer dependency is currently declared.
- SQLite requires no added runtime package and is the smallest compatible first persistence target. Use explicit transactions, foreign keys, WAL, busy timeout, schema versioning, and indexes; design job claiming so multiple processes cannot run the same due check.
- A durable multi-instance production scheduler and PostgreSQL support need deployment-specific validation. The current Render free service uses ephemeral local storage; its monitoring history cannot be promised durable there.
- The current dashboard has no module loader or component framework. Plain script files and safe DOM creation fit the existing architecture.
- Keep API credentials request-scoped as they are now. A background Watchtower job needs an intentional credential strategy; do not persist user keys. This needs to be resolved before live scheduled checks are enabled (for example, a server-configured provider credential for self-hosted deployments).

## Compatibility risks and decisions for later phases

- Policies need an unambiguous request selection mechanism while preserving existing `/verify` clients. Add an optional policy identifier without changing the default path or response fields.
- Existing claims have a category string but no jurisdiction field. Fingerprints can accept optional context without forcing extraction changes; jurisdiction for policies should initially be explicit policy metadata.
- Existing audits are whole-request rows, not immutable per-claim snapshots. Leave them intact and add feature history as a separate append-only table/service.
- Provider keys entered in the dashboard are cleared after each request and are not retained. Watchtower cannot silently reuse them. Scheduled reverification must require configured server-side provider credentials or another explicit service credential source.
- Background reverification can add provider cost and must have bounded registration, frequency, concurrency, and retry limits.
- The master plan's policy-preview feature is not the removed offline extractor preview. Keep those product concepts separate.
- The test suite currently has one network-leaking mocked-route test and Windows temporary-directory permission failures. Fix test isolation before using full-suite results to claim feature acceptance.

## Phase 0 status

Audit and architecture recommendation were completed before implementation. The full baseline suite did not pass in this environment for the reasons above. No application behavior was changed during Phase 0.

## Phase 1 implementation record

- Added `core/claim_identity.py` with NFKC, case-folded, whitespace-normalized text identities and SHA-256 fingerprints. Category, sorted entity context, and jurisdiction are included; punctuation and factual values remain intact.
- Added `core/persistence.py` with a versioned SQLite schema (`PRAGMA user_version=1`) for policies, monitored claims, verification snapshots, and Watchtower alerts. It uses foreign keys, WAL, a busy timeout, parameterized SQL, JSON validation, and explicit transaction/connection cleanup.
- Verification snapshots are append-only: SQLite triggers reject direct `UPDATE` and `DELETE`. Each snapshot has a unique verification ID and indexed fingerprint/timestamp lookup.
- Added `FRESHGATE_FEATURE_DB` configuration. Local default: `freshgate_features.db`; Compose path: `/data/freshgate_features.db` on the existing persistent data volume.
- Added `tests/test_feature_persistence.py`; its **13 focused persistence and fingerprint tests pass** on the repository virtual environment.
- The broader offline selection passes **113 tests**, with **12 deselected**: 10 tests that require pytest's denied temp-directory operations, the known network-leaking route test, and one live-provider test. Those baseline blockers are unchanged.
- PostgreSQL remains unsupported. No new runtime dependency was added. No existing API or verification pipeline behavior was changed in this phase.

## Phase 2 implementation record

- Added validated policy schemas and deterministic policy services for categories, DNS domains, source authority/count, evidence age, freshness thresholds, verification mode, jurisdiction, and cache behavior.
- Added policy CRUD, validation, simulation, category listing, and duplication endpoints under `/api/policies`.
- Integrated optional `policy_id` selection into direct `/verify`. Enforced policies filter evidence and validate cache eligibility; shadow and warn modes report policy outcomes without filtering evidence. No-policy requests keep the existing pipeline behavior.
- Added a Policy Studio dashboard tab with policy editing, validation, sample simulation, and policy selection for live verification. Its simulation is distinct from the removed offline claim extraction preview.
- Added API, operator, README, and specification documentation for policy behavior and storage limits.
- Focused command: `python -m pytest tests/test_policies.py tests/test_feature_persistence.py tests/test_server_api.py::test_post_verify_endpoint_contract -q -p no:cacheprovider` — **26 passed**. Coverage includes create/update/delete, invalid settings, source allow/block rules, category settings, preview, policy application, default behavior, concurrent reads, and the formerly network-leaking `/verify` contract. `node --check dashboard/app.js`, Python compilation, and `git diff --check` also passed. The known temp-directory limitations remain excluded from this focused run.

Phase 2 was approved; Phase 3 implementation follows.

## Phase 3 implementation record

- Added an idempotent schema v2 migration for retry state, last successful check, score, error details, SQLite job leases, and unresolved alert transition deduplication. Due jobs are claimed transactionally; expired leases are recoverable after restart.
- Added a lifecycle-managed Watchtower service that uses the existing verification pipeline and selected policy, bounds concurrency and retries, records snapshots, detects meaningful changes, and classifies provider failures separately from factual changes.
- Added the Watchtower API, dashboard tab, “Monitor this claim” action on verified results, check/enable/pause/remove controls, evidence links, and alert acknowledgement.
- Added bounded `FRESHGATE_WATCHTOWER_*` settings and operator/API documentation. Scheduled jobs use only server-configured provider credentials; request-scoped keys are not retained.
- Focused command: `python -m pytest tests/test_watchtower.py tests/test_feature_persistence.py tests/test_policies.py tests/test_server_api.py::test_post_verify_endpoint_contract -q -p no:cacheprovider` — **32 passed**. Existing API tests passed (**4 passed**) and config/security lifecycle tests passed (**22 passed, 1 upstream deprecation warning**). Coverage includes confirmed-to-contradicted alert creation, policy use, provider rate-limit handling, repeated inconclusive evidence, manual check/pause/resume, acknowledgement, duplicate alert prevention, cross-store lease contention, restart recovery, and v1-to-v2 migration. Python compilation, `node --check dashboard/app.js`, and `git diff --check` passed.

Phase 3 implementation is ready for review. Proceed to Phase 4 only after approval, as required by the master plan.

## Phase 4 implementation record

- Added `core/history.py`; the normal verification pipeline now persists an immutable snapshot for each checked volatile claim, attaches a claim fingerprint and verification ID to the response, and records current score dimensions, sources, policy, cache status, correction, and duration. Watchtower rechecks use the same snapshot writer.
- Added `core/replay.py` and read-only history/replay routes for fingerprint and verification ID lookup, pagination, side-by-side comparison, and chronological timelines with Watchtower alert events.
- Added the Replay dashboard tab and links from verified claims, monitored claims, and alerts. Comparison shows verdict, score, source changes, authority/recency/agreement dimensions, correction, and policy/revision changes.
- Updated README, SPEC, API, architecture, deployment, operations, and dashboard documentation. The feature database is described as demo SQLite storage unless deployed on durable storage.
- Focused command: `python -m pytest tests/test_replay.py tests/test_watchtower.py tests/test_feature_persistence.py tests/test_policies.py tests/test_server_api.py -q -p no:cacheprovider` — **37 passed**. Python compilation, `node --check dashboard/app.js`, and `git diff --check` passed.

Phase 4 implementation is ready for review. Proceed to Phase 5 only after approval, as required by the master plan.

## Phase 5 implementation record

- Added an installable `freshgate` command and `python -m cli` entry point. The CLI talks to the existing HTTP API and does not import the verification pipeline.
- Added `verify` for text or UTF-8 files, `batch` for JSON input, `status` for readiness, and `benchmark` delegating to the existing `bench/evaluate.py` runner.
- Added request timeout and bounded retries, transient server/transport error handling, clear connection/configuration/input errors, optional request-scoped provider-key headers, and provider-key redaction from returned reports and error details.
- Added text, JSON, and Markdown output for verification and batch reports. CI conditions support minimum freshness score, selected-policy rejection, optional failure on unverifiable claims, and warning-only mode. Unverifiable claims do not independently fail a minimum-score condition; a verifiable claim missing its score does.
- Added installation, environment configuration, command usage, exit codes, CI, batch, and benchmark documentation in `docs/developer-cli.md` and updated the README.
- Focused regression command: `python -m pytest tests/test_cli.py tests/test_replay.py tests/test_watchtower.py tests/test_feature_persistence.py tests/test_policies.py tests/test_server_api.py -q -p no:cacheprovider` — **52 passed**. Python compilation, dashboard JavaScript syntax, and `git diff --check` passed. `python -m cli --help` was also checked.
- Editable package installation was not exercised in this environment; the declared `pyproject.toml` build dependency (`setuptools>=68`) is not installed in the current venv and network installation is unavailable. Direct module invocation works.

Phase 5 implementation is ready for review. Proceed to Phase 6 only after approval, as required by the master plan.

## Phase 6 implementation record

- Integrated Policy Studio, Watchtower, and Replay into the existing dashboard navigation and shared dark, responsive layout. Feature pages retain safe text-node rendering, confirmation prompts for destructive actions, per-panel loading/error/empty states, and no browser storage for credentials.
- Fixed two user-facing edge cases: a Watchtower load failure now updates both the claims and alerts status regions, and a valid Replay fingerprint with no history now displays a friendly empty state rather than an API error.
- Added `tests/test_dashboard_integration.py` checks for navigation/section consistency, referenced control IDs, feature API flows, Watchtower-to-Replay linking, confirmation prompts, safe rendering, credential-storage boundaries, responsive breakpoints, and reduced-motion support.
- Expanded `docs/dashboard.md` with empty/error state behavior and the focused dashboard test command.
- Focused feature and dashboard regression command: `python -m pytest tests/test_dashboard_integration.py tests/test_cli.py tests/test_replay.py tests/test_watchtower.py tests/test_feature_persistence.py tests/test_policies.py tests/test_server_api.py -q -p no:cacheprovider` — **56 passed**. Python compilation, `node --check dashboard/app.js`, and `git diff --check` passed.

Phase 6 implementation is ready for review. Proceed to Phase 7 only after approval, as required by the master plan.

## Phase 7 quality assurance record

- Added `tests/test_feature_integration.py`, a deterministic end-to-end API workflow covering policy creation, policy-backed `/verify`, Watchtower registration and changed-verdict alert creation, Replay comparison/timeline, and CLI verification through the public API.
- Added `bench/evaluate_features.py` with ten deterministic policy, Watchtower, Replay, and CLI scenarios. Its last run passed **10/10**, including a three-job Watchtower batch that observed the configured concurrency cap (**3/3**) and completed in **116.85 ms** on this host. These synthetic timings exclude provider and public-network latency.
- Updated `bench/evaluate.py` so deterministic runs persist history into their own short-lived feature database and clean both benchmark databases after completion; benchmark runs no longer append synthetic claims to the user's configured feature store. Existing labelled results/datasets were not rewritten.
- Closed SQLite cache connections deterministically. Added workspace-local temporary database fixtures and per-test global rate-limiter reset so the Windows offline suite does not depend on inaccessible system temp paths or shared request state.
- Fixed `test_post_verify_endpoint_contract` to mock the request-scoped pipeline built by `/verify`, preventing accidental real provider calls while checking the existing response contract.
- Set feature-store SQLite connections to WAL with `synchronous=NORMAL`. This keeps transaction consistency across process crashes with lower snapshot-write latency; a host/power failure may still lose the latest committed transaction. Production deployments need durable storage and backups.
- Updated the old unit benchmark's warm-latency test budget from 10 ms to 20 ms because the timed operation now includes mandatory immutable history writes. The latest 50-item deterministic benchmark measured 100% precision, 90% recall, 94.7% F1, 0% false positives, 10% false negatives, 87.3% source-authority accuracy, 21.82 ms cold p50, 37.26 ms cold p95, 12.51 ms warm mean, and 100% warm cache hits. These host-local figures are documented without altering the labelled dataset.
- Full offline suite: `python -m pytest -m "not live" -q -p no:cacheprovider` — **165 passed, 1 deselected**, with one Starlette/httpx deprecation warning. Python compilation, dashboard JavaScript syntax, `git diff --check`, the feature benchmark, and the end-to-end workflow passed.
- Existing proxy/fail-open, streaming, health, metrics, API, security, migration, and feature tests all ran in that offline suite. The native Bifrost Go plugin could not be compiled because Go is not installed here. Live-provider tests and browser screenshot automation were not run; the environment has neither provider-enabled credentials for a live benchmark nor a browser automation tool.

Phase 7 implementation and available offline QA are complete. Deployment-specific, live-provider, native Go plugin build, and visual browser checks remain environment-dependent limitations.
