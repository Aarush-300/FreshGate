# FreshGate: Antigravity Implementation Plan

*A stale-answer detector built as a Bifrost gateway plugin, using Tavily for live verification.*

**How to use this doc:** Put the "Project spec" section into a `SPEC.md` at the repo root. Then feed Antigravity one phase at a time, using the prompt in each phase. Do not move to the next phase until the acceptance tests pass.

## 1. Project spec (paste into SPEC.md)

**Goal:** Any app that sends LLM calls through the Bifrost gateway gets automatic fact-freshness checking with no app code changes.

**Flow for each LLM response:**

1. Intercept the response in the Bifrost response path.
2. Extract atomic factual claims as JSON, each tagged `pricing`, `version`, `law`, `leadership`, or `other`.
3. Triage: only volatile types (`pricing`, `version`, `law`, `leadership`) go forward.
4. Verify each flagged claim: Tavily search, then an LLM judge compares the claim with the results. Verdict is `confirmed`, `contradicted`, or `unverifiable`, with source URLs and dates.
5. Score each claim from 0 to 100.
6. Return results as response headers and a `freshgate` metadata block. Optionally include a corrected answer.
7. Cache verdicts by claim hash with a TTL based on claim type.

**Freshness score:** `score = 100 × verdict_weight × recency_factor × agreement_factor`

- `verdict_weight`: confirmed = 1.0, unverifiable = 0.5, contradicted = 0.1
- `recency_factor`: 1.0 if the newest supporting source is under 30 days old, decaying linearly to 0.5 at 365 days, floor 0.5
- `agreement_factor`: `min(1, agreeing_sources / 3)`, with a floor of 0.4 when at least one source exists

**Cache TTLs:** pricing 1 hour, version 6 hours, leadership 24 hours, law 7 days.

**Target JSON contract** (the core function returns this):

```json
{
  "claims": [
    {
      "id": "c1",
      "text": "GPT-4 costs $30 per million input tokens",
      "type": "pricing",
      "verdict": "contradicted",
      "score": 10,
      "sources": [{"url": "https://...", "published": "2026-09-01", "snippet": "..."}],
      "cached": false,
      "correction": "Current price is ..."
    }
  ],
  "summary": {"total": 5, "checked": 3, "stale": 1, "avg_score": 72}
}
```

**Tech choices (default, change only if blocked):**

- Verifier core: Python 3.11 with FastAPI (fastest to build and test)
- Bifrost integration: Bifrost's plugin mechanism if it allows it; otherwise a thin proxy in front of Bifrost (see Phase 5 fallback)
- Cache: SQLite first, Redis only if time allows
- Dashboard: single-page HTML/JS or a small React app, served by the same FastAPI app
- Tests: pytest

**Non-goals:** user accounts, multi-tenant config, streaming-response verification (stretch goal only).

## 2. Repo layout

```
freshgate/
  SPEC.md
  README.md
  .env.example
  core/
    extractor.py     # claim extraction
    triage.py        # volatility filter
    verifier.py      # Tavily search + LLM judge
    scorer.py        # freshness score
    cache.py         # SQLite cache with TTLs
    pipeline.py      # verify(answer_text) -> contract JSON
  server/
    app.py           # FastAPI: /verify, /proxy, /dashboard API
  plugin/            # Bifrost plugin or proxy adapter
  dashboard/         # frontend
  bench/
    dataset.json     # 20 to 30 questions with known stale answers
    run_bench.py
  tests/
```

## 3. Phases

Each phase has a goal, a prompt to give Antigravity, and acceptance tests. Give the agent only one phase at a time.

### Phase 0: Setup and Bifrost research (about 30 to 45 min)

**Goal:** Know exactly how Bifrost plugins work before writing integration code.

**Prompt:**

> Read SPEC.md. Clone the open-source Bifrost gateway repo into `./vendor/bifrost`. Read its docs and source for the plugin system. Write `docs/bifrost-notes.md` covering: (1) how plugins are written and loaded, in which language, (2) which hooks exist (pre-request, post-response, etc.), (3) how to run the gateway locally and send a test request through it, (4) how its caching works. Quote file paths for every claim. Do not write any project code yet.

**Acceptance:**

- Bifrost runs locally and a test chat request goes through it.
- `bifrost-notes.md` identifies a post-response hook, or states clearly that none exists. Verify this yourself in the repo, since the agent can misread docs.

**Decision gate:** If there is no usable post-response hook, plan for the Phase 5 fallback (proxy) and note a possible upstream contribution as a bonus.

### Phase 1: Claim extractor and triage (about 1 to 1.5 hr)

**Goal:** Turn answer text into tagged atomic claims.

**Prompt:**

> Implement `core/extractor.py` and `core/triage.py` per SPEC.md. The extractor calls a cheap LLM with a system prompt that returns ONLY JSON: a list of `{id, text, type}`. Parse safely: strip code fences, retry once on invalid JSON. Triage keeps only `pricing`, `version`, `law`, `leadership`. Make the LLM client a small interface so it can be mocked. Write pytest tests FIRST using a mocked LLM, then implement.

**Acceptance tests:**

- Input: "GPT-4 costs $30 per million tokens and the CEO of X is Y." gives at least 2 claims with types `pricing` and `leadership`.
- Input with no facts ("Hello, how can I help?") gives an empty list.
- Malformed LLM output is handled without a crash.
- Triage drops `other` claims.

### Phase 2: Tavily verifier and judge (about 1.5 to 2 hr)

**Goal:** Verify one claim against live sources.

**Prompt:**

> Implement `core/verifier.py`. For a claim, build a short search query, call the Tavily search API (key from `TAVILY_API_KEY`), and request recent results. Then call an LLM judge with the claim and the top results; it returns JSON `{verdict, correction, supporting_urls}` where verdict is one of confirmed, contradicted, unverifiable. Return sources with URL, published date (if present), and snippet. Handle HTTP errors, timeouts, and rate limits with retry and backoff; on failure return `unverifiable`. Look up the current Tavily API docs before writing the client and cite what you used. Add tests with mocked Tavily responses, plus one optional live test marked `@pytest.mark.live`.

**Acceptance tests:**

- A known-outdated claim returns `contradicted` with at least one source URL (live test).
- A true, stable claim returns `confirmed`.
- Tavily failure returns `unverifiable`, not an exception.
- Claims are verified concurrently (asyncio) with a configurable concurrency cap (default 5).

### Phase 3: Scorer, cache, and pipeline (about 1 to 1.5 hr)

**Goal:** Wire everything into `verify(answer_text)` and return the contract JSON.

**Prompt:**

> Implement `core/scorer.py` exactly per the formula in SPEC.md, with unit tests for each factor. Implement `core/cache.py` using SQLite: key is a hash of the normalized claim text, value is the verdict JSON, with per-type TTLs from SPEC.md and a `cached` flag on read. Implement `core/pipeline.py`: extract, triage, check cache, verify misses, score, assemble the contract JSON. Add a FastAPI endpoint `POST /verify` taking `{"answer": "..."}`.

**Acceptance tests:**

- Same claim twice: second call has `cached: true` and makes zero Tavily calls (assert with a mock).
- Expired entries are re-verified.
- Score of a contradicted claim with one old source is under 15.
- Response matches the contract in SPEC.md (add a JSON schema test).
- `curl` on `/verify` with an outdated answer returns flagged claims.

### Phase 4: Demo dataset and mini benchmark (about 1 hr)

**Goal:** Produce real numbers for the pitch, and get early signal on quality.

**Prompt:**

> Create `bench/dataset.json` with 25 items: each has a `question`, a `stale_answer` (plausible but outdated: old model names, old pricing, former office holders, changed API versions), and the `stale_claim_expected`. Include 5 control items whose answers are still correct. Mark each item's category. Then write `bench/run_bench.py` that runs each answer through `pipeline.verify`, and reports: detection rate on stale items, false-positive rate on controls, average latency per answer, Tavily calls made with and without the cache (run the dataset twice). Output a markdown table to `bench/results.md`.

**Acceptance:** `results.md` exists with all four metrics. Review the dataset yourself: you must confirm each stale item is actually stale today, since the agent may pick facts that are still true.

**Iterate:** Look at the misses and tune the extractor prompt and judge prompt. Budget up to 45 minutes for this.

### Phase 5: Bifrost integration (about 2 to 3 hr, the riskiest phase)

**Goal:** Responses flowing through Bifrost get FreshGate metadata automatically.

**Path A, native plugin (if Phase 0 found a post-response hook):**

> Using `docs/bifrost-notes.md`, implement a Bifrost plugin in `plugin/` that, on each completed chat response, takes the assistant text, calls the FreshGate `/verify` endpoint (or embeds the core if the plugin language allows), and attaches the result as response headers (`X-FreshGate-Score`, `X-FreshGate-Stale-Count`) and a `freshgate` metadata field. It must fail open: if FreshGate errors or exceeds a timeout (default 4 s), return the original response untouched. Add a config flag to run verification asynchronously and log results instead of blocking.

**Path B, fallback proxy (if no usable hook):**

> Implement `POST /proxy/v1/chat/completions` in `server/app.py`. It forwards the request to the local Bifrost gateway unchanged, receives the response, runs `pipeline.verify` on the assistant message, and returns the response with the added headers and `freshgate` field. Same fail-open and timeout rules. Document in the README that FreshGate sits in front of Bifrost and is drop-in by changing the base URL.

**Acceptance tests:**

- Using the OpenAI-compatible client pointed at Bifrost (or the proxy), a question that yields an outdated answer returns flagged claims in the output.
- Killing FreshGate or Tavily still returns the normal LLM answer.
- Added latency is measured and written to the README.

### Phase 6: Dashboard (about 1.5 to 2 hr)

**Goal:** A demo view judges remember.

**Prompt:**

> Build a single-page dashboard in `dashboard/` served by FastAPI. Left: a question box that sends the question through the gateway path and shows the raw LLM answer. Right: the answer with each flagged claim highlighted green (score 70+), yellow (40 to 69), red (under 40). Clicking a claim shows verdict, score breakdown (verdict, recency, agreement), sources as links, the suggested correction, and a "cached" badge. Add a header strip with totals (claims checked, stale found, average freshness) and a small panel showing benchmark results from `bench/results.md`. Keep it responsive and clean, no heavy frameworks required.

**Acceptance:**

- Demo question produces red and green highlights in under about 8 seconds uncached.
- Second run of the same question is visibly faster with cached badges.
- Works at laptop resolution without scrolling for the main view.

### Phase 7: Polish and submission (about 1.5 hr)

**Prompt:**

> Write `README.md` with: the problem, architecture diagram (Mermaid), quick start (under 5 commands, with `.env.example`), how it integrates with Bifrost, benchmark results table, limitations, and future work. Add a `docker-compose.yml` that starts Bifrost and FreshGate together. Run the quick start from a clean clone and fix anything that fails.

**Acceptance:** A teammate can clone, add keys, run, and see the dashboard working in under 10 minutes.

**Demo script (3 minutes):**

1. The problem: show a confident LLM answer with outdated pricing (20 s).
2. Same app, base URL pointed at Bifrost with FreshGate: flagged red, with sources (60 s).
3. Show the cache making the second call instant and cheap (30 s).
4. Benchmark numbers (30 s).
5. How it plugs into any app with one config change (30 s).

## 4. Rules to give Antigravity throughout

- Follow SPEC.md. If something conflicts or is unclear, stop and ask instead of guessing.
- One phase at a time. After each phase, run the tests and show the output.
- Never hardcode API keys; use `.env`.
- Look up current library and API docs (Tavily, Bifrost) instead of relying on memory, and say which docs were used.
- Prefer simple code over clever code. Commit after every passing phase.
- Fail open: FreshGate must never break the user's normal LLM response.

## 5. Time budget and cut list

Total is about 11 to 14 hours of focused work.

**If time is short, cut in this order:**

1. Docker compose and Redis (keep SQLite)
2. Corrected-answer generation
3. Benchmark panel inside the dashboard (keep `results.md`)
4. Native plugin (use the proxy)

**Never cut:** the extractor, the Tavily verifier, per-claim scores, the cache, and the dashboard highlights. They are the core of the pitch.

## 6. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Bifrost has no suitable hook, or it is in a language you do not know | Decide at the Phase 0 gate; use the proxy path |
| Tavily credits or rate limits | Cache aggressively, cap concurrency, verify only volatile claims |
| Judge LLM gives wrong verdicts | Show sources to the user, tune prompts in Phase 4, report false-positive rate honestly |
| Added latency hurts the demo | Triage claims, run checks in parallel, offer an async mode, prewarm the cache for the demo questions |
| Agent invents APIs | Require doc lookups and tests for every external call |

## 7. Pre-submission checklist

- [ ] Hackathon rules checked on submitting one project to both tracks
- [ ] Both tracks' framing is in the README
- [ ] Benchmark numbers are real and reproducible
- [ ] Demo works offline from cached results if the venue Wi-Fi fails
- [ ] Repo is public with a clear license, and no keys in git history
