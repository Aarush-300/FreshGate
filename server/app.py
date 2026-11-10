import os
import time
import json
import asyncio
import logging
from typing import Optional, Dict, Any, Tuple
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, Response, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import httpx
from dotenv import load_dotenv

from core.models import VerificationResult
from core.pipeline import FreshGatePipeline

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("freshgate")

BIFROST_UPSTREAM_URL = os.getenv("BIFROST_UPSTREAM_URL", "http://127.0.0.1:8080").rstrip("/")
FRESHGATE_TIMEOUT = float(os.getenv("FRESHGATE_TIMEOUT", "4.0"))
FRESHGATE_FAIL_OPEN = os.getenv("FRESHGATE_FAIL_OPEN", "true").lower() == "true"
FRESHGATE_ASYNC_MODE = os.getenv("FRESHGATE_ASYNC_MODE", "false").lower() == "true"

app = FastAPI(
    title="FreshGate AI Gateway Plugin & Proxy",
    description="Drop-in fact-freshness verification layer for Bifrost and OpenAI-compatible gateways",
    version="1.0.0",
)

pipeline = FreshGatePipeline()


class VerifyRequest(BaseModel):
    answer: str


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
        logger.error("Async verification background task failed: %s", e)


@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "freshgate", "upstream": BIFROST_UPSTREAM_URL}


@app.post("/verify", response_model=VerificationResult)
async def verify_answer(req: VerifyRequest):
    """Direct verification endpoint for any answer text."""
    try:
        return await pipeline.verify(req.answer)
    except Exception as e:
        logger.error("Direct verification failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/proxy/v1/chat/completions")
@app.post("/v1/chat/completions")
async def proxy_chat_completions(request: Request, background_tasks: BackgroundTasks):
    """Drop-in OpenAI chat completions proxy in front of Bifrost.

    Intercepts the response, verifies facts, attaches X-FreshGate headers and
    'freshgate' metadata, and fails open safely on any timeout or exception.
    """
    body_bytes = await request.body()
    headers = dict(request.headers)

    # 1. Forward request to Bifrost upstream
    try:
        status_code, upstream_data, upstream_headers = await forward_to_upstream(
            method=request.method,
            path="/v1/chat/completions",
            headers=headers,
            body=body_bytes,
        )
    except Exception as e:
        logger.error("Failed to forward request to Bifrost upstream at %s: %s", BIFROST_UPSTREAM_URL, e)
        raise HTTPException(status_code=502, detail=f"Bad Gateway: Unable to reach Bifrost at {BIFROST_UPSTREAM_URL}")

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

    # 3. Check for async mode
    is_async = (
        FRESHGATE_ASYNC_MODE
        or request.query_params.get("freshgate_async") == "true"
        or request.headers.get("X-FreshGate-Async") == "true"
    )

    if is_async:
        background_tasks.add_task(run_async_verification, assistant_content)
        resp_headers = {"X-FreshGate-Mode": "async"}
        return JSONResponse(status_code=200, content=upstream_data, headers=resp_headers)

    # 4. Run FreshGate verification with fail-open timeout
    t0 = time.perf_counter()
    try:
        verification: VerificationResult = await asyncio.wait_for(
            pipeline.verify(assistant_content),
            timeout=FRESHGATE_TIMEOUT,
        )
        latency_ms = int((time.perf_counter() - t0) * 1000)

        # Attach FreshGate metadata block
        upstream_data["freshgate"] = verification.model_dump()

        headers_to_add = {
            "X-FreshGate-Score": str(verification.summary.avg_score),
            "X-FreshGate-Stale-Count": str(verification.summary.stale),
            "X-FreshGate-Total-Claims": str(verification.summary.total),
            "X-FreshGate-Checked-Claims": str(verification.summary.checked),
            "X-FreshGate-Latency-Ms": str(latency_ms),
            "X-FreshGate-Status": "verified",
        }
        return JSONResponse(status_code=200, content=upstream_data, headers=headers_to_add)

    except (asyncio.TimeoutError, Exception) as e:
        logger.warning("FreshGate verification failed/timed out, failing open: %s", e)
        headers_to_add = {
            "X-FreshGate-Status": "fail-open",
            "X-FreshGate-Error": str(e),
        }
        return JSONResponse(status_code=200, content=upstream_data, headers=headers_to_add)


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


@app.post("/api/cache/clear")
async def clear_cache():
    """Clear SQLite cache."""
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
