# Claim cache and reuse

A paragraph is extracted again when submitted for verification. Once the volatile claims have been identified, a matching cached claim can satisfy a check without another search and judgment. This lets two different answers reuse a check for the same normalized statement.

## What determines a hit

The versioned key includes normalized text, category, jurisdiction, entity and an optional policy scope. A saved policy contributes its identifier and update timestamp. Editing the policy therefore changes its cache scope. An enforce-mode hit is also checked for current evidence eligibility. A policy can bypass caching altogether.

Ordinary entries take their lifetime from the category registry. Unverifiable results have a shorter default lifetime so a temporary retrieval problem can be reconsidered. A configured per-category policy TTL can override the default. A hit carries `cached: true`. The newest snapshot may consequently reuse evidence from an earlier search.

## Operating the cache

SQLite is the local backend. When Redis is configured and available at selection time, FreshGate can use it for shared caching. Cache startup selection and later connectivity failures are separate concerns. Inspect backend statistics when investigating unexpected misses. The default SQLite paths for cache, features and audit refer to distinct responsibilities.

Use cache-hit and cache-miss counters over the same time window. A cold run is a useful baseline before comparing a repeated run with identical text, policy and entity. Clearing the cache is an administrative action affecting other requests. To investigate one claim, prefer a verification under a policy with caching disabled.

The [cache implementation](../core/cache.py) defines key normalization, TTL handling and storage selection. The [category registry](../core/categories.py) supplies category defaults.
