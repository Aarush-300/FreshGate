# FreshGate documentation

FreshGate follows a factual claim from the first check through later changes in the evidence. Start with the guide that matches what you want to do.

| I want to… | Start here |
| --- | --- |
| Try a complete verification | [Install FreshGate](../README.md#run-the-freshgate-page-locally), then follow the [guided walkthrough](demo-script.md). |
| Check answers inside my gateway | [Bifrost plugin and HTTP proxy](bifrost-notes.md) |
| Understand a disputed result | [Evidence evaluation](06_evidence_evaluation_and_aggregation.md) and [score calculation](07_scoring_methodology.md) |
| Manage changing claims | [FreshGate page](dashboard.md), [Policy Studio](policy-studio.md), [Watchtower](watchtower.md) and [Replay](verification-replay.md) |
| Automate checks | [Command-line client](developer-cli.md) and [HTTP API reference](13_api_reference.md) |
| Operate a deployment | [Deployment and rollback](11_deployment_and_rollback_guide.md), [operations](10_production_operations_guide.md) and [incident response](12_monitoring_and_incident_response_runbook.md) |

## Design and implementation

Read the [product overview](01_executive_summary.md) for the problem and intended users. The [system architecture](02_architecture_document.md) traces requests across processes. The [verification pipeline](05_verification_engine_specification.md), [cache design](08_cache_architecture_and_strategy.md) and [stored feature relationships](feature-architecture.md) describe the code behind those flows.

The [security architecture](03_security_architecture.md) documents credentials and data boundaries. The [threat model](04_threat_model.md) explains what an operator still needs to protect. For release work, use the [capability matrix](feature-delivery-report.md), [implementation and test map](feature-implementation-audit.md) and [deployment readiness review](14_production_readiness_report.md).
