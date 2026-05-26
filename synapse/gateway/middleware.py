"""HTTP middleware for the gateway.

`RequestLogMiddleware` logs every request with method, path, status, and
elapsed time. Sensitive headers (authorization, x-synapse-*-key) are never
emitted into log output.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware


class RequestLogMiddleware(BaseHTTPMiddleware):
    """Log each request's outcome at INFO level."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.exception(
                "{method} {path} → exception ({elapsed:.1f} ms)",
                method=request.method,
                path=request.url.path,
                elapsed=elapsed_ms,
            )
            raise
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "{method} {path} → {status} ({elapsed:.1f} ms)",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            elapsed=elapsed_ms,
        )
        return response
