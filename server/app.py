import os
import time
import json
import asyncio
import logging
from typing import Optional, Dict, Any, Tuple
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, Response, BackgroundTasks, Depends, status
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import httpx
from dotenv import load_dotenv

# Ensure environment configuration is loaded before constructing services
load_dotenv()

from core.config import get_config
from core.models import VerificationResult
from core.pipeline import FreshGatePipeline
from core.extractor import ClaimExtractor, FallbackHeuristicExtractor
from core.llm import OpenAILLMClient
from core.llm_manager import get_llm_manager
from core.verifier import ClaimVerifier, TavilySearchClient
from core.config import is_valid_credential
from core.policies import get_policy
from core.security import (
    SecurityHeadersMiddleware,
    RequestBodySizeLimitMiddleware,
    RateLimitMiddleware,
    rate_limiter,
    verify_admin_key,
    sanitize_error_detail,
)
from core.metrics import metrics
from server.routes.policies import router as policies_router
from server.routes.watchtower import router as watchtower_router, watchtower_service
from server.routes.replay import history_router, replay_router
from core.logging_config import CorrelationIdMiddleware, get_correlation_id
from core.audit import audit_store
from core.circuit_breaker import circuit_breaker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("freshgate")

cfg = get_config()
BIFROST_UPSTREAM_URL = cfg.bifrost_upstream_url
UPSTREAM_TIMEOUT = cfg.upstream_timeout
EXTRACTION_TIMEOUT = cfg.extraction_timeout
SEARCH_TIMEOUT = cfg.search_timeout
JUDGING_TIMEOUT = cfg.judging_timeout
FRESHGATE_TIMEOUT = cfg.freshgate_timeout
FRESHGATE_FAIL_OPEN = cfg.fail_open
FRESHGATE_ASYNC_MODE = cfg.async_mode
FRESHGATE_MODE = cfg.freshgate_mode  # shadow, warn, enforce
FRESHGATE_MIN_SCORE = cfg.min_score
FRESHGATE_BLOCK_STALE = cfg.block_stale
MAX_BODY_SIZE = cfg.max_body_size_bytes


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI application lifecycle managing connection pools and startup initialization."""
    current_cfg = get_config()
    logger.info("Starting FreshGate Gateway (mode=%s, upstream=%s)", current_cfg.freshgate_mode, current_cfg.bifrost_upstream_url)
    _ = get_llm_manager()
    await watchtower_service.start()
    yield
    logger.info("Shutting down FreshGate Gateway connection pools...")
    try:
        await watchtower_service.stop()
    except Exception as exc:
        logger.debug("Error during watchtower stop: %s", exc)
    try:
        mgr = get_llm_manager()
        await mgr.aclose()
    except Exception as exc:
        logger.debug("Error during LLM manager aclose: %s", exc)
    try:
        if hasattr(pipeline.verifier, "tavily_client") and hasattr(pipeline.verifier.tavily_client, "aclose"):
            await pipeline.verifier.tavily_client.aclose()
    except Exception as exc:
        logger.debug("Error during Tavily client aclose: %s", exc)


app = FastAPI(
    title="FreshGate AI Gateway Middleware",
    description="Production-grade fact-freshness verification layer for AI Gateways with Tavily & LLM Judge",
    version="1.1.0",
    lifespan=lifespan,
)

# Register Security & Observability Middlewares
app.add_middleware(CorrelationIdMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestBodySizeLimitMiddleware, max_body_bytes=MAX_BODY_SIZE)
app.add_middleware(RateLimitMiddleware, limiter=rate_limiter)
app.include_router(policies_router)
app.include_router(watchtower_router)
app.include_router(history_router)
app.include_router(replay_router)

pipeline = FreshGatePipeline()


class VerifyRequest(BaseModel):
    answer: str = Field(..., min_length=1, max_length=50000, description="Assistant answer text to verify")
    policy_id: Optional[str] = Field(default=None, min_length=1, max_length=80)


def build_request_pipeline(openai_api_key: Optional[str], tavily_api_key: Optional[str]) -> FreshGatePipeline:
    """Build clients with keys scoped to this request, sharing bounded LLM resources and cache."""
    manager = get_llm_manager()
    extraction_client = OpenAILLMClient(
        api_key=openai_api_key, manager=manager, request_type="extraction"
    )
    judge_client = OpenAILLMClient(
        api_key=openai_api_key, manager=manager, request_type="judging"
    )
    tavily_client = TavilySearchClient(api_key=tavily_api_key)
    request_verifier = ClaimVerifier(tavily_client=tavily_client, judge_llm=judge_client)
    # Keep the existing process-wide bound on concurrent evidence checks.
    request_verifier._semaphore = pipeline.verifier._semaphore
    return FreshGatePipeline(
        extractor=ClaimExtractor(llm_client=extraction_client),
        verifier=request_verifier,
        cache=pipeline.cache,
    )


async def forward_to_upstream(
    method: str,
    path: str,
    headers: Dict[str, str],
    body: bytes,
    timeout: float = 60.0,
) -> Tuple[int, Any, Dict[str, str]]:
    """Forward incoming request directly to upstream Bifrost gateway."""
    target_url = f"{BIFROST_UPSTREAM_URL}{path}"
    # Filter out hop-by-hop headers
    forward_headers = {
        k: v
        for k, v in headers.items()
        if k.lower() not in ("host", "content-length", "connection")
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.request(
            method=method,
            url=target_url,
            headers=forward_headers,
            content=body,
        )
        try:
            json_body = resp.json()
        except Exception:
            json_body = resp.text

        resp_headers = dict(resp.headers)
        return resp.status_code, json_body, resp_headers


async def stream_from_upstream(
    method: str,
    path: str,
    headers: Dict[str, str],
    body: bytes,
    timeout: float = 60.0,
):
    """Open streaming connection to upstream Bifrost gateway."""
    target_url = f"{BIFROST_UPSTREAM_URL}{path}"
    forward_headers = {
        k: v
        for k, v in headers.items()
        if k.lower() not in ("host", "content-length", "connection")
    }
    client = httpx.AsyncClient(timeout=timeout)
    req = client.build_request(
        method=method,
        url=target_url,
        headers=forward_headers,
        content=body,
    )
    return await client.send(req, stream=True)


stream_verifications: Dict[str, Any] = {}


async def run_streaming_verification(completion_id: str, text: str):
    """Run verification asynchronously for a completed SSE stream."""
    try:
        t0 = time.perf_counter()
        result = await pipeline.verify(text)
        duration_ms = (time.perf_counter() - t0) * 1000
        stream_verifications[completion_id] = {
            "status": "completed",
            "result": result.model_dump(),
            "duration_ms": duration_ms,
            "timestamp": time.time(),
        }
        if len(stream_verifications) > 500:
            oldest_keys = sorted(stream_verifications, key=lambda k: stream_verifications[k].get("timestamp", 0))[:50]
            for old_key in oldest_keys:
                stream_verifications.pop(old_key, None)
        logger.info("Streaming verification for %s finished in %.1fms", completion_id, duration_ms)
    except Exception as e:
        safe_err = sanitize_error_detail(e)
        stream_verifications[completion_id] = {
            "status": "failed",
            "error": safe_err,
            "timestamp": time.time(),
        }
        if len(stream_verifications) > 500:
            oldest_keys = sorted(stream_verifications, key=lambda k: stream_verifications[k].get("timestamp", 0))[:50]
            for old_key in oldest_keys:
                stream_verifications.pop(old_key, None)
        logger.error("Streaming verification for %s failed: %s", completion_id, safe_err)


async def run_async_verification(text: str):
    """Run verification asynchronously in background and log findings."""
    try:
        t0 = time.perf_counter()
        result = await pipeline.verify(text)
        duration_ms = (time.perf_counter() - t0) * 1000
        logger.info(
            "Async verification complete in %.1fms. Stale=%d, AvgScore=%d",
            duration_ms,
            result.summary.stale,
            result.summary.avg_score,
        )
    except Exception as e:
        logger.error("Async verification background task failed safely: %s", sanitize_error_detail(e))


@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "freshgate", "upstream": BIFROST_UPSTREAM_URL}


@app.get("/health/live")
async def health_live():
    """Liveness probe for container orchestrators."""
    return {"status": "alive"}


@app.get("/health/ready")
async def health_ready():
    """Readiness probe checking database connectivity and cache availability."""
    db_status = "connected"
    try:
        _ = pipeline.cache.get_stats()
    except Exception as e:
        logger.error("Readiness probe database check failed: %s", e)
        db_status = "unhealthy"

    is_ready = (db_status == "connected")
    status_code = status.HTTP_200_OK if is_ready else status.HTTP_503_SERVICE_UNAVAILABLE

    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ready" if is_ready else "not_ready",
            "database": db_status,
            "cache": "connected" if is_ready else "unavailable",
            "upstream": BIFROST_UPSTREAM_URL,
        },
    )


@app.get("/metrics")
async def get_metrics():
    """Prometheus-compatible operational metrics endpoint."""
    return Response(
        content=metrics.to_prometheus_format(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@app.get("/api/audits/recent", dependencies=[Depends(verify_admin_key)])
async def get_recent_audits(limit: int = 50):
    """Retrieve recent immutable verification audit logs for compliance monitoring."""
    return {"audits": audit_store.get_recent_audits(limit=limit)}


@app.post("/verify", response_model=VerificationResult)
async def verify_answer(req: VerifyRequest, request: Request):
    """Direct verification endpoint for any answer text."""
    openai_key = request.headers.get("X-OpenAI-API-Key")
    tavily_key = request.headers.get("X-Tavily-API-Key")
    if openai_key is not None and not is_valid_credential(openai_key):
        raise HTTPException(status_code=400, detail="The OpenAI API key is invalid or malformed.")
    if tavily_key is not None and not is_valid_credential(tavily_key):
        raise HTTPException(status_code=400, detail="The Tavily API key is invalid or malformed.")
    policy = get_policy(req.policy_id) if req.policy_id else None
    if req.policy_id and policy is None:
        raise HTTPException(status_code=404, detail="Policy not found")
    if policy and not policy.enabled:
        raise HTTPException(status_code=409, detail="Selected policy is disabled")
    request_pipeline = build_request_pipeline(openai_key, tavily_key) if (openai_key or tavily_key) else pipeline
    try:
        return await request_pipeline.verify(
            req.answer,
            single_flight_scope=getattr(request.state, "correlation_id", None),
            policy=policy,
        )
    except Exception as e:
        safe_msg = sanitize_error_detail(e)
        logger.error("Direct verification failed: %s", safe_msg)
        raise HTTPException(status_code=500, detail=safe_msg)
    finally:
        if request_pipeline is not pipeline:
            await request_pipeline.verifier.tavily_client.aclose()


@app.post("/proxy/v1/chat/completions")
@app.post("/v1/chat/completions")
async def proxy_chat_completions(request: Request, background_tasks: BackgroundTasks):
    """Drop-in OpenAI chat completions proxy in front of Bifrost.

    Intercepts the response, verifies facts, attaches X-FreshGate headers and
    'freshgate' metadata, and fails open safely on any timeout or exception.
    """
    body_bytes = await request.body()
    headers = dict(request.headers)
    metrics.inc_requests()

    # Check if stream is requested
    is_stream = False
    try:
        req_json = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
        is_stream = bool(req_json.get("stream", False))
    except Exception:
        req_json = {}

    if is_stream:
        try:
            stream_resp = await stream_from_upstream(
                method=request.method,
                path="/v1/chat/completions",
                headers=headers,
                body=body_bytes,
            )
        except Exception as e:
            safe_err = sanitize_error_detail(e)
            logger.error("Failed to connect streaming upstream: %s", safe_err)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Bad Gateway: Unable to reach Bifrost at {BIFROST_UPSTREAM_URL}",
            )

        async def event_generator():
            accumulated_chunks = []
            accumulated_chars = 0
            completion_id = None
            try:
                async for chunk in stream_resp.aiter_bytes():
                    yield chunk
                    try:
                        text_chunk = chunk.decode("utf-8", errors="ignore")
                        for line in text_chunk.splitlines():
                            line = line.strip()
                            if line.startswith("data: ") and not line.endswith("[DONE]"):
                                data_str = line[6:]
                                parsed = json.loads(data_str)
                                if not completion_id and parsed.get("id"):
                                    completion_id = parsed.get("id")
                                choices = parsed.get("choices", [])
                                if choices and isinstance(choices, list):
                                    delta = choices[0].get("delta", {})
                                    content_piece = delta.get("content")
                                    if content_piece and accumulated_chars < 50000:
                                        accumulated_chunks.append(content_piece)
                                        accumulated_chars += len(content_piece)
                    except Exception:
                        pass
            finally:
                if hasattr(stream_resp, "aclose"):
                    await stream_resp.aclose()

                full_content = "".join(accumulated_chunks).strip()
                if full_content and completion_id:
                    background_tasks.add_task(run_streaming_verification, completion_id, full_content)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    # 1. Forward request to Bifrost upstream
    try:
        status_code, upstream_data, upstream_headers = await forward_to_upstream(
            method=request.method,
            path="/v1/chat/completions",
            headers=headers,
            body=body_bytes,
        )
    except Exception as e:
        safe_err = sanitize_error_detail(e)
        logger.error("Failed to forward request to Bifrost upstream at %s: %s", BIFROST_UPSTREAM_URL, safe_err)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Bad Gateway: Unable to reach Bifrost at {BIFROST_UPSTREAM_URL}",
        )

    # If upstream returned an error or non-dict, return it immediately untouched
    if status_code != 200 or not isinstance(upstream_data, dict):
        return JSONResponse(status_code=status_code, content=upstream_data)

    # 2. Extract assistant response content
    assistant_content = None
    choices = upstream_data.get("choices")
    if choices and isinstance(choices, list) and len(choices) > 0:
        message = choices[0].get("message")
        if message and isinstance(message, dict):
            assistant_content = message.get("content")

    if not assistant_content:
        return JSONResponse(status_code=status_code, content=upstream_data)

    # 3. Check for async or shadow mode
    active_mode = (
        request.headers.get("X-FreshGate-Mode")
        or request.query_params.get("freshgate_mode")
        or ("async" if (FRESHGATE_ASYNC_MODE or request.query_params.get("freshgate_async") == "true" or request.headers.get("X-FreshGate-Async") == "true") else FRESHGATE_MODE)
    ).lower()

    if active_mode in ("async", "shadow"):
        background_tasks.add_task(run_async_verification, assistant_content)
        resp_headers = {"X-FreshGate-Mode": active_mode}
        return JSONResponse(status_code=200, content=upstream_data, headers=resp_headers)

    # 3b. Circuit breaker check
    if not circuit_breaker.can_execute():
        logger.warning("Circuit breaker is OPEN. Immediate fail-open pass-through.")
        headers_to_add = {
            "X-FreshGate-Status": "fail-open",
            "X-FreshGate-Circuit-Breaker": "open",
            "X-FreshGate-Reason": "circuit_breaker_tripped",
        }
        return JSONResponse(status_code=200, content=upstream_data, headers=headers_to_add)

    # 4. Run FreshGate verification with fail-open timeout
    t0 = time.perf_counter()
    try:
        verification: VerificationResult = await asyncio.wait_for(
            pipeline.verify(assistant_content),
            timeout=FRESHGATE_TIMEOUT,
        )
        circuit_breaker.record_success()
        latency_ms = int((time.perf_counter() - t0) * 1000)

        headers_to_add = {
            "X-FreshGate-Score": str(verification.summary.avg_score if verification.summary.avg_score is not None else "unavailable"),
            "X-FreshGate-Stale-Count": str(verification.summary.stale),
            "X-FreshGate-Total-Claims": str(verification.summary.total),
            "X-FreshGate-Checked-Claims": str(verification.summary.checked),
            "X-FreshGate-Latency-Ms": str(latency_ms),
            "X-FreshGate-Status": str(verification.summary.status or "verified"),
        }
        # Record Prometheus latency and audit history
        duration_sec = time.perf_counter() - t0
        metrics.observe_latency(duration_sec)
        corr_id = getattr(request.state, "correlation_id", "req-unknown")
        background_tasks.add_task(
            audit_store.record_audit,
            audit_id=upstream_data.get("id") or f"audit-{int(time.time() * 1000)}",
            correlation_id=corr_id,
            input_text=assistant_content,
            claims_data=[c.model_dump() for c in verification.claims],
            total_claims=verification.summary.total,
            stale_claims=verification.summary.stale,
            avg_score=verification.summary.avg_score,
            latency_ms=latency_ms,
            status=str(verification.summary.status or "verified"),
        )

        # Policy enforcement check
        if active_mode == "enforce" and FRESHGATE_BLOCK_STALE and (
            verification.summary.stale > 0
            or (verification.summary.avg_score is not None and verification.summary.avg_score < FRESHGATE_MIN_SCORE)
        ):
            logger.warning(
                "FreshGate policy violation: stale count=%d, avg_score=%s",
                verification.summary.stale,
                str(verification.summary.avg_score),
            )
            return JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                content={
                    "error": "FreshGate Policy Violation: Response contains outdated factual claims.",
                    "freshgate": verification.model_dump(),
                },
                headers={
                    "X-FreshGate-Policy": "violated",
                    "X-FreshGate-Score": str(verification.summary.avg_score if verification.summary.avg_score is not None else "unavailable"),
                    "X-FreshGate-Stale-Count": str(verification.summary.stale),
                },
            )

        if active_mode == "warn":
            # Return metadata in HTTP headers only, do not modify body JSON
            headers_to_add["X-FreshGate-Mode"] = "warn"
            return JSONResponse(status_code=200, content=upstream_data, headers=headers_to_add)

        # Mode: enforce - enrich JSON body
        upstream_data["freshgate"] = verification.model_dump()
        headers_to_add["X-FreshGate-Mode"] = "enforce"
        return JSONResponse(status_code=200, content=upstream_data, headers=headers_to_add)

    except (asyncio.TimeoutError, Exception) as e:
        circuit_breaker.record_failure()
        safe_msg = sanitize_error_detail(e)
        logger.warning("FreshGate verification failed/timed out, failing open: %s", safe_msg)
        duration_sec = time.perf_counter() - t0
        metrics.observe_latency(duration_sec)
        # Never leak raw stack traces or internal exception details into HTTP headers
        headers_to_add = {
            "X-FreshGate-Status": "fail-open",
            "X-FreshGate-Reason": "timeout" if isinstance(e, asyncio.TimeoutError) else "internal_error",
        }
        return JSONResponse(status_code=200, content=upstream_data, headers=headers_to_add)


@app.get("/api/stream/verification/{completion_id}")
async def get_stream_verification(completion_id: str):
    """Retrieve async fact-checking verification result for a completed SSE stream."""
    entry = stream_verifications.get(completion_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Verification not found or still in progress")
    return entry


@app.get("/api/provider/status")
async def get_provider_status():
    """Retrieve operational metrics, provider status, rate limit budgets, and circuit breaker health."""
    llm_stats = get_llm_manager().get_stats_summary()
    tavily_stats = getattr(pipeline.verifier.tavily_client, "get_stats_summary", lambda: {
        "configured": get_config().is_tavily_configured,
        "status": "configured" if get_config().is_tavily_configured else "missing_credentials",
    })()
    return {
        "llm": llm_stats,
        "openai": llm_stats,
        "tavily": tavily_stats,
        "circuit_breakers": circuit_breaker.get_all_states(),
        "cache": pipeline.cache.get_stats(),
        "metrics": metrics.get_summary(),
        "config": get_config().get_safe_diagnostics(),
    }


@app.get("/api/deployment/status")
async def get_deployment_status():
    """Retrieve operational deployment mode and circuit breaker state."""
    return {
        "mode": FRESHGATE_MODE,
        "min_score": FRESHGATE_MIN_SCORE,
        "block_stale": FRESHGATE_BLOCK_STALE,
        "circuit_breaker": circuit_breaker.get_state(),
    }


@app.post("/api/circuit-breaker/reset", dependencies=[Depends(verify_admin_key)])
async def reset_circuit_breaker():
    """Reset circuit breaker to CLOSED. Protected by Admin Key."""
    circuit_breaker.reset()
    return {"status": "reset", "circuit_breaker": circuit_breaker.get_state()}


@app.get("/api/benchmark")
async def get_benchmark_results():
    """Return benchmark markdown and dataset overview."""
    results_path = Path(__file__).resolve().parent.parent / "bench" / "results.md"
    dataset_path = Path(__file__).resolve().parent.parent / "bench" / "dataset_production.json"

    results_md = results_path.read_text(encoding="utf-8") if results_path.exists() else "No results yet."
    dataset = json.loads(dataset_path.read_text(encoding="utf-8")) if dataset_path.exists() else []

    verdicts = [str(item.get("ground_truth_verdict", "")).lower() for item in dataset]
    stale_items = sum(verdict == "contradicted" for verdict in verdicts)
    control_items = sum(verdict == "confirmed" for verdict in verdicts)
    return {
        "results_md": results_md,
        "total_items": len(dataset),
        "stale_items": stale_items,
        "control_items": control_items,
        "partial_or_unverifiable_items": len(dataset) - stale_items - control_items,
    }


@app.post("/api/cache/clear", dependencies=[Depends(verify_admin_key)])
async def clear_cache():
    """Clear SQLite cache. Protected by Admin Key."""
    pipeline.cache.clear()
    return {"status": "cleared"}


# Mount dashboard directory for static UI serving
dashboard_dir = Path(__file__).resolve().parent.parent / "dashboard"
if dashboard_dir.exists():
    app.mount("/static", StaticFiles(directory=str(dashboard_dir)), name="static")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        ico_file = dashboard_dir / "favicon.ico"
        if ico_file.exists():
            from fastapi.responses import FileResponse
            return FileResponse(str(ico_file), media_type="image/x-icon")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/", response_class=HTMLResponse)
    async def get_dashboard():
        index_file = dashboard_dir / "index.html"
        if index_file.exists():
            return index_file.read_text(encoding="utf-8")
        return "<h1>FreshGate Gateway</h1><p>Dashboard index.html not found</p>"


@app.get("/sw.js")
@app.get("/service-worker.js")
async def service_worker_noop():
    """Handle stale service worker requests from browser cache cleanly by unregistering."""
    content = (
        "// Self-unregistering service worker for FreshGate\n"
        "self.addEventListener('install', () => { self.skipWaiting(); });\n"
        "self.addEventListener('activate', (event) => {\n"
        "  event.waitUntil(\n"
        "    self.registration.unregister().then(() => self.clients.matchAll()).then((clients) => {\n"
        "      clients.forEach((client) => client.navigate(client.url));\n"
        "    })\n"
        "  );\n"
        "});\n"
    )
    return Response(
        content=content,
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )
