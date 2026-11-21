# Current capabilities and their boundaries

| Workflow | Delivered behavior | Operational limit |
| --- | --- | --- |
| Review an answer or file | Extract volatile claims and inspect verdicts, scores, evidence and corrections. | Documents need readable text. Retrieval can yield insufficient evidence. |
| Apply an editorial policy | Preview or apply category, domain, age, source-count and score requirements. | Preview evaluates supplied evidence. Live results depend on retrieval. |
| Integrate Bifrost natively | Check assistant text and attach a report to the gateway response. | First text choice of completed non-streaming chat. Plugin and host builds must match. |
| Verify a proxied stream | Forward events and check collected assistant text after the stream. | The result is held by the serving process. |
| Track a changing claim | Schedule rechecks and raise alerts for selected changes. | Timeliness depends on uptime, providers, credentials and backlog. |
| Explain a historical change | Compare saved observations and export a knowledge-base record or full report. | A comparison needs two snapshots of the same fingerprint. |
| Automate acceptance criteria | Verify batches and signal failed thresholds using CLI exit codes. | Direct verification uses credentials supplied by the CLI process. |
| Review service activity | Inspect readiness, operational metrics and audits. | Operator audit access includes sensitive submitted text. |

## Choose an artifact for the reader

For an author, show a claim, its citation and the applicable correction. For an application engineer, show a gateway completion retaining its provider metadata alongside the report. For an operator, use two snapshots to identify the evidence behind a changed score or verdict. A test fixture or packaged benchmark can illustrate behavior when clearly identified as such. [Guided walkthrough](demo-script.md) · [Implementation and tests](feature-implementation-audit.md)
