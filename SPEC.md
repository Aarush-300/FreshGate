# FreshGate Spec

## Goal
Any app that sends LLM calls through the Bifrost gateway gets automatic fact-freshness checking with no app code changes.

## Flow for each LLM response
1. Intercept the response in the Bifrost response path.
2. Extract atomic factual claims as JSON, each tagged `pricing`, `version`, `law`, `leadership`, or `other`.
3. Triage: only volatile types (`pricing`, `version`, `law`, `leadership`) go forward.
4. Verify each flagged claim: Tavily search, then an LLM judge compares the claim with the results. Verdict is `confirmed`, `contradicted`, or `unverifiable`, with source URLs and dates.
5. Score each claim from 0 to 100.
6. Return results as response headers and a `freshgate` metadata block. Optionally include a corrected answer.
7. Cache verdicts by claim hash with a TTL based on claim type.

## Freshness score
`score = 100 × verdict_weight × recency_factor × agreement_factor`

- `verdict_weight`: confirmed = 1.0, unverifiable = 0.5, contradicted = 0.1
- `recency_factor`: 1.0 if the newest supporting source is under 30 days old, decaying linearly to 0.5 at 365 days, floor 0.5 (for sources older than 365 days or where date is unknown, recency_factor = 0.5)
- `agreement_factor`: `min(1.0, agreeing_sources / 3)`, with a floor of 0.4 when at least one source exists. If 0 sources, agreement_factor = 0.4.

## Cache TTLs
- `pricing`: 1 hour (3600 seconds)
- `version`: 6 hours (21600 seconds)
- `leadership`: 24 hours (86400 seconds)
- `law`: 7 days (604800 seconds)

## Target JSON contract
```json
{
  "claims": [
    {
      "id": "c1",
      "text": "GPT-4 costs $30 per million input tokens",
      "type": "pricing",
      "verdict": "contradicted",
      "score": 10,
      "sources": [
        {
          "url": "https://...",
          "published": "2026-09-01",
          "snippet": "..."
        }
      ],
      "cached": false,
      "correction": "Current price is ..."
    }
  ],
  "summary": {
    "total": 5,
    "checked": 3,
    "stale": 1,
    "avg_score": 72
  }
}
```

## Tech choices
- Verifier core: Python with FastAPI
- Bifrost integration: Bifrost's plugin mechanism if it allows it; otherwise a thin proxy in front of Bifrost (Phase 5 fallback)
- Cache: SQLite first, Redis only if time allows
- Dashboard: Single-page HTML/JS served by the FastAPI app
- Tests: pytest

## Non-goals
- User accounts
- Multi-tenant config
- Streaming-response verification (stretch goal only)

## Policy Studio
- Operators can configure supported claim categories, preferred/allowed/blocked domains, source count and authority requirements, evidence age and freshness thresholds, verification mode, jurisdiction, and cache behavior.
- Policies are optional on direct `/verify` requests. Without a selected policy, the existing default verification behavior applies.
- `shadow` and `warn` report policy outcomes without filtering live evidence. `enforce` filters evidence and may reject policy acceptance when evidence requirements are not met; it does not alter the underlying evidence verdict.
- Policy simulation evaluates caller-supplied sample claims without live search or LLM calls. It is distinct from claim extraction and does not restore the removed offline extraction preview feature.
- Policy configuration is stored in the shared SQLite feature database. PostgreSQL and multi-tenant access controls are not part of the current implementation.

## Watchtower
- Operators can monitor volatile claims at bounded intervals and associate an enabled policy.
- A lifecycle-managed background worker claims due jobs through SQLite leases, so concurrent workers do not run the same claim and expired work can be recovered after restart.
- Scheduled verification requires server-configured provider credentials. Per-request dashboard credentials are never persisted.
- Alerts capture verdict/score transitions and old/new evidence. Provider errors and rate limits are retried and do not produce factual-change alerts.

## Verification Replay
- Each volatile claim verification writes a new immutable snapshot to the shared feature store, including its fingerprint, verification ID, verdict, score dimensions, evidence, policy, cache status, and elapsed duration.
- History is retrieved by fingerprint or verification ID. Replay compares snapshots and provides a chronological timeline that includes matching Watchtower alerts.
- Replay is read-only; it never modifies saved snapshots.

## Developer CLI
- `freshgate` is an HTTP client for the public FreshGate API; it does not duplicate extraction, verification, policy evaluation, or benchmarking logic.
- It supports answer text/files, JSON batch files, text/JSON/Markdown reports, policy selection, minimum-score gates, optional fail-on-unverifiable, warning-only mode, and readiness checks.
- CLI reports do not include configured provider API keys. Unverifiable claims do not fail a score threshold unless explicitly configured.
