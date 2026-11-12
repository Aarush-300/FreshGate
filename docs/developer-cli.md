# FreshGate Developer CLI

The CLI is a small HTTP client. It sends requests to a running FreshGate server and does not import or duplicate the verification pipeline.

## Install and connect

From a FreshGate source checkout:

```bash
python -m pip install -e .
freshgate status
```

The command defaults to `http://127.0.0.1:8000`. Set `FRESHGATE_URL` or pass `--url` for another server. `--timeout` overrides the request timeout; `FRESHGATE_CLI_TIMEOUT` sets its default. Transient network errors and selected server statuses are retried up to two times by default; set `--retries 0` through `--retries 5` to adjust this.

The CLI sends provider keys from `FRESHGATE_OPENAI_API_KEY` / `FRESHGATE_TAVILY_API_KEY`, falling back to the standard `OPENAI_API_KEY` / `TAVILY_API_KEY` variables. It sends them only in request headers to `/verify`; it never writes them to report files. A self-hosted server may instead have server-side provider credentials configured. `/verify` currently has no separate FreshGate API authentication requirement, so deploy it behind an appropriate access boundary.

## Verify an answer

```bash
freshgate verify --text "The current API version is 2.4"
freshgate verify --file answer.txt --format json
freshgate verify --file answer.txt --policy-id policy-id --format markdown
```

Each claim shows its verdict, score, and evidence URLs. JSON output includes the API result and a `passed` evaluation with any CI conditions that were not met.

## CI checks

```bash
freshgate verify --file docs/answer.md --min-score 70 --format json > freshgate-report.json
```

- Exit `0`: configured conditions passed.
- Exit `1`: a scored claim was below `--min-score`, the selected policy rejected the result, or `--fail-on-unverifiable` was explicitly set and a claim was unverifiable.
- Exit `2`: invalid input/configuration, a server/API error, or an exhausted timeout/connection retry.
- Exit `130`: interrupted by the user or CI cancellation.

Unverifiable claims do not fail a score threshold by themselves because they have no measured score. Add `--fail-on-unverifiable` when a pipeline requires every claim to be verifiable. `--warning-only` preserves the failed conditions in output but always exits `0`.

Example GitHub Actions step for a FreshGate service available to the runner:

```yaml
- name: Verify documentation claims
  env:
    FRESHGATE_URL: ${{ vars.FRESHGATE_URL }}
    FRESHGATE_OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
    FRESHGATE_TAVILY_API_KEY: ${{ secrets.TAVILY_API_KEY }}
  run: freshgate verify --file docs/answer.md --min-score 70 --format json > freshgate-report.json
```

Batch input can be a JSON array of strings/objects or an object containing an `answers` array:

```json
{
  "answers": [
    {"id": "pricing-page", "text": "The plan costs $20 per month", "policy_id": "pricing-policy"},
    "Python 3.14 is the current stable release"
  ]
}
```

Run `freshgate batch --file answers.json --format json --min-score 70` to get one aggregate report and exit status. A per-answer policy ID overrides the command-level `--policy-id`.

## Other commands

```bash
freshgate status
freshgate benchmark --suite all
```

The benchmark command delegates to `bench/evaluate.py`; it does not introduce another benchmark runner. Run it from a source checkout with FreshGate's development/runtime dependencies installed. Add `--live` only when you intend to call external provider APIs and incur their costs. The CLI itself can also be run without installation using `python -m cli`.

For deterministic feature-specific scenarios, run `python bench/evaluate_features.py`. This checks source/score policy decisions, verdict-change and stable-claim alert behavior, Replay verdict/score/evidence comparisons, and CLI score-threshold output. It reports local microbenchmark timings and makes no provider calls.
