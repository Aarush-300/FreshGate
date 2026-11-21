# Compare two checks with Replay

A claim fingerprint identifies a normalized statement in context. A verification ID identifies one observation. The fingerprint includes normalized text, category, entities and jurisdiction. It preserves factual details such as numbers and currency, so changing a price can produce a different identity.

## Investigate a change

Open Replay from a monitored claim or paste its 64-character fingerprint. Choose two saved snapshots and compare their timestamps. Inspect the previous and current verdict, score delta, correction, policy, and added, removed or changed sources. A changed snippet at the same URL can matter even when the list of links looks identical.

Read cache status when interpreting history. Two snapshots may reuse the same evidence. A monitoring enrollment baseline can appear before the first stored verification snapshot. A comparison requires two observations of the same fingerprint under the accessible account scope.

## Retrieve or export records

| Goal | Route |
| --- | --- |
| Paginate checks for a claim | `GET /api/history/claims/{fingerprint}` |
| Retrieve one verification | `GET /api/history/verifications/{verification_id}` |
| Load the Replay page data | `GET /api/replay/{fingerprint}` |
| Compare specific observations | `GET /api/replay/{fingerprint}/compare?from={earlier_id}&to={later_id}` |
| Read verification and alert events | `GET /api/replay/{fingerprint}/timeline` |

Use the signed-in account that owns the history. For downloads the page targets `/api/export/claim/{fingerprint}`. The knowledge-base record is available in JSON, XML and CSV. The full report is available as JSON or HTML. Use `download=true` with XML, CSV or HTML for file content. Without that flag those formats are returned inside JSON envelopes.

Preserve feature storage to retain comparisons after a restart. Snapshots, audit entries and cache entries follow different storage paths. [Claim identity](../core/claim_identity.py) · [Comparison implementation](../core/replay.py) · [Export endpoints](../server/routes/export.py)
