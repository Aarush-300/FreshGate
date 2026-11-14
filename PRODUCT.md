# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

AI platform operators using FreshGate to check the freshness of claims in AI responses and to monitor claims after verification.

## Product Purpose

FreshGate is an AI gateway middleware and dashboard for finding and verifying time-sensitive factual claims in AI responses. Its workflow includes direct verification, policy-based evaluation, scheduled claim monitoring, alert acknowledgement, and read-only comparison of verification history.

## Capabilities and Constraints

- The FastAPI service exposes verification, health, metrics, provider status, benchmark, recent audit, policy, Watchtower, and Replay APIs.
- The dashboard must use these service APIs and display their returned data and states. It must not present fabricated operational metrics or pretend records as live data.
- The documented dashboard areas are Verify, System, Benchmarks, Audit log, Policy Studio, Watchtower, and Replay.
- Request-scoped provider credentials entered in the browser apply only to that request and are not stored in browser storage.
- The service currently serves its dashboard from a static `dashboard/` directory. The selected dashboard surface is a React/Vite implementation.
- Dashboard stack selected for this surface: React, TypeScript, Vite, and Tailwind CSS, served as static assets by the existing FastAPI service.

## Brand Commitments

- Product name: FreshGate.

## Evidence on Hand

- Product workflows and dashboard behavior: `docs/dashboard.md`.
- API implementation: `server/app.py` and `server/routes/`.
- Workflow and persistence architecture: `docs/feature-architecture.md` and `core/persistence.py`.
- No verified customer, performance, or market claims are available for the dashboard to display.

## Product Principles

- Make the verification outcome and its evidence understandable.
- Distinguish unavailable, empty, failed, and successful states.
- Make monitoring and policy changes observable and recoverable.
- Keep verification history immutable and comparison read-only.
- Do not persist request-scoped provider credentials in the browser.
