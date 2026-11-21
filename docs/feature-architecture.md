# How stored features fit together

Start with the object being investigated. One request can create several records with different identifiers and owners.

| Record | Identity and purpose |
| --- | --- |
| Policy | A saved ID plus an update timestamp. Defines category, evidence, mode and cache rules. |
| Checked claim | A response-local claim ID and, when history is recorded, a stable fingerprint and verification ID. |
| Snapshot | One observation of that fingerprint with verdict, score, evidence and policy context. |
| Monitored claim | A mutable enrollment with its own ID, enabled state, baseline, last attempt and next check. |
| Alert | A recorded transition or evidence issue linked to a monitored claim. Acknowledgment tracks review. |
| Audit record | Request-level activity with source, status, summary and correlation information. |
| Digest schedule | Per-user delivery metadata including interval, recipient and next due time. |

## Follow a claim across features

Verify produces a result and attempts to record a snapshot. **Monitor claim** copies the displayed verdict, score and sources onto the enrollment as a baseline when available. A Watchtower check leases the enrollment, resolves a pipeline, evaluates the claim and updates its scheduling metadata. A qualifying transition creates an alert. Replay uses the fingerprint to retrieve observations across these checks. The digest reads stored results for enrolled claims and outstanding alerts.

Changing a policy affects future evaluations. An earlier snapshot retains the context captured at check time. A cache entry may expire while that snapshot remains available. Clearing reusable verification data, acknowledging an alert and deleting an enrollment are different state changes.

MongoDB scopes feature data by owner for shared deployments. SQLite supports local single-account use. Audit records and cache entries use separately configured storage. The session secret is also required to reuse encrypted Profile credentials after a restore. [SQLite store](../core/persistence.py) · [MongoDB store](../core/mongo_store.py) · [History writer](../core/history.py)
