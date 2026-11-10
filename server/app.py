import os
import time
import json
import asyncio
import logging
from typing import Optional, Dict, Any, Tuple
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, Response, BackgroundTasks, Depends, status
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import httpx
from dotenv import load_dotenv

from core.models import VerificationResult
from core.pipeline import FreshGatePipeline
from core.security import (
    SecurityHeadersMiddleware,
    RequestBodySizeLimitMiddleware,
    RateLimitMiddleware,
    rate_limiter,
    verify_admin_key,
    sanitize_error_detail,
)
from core.metrics import metrics
from core.logging_config import CorrelationIdMiddleware, get_correlation_id
from core.audit import audit_store
from core.circuit_breaker import circuit_breaker

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("freshgate")

BIFROST_UPSTREAM_URL = os.getenv("BIFROST_UPSTREAM_URL", "http://127.0.0.1:8080").rstrip("/")
FRESHGATE_TIMEOUT = float(os.getenv("FRESHGATE_TIMEOUT", "4.0"))
FRESHGATE_FAIL_OPEN = os.getenv("FRESHGATE_FAIL_OPEN", "true").lower() == "true"
FRESHGATE_ASYNC_MODE = os.getenv("FRESHGATE_ASYNC_MODE", "false").lower() == "true"
FRESHGATE_MODE = os.getenv("FRESHGATE_MODE", "enforce").lower()  # shadow, warn, enforce
FRESHGATE_MIN_SCORE = int(os.getenv("FRESHGATE_MIN_SCORE", "0"))
FRESHGATE_BLOCK_STALE = os.getenv("FRESHGATE_BLOCK_STALE", "false").lower() == "true"
MAX_BODY_SIZE = int(os.getenv("FRESHGATE_MAX_BODY_SIZE_BYTES", "1048576"))

app = FastAPI(
    title="FreshGate AI Gateway Middleware",
    description="Production-grade fact-freshness verification layer for AI Gateways with Tavily & LLM Judge",
    version="1.1.0",
)

# Register Security & Observability Middlewares
app.add_middleware(CorrelationIdMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestBodySizeLimitMiddleware, max_body_bytes=MAX_BODY_SIZE)
app.add_middleware(RateLimitMiddleware, limiter=rate_limiter)

pipeline = FreshGatePipeline()


class VerifyRequest(BaseModel):
    answer: str = Field(..., min_length=1, max_length=50000, description="Assistant answer text to verify")


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
        logger.info("Streaming verification for %s finished in %.1fms", completion_id, duration_ms)
    except Exception as e:
        safe_err = sanitize_error_detail(e)
        stream_verifications[completion_id] = {
            "status": "failed",
            "error": safe_err,
            "timestamp": time.time(),
        }
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
async def verify_answer(req: VerifyRequest):
    """Direct verification endpoint for any answer text."""
    try:
        return await pipeline.verify(req.answer)
    except Exception as e:
        safe_msg = sanitize_error_detail(e)
        logger.error("Direct verification failed: %s", safe_msg)
        raise HTTPException(status_code=500, detail=safe_msg)


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
            "X-FreshGate-Score": str(verification.summary.avg_score),
            "X-FreshGate-Stale-Count": str(verification.summary.stale),
            "X-FreshGate-Total-Claims": str(verification.summary.total),
            "X-FreshGate-Checked-Claims": str(verification.summary.checked),
            "X-FreshGate-Latency-Ms": str(latency_ms),
            "X-FreshGate-Status": "verified",
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
            status="verified",
        )

        # Policy enforcement check
        if active_mode == "enforce" and FRESHGATE_BLOCK_STALE and (verification.summary.stale > 0 or verification.summary.avg_score < FRESHGATE_MIN_SCORE):
            logger.warning("FreshGate policy violation: stale count=%d, avg_score=%d", verification.summary.stale, verification.summary.avg_score)
            return JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                content={
                    "error": "FreshGate Policy Violation: Response contains outdated factual claims.",
                    "freshgate": verification.model_dump(),
                },
                headers={
                    "X-FreshGate-Policy": "violated",
                    "X-FreshGate-Score": str(verification.summary.avg_score),
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
    dataset_path = Path(__file__).resolve().parent.parent / "bench" / "dataset.json"

    results_md = results_path.read_text(encoding="utf-8") if results_path.exists() else "No results yet."
    dataset = json.loads(dataset_path.read_text(encoding="utf-8")) if dataset_path.exists() else []

    return {
        "results_md": results_md,
        "total_items": len(dataset),
        "stale_items": len([d for d in dataset if d.get("is_stale")]),
        "control_items": len([d for d in dataset if not d.get("is_stale")]),
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

    @app.get("/", response_class=HTMLResponse)
    async def get_dashboard():
        index_file = dashboard_dir / "index.html"
        if index_file.exists():
            return index_file.read_text(encoding="utf-8")
        return "<h1>FreshGate Gateway</h1><p>Dashboard index.html not found</p>"
