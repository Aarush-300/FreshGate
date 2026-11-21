# Implementation and regression coverage

Use this map to locate the code and tests for a user-visible contract. Record test outcomes for the specific revision being released.

| Contract | Implementation | Regression coverage |
| --- | --- | --- |
| Extraction and volatile-category selection | [Extractor](../core/extractor.py), [triage](../core/triage.py) | [Extractor tests](../tests/test_extractor_triage.py), [outdated-claim regressions](../tests/test_regression_issue.py) |
| Evidence and scoring | [Verifier](../core/verifier.py), [scorer](../core/scorer.py) | [Engine tests](../tests/test_verification_engine.py), [score and cache tests](../tests/test_scoring_cache_phase3.py) |
| Owner-scoped records | [MongoDB store](../core/mongo_store.py), [pipeline factory](../server/pipeline_factory.py) | [Mongo isolation](../tests/test_mongo_store_isolation.py), [local backend regressions](../tests/test_backend_fixes_phase2.py) |
| Policy evaluation | [Policy logic](../core/policies.py) | [Policy tests](../tests/test_policies.py) |
| Monitoring and alerts | [Scheduler](../core/watchtower.py) | [Watchtower tests](../tests/test_watchtower.py) |
| History comparison and exports | [Replay](../core/replay.py), [export routes](../server/routes/export.py) | [Replay tests](../tests/test_replay.py), [feature and export tests](../tests/test_strategic_features.py) |
| Gateway response preservation | [Proxy](../server/app.py), [native plugin](../plugin/freshgate_plugin.go) | [Streaming tests](../tests/test_streaming_gateway.py), [native hook test](../plugin/freshgate_plugin_test.go) |
| Dashboard and CLI | [React page](../dashboard/src/App.tsx), [CLI](../cli/main.py) | [Dashboard integration](../tests/test_dashboard_integration.py), [CLI tests](../tests/test_cli.py) |

## Run the checks

From the repository root, install the development requirements and dashboard dependencies. Run the benchmark-bearing backend suite separately from heavy builds when comparing timing results.

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q -m "not live"
npm ci --prefix dashboard
npm run build --prefix dashboard
```

From `plugin/`, run `go test ./...` and build a shared object using the target host's toolchain. A compile and hook test cover different risks from loading the artifact inside the gateway. Similarly, a dashboard build verifies TypeScript and packaging, while a signed-in walkthrough exercises credentials, interaction and live retrieval. [Release acceptance exercises](14_production_readiness_report.md)
