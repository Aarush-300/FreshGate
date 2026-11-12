import time
import pytest
import concurrent.futures
from core.models import Claim, Source
from core.scorer import calculate_score, compute_score_dimensions
from core.cache import SQLiteClaimCache, build_structured_cache_key


def test_four_scoring_dimensions():
    # Test a contradicted claim with an old source
    sources_old = [
        Source(
            url="https://legacy-pricing.com",
            domain="legacy-pricing.com",
            published="2020-01-01",
            snippet="old price",
            authority_score=0.5,
            is_primary=False,
            relevance_score=0.6,
        )
    ]
    claim_stale = Claim(
        id="c1",
        text="GPT-4 costs $30",
        type="pricing",
        verdict="contradicted",
        confidence=0.9,
        sources=sources_old,
    )

    dimensions = compute_score_dimensions(claim_stale)
    assert dimensions["verdict_quality"] == 0.10
    assert dimensions["recency"] == 0.5
    assert dimensions["source_authority"] <= 0.6
    assert dimensions["final_score"] < 15

    # Test confirmed claim with recent primary source
    today_str = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 5 * 86400))
    sources_fresh = [
        Source(
            url="https://openai.com/pricing",
            domain="openai.com",
            published=today_str,
            snippet="official pricing",
            authority_score=0.95,
            is_primary=True,
            relevance_score=1.0,
        ),
        Source(
            url="https://docs.openai.com",
            domain="openai.com",
            published=today_str,
            snippet="official docs",
            authority_score=0.9,
            is_primary=True,
            relevance_score=1.0,
        ),
        Source(
            url="https://reuters.com/tech",
            domain="reuters.com",
            published=today_str,
            snippet="independent news",
            authority_score=0.85,
            is_primary=False,
            relevance_score=0.9,
        ),
    ]
    claim_fresh = Claim(
        id="c2",
        text="Python 3.12 was released in October 2023",
        type="version",
        verdict="confirmed",
        confidence=0.95,
        sources=sources_fresh,
    )

    dims_fresh = compute_score_dimensions(claim_fresh)
    assert dims_fresh["verdict_quality"] == 1.0
    assert dims_fresh["recency"] == 1.0
    assert dims_fresh["source_authority"] >= 0.9
    assert dims_fresh["final_score"] >= 85


def test_structured_cache_keys_jurisdiction_isolation():
    # Same bill name "AI Safety Act" in California vs EU
    key_ca = build_structured_cache_key(
        claim_text="AI Safety Act requires frontier testing",
        category="law",
        jurisdiction="california",
    )
    key_eu = build_structured_cache_key(
        claim_text="AI Safety Act requires frontier testing",
        category="law",
        jurisdiction="eu",
    )

    assert key_ca != key_eu
    assert ":california:" in key_ca
    assert ":eu:" in key_eu


def test_cache_jurisdiction_isolation_in_db(workspace_tmp_path):
    db_file = str(workspace_tmp_path / "jurisdiction_test.db")
    cache = SQLiteClaimCache(db_path=db_file)

    claim_ca = Claim(
        id="c1",
        text="Frontier AI model law was passed",
        type="law",
        verdict="contradicted",  # Vetoed in CA
        score=10,
    )
    claim_eu = Claim(
        id="c2",
        text="Frontier AI model law was passed",
        type="law",
        verdict="confirmed",  # Passed in EU
        score=95,
    )

    cache.set(claim_ca, jurisdiction="california")
    cache.set(claim_eu, jurisdiction="eu")

    read_ca = cache.get("Frontier AI model law was passed", category="law", jurisdiction="california")
    read_eu = cache.get("Frontier AI model law was passed", category="law", jurisdiction="eu")

    assert read_ca is not None
    assert read_ca.verdict == "contradicted"

    assert read_eu is not None
    assert read_eu.verdict == "confirmed"


def test_unverifiable_claim_short_ttl(workspace_tmp_path):
    db_file = str(workspace_tmp_path / "short_ttl_test.db")
    cache = SQLiteClaimCache(db_path=db_file)

    claim_unverifiable = Claim(
        id="c1",
        text="Secret acquisition happened yesterday",
        type="company_info",
        verdict="unverifiable",
        score=30,
    )

    # Stores unverifiable claim with default short TTL (300s = 5m)
    cache.set(claim_unverifiable)

    # Immediately available
    cached = cache.get("Secret acquisition happened yesterday", category="company_info")
    assert cached is not None
    assert cached.verdict == "unverifiable"


def test_cache_stats_and_invalidation(workspace_tmp_path):
    db_file = str(workspace_tmp_path / "stats_test.db")
    cache = SQLiteClaimCache(db_path=db_file)

    claim = Claim(id="c1", text="Node 20 is active LTS", type="version", verdict="confirmed", score=90)
    cache.set(claim)

    # 1 Hit
    h1 = cache.get("Node 20 is active LTS", category="version")
    assert h1 is not None

    # 1 Miss
    m1 = cache.get("Unknown claim that does not exist", category="version")
    assert m1 is None

    stats = cache.get_stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["hit_rate"] == 0.5
    assert stats["total_entries"] == 1

    # Invalidate by category
    cache.invalidate(category="version")
    stats_after = cache.get_stats()
    assert stats_after["total_entries"] == 0


def test_sqlite_concurrent_access_protection(workspace_tmp_path):
    db_file = str(workspace_tmp_path / "concurrent_cache.db")
    cache = SQLiteClaimCache(db_path=db_file)

    def write_worker(idx):
        claim = Claim(
            id=f"c{idx}",
            text=f"Concurrent claim number {idx}",
            type="pricing",
            verdict="confirmed",
            score=idx,
        )
        cache.set(claim)
        return cache.get(f"Concurrent claim number {idx}", category="pricing")

    # Run 20 concurrent threads writing and reading SQLite
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(write_worker, range(20)))

    assert len(results) == 20
    for r in results:
        assert r is not None
        assert r.verdict == "confirmed"
