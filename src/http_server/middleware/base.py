"""Middleware base (Phase 11)."""

from __future__ import annotations

from typing import Any, Callable, Awaitable

from http_server.http.request import Request
from http_server.http.response import Response

Handler = Callable[[Request], Response]
MiddlewareCallable = Callable[[Request, Handler], Response]


class Middleware:
    """Base middleware class.

    Subclass and override `__call__` to implement logic.
    """

    def __call__(self, request: Request, handler: Handler) -> Response:
        return handler(request)


def chain_middleware(middlewares: list[Middleware], handler: Handler) -> Handler:
    """Build handler chain: middlewares[0] -> ... -> handler."""

    def wrapped(request: Request) -> Response:
        # Build chain in reverse
        current: Handler = handler
        for mw in reversed(middlewares):
            # Capture current and mw
            nxt = current

            def make(req: Request, _mw=mw, _next=nxt) -> Response:  # type: ignore
                return _mw(req, _next)

            current = make
        return current(request)

    return wrapped
