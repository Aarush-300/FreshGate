# What FreshGate checks

A support assistant can get a subscription price right and a supported API version wrong in the same paragraph. A document-level confidence rating gives the reader little help deciding what to edit. FreshGate identifies individual factual statements, selects those in categories that change over time, and records the evidence for each selected claim.

## What the reader receives

A checked claim has a category, verdict, freshness score, explanation and source records when evidence is available. A contradicted claim may include a correction. An unverifiable claim marks a gap in the available evidence. The answer summary counts contradicted claims separately, so a reader can find an outdated statement without treating every uncertain statement as false.

## Where it fits

- **An author reviewing a document** can upload it through the FreshGate page, inspect a source and use a suggested correction to revise the original.
- **An application using Bifrost** can receive verification alongside a generated chat completion. The native plugin calls a separately running FreshGate service. An HTTP proxy offers another integration for hosts that cannot load Go plugins.
- **An operator maintaining published content** can monitor a claim, receive a change alert and compare the evidence from two checks. Policy Studio gives the team a way to express what qualifies as acceptable evidence for that content.

A check captures the evidence available at a particular time. Source coverage, model behavior, retrieval failures and cache age all matter when interpreting it. For a hands-on introduction, follow the [walkthrough](demo-script.md).
