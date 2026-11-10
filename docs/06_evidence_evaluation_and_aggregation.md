# FreshGate: Evidence Evaluation and Aggregation Specification

## 1. Domain Authority Architecture

Search engine ranking does not equate to factual credibility. FreshGate implements an independent Source Validation Engine (`core/sources.py`) to systematically assess every retrieved citation.

### 1.1 Authority Tier Hierarchy

| Source Tier | Domain Patterns / Characteristics | Base Authority Score |
| :--- | :--- | :--- |
| **Tier 1: Government & Regulatory** | `.gov`, `.mil`, `europa.eu`, `who.int`, `nist.gov` | **0.95** |
| **Tier 2: Primary Documentation** | Official vendor documentation (e.g. `docs.python.org`, `aws.amazon.com`, `learn.microsoft.com`) | **0.90** |
| **Tier 3: Reputable News & Wires** | `reuters.com`, `apnews.com`, `bloomberg.com`, `nature.com` | **0.85** |
| **Tier 4: General Technology Press** | `theverge.com`, `techcrunch.com`, `arstechnica.com` | **0.70** |
| **Tier 5: Secondary Blogs / Medium** | `medium.com`, `substack.com`, generic blogs | **0.50** |
| **Tier 6: Low-Authority / Forums** | `reddit.com`, generic forums, unverified discussion boards | **0.30** |

### 1.2 Primary Source Identification
If a retrieved URL originates from the official primary domain of the entity named in the claim (e.g., an `openai.com` citation for a ChatGPT Plus pricing claim), the source is marked `is_primary = True` and receives an additional **+0.10 authority boost** (capped at 1.0).

---

## 2. Syndication & Duplicate Reporting Filtration

Major news wires frequently syndicate articles across hundreds of regional portals. Treating 10 copies of the exact same syndicated wire article as 10 independent confirmations artificially inflates agreement metrics.

FreshGate applies two filtering rules:
1. **Domain Deduplication:** Only the highest-authority URL from a given root domain (e.g., `reuters.com`) is retained in the evaluation set.
2. **Snippet Hash Distance:** Snippets exhibiting high token overlap (>80% Jaccard index) from secondary republishers are clustered into a single provenance node.

---

## 3. Evidence Aggregation & Conflict Resolution

When retrieved evidence yields divergent claims (e.g., a forum user claiming a service is \$10, while the vendor pricing page states \$15):

```
       [ Discrepancy Detected ]
                 │
                 ▼
  Evaluate Authority Differential:
  Auth(Vendor) = 0.95 vs Auth(Forum) = 0.30
                 │
                 ▼
  Delta ≥ 0.25 ?
    ├── YES ──► Authoritative Primary Source Overrides Low-Tier Source
    └── NO  ──► Genuine Controversy ──► Mark "unverifiable" or "partially_supported"
```

1. **Authority-Weighted Consensus:** High-tier primary sources systematically override lower-tier secondary discussions.
2. **Recency Disambiguation:** If two authoritative sources disagree, publication dates are checked. The newer timestamped announcement supersedes older documentation.
3. **Missing vs Contradictory Evidence:** If no source addresses the claim, FreshGate marks it `unverifiable`. It is never falsely marked confirmed.
