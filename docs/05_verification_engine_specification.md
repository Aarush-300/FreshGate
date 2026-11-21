# From an answer to a verification result

1. **Extract statements.** The extractor asks the configured language model for factual claims, then parses and normalizes the response. It records extraction status, method, dropped candidates and fallback use. A heuristic path can recover some claims when model extraction degrades.
2. **Select volatile claims.** Triage maps aliases to the category registry. Pricing, releases, leadership, law and product availability are examples of categories that change. A selected policy can narrow the categories further.
3. **Resolve cached work.** The pipeline checks eligible cache entries before running live checks. Cache misses can run concurrently. In-flight coalescing reduces duplicate work within a matching request scope.
4. **Retrieve and judge.** Each uncached check gets evidence, applies eligible-source rules and attempts a verdict. A search failure, an empty result set and conflicting evidence can produce different explanations even when the verdict is the same.
5. **Score and record.** The pipeline assigns a freshness score, adds temporal indicators, evaluates the selected policy and attempts to save snapshots. History identifiers let the reader reopen a claim later.

## Reading the contract

`summary.total` describes the extracted population. `summary.checked` describes the checked set, including eligible cache hits. `summary.stale` is the number classified as contradicted. `avg_score` averages available scores and can be null. Inspect the individual claims whenever an application decision depends on a specific fact.

Check the top-level and summary status before interpreting zeroes. A no-claims result, an extraction failure and a completed check with zero contradictions describe different outcomes. Partial extraction and insufficient evidence can also lead to warnings. Look at `verification_method`, `cached`, source records and the explanation when tracing an individual result.

The [response models](../core/models.py) define the wire fields. The [pipeline](../core/pipeline.py) defines the execution order and summary logic. For evidence selection, continue to [evidence evaluation](06_evidence_evaluation_and_aggregation.md).
