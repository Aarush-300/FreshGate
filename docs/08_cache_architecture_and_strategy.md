# FreshGate: Cache Architecture and Strategy

## 1. Structured Cache Key Design

To prevent key collisions across distinct factual domains, jurisdictions, and entities, FreshGate formats all cache entries according to a versioned schema:

$$\texttt{key} = \texttt{v2:\{category\}:\{jurisdiction\}:\{entity\}:\{sha256(normalized\_text)[:16]\}}$$

- `v2`: Explicit key schema version, allowing instant invalidation during protocol upgrades.
- `{category}`: Volatile category (e.g. `pricing`, `law`, `version`).
- `{jurisdiction}`: Geographical/regulatory jurisdiction (e.g. `us`, `eu`, `california`, `global`). Guarantees that regional legal rulings do not cross-pollinate.
- `{entity}`: Normalized primary entity (e.g. `openai`, `postgresql`, `crowdstrike`).
- `{hash}`: First 16 hexadecimal characters of SHA-256 over normalized, whitespace-collapsed claim text.

---

## 2. Multi-Backend Architecture

FreshGate abstracts storage behind the `ClaimCacheBase` interface (`core/cache.py`), supporting two deployment topologies:

```
                  [ ClaimCacheBase ]
                          │
         ┌────────────────┴────────────────┐
         ▼                                 ▼
[ SQLiteClaimCache ]              [ RedisClaimCache ]
  - Zero external dependency        - Distributed across instances
  - WAL Journaling mode             - Cluster-native replication
  - 5000ms busy timeout             - Native EXPIRE TTL management
  - Local micro-benchmark           - High-concurrency production
```

### 2.1 SQLite Concurrency & Zero-Downtime Migration
For containerized or single-node deployments, `SQLiteClaimCache` eliminates operational overhead while guaranteeing concurrency safety:
- **Write-Ahead Logging (WAL):** Enabled via `PRAGMA journal_mode=WAL;`, permitting simultaneous readers without blocking writers.
- **Busy Timeout:** Configured with `PRAGMA busy_timeout=5000;` to eliminate `sqlite3.OperationalError: database is locked` under multi-threaded loads.
- **Automated Column Migrations:** Upon startup, `_init_db()` queries `PRAGMA table_info(claim_cache);` and automatically executes dynamic `ALTER TABLE` statements to add new metadata columns without corrupting existing cache records.

### 2.2 Redis Distributed Cache
When `REDIS_URL` is set, `RedisClaimCache` activates:
- Serializes complete `Claim` models to JSON.
- Leverages Redis `EX` TTL parameters to ensure automatic key expiration without garbage-collection overhead.

---

## 3. Dynamic TTL Strategy & Unverifiable Expiration

Static TTLs are fundamentally ill-suited for mixed factual categories. FreshGate enforces category-differentiated expiration:

| Factual Category | Default TTL | Rationale |
| :--- | :--- | :--- |
| `cybersecurity` | **6 hours** | Vulnerability patches and zero-day advisories evolve continuously |
| `pricing` | **12 hours** | Cloud token prices and SaaS tiers update frequently |
| `financial` | **12 hours** | Interest rates and market metrics require intraday freshness |
| `version` | **24 hours** | Software releases typically follow daily release cadences |
| `leadership` | **48 hours** | Executive appointments remain stable for days to months |
| `public_health` | **3 days** | Disease directives and isolation rules evolve over days |
| `law` / `government_policy` | **7 days** | Statutory enforcement deadlines follow multi-week timelines |
| **`unverifiable` (Any category)** | **300 seconds (5 min)** | **Fast TTL:** Prevents transient search outages from locking claims as unverifiable |

---

## 4. Invalidation & Cache Performance Statistics

- **Selective Invalidation:** `cache.invalidate(category="pricing", jurisdiction="us")` deletes entries targeting specific domains without purging the entire cache.
- **Runtime Statistics:** `cache.get_stats()` exposes total hits, misses, hit rate percentage, total active keys, and backend status for Prometheus and dashboard consumption.
