# Dashboard guide

The operator dashboard is a React, TypeScript, Vite, and Tailwind CSS application. FastAPI serves the static production build from `dashboard/dist`; the Docker build compiles the UI before packaging it. For local frontend development, see `dashboard/README.md`.

FreshGate serves one dashboard with these tabs:

- **Verify** checks pasted answers with live web evidence and can apply an enabled Policy Studio policy. Provider keys entered here apply only to that request.
- **System**, **Benchmarks**, and **Audit log** show existing health, evaluation, and administrative information.
- **Policy Studio** creates and manages source, freshness, verification-mode, and cache policies. Its sample policy preview does not make live provider calls.
- **Watchtower** registers a successful claim for scheduled rechecks, displays status and alerts, and supports manual checks, pausing/resuming, and acknowledgement. Scheduled checks need server-configured provider credentials.
- **Replay** opens from a Watchtower claim or alert and compares immutable snapshots. It can also load a claim by fingerprint.

The browser renders server-provided content with text nodes and safe HTTP(S) links. It does not store request-scoped provider credentials.

## Direct verification provider

Direct verification requires a provider, that provider's API key, a base URL, and a model. The provider selector supplies an editable default base URL and model; its model field offers provider-specific suggestions and accepts a model ID not in the suggestions. Supported OpenAI-compatible entries are OpenAI, Groq, Gemini, Ollama, OpenRouter, and a custom endpoint. The endpoint must implement chat completions.

The LLM key is sent in the X-LLM-API-Key request header and cleared from the form when the request finishes. It is not written to local or session storage. The optional Tavily key is only needed when the server does not have TAVILY_API_KEY configured. Scheduled Watchtower checks use server-side provider credentials and do not reuse browser-submitted keys.

The theme control switches the entire dashboard between light and dark palettes. The selection is stored in browser local storage; provider credentials are never stored there.

Feature lists show explicit empty states when there are no saved policies, monitored claims, alerts, or snapshots. API failures are reported in the affected panel; use **Refresh** in Watchtower to retry. A fingerprint with no saved history is treated as an empty Replay state, and comparisons become available after two snapshots.

The source integration checks are in `tests/test_dashboard_integration.py`; they cover API wiring, request-scoped keys, empty states, the static asset base, responsive breakpoints, and reduced-motion handling.
