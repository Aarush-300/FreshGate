"""Pipeline factory constructing shared and per-user pipelines."""
from __future__ import annotations

import asyncio
from typing import Optional

from core.extractor import ClaimExtractor
from core.llm import OpenAILLMClient
from core.llm_manager import get_llm_manager
from core.persistence import get_feature_store
from core.pipeline import FreshGatePipeline
from core.verifier import ClaimVerifier, TavilySearchClient
from core.mongo import uses_mongo_store
from server.users import SQLiteUsersCollection, load_credentials, users_collection

_shared: FreshGatePipeline | None = None


def get_shared_pipeline() -> FreshGatePipeline:
    global _shared
    if _shared is None:
        _shared = FreshGatePipeline()
    return _shared


def build_user_pipeline(
    creds: dict, owner_id: str, base: FreshGatePipeline | None = None
) -> FreshGatePipeline:
    base = base or get_shared_pipeline()
    mgr = get_llm_manager()
    base_url = creds.get("base_url")
    if base_url:
        from server.netguard import validate_base_url
        base_url = validate_base_url(base_url)
    kw = dict(
        api_key=creds["api_key"],
        base_url=base_url,
        model=creds["model"],
        manager=mgr,
        allow_keyless=creds["keyless_ok"],
    )
    extraction = OpenAILLMClient(request_type="extraction", **kw)
    judge = OpenAILLMClient(request_type="judging", **kw)
    verifier = ClaimVerifier(
        tavily_client=TavilySearchClient(api_key=creds["tavily_key"]),
        judge_llm=judge,
    )
    verifier._semaphore = getattr(base.verifier, "_semaphore", None)
    return FreshGatePipeline(
        extractor=ClaimExtractor(llm_client=extraction),
        verifier=verifier,
        cache=base.cache,
        history_store=get_feature_store().for_owner(owner_id),
    )


async def pipeline_for_owner(owner_id: str | None) -> FreshGatePipeline:
    accounts = users_collection()
    if owner_id:
        user = await asyncio.to_thread(accounts.find_one, {"_id": owner_id})
    elif isinstance(accounts, SQLiteUsersCollection) and not uses_mongo_store():
        user = await asyncio.to_thread(accounts.single_account)
        owner_id = user["_id"] if user else None
    else:
        raise RuntimeError("This claim has no owner, so there are no credentials to check it with.")
    creds = load_credentials(user) if user else None
    if creds is None:
        raise RuntimeError("Add your AI provider and Tavily key in Profile to enable checks.")
    return build_user_pipeline(creds, owner_id)
