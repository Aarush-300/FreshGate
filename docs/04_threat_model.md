# Trust boundaries and operational risks

FreshGate processes user submissions, third-party snippets and model output. Each can carry sensitive or misleading content. A verification verdict describes the evidence the pipeline evaluated. Decisions such as publishing a correction or rejecting a customer-facing answer still belong to the consuming application.

| Boundary | What can go wrong | Control and remaining responsibility |
| --- | --- | --- |
| Documents and answers enter FastAPI | Large requests consume resources or submitted material reaches downstream providers | Configure request and upload limits. Submit material appropriate for the selected model, search service and audit retention. |
| Search results enter the judge | A copied article, ambiguous entity or stale snippet gives a claim apparent support | Inspect links, entity, date and scope. Use independent-source and domain rules where appropriate. |
| A user configures a provider | The server connects to an unintended destination | Production URL checks constrain schemes and resolved addresses. Network-level outbound policy remains an operator decision. |
| A team shares a service | Another user could see sensitive feature data if a shared store lacks owner isolation | Use owner-scoped MongoDB for shared accounts and features. Restrict operator credentials and SQLite audit-file access. |
| An upstream provider fails | A client treats a delivered completion as successfully checked | Read verification status and failure headers. Make an explicit decision about synchronous fail-open behavior. |
| A gateway loads native code | A plugin executes with the gateway's process privileges | Build from reviewed sources and restrict write access to the shared object, configuration and runtime secrets. |

## Data retained after a check

A verification can leave a cache entry, audit record and evidence snapshot. Each serves a different purpose and may persist on a different volume. Removing a monitored item or clearing a cache is insufficient as a general retention operation. Include all stores and backups in the deployment's retention plan. [Storage architecture](feature-architecture.md) · [Access controls](03_security_architecture.md)
