"""
Request logging and correlation ID middleware.

Attaches a unique correlation ID to every request for distributed tracing.
Logs request start, end, duration, and status code.
"""

from __future__ import annotations

import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

log = structlog.get_logger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every HTTP request with correlation ID, path, method, status, and duration."""

    async def dispatch(self, request: Request, call_next: object) -> Response:
        correlation_id = str(uuid.uuid4())
        request.state.correlation_id = correlation_id

        # Bind correlation ID to structlog context for this request
        structlog.contextvars.bind_contextvars(
            correlation_id=correlation_id,
            http_method=request.method,
            http_path=request.url.path,
        )

        start_time = time.perf_counter()
        log.info("http_request_start")

        try:
            response: Response = await call_next(request)  # type: ignore
        except Exception as exc:
            log.exception("http_request_unhandled_exception")
            raise
        finally:
            duration_ms = (time.perf_counter() - start_time) * 1000
            status_code = getattr(response, "status_code", 500) if "response" in dir() else 500
            log.info(
                "http_request_complete",
                status_code=status_code,
                duration_ms=round(duration_ms, 2),
            )
            structlog.contextvars.unbind_contextvars("correlation_id", "http_method", "http_path")

        # Add correlation ID to response headers
        response.headers["X-Correlation-ID"] = correlation_id
        return response
