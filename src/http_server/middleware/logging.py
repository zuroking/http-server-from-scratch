"""Logging middleware (Phase 11)."""

from __future__ import annotations

import logging
import time

from http_server.http.request import Request
from http_server.http.response import Response
from http_server.middleware.base import Middleware

logger = logging.getLogger("http_server.access")


class LoggingMiddleware(Middleware):
    """Simple access logging middleware."""

    def __call__(self, request: Request, handler):  # type: ignore[no-untyped-def]
        start = time.monotonic()
        try:
            response = handler(request)
        except Exception:
            logger.exception("Handler error for %s %s", request.method, request.path)
            raise
        duration = (time.monotonic() - start) * 1000
        # Log without body or sensitive headers
        client = request.client_address[0] if request.client_address else "-"
        logger.info(
            '%s "%s %s %s" %s %s %.2fms',
            client,
            request.method,
            request.path,
            request.http_version,
            response.status_code,
            len(response.body),
            duration,
        )
        return response
