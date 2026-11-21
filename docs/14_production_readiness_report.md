# Review a deployment for release

Validate the actual image, gateway and storage configuration intended for traffic. Fixture tests establish application behavior under known inputs. Provider access, mail delivery, backup recovery and sustained throughput depend on the deployment.

| Area | Acceptance exercise | Evidence to retain |
| --- | --- | --- |
| Accounts | Sign in, save a Profile, restart and verify again. For a shared service use two accounts with distinct monitors. | Credential reuse and owner-scoped lists, history and exports |
| Verification | Check a changing fact and a claim with insufficient evidence using the intended provider. | Status, method, sources, score handling and request timing |
| Gateway | Send a non-streaming request and exercise the configured verifier failure behavior in staging. | Expected completion, headers and failure response |
| Monitoring | Enroll a claim, run a recheck, allow a scheduled check and inspect Replay. | Updated timestamps, snapshots and any qualifying change alerts |
| Delivery | Configure a recipient, send a test email and observe a due digest. | Inbox receipt and next scheduled delivery |
| Recovery | Restore a backup and session secret into an isolated deployment. | Readable accounts, policies, history and audits after restart |

The included benchmarks use labeled examples and default to mocked providers. Precision and recall describe those fixtures. Warm and cold latency depend on storage and machine load. For deployment sizing, measure representative answer lengths, concurrency, policies, provider quotas and cache hit rates.

Record operational limits alongside the release decision. Stream results are process-local, SQLite features assume a single account, native plugins require binary compatibility, and scheduled work requires a running server. [Implementation coverage](feature-implementation-audit.md) · [Deployment guide](11_deployment_and_rollback_guide.md)
