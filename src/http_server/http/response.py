"""HTTP response representation and serialization."""

from __future__ import annotations

from dataclasses import dataclass, field

from http_server.http.headers import Headers

try:
    # Python 3.12+ has HTTPStatus; use for fallback reason phrases
    from http import HTTPStatus as _HTTPStatus
except ImportError:  # pragma: no cover
    _HTTPStatus = None  # type: ignore


_REASON_PHRASES: dict[int, str] = {
    100: "Continue",
    101: "Switching Protocols",
    102: "Processing",
    103: "Early Hints",
    200: "OK",
    201: "Created",
    202: "Accepted",
    203: "Non-Authoritative Information",
    204: "No Content",
    205: "Reset Content",
    206: "Partial Content",
    300: "Multiple Choices",
    301: "Moved Permanently",
    302: "Found",
    303: "See Other",
    304: "Not Modified",
    305: "Use Proxy",
    307: "Temporary Redirect",
    308: "Permanent Redirect",
    400: "Bad Request",
    401: "Unauthorized",
    402: "Payment Required",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    406: "Not Acceptable",
    407: "Proxy Authentication Required",
    408: "Request Timeout",
    409: "Conflict",
    410: "Gone",
    411: "Length Required",
    412: "Precondition Failed",
    413: "Content Too Large",
    414: "URI Too Long",
    415: "Unsupported Media Type",
    416: "Range Not Satisfiable",
    417: "Expectation Failed",
    418: "Misdirected Request",
    421: "Misdirected Request",
    422: "Unprocessable Entity",
    426: "Upgrade Required",
    428: "Precondition Required",
    429: "Too Many Requests",
    431: "Request Header Fields Too Large",
    500: "Internal Server Error",
    501: "Not Implemented",
    502: "Bad Gateway",
    503: "Service Unavailable",
    504: "Gateway Timeout",
    505: "HTTP Version Not Supported",
    506: "Variant Also Negotiates",
    507: "Insufficient Storage",
    508: "Loop Detected",
    511: "Network Authentication Required",
}

_ALLOWED_HTTP_VERSIONS = {"HTTP/1.0", "HTTP/1.1"}
_STATUS_CODES_NO_BODY = {204, 304}


def _get_reason(status_code: int) -> str:
    if status_code in _REASON_PHRASES:
        return _REASON_PHRASES[status_code]
    if _HTTPStatus is not None:
        try:
            return _HTTPStatus(status_code).phrase
        except ValueError:
            pass
    return "Unknown"


def _validate_status_code(code: int) -> None:
    if not isinstance(code, int):
        raise ValueError(f"status_code must be int, got {type(code)}")
    if not 100 <= code <= 599:
        raise ValueError(f"Invalid status code {code}: must be 100-599")


def _validate_http_version(version: str) -> None:
    if "\r" in version or "\n" in version:
        raise ValueError(f"Invalid HTTP version (CRLF): {version!r}")
    if version not in _ALLOWED_HTTP_VERSIONS:
        raise ValueError(f"Unsupported HTTP version: {version!r}")


def _validate_header_field(name: str, value: str) -> None:
    # Header names already validated by Headers, but double-check for injection
    if "\r" in name or "\n" in name:
        raise ValueError(f"Header name injection: {name!r}")
    if "\r" in value or "\n" in value:
        raise ValueError(f"Header value injection: {name}: {value!r}")
    # Also ensure no colon in name already checked, but keep
    # Ensure encodable as latin-1 (headers should be ascii/latin-1)
    try:
        name.encode("latin-1")
        value.encode("latin-1")
    except UnicodeEncodeError as exc:
        raise ValueError(f"Header not encodable as latin-1: {name}: {value!r}") from exc


@dataclass(slots=True)
class Response:
    """Structured HTTP response.

    Attributes:
        status_code: HTTP status code 100-599.
        headers: Case-insensitive headers. If Content-Length missing, serializer adds it.
        body: Raw body bytes.
        reason: Optional custom reason phrase. If None, derived from status_code.
    """

    status_code: int
    headers: Headers = field(default_factory=Headers)
    body: bytes = b""
    reason: str | None = None

    def __post_init__(self) -> None:
        _validate_status_code(self.status_code)
        if not isinstance(self.body, (bytes, bytearray)):
            raise TypeError(f"body must be bytes, got {type(self.body)}")
        # Normalize body to bytes (immutable)
        if isinstance(self.body, bytearray):
            self.body = bytes(self.body)
        if not isinstance(self.headers, Headers):
            if isinstance(self.headers, dict):
                self.headers = Headers(list(self.headers.items()))  # type: ignore[arg-type]
            else:
                raise TypeError("headers must be Headers instance")
        if self.reason is not None:
            if not isinstance(self.reason, str):
                raise TypeError("reason must be str")
            if "\r" in self.reason or "\n" in self.reason:
                raise ValueError(f"Invalid reason phrase (CRLF): {self.reason!r}")
            # Also validate reason encodable? allow ascii/latin-1
            try:
                self.reason.encode("latin-1")
            except UnicodeEncodeError as exc:
                raise ValueError(f"Reason not encodable: {self.reason!r}") from exc

    def serialize(
        self, http_version: str = "HTTP/1.1", *, send_body: bool = True
    ) -> bytes:
        """Serialize response to raw HTTP bytes.

        Args:
            http_version: HTTP version string, default HTTP/1.1. Only HTTP/1.0 and HTTP/1.1 allowed.
            send_body: If False, body is not transmitted (HEAD semantics) but Content-Length reflects original body length.

        Returns:
            Raw HTTP response bytes with CRLF line endings.

        Raises:
            ValueError: on invalid status, version, header injection, or Content-Length mismatch.
        """
        _validate_status_code(self.status_code)
        _validate_http_version(http_version)

        reason = self.reason if self.reason is not None else _get_reason(self.status_code)
        if "\r" in reason or "\n" in reason:
            raise ValueError(f"Invalid reason phrase (CRLF): {reason!r}")

        # Validate headers for injection before any auto logic
        for name, value in self.headers.items():
            _validate_header_field(name, value)

        # Determine body suppression semantics
        is_status_no_body = (100 <= self.status_code < 200) or self.status_code in _STATUS_CODES_NO_BODY

        # Original body length (bytes, not chars)
        actual_body_len = len(self.body)

        # Decide effective body and expected Content-Length
        if is_status_no_body:
            # MUST NOT include body per RFC
            effective_body = b""
            expected_cl = 0
        elif not send_body:
            # HEAD: same headers as GET, no body transmitted
            effective_body = b""
            expected_cl = actual_body_len
        else:
            effective_body = self.body
            expected_cl = actual_body_len

        # Content-Length handling (case-insensitive)
        explicit_cl_raw = self.headers.get("content-length")
        # Create a copy of headers for serialization to avoid mutating original
        headers_copy = Headers(list(self.headers.items()))

        if explicit_cl_raw is not None:
            # Validate numeric and mismatch
            stripped = explicit_cl_raw.strip()
            if stripped == "":
                raise ValueError("Empty Content-Length header")
            if not stripped.isdigit():
                raise ValueError(f"Invalid Content-Length header value: {explicit_cl_raw!r}")
            explicit_int = int(stripped)
            if explicit_int != expected_cl:
                raise ValueError(
                    f"Content-Length mismatch: header {explicit_int} != expected {expected_cl} "
                    f"(status={self.status_code}, send_body={send_body})"
                )
            # Header already present and valid, keep it (preserve original casing)
            # No need to modify headers_copy
        else:
            # Auto-add Content-Length
            headers_copy.set("Content-Length", str(expected_cl))

        # Re-validate injected headers after auto-add (auto header is safe)
        for name, value in headers_copy.items():
            _validate_header_field(name, value)

        # Build status line
        status_line = f"{http_version} {self.status_code} {reason}\r\n"

        # Build headers block
        # Each header: Name: value\r\n  (preserve original casing from headers_copy)
        header_lines = ""
        for name, value in headers_copy.items():
            header_lines += f"{name}: {value}\r\n"

        # Final CRLF + body
        # Use latin-1 for status/headers (they are ascii); body is already bytes
        head_bytes = (status_line + header_lines + "\r\n").encode("latin-1")
        return head_bytes + effective_body

    # Alias for convenience
    def to_bytes(self, http_version: str = "HTTP/1.1", *, send_body: bool = True) -> bytes:
        return self.serialize(http_version=http_version, send_body=send_body)
