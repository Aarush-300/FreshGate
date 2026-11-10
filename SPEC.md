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
