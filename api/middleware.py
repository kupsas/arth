"""
HTTP middleware for request correlation and access-style logging.

Successful requests (2xx/3xx) are logged at DEBUG so they land in ``data/logs/arth.log``
but stay off the default INFO console. Client errors (4xx) log WARNING; server errors (5xx)
log ERROR — both visible on stdout when running under Docker/Uvicorn.

Health and lightweight polling endpoints are omitted from per-request logs to avoid noise.
"""

from __future__ import annotations

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Adds ``X-Request-Id`` and logs method, path, status, and duration."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id

        path = request.url.path
        skip_log = path == "/health" or path.startswith("/api/setup/status")

        if skip_log:
            response = await call_next(request)
            response.headers["X-Request-Id"] = request_id
            return response

        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        status = response.status_code

        response.headers["X-Request-Id"] = request_id

        line = (
            f"{request.method} {path} {status} {elapsed_ms:.1f}ms "
            f"request_id={request_id}"
        )
        if status >= 500:
            logger.error("%s", line)
        elif status >= 400:
            logger.warning("%s", line)
        else:
            logger.debug("%s", line)

        return response
