# Define what counts as acceptable evidence

Use Policy Studio when the same source rule should apply to a series of checks. A software documentation team might want recent version information, an official publisher among preferred sources and at least two independent sources before accepting a claim.

## Create and preview a policy

The following body is suitable for `POST /api/policies` with a signed-in session. Change the preferred publisher for your subject.

```json
{
  "name": "Release documentation",
  "categories": ["version"],
  "verification_mode": "warn",
  "preferred_domains": ["python.org"],
  "minimum_source_count": 2,
  "maximum_evidence_age_days": 90
}
```

Validate the candidate or save it, then preview it in the page. For an API preview, post sample claims to `/api/policies/{policy_id}/preview`. Supply the category, text and source records you want to evaluate. A preview evaluates the supplied data without starting retrieval. Select the saved policy in Verify to test it against a real answer.

## Choose the mode deliberately

| Mode | Effect |
| --- | --- |
| Shadow | Evaluates requirements and reports what would fail. |
| Warn | Records warnings while using the normal verification evidence. |
| Enforce | Filters evidence for eligibility before judgment and checks the minimum independent-source requirement. |

Preferred domains guide search. An allowlist restricts eligibility, and a blocklist excludes domains. Subdomains match their parent domain. Use domain names without paths. Warnings from validation can help identify a preferred domain outside an allowlist.

Policies support a minimum score, source authority, jurisdiction, age and score overrides by category, and cache controls. The policy decision and the factual verdict answer different questions. A supported claim can still fail the team's evidence rule. Editing a saved policy changes its cache scope. The proxy's server-level delivery mode is configured separately. [Policy schema](../core/policy_models.py) · [Policy evaluation](../core/policies.py)
