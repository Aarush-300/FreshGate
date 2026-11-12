# Dashboard guide

FreshGate serves one dashboard with these tabs:

- **Verify** checks pasted answers with live web evidence and can apply an enabled Policy Studio policy. Provider keys entered here apply only to that request.
- **System**, **Benchmarks**, and **Audit log** show existing health, evaluation, and administrative information.
- **Policy Studio** creates and manages source, freshness, verification-mode, and cache policies. Its sample policy preview does not make live provider calls.
- **Watchtower** registers a successful claim for scheduled rechecks, displays status and alerts, and supports manual checks, pausing/resuming, and acknowledgement. Scheduled checks need server-configured provider credentials.
- **Replay** opens from a Watchtower claim or alert and compares immutable snapshots. It can also load a claim by fingerprint.

The browser renders server-provided content with text nodes and safe HTTP(S) links. It does not store request-scoped provider credentials.

Feature lists show explicit empty states when there are no saved policies, monitored claims, alerts, or snapshots. API failures are reported in the affected panel; use **Refresh** in Watchtower to retry. A fingerprint with no saved history is treated as an empty Replay state, and comparisons become available after two snapshots.

Dashboard integration checks are in `tests/test_dashboard_integration.py`. They validate tab-to-section wiring, referenced control IDs, API flow links, safe DOM rendering, confirmation prompts, and responsive/reduced-motion CSS. Run them with `python -m pytest tests/test_dashboard_integration.py -q`.
