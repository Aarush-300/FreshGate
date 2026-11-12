# Policy Studio

Policy Studio lets an operator define which volatile claim categories to check, which web sources count as eligible evidence, how fresh that evidence must be, and whether the policy runs in `shadow`, `warn`, or `enforce` mode. Policies are stored in the shared SQLite feature database configured by `FRESHGATE_FEATURE_DB`.

## Create and apply a policy

Open the Policy Studio tab in the dashboard to create, edit, enable, duplicate, validate, or delete a policy. A policy can include:

- Claim categories supported by FreshGate's category registry.
- Preferred, allowed, and blocked DNS domains. Domain matching includes subdomains; entries are domain names, not URL paths.
- Minimum independent source count and source authority.
- A minimum freshness score and maximum evidence age, with per-category overrides.
- A verification mode, optional jurisdiction, and cache settings/TTL overrides.

To apply a saved policy to a direct verification request, select it in the Verify tab. API clients can send its identifier as `policy_id` in `POST /verify`. If no policy is selected, FreshGate keeps its default verification behavior.

## Modes

- **Shadow** performs the normal evidence search and judgment, then reports policy failures as `would_fail`. It does not restrict live evidence.
- **Warn** performs normal verification and reports policy failures as `warning`. It does not restrict live evidence.
- **Enforce** restricts evidence to eligible domains/authority/age, requests preferred domains from search when configured, and skips judging when fewer than the required eligible independent sources are found. The evidence-based verdict remains separate from the policy decision.

Enforcement can reject a result that otherwise has a verdict if it misses a policy's source or freshness requirements. A policy never changes contradictory evidence into a confirmed verdict. Cache entries are scoped to the selected policy revision; enforced policies also re-check cached evidence eligibility. Set `cache_enabled` false to bypass caching for a policy.

## Policy simulation

The **Preview policy** action and `POST /api/policies/{policy_id}/preview` evaluate supplied sample claims and sources without performing web search or calling an LLM. This is a deterministic policy simulation; it is separate from the removed offline claim extraction preview.

## API

- `GET /api/policies/categories`
- `GET /api/policies`
- `POST /api/policies`
- `POST /api/policies/validate`
- `GET /api/policies/{policy_id}`
- `PUT /api/policies/{policy_id}`
- `DELETE /api/policies/{policy_id}`
- `POST /api/policies/{policy_id}/validate`
- `POST /api/policies/{policy_id}/preview`
- `POST /api/policies/{policy_id}/duplicate`

`POST /verify` accepts an optional `policy_id`. Its response adds `policy_evaluation` when a policy is applied, including its ID, mode, overall decision, acceptance flag, and per-claim requirement results. Existing requests without `policy_id` retain their prior behavior.

## Storage notes

Policies use the Phase 1 SQLite feature store. Back up the configured database file to preserve policy configurations. This phase does not add PostgreSQL support or multi-tenant access controls; protect the service at the deployment boundary before exposing policy management publicly.
