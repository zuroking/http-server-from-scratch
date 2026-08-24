"""Structured HTTP Request."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from http_server.http.headers import Headers


@dataclass(slots=True)
class Request:
    """Structured HTTP request.

    Attributes:
        method: HTTP method (e.g. "GET").
        target: Raw request target as sent (e.g. "/path?query=1").
        path: URL path without query string (e.g. "/path").
        query_string: Raw query string without leading "?" (e.g. "query=1").
        http_version: HTTP version string (e.g. "HTTP/1.1").
        headers: Case-insensitive headers.
        body: Raw body bytes.
        client_address: Optional client address tuple set by connection layer.
        route_params: Extracted path parameters set by router.
        context: Arbitrary per-request context dict for middleware.
    """

    method: str
    target: str
    path: str
    query_string: str
    http_version: str
    headers: Headers
    body: bytes = b""

    # Extensible fields for future phases — not used by parser
    client_address: tuple[str, int] | None = None
    route_params: dict[str, str] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.headers, Headers):
            # Allow dict-like initialisation convenience
            if isinstance(self.headers, dict):
                self.headers = Headers(list(self.headers.items()))  # type: ignore[arg-type]
            else:
                raise TypeError("headers must be Headers instance")

    @property
    def query(self) -> str:
        """Alias for query_string."""
        return self.query_string
