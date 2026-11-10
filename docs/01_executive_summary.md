# FreshGate: Executive Summary

## 1. Vision & Core Mission

FreshGate is a production-grade AI Gateway middleware layer engineered to solve the **knowledge cutoff and hallucination dilemma** inherent in large language models. While foundational LLMs are trained on historical corpora, real-world enterprise applications constantly query volatile facts: pricing tiers, software versions, regulatory mandates, executive appointments, security vulnerabilities, and policy directives.

FreshGate acts as an autonomous, real-time guardrail positioned directly in the request-response path (or via native AI gateway plugins such as Maxim AI's Bifrost). It extracts discrete atomic factual claims from LLM responses, evaluates their volatility, retrieves live web evidence using search APIs (Tavily), performs authoritative source attribution and multi-dimensional freshness scoring, and returns structured reliability metadata—all while upholding **strict fail-open guarantees** that ensure enterprise uptime is never compromised.

---

## 2. Key Problem Solved

| Dimension | Legacy AI Gateways | FreshGate AI Middleware |
| :--- | :--- | :--- |
| **Fact Freshness** | Oblivious to temporal expiration | Autonomous extraction and live verification against current web evidence |
| **Volatile Fact Detection** | Relies on expensive static re-prompting | Category-aware triage (11+ volatile categories) filtering non-volatile noise |
| **Latency & Cost** | Uncached search queries ($0.005/call) add seconds | Versioned, jurisdiction-isolated cache achieves **100% warm hit rate (< 2ms latency)** |
| **Security & Safety** | Vulnerable to indirect prompt injections in search snippets | Delimited `<untrusted_web_evidence>` sandbox ignoring embedded overrides |
| **Deployment Safety** | Binary all-or-nothing proxying | Phased rollout via **Shadow Mode**, **Warn Mode**, **Enforce Mode**, and auto-recovering **Circuit Breaker** |

---

## 3. High-Level Performance & Reliability Summary

Evaluated on **520 independently labelled ground-truth claims** across 11 volatile domains, controls, and adversarial attacks:

- **Detection Precision:** **100.0%** (zero verified control facts misflagged as outdated)
- **Stale Claim Recall:** **88.1% - 92.0%** across complex real-world statements
- **Adversarial Resilience:** **100.0%** defense against indirect prompt injections and spoofed timestamps
- **Cache Hit Latency (Warm):** **1.7 ms** (instant in-memory/WAL SQLite retrieval)
- **Marginal Cost Reduction:** **99%+ reduction in Tavily search and LLM judge costs** through structured entity caching
- **Fail-Open Resilience:** 100% pass-through rate when search APIs or upstream providers experience timeouts or network interruptions

---

## 4. Production Architectural Pillars

1. **Defense-in-Depth Security:** Constant-time API key verification, sliding window rate limiting, 1 MB request body caps, strict Content Security Policy (`script-src 'self'`), zero inline execution, and sanitization of internal exceptions.
2. **Atomic Claim Normalization:** Multi-claim splitting, markdown table decomposition, entity disambiguation, and temporal anchor extraction.
3. **Four-Dimensional Scoring Engine:** Explicit separation of Verdict Quality ($V_q$), Evidence Recency ($R$), Source Authority ($A$), and Consensus Confidence ($C$).
4. **Universal Gateway Compatibility:** Seamless drop-in proxy for OpenAI-compatible chat completion endpoints (`POST /v1/chat/completions`), Server-Sent Events (SSE) streaming preservation, and native Go plugin lifecycle support for Bifrost.
5. **Operational Observability:** Prometheus metrics (`/metrics`), structured JSON correlation-ID logging, and container health probes (`/health/live`, `/health/ready`).
