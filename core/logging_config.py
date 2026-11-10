"""Structured JSON Logging & Correlation ID Tracking for FreshGate (Phase 6)."""

import json
import logging
import time
import uuid
from typing import Optional
from contextvars import ContextVar
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

correlation_id_ctx: ContextVar[str] = ContextVar("correlation_id", default="")


def get_correlation_id() -> str:
    return correlation_id_ctx.get()


class JSONLogFormatter(logging.Formatter):
    """Formats log records as structured, machine-parseable JSON."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": getattr(record, "correlation_id", get_correlation_id()),
        }

        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry)


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Attaches a unique Correlation ID to every incoming HTTP request."""

    async def dispatch(self, request: Request, call_next) -> Response:
        corr_id = (
            request.headers.get("X-Correlation-ID")
            or request.headers.get("X-Request-ID")
            or str(uuid.uuid4())
        )
        token = correlation_id_ctx.set(corr_id)
        request.state.correlation_id = corr_id

        try:
            response = await call_next(request)
            response.headers["X-Correlation-ID"] = corr_id
            return response
        finally:
            correlation_id_ctx.reset(token)
