import os
import time
import json
import asyncio
import logging
import sqlite3
import re
import threading
import uuid
import codecs
from typing import Optional, Dict, Any, Tuple
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, Response, BackgroundTasks, Depends, status, Query, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
import httpx
from dotenv import load_dotenv
from pymongo.errors import DuplicateKeyError
from core.mongo import ensure_indexes, mongo_is_required, ping as mongo_ping
from core.audit_helpers import write_audit, write_audit_failure
from server.routes.profile import router as profile_router
from server.pipeline_factory import get_shared_pipeline, build_user_pipeline
from server.netguard import validate_base_url
from server.users import (
    SQLiteUserLimitError,
    users_collection,
    serializer,
    password_hash,
    check_password,
    DUMMY_PASSWORD_HASH,
    current_user,
    optional_current_user,
    public_settings,
    encrypt_secret,
    decrypt_secret,
    load_credentials,
)

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
from core.persistence import get_feature_store
from core.security import (
    SecurityHeadersMiddleware,
    RequestBodySizeLimitMiddleware,
    RateLimitMiddleware,
    rate_limiter,
    verify_api_key,
    require_api_key_strict,
    verify_admin_key,
    sanitize_error_detail,
    validate_upload,
)
from core.metrics import metrics
from server.routes.policies import router as policies_router
from server.routes.watchtower import router as watchtower_router, watchtower_service
from server.routes.replay import history_router, replay_router
from server.routes.providers import router as providers_router, SUPPORTED_PROVIDER_IDS
from server.routes.export import router as export_router
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

_mongo_indexes_ready = False


async def _ensure_indexes_with_backoff():
    global _mongo_indexes_ready
    delay = 1.0
    while not _mongo_indexes_ready:
        try:
            await asyncio.to_thread(ensure_indexes)
            _mongo_indexes_ready = True
            logger.info("MongoDB indexes successfully created/verified.")
            break
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("MongoDB index setup failed (retrying in %.1fs): %s", delay, exc)
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                break
            delay = min(delay * 2, 30.0)


async def _periodic_cache_cleanup():
    try:
        # Run once at startup
        await asyncio.to_thread(pipeline.cache.cleanup_expired)
        while True:
            await asyncio.sleep(1800)  # 30 minutes
            await asyncio.to_thread(pipeline.cache.cleanup_expired)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.warning("Periodic cache cleanup sweep error: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI application lifecycle managing connection pools and startup initialization."""
    current_cfg = get_config()
    logger.info("Starting FreshGate Gateway (mode=%s, upstream=%s)", current_cfg.freshgate_mode, current_cfg.bifrost_upstream_url)
    _ = get_llm_manager()
    cache_task = asyncio.create_task(_periodic_cache_cleanup())
    use_mongo = mongo_is_required()
    if use_mongo:
        # Retry index setup in the background, but don't take down local startup if
        # Mongo is temporarily unavailable. Feature calls report 503 while offline.
        index_task = asyncio.create_task(_ensure_indexes_with_backoff())
    else:
        index_task = None
        logger.info("Using local storage without MongoDB; MongoDB startup checks are disabled.")
    await watchtower_service.start()
    yield
    logger.info("Shutting down FreshGate Gateway connection pools...")
    cache_task.cancel()
    if index_task is not None:
        index_task.cancel()
    try:
        await cache_task
    except (asyncio.CancelledError, Exception):
        pass
    if index_task is not None:
        try:
            await index_task
        except (asyncio.CancelledError, Exception):
            pass
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
app.add_middleware(
    RequestBodySizeLimitMiddleware,
    max_body_bytes=MAX_BODY_SIZE,
    path_limits={
        # Multipart form boundaries and per-part headers add overhead to the file itself.
        "/verify/upload": (cfg.max_upload_size_bytes * 4 + 2) // 3 + 1024 * 1024,
    },
)
app.add_middleware(RateLimitMiddleware, limiter=rate_limiter)
app.include_router(policies_router)
app.include_router(watchtower_router)
app.include_router(history_router)
app.include_router(replay_router)
app.include_router(providers_router)
app.include_router(profile_router)
app.include_router(export_router)

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
class AuthBody(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=10, max_length=200)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not EMAIL_REGEX.match(v):
            raise ValueError("Enter a valid email address.")
        return v


_login_attempts: dict[str, list[float]] = {}
_login_lock = threading.Lock()


def _prune_login_attempts_if_needed(cutoff: float) -> None:
    if len(_login_attempts) > 10000:
        stale_keys = [k for k, ts in _login_attempts.items() if not ts or max(ts) <= cutoff]
        for k in stale_keys:
            _login_attempts.pop(k, None)


def _check_login_attempts(key: str, max_attempts: int = 10) -> None:
    now = time.time()
    cutoff = now - 900  # 15 minutes
    with _login_lock:
        _prune_login_attempts_if_needed(cutoff)
        timestamps = [t for t in _login_attempts.get(key, []) if t > cutoff]
        if len(timestamps) >= max_attempts:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Too many attempts. Try again later.",
            )
        _login_attempts[key] = timestamps


def _record_failed_login(key: str) -> None:
    now = time.time()
    cutoff = now - 900
    with _login_lock:
        _prune_login_attempts_if_needed(cutoff)
        timestamps = [t for t in _login_attempts.get(key, []) if t > cutoff]
        timestamps.append(now)
        _login_attempts[key] = timestamps


def _clear_login_attempts(key: str) -> None:
    with _login_lock:
        _login_attempts.pop(key, None)


def is_cookie_secure() -> bool:
    if os.getenv("FRESHGATE_ENV", "development").lower() == "production":
        return True
    return os.getenv("COOKIE_SECURE", "false").lower() == "true"


@app.post("/api/auth/register", status_code=status.HTTP_201_CREATED)
async def register(body: AuthBody, response: Response):
    signer = serializer()
    email = body.email
    uid = uuid.uuid4().hex
    pw_hash = await asyncio.to_thread(password_hash, body.password)
    try:
        existing = await asyncio.to_thread(users_collection().find_one, {"email": email})
        if existing:
            raise HTTPException(status.HTTP_409_CONFLICT, "Email is already registered.")
        await asyncio.to_thread(
            users_collection().insert_one,
            {
                "_id": uid,
                "email": email,
                "password_hash": pw_hash,
                "settings": {},
                "created_at": time.time(),
            },
        )
    except SQLiteUserLimitError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except (DuplicateKeyError, sqlite3.IntegrityError):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email is already registered.")
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Registration database error: %s", exc)
        if mongo_is_required():
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "MongoDB is unavailable. Start MongoDB or configure MONGODB_URI, then retry.",
            ) from exc
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Database error during registration.")
    token = signer.dumps(uid)
    response.set_cookie(
        "freshgate_session",
        token,
        httponly=True,
        samesite="lax",
        secure=is_cookie_secure(),
        max_age=2592000,
        path="/",
    )
    return {"email": email}


@app.post("/api/auth/login")
async def login(body: AuthBody, request: Request, response: Response):
    client_ip = request.client.host if request.client else "unknown"
    rate_key = f"{client_ip}:{body.email}"
    ip_key = f"ip:{client_ip}"
    _check_login_attempts(rate_key, max_attempts=10)
    _check_login_attempts(ip_key, max_attempts=30)

    email = body.email
    try:
        user = users_collection().find_one({"email": email})
    except Exception as exc:
        logger.error("Login database error: %s", exc)
        if mongo_is_required():
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "MongoDB is unavailable. Start MongoDB or configure MONGODB_URI, then retry.",
            ) from exc
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Database connection error.")

    if user:
        is_valid = await asyncio.to_thread(check_password, body.password, user.get("password_hash", ""))
    else:
        await asyncio.to_thread(check_password, body.password, DUMMY_PASSWORD_HASH)
        is_valid = False

    if not user or not is_valid:
        _record_failed_login(rate_key)
        _record_failed_login(ip_key)
        detail = (
            "No local account exists in SQLite. Create an account, or set FRESHGATE_STORE=mongo and MONGODB_URI to your original database, then restart FreshGate."
            if not user and not mongo_is_required()
            else "Email or password is incorrect."
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail)
    _clear_login_attempts(rate_key)

    token = serializer().dumps(user["_id"])
    response.set_cookie(
        "freshgate_session",
        token,
        httponly=True,
        samesite="lax",
        secure=is_cookie_secure(),
        max_age=2592000,
        path="/",
    )
    return {"email": user["email"]}


@app.post("/api/auth/logout")
async def logout(response: Response):
    response.delete_cookie("freshgate_session", path="/")
    return {"ok": True}


@app.get("/api/auth/me")
async def me(request: Request):
    user = current_user(request)
    return {"email": user["email"], "settings": public_settings(user)}

_shared_pipeline_instance = get_shared_pipeline()
pipeline = _shared_pipeline_instance


def get_request_pipeline() -> Optional[FreshGatePipeline]:
    """Dependency for injecting or overriding the verification pipeline (e.g. in tests)."""
    return None


class VerifyRequest(BaseModel):
    answer: str = Field(..., min_length=1, max_length=50000, description="Assistant answer text to verify")
    policy_id: Optional[str] = Field(default=None, min_length=1, max_length=80)
    provider: Optional[str] = Field(default=None, max_length=80)
    base_url: Optional[str] = Field(default=None, max_length=500)
    model: Optional[str] = Field(default=None, max_length=120)


def build_request_pipeline(
    openai_api_key: Optional[str] = None,
    tavily_api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
) -> FreshGatePipeline:
    creds = {
        "provider": "openai",
        "base_url": base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        "model": model or os.getenv("LLM_MODEL", "gpt-4o-mini"),
        "api_key": openai_api_key or "",
        "tavily_key": tavily_api_key or "",
        "keyless_ok": False,
    }
    return build_user_pipeline(creds, owner_id=None, base=pipeline)


def _filter_upstream_headers(headers: Dict[str, str]) -> Dict[str, str]:
    """Allow-list headers forwarded upstream to Bifrost."""
    allowed_exact = {"authorization", "content-type", "accept", "x-request-id"}
    forward_headers = {}
    for k, v in headers.items():
        k_lower = k.lower()
        if k_lower in allowed_exact or k_lower.startswith("x-bf-"):
            forward_headers[k] = v
    return forward_headers


async def forward_to_upstream(
    method: str,
    path: str,
    headers: Dict[str, str],
    body: bytes,
    timeout: float = 60.0,
) -> Tuple[int, Any, Dict[str, str]]:
    """Forward incoming request directly to upstream Bifrost gateway."""
    target_url = f"{BIFROST_UPSTREAM_URL}{path}"
    forward_headers = _filter_upstream_headers(headers)

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
    forward_headers = _filter_upstream_headers(headers)
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
        duration_ms = int((time.perf_counter() - t0) * 1000)
        stream_verifications[completion_id] = {
            "status": "completed",
            "result": result.model_dump(),
            "duration_ms": duration_ms,
            "timestamp": time.time(),
        }
        now = time.time()
        for old_k in [k for k, v in stream_verifications.items() if now - v.get("timestamp", 0) > 900]:
            stream_verifications.pop(old_k, None)
        if len(stream_verifications) > 500:
            oldest_keys = sorted(stream_verifications, key=lambda k: stream_verifications[k].get("timestamp", 0))[:50]
            for old_key in oldest_keys:
                stream_verifications.pop(old_key, None)
        logger.info("Streaming verification for %s finished in %dms", completion_id, duration_ms)
        await write_audit(
            result,
            text=text,
            source="proxy_stream",
            mode=None,
            latency_ms=duration_ms,
            audit_id=completion_id,
        )
    except Exception as e:
        safe_err = sanitize_error_detail(e)
        stream_verifications[completion_id] = {
            "status": "failed",
            "error": safe_err,
            "timestamp": time.time(),
        }
        now = time.time()
        for old_k in [k for k, v in stream_verifications.items() if now - v.get("timestamp", 0) > 900]:
            stream_verifications.pop(old_k, None)
        if len(stream_verifications) > 500:
            oldest_keys = sorted(stream_verifications, key=lambda k: stream_verifications[k].get("timestamp", 0))[:50]
            for old_key in oldest_keys:
                stream_verifications.pop(old_key, None)
        logger.error("Streaming verification for %s failed: %s", completion_id, safe_err)


async def run_async_verification(text: str, corr_id: Optional[str] = None, mode: Optional[str] = None):
    """Run verification asynchronously in background and log findings."""
    try:
        t0 = time.perf_counter()
        result = await pipeline.verify(text)
        duration_ms = int((time.perf_counter() - t0) * 1000)
        logger.info(
            "Async verification complete in %dms. Stale=%d, AvgScore=%s",
            duration_ms,
            result.summary.stale,
            str(result.summary.avg_score),
        )
        await write_audit(
            result,
            text=text,
            source="proxy_async",
            mode=mode,
            latency_ms=duration_ms,
            corr_id=corr_id,
        )
        return result
    except Exception as e:
        logger.error("Async verification background task failed safely: %s", sanitize_error_detail(e))


@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "service": "freshgate",
        "upstream": BIFROST_UPSTREAM_URL,
        "providers": ["openai", "groq", "gemini", "ollama", "openrouter", "custom"],
    }


@app.get("/health/live")
async def health_live():
    """Liveness probe for container orchestrators."""
    return {"status": "alive"}


@app.get("/health/ready")
async def health_ready():
    """Readiness probe checking database connectivity and cache availability."""
    db_status = "connected"
    if mongo_is_required():
        try:
            mongo_ok = await asyncio.to_thread(mongo_ping)
            if not mongo_ok:
                db_status = "mongo_unavailable"
        except Exception as e:
            logger.error("Readiness probe mongo check failed: %s", e)
            db_status = "mongo_unavailable"

    cache_status = "connected"
    try:
        _ = await asyncio.to_thread(pipeline.cache.get_stats)
    except Exception as e:
        logger.error("Readiness probe database check failed: %s", e)
        cache_status = "unavailable"
        if db_status == "connected":
            db_status = "unhealthy"

    is_ready = (db_status == "connected" and cache_status == "connected")
    status_code = status.HTTP_200_OK if is_ready else status.HTTP_503_SERVICE_UNAVAILABLE

    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ready" if is_ready else "not_ready",
            "database": db_status,
            "cache": cache_status,
            "indexes": _mongo_indexes_ready if mongo_is_required() else True,
            "upstream": BIFROST_UPSTREAM_URL,
        },
    )


def is_admin(request: Request) -> bool:
    try:
        return verify_admin_key(request)
    except HTTPException:
        return False


def require_user_or_admin(request: Request) -> dict[str, Any]:
    if is_admin(request):
        return {"type": "admin", "user": None}
    user = optional_current_user(request)
    if user:
        return {"type": "user", "user": user}
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Please log in or provide an admin key.")


@app.get("/metrics", dependencies=[Depends(require_user_or_admin)])
async def get_metrics():
    """Prometheus-compatible operational metrics endpoint."""
    return Response(
        content=metrics.to_prometheus_format(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@app.get("/api/audits/recent", dependencies=[Depends(verify_admin_key)])
async def get_recent_audits(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    source: Optional[str] = Query(None, max_length=32),
):
    """Retrieve recent immutable verification audit logs for compliance monitoring."""
    rows = await audit_store.aget_recent_audits(limit=limit, offset=offset, source=source)
    total = await asyncio.to_thread(audit_store.count_audits, source=source)
    persistent = not str(getattr(audit_store, "db_path", "")).startswith("/tmp")
    return {"audits": rows, "limit": limit, "offset": offset, "total": total, "persistent": persistent}


def _load_policy(req: VerifyRequest, user: Optional[dict] = None):
    if not req.policy_id:
        return None
    if user:
        from core.policies import get_feature_store as get_policies_feature_store
        store = get_policies_feature_store().for_owner(user["_id"])
        try:
            policy = get_policy(req.policy_id, store=store)
        except TypeError:
            policy = get_policy(req.policy_id)
    else:
        policy = get_policy(req.policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Policy not found")
    if not policy.enabled:
        raise HTTPException(status_code=409, detail="Selected policy is disabled")
    return policy


@app.post("/verify", response_model=VerificationResult)
async def verify_answer(
    req: VerifyRequest,
    request: Request,
    req_pipeline_dep: Optional[FreshGatePipeline] = Depends(get_request_pipeline),
):
    """Direct verification endpoint for any answer text."""
    user = optional_current_user(request)
    req_pipeline: FreshGatePipeline
    is_ephemeral = False

    if req_pipeline_dep is not None:
        req_pipeline = req_pipeline_dep
        is_ephemeral = False
    elif user:
        creds = load_credentials(user)
        if creds is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Finish your Profile first: pick a provider and model, add your API key (if required) and your Tavily key.",
            )
        req_pipeline = build_user_pipeline(creds, user["_id"], base=pipeline)
        is_ephemeral = True
    else:
        header_provider = request.headers.get("X-LLM-Provider") or getattr(req, "provider", None)
        llm_key = (
            request.headers.get("X-LLM-API-Key")
            or request.headers.get("X-OpenAI-API-Key")
            or request.headers.get("X-OpenAI-Key")
        )
        tavily_key = (
            request.headers.get("X-Tavily-API-Key")
            or request.headers.get("X-Tavily-Key")
        )
        header_base_url = request.headers.get("X-LLM-Base-URL") or getattr(req, "base_url", None)
        header_model = request.headers.get("X-LLM-Model") or getattr(req, "model", None)

        if header_base_url:
            try:
                header_base_url = validate_base_url(header_base_url)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))

        if header_provider is not None:
            if header_provider not in SUPPORTED_PROVIDER_IDS:
                raise HTTPException(status_code=400, detail="Choose a supported AI provider.")
            if header_provider != "ollama" and (not llm_key or not is_valid_credential(llm_key)):
                raise HTTPException(status_code=400, detail="The selected AI provider API key is invalid or missing.")

        if not llm_key and header_provider != "ollama" and not tavily_key:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Please log in.")

        req_pipeline = build_request_pipeline(
            llm_key,
            tavily_key,
            header_base_url,
            header_model,
        )
        is_ephemeral = True

    policy = _load_policy(req, user)
    try:
        t0 = time.perf_counter()
        result = await req_pipeline.verify(
            req.answer,
            single_flight_scope=getattr(request.state, "correlation_id", None),
            policy=policy,
            owner_id=(user["_id"] if user else None),
        )
        latency_ms = int((time.perf_counter() - t0) * 1000)
        await write_audit(
            result,
            text=req.answer,
            source="verify",
            mode=None,
            latency_ms=latency_ms,
            corr_id=getattr(request.state, "correlation_id", None),
            owner_id=(user["_id"] if user else None),
        )
        return result
    except Exception as e:
        safe_msg = sanitize_error_detail(e)
        logger.error("Direct verification failed: %s", safe_msg)
        await write_audit_failure(
            req.answer,
            source="verify",
            mode=None,
            reason=safe_msg,
            corr_id=getattr(request.state, "correlation_id", None),
            owner_id=(user["_id"] if user else None),
        )
        raise HTTPException(status_code=500, detail=safe_msg)
    finally:
        if is_ephemeral and hasattr(req_pipeline, "verifier") and hasattr(req_pipeline.verifier, "tavily_client"):
            await req_pipeline.verifier.tavily_client.aclose()


@app.post("/verify/upload", response_model=VerificationResult)
async def verify_upload(
    request: Request,
    req_pipeline_dep: Optional[FreshGatePipeline] = Depends(get_request_pipeline),
):
    """
    Phase 7: Direct verification endpoint with file upload support.
    Supports multipart/form-data and application/json.
    Extracts text in-memory from .pdf, .docx, .txt, .md, .json, .csv files,
    combines with user answer text, verifies, and discards all file data without saving to disk.
    """
    content_type = request.headers.get("content-type", "").lower()
    answer_text = ""
    file_bytes = b""
    filename = ""
    policy_id = None
    provider = None
    base_url = None
    model = None

    if "multipart/form-data" in content_type:
        body = await request.body()
        import email
        from email import policy as email_policy
        ct_header = f"Content-Type: {request.headers.get('content-type')}\r\n\r\n".encode("utf-8")
        msg = email.message_from_bytes(ct_header + body, policy=email_policy.default)
        if msg.is_multipart():
            for part in msg.iter_parts():
                name = part.get_param("name", header="content-disposition")
                part_filename = part.get_filename()
                if part_filename:
                    filename = part_filename
                    file_bytes = part.get_payload(decode=True) or b""
                elif name == "answer":
                    content = part.get_content()
                    answer_text = content if isinstance(content, str) else content.decode("utf-8", errors="replace")
                elif name == "policy_id":
                    policy_id = str(part.get_content()).strip() or None
                elif name == "provider":
                    provider = str(part.get_content()).strip() or None
                elif name == "base_url":
                    base_url = str(part.get_content()).strip() or None
                elif name == "model":
                    model = str(part.get_content()).strip() or None
    elif "application/json" in content_type:
        data = await request.json()
        answer_text = data.get("answer", "")
        filename = data.get("filename", "")
        raw_content = data.get("file_content") or data.get("file_base64", "")
        if raw_content:
            import base64
            try:
                file_bytes = base64.b64decode(raw_content)
            except Exception:
                file_bytes = raw_content.encode("utf-8")
        policy_id = data.get("policy_id")
        provider = data.get("provider")
        base_url = data.get("base_url")
        model = data.get("model")
    else:
        body = await request.body()
        answer_text = body.decode("utf-8", errors="replace")

    # If a file was uploaded, validate and extract text
    extracted_file_text = ""
    if file_bytes and filename:
        is_valid, reason = validate_upload(
            filename,
            len(file_bytes),
            max_size=cfg.max_upload_size_bytes,
            allowed_types={
                extension.strip().lower()
                if extension.strip().startswith(".")
                else f".{extension.strip().lower()}"
                for extension in cfg.allowed_upload_types
                if extension.strip()
            },
        )
        if not is_valid:
            raise HTTPException(status_code=400, detail=reason)
        from core.extractor import extract_text_from_file_bytes
        extracted_file_text = extract_text_from_file_bytes(file_bytes, filename)

    combined_text = f"{answer_text.strip()}\n\n{extracted_file_text.strip()}".strip() if answer_text.strip() else extracted_file_text.strip()
    if not combined_text:
        raise HTTPException(status_code=400, detail="No content provided to verify. Submit answer text or a valid document.")

    verify_req = VerifyRequest(
        answer=combined_text,
        policy_id=policy_id,
        provider=provider,
        base_url=base_url,
        model=model,
    )

    user = optional_current_user(request)
    req_pipeline: FreshGatePipeline
    is_ephemeral = False

    if req_pipeline_dep is not None:
        req_pipeline = req_pipeline_dep
        is_ephemeral = False
    elif user:
        creds = load_credentials(user)
        if creds is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Finish your Profile first: pick a provider and model, add your API key (if required) and your Tavily key.",
            )
        req_pipeline = build_user_pipeline(creds, user["_id"], base=pipeline)
        is_ephemeral = True
    else:
        header_provider = request.headers.get("X-LLM-Provider") or provider
        llm_key = (
            request.headers.get("X-LLM-API-Key")
            or request.headers.get("X-OpenAI-API-Key")
            or request.headers.get("X-OpenAI-Key")
        )
        tavily_key = (
            request.headers.get("X-Tavily-API-Key")
            or request.headers.get("X-Tavily-Key")
        )
        header_base_url = request.headers.get("X-LLM-Base-URL") or base_url
        header_model = request.headers.get("X-LLM-Model") or model

        if header_base_url:
            try:
                header_base_url = validate_base_url(header_base_url)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))

        if header_provider is not None:
            if header_provider not in SUPPORTED_PROVIDER_IDS:
                raise HTTPException(status_code=400, detail="Choose a supported AI provider.")
            if header_provider != "ollama" and (not llm_key or not is_valid_credential(llm_key)):
                raise HTTPException(status_code=400, detail="The selected AI provider API key is invalid or missing.")

        if not llm_key and header_provider != "ollama" and not tavily_key:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Please log in.")

        req_pipeline = build_request_pipeline(
            llm_key,
            tavily_key,
            header_base_url,
            header_model,
        )
        is_ephemeral = True

    policy = _load_policy(verify_req, user)
    try:
        t0 = time.perf_counter()
        result = await req_pipeline.verify(
            combined_text,
            single_flight_scope=getattr(request.state, "correlation_id", None),
            policy=policy,
            owner_id=(user["_id"] if user else None),
        )
        latency_ms = int((time.perf_counter() - t0) * 1000)
        await write_audit(
            result,
            text=combined_text[:1000],
            source="upload",
            mode=None,
            latency_ms=latency_ms,
            corr_id=getattr(request.state, "correlation_id", None),
            owner_id=(user["_id"] if user else None),
        )
        return result
    except Exception as e:
        safe_msg = sanitize_error_detail(e)
        logger.error("Upload verification failed: %s", safe_msg)
        await write_audit_failure(
            combined_text[:1000],
            source="upload",
            mode=None,
            reason=safe_msg,
            corr_id=getattr(request.state, "correlation_id", None),
            owner_id=(user["_id"] if user else None),
        )
        raise HTTPException(status_code=500, detail=safe_msg)
    finally:
        if is_ephemeral and hasattr(req_pipeline, "verifier") and hasattr(req_pipeline.verifier, "tavily_client"):
            await req_pipeline.verifier.tavily_client.aclose()


def _consume_sse_line(line: str, state: dict) -> None:
    line = line.strip()
    if not line.startswith("data:"):
        return
    data = line[5:].strip()
    if not data or data == "[DONE]":
        return
    try:
        parsed = json.loads(data)
    except ValueError:
        return
    if not state.get("completion_id") and parsed.get("id"):
        state["completion_id"] = parsed["id"]
    choices = parsed.get("choices") or []
    if choices and isinstance(choices, list):
        piece = (choices[0].get("delta") or {}).get("content")
        if piece and state["chars"] < 50000:
            state["chunks"].append(piece)
            state["chars"] += len(piece)


@app.post("/proxy/v1/chat/completions", dependencies=[Depends(require_api_key_strict)])
@app.post("/v1/chat/completions", dependencies=[Depends(require_api_key_strict)])
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
            state = {"chunks": [], "chars": 0, "completion_id": None}
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            buffer = ""
            try:
                async for chunk in stream_resp.aiter_bytes():
                    yield chunk
                    try:
                        text_chunk = decoder.decode(chunk)
                        buffer += text_chunk
                        lines = buffer.split("\n")
                        buffer = lines.pop()
                        for line in lines:
                            _consume_sse_line(line, state)
                    except Exception:
                        pass
            finally:
                try:
                    remaining = decoder.decode(b"", final=True)
                    buffer += remaining
                    if buffer.strip():
                        for line in buffer.split("\n"):
                            _consume_sse_line(line, state)
                except Exception:
                    pass

                if hasattr(stream_resp, "aclose"):
                    await stream_resp.aclose()

                full_content = "".join(state["chunks"]).strip()
                if full_content and state["completion_id"]:
                    background_tasks.add_task(run_streaming_verification, state["completion_id"], full_content)

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
        corr_id = getattr(request.state, "correlation_id", "req-unknown")
        background_tasks.add_task(run_async_verification, assistant_content, corr_id=corr_id, mode=active_mode)
        resp_headers = {"X-FreshGate-Mode": active_mode}
        return JSONResponse(status_code=200, content=upstream_data, headers=resp_headers)

    # 3b. Circuit breaker check
    if not circuit_breaker.can_execute():
        logger.warning("Circuit breaker is OPEN. Immediate fail-open pass-through.")
        background_tasks.add_task(
            write_audit_failure,
            assistant_content,
            source="proxy",
            mode=active_mode,
            reason="circuit_breaker_tripped",
            corr_id=getattr(request.state, "correlation_id", "req-unknown"),
        )
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
        if getattr(getattr(verification, "summary", None), "status", None) in ("verification_failed", "extraction_failed"):
            circuit_breaker.record_failure()
        else:
            circuit_breaker.record_success()
    except (asyncio.TimeoutError, Exception) as e:
        circuit_breaker.record_failure()
        safe_msg = sanitize_error_detail(e)
        logger.warning("FreshGate verification failed/timed out, failing open: %s", safe_msg)
        duration_sec = time.perf_counter() - t0
        metrics.observe_latency(duration_sec)
        reason = "timeout" if isinstance(e, asyncio.TimeoutError) else "internal_error"
        background_tasks.add_task(
            write_audit_failure,
            assistant_content,
            source="proxy",
            mode=active_mode,
            reason=reason,
            corr_id=getattr(request.state, "correlation_id", "req-unknown"),
            latency_ms=int(duration_sec * 1000),
        )
        # Never leak raw stack traces or internal exception details into HTTP headers
        headers_to_add = {
            "X-FreshGate-Status": "fail-open",
            "X-FreshGate-Reason": reason,
        }
        return JSONResponse(status_code=200, content=upstream_data, headers=headers_to_add)

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
    upstream_id = str(upstream_data.get("id") or "upstream")
    audit_id = f"{upstream_id}:{uuid.uuid4().hex[:8]}"
    background_tasks.add_task(
        write_audit,
        verification,
        text=assistant_content,
        source="proxy",
        mode=active_mode,
        latency_ms=latency_ms,
        corr_id=corr_id,
        audit_id=audit_id,
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
            status_code=422,
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


@app.get("/api/stream/verification/{completion_id}", dependencies=[Depends(require_user_or_admin)])
async def get_stream_verification(completion_id: str):
    """Retrieve async fact-checking verification result for a completed SSE stream."""
    entry = stream_verifications.get(completion_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Verification not found or still in progress")
    return entry


@app.post("/api/plugin/verify", response_model=VerificationResult, dependencies=[Depends(require_api_key_strict)])
async def plugin_verify(req: VerifyRequest, request: Request):
    """Dedicated verification endpoint for the native Go plugin."""
    t0 = time.perf_counter()
    result = await pipeline.verify(req.answer)
    duration_ms = int((time.perf_counter() - t0) * 1000)
    await write_audit(
        result,
        text=req.answer,
        source="plugin",
        mode=None,
        latency_ms=duration_ms,
        corr_id=getattr(request.state, "correlation_id", None),
    )
    return result


@app.get("/api/provider/status")
async def get_provider_status(auth: dict[str, Any] = Depends(require_user_or_admin)):
    """Retrieve operational metrics, provider status, rate limit budgets, and circuit breaker health."""
    llm_stats = get_llm_manager().get_stats_summary()
    tavily_stats = getattr(pipeline.verifier.tavily_client, "get_stats_summary", lambda: {
        "configured": get_config().is_tavily_configured,
        "status": "configured" if get_config().is_tavily_configured else "missing_credentials",
    })()
    resp: dict[str, Any] = {
        "llm": llm_stats,
        "openai": llm_stats,
        "tavily": tavily_stats,
        "circuit_breakers": circuit_breaker.get_all_states(),
        "cache": await asyncio.to_thread(pipeline.cache.get_stats),
        "metrics": metrics.get_summary(),
    }
    if auth.get("type") == "admin":
        resp["config"] = get_config().get_safe_diagnostics()
    elif auth.get("user"):
        resp["config"] = public_settings(auth["user"])
    return resp


@app.get("/api/deployment/status", dependencies=[Depends(require_user_or_admin)])
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


@app.get("/api/benchmark", dependencies=[Depends(require_user_or_admin)])
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
    await asyncio.to_thread(pipeline.cache.clear)
    return {"status": "cleared"}


# Mount dashboard directory for static UI serving
dashboard_dir = Path(__file__).resolve().parent.parent / "dashboard" / "dist"
dashboard_public_dir = Path(__file__).resolve().parent.parent / "dashboard" / "public"
dashboard_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(dashboard_dir)), name="static")

DEFAULT_FAVICON_BYTES = (
    b"\x00\x00\x01\x00\x01\x00  \x00\x00\x01\x00 \x00\xe2\x00\x00\x00\x16\x00\x00\x00"
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00 \x00\x00\x00 \x08\x06\x00\x00\x00szz\xf4"
    b"\x00\x00\x00\xa9IDATx\xda\xed\x971\x0e\x80 \x0cE9\x91\xabg\xf0\xfewpvp\xd7\x15AI\xff\xff"
    b"4\xd4\x04\x12\xb6\xa6\xef\x11\xa5\xb4)\xfdm-\xdbz\xb5\xf6\x10\xa8\xab\x0c\x0b\x97%T\xb0$"
    b"\xd2\x1b\x0eIx\xc1\xcd\x12*\xe0<\xf6\xc7\x86\x04\x98\x84\xad\xd8\xafx\xe8\xe4\xd6\xa4oq"
    b"\x90\x04\"P&m\xc1\xdd\x04\xf2\xc4(\xbc\x12@\xbfk\t@\xe1\x95\x84\"\xc0\xc2\xcd\x02\x8c\x04\\"
    b"\x13\xd8;\xce\x82i\x01\xb6>\xb8\x08\xe4\x12rY\x9e\x02\xea\x7f\xd0\xe5U\x9c\x02\x8c\x84|"
    b"\xffU\tF\xa0kG\xe4\"0\xbc)\r\xd1\x96\x87\x18L\xc2\x8cf!\x86S\xcfu\x03xy\xb3\x9c\xe3\xb6&"
    b"\xd8\x00\x00\x00\x00IEND\xaeB`\x82"
)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    from fastapi.responses import FileResponse
    ico_dist = dashboard_dir / "favicon.ico"
    if ico_dist.exists():
        return FileResponse(str(ico_dist), media_type="image/x-icon")
    ico_public = dashboard_public_dir / "favicon.ico"
    if ico_public.exists():
        return FileResponse(str(ico_public), media_type="image/x-icon")
    return Response(content=DEFAULT_FAVICON_BYTES, media_type="image/x-icon", status_code=status.HTTP_200_OK)


@app.get("/", response_class=HTMLResponse)
async def get_dashboard():
    index_file = dashboard_dir / "index.html"
    if index_file.exists():
        return index_file.read_text(encoding="utf-8")
    return HTMLResponse(
        content="""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <title>FreshGate</title>
    <link rel="stylesheet" href="/static/assets/index.css" />
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/static/assets/index.js"></script>
  </body>
</html>""",
        status_code=200,
    )


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
