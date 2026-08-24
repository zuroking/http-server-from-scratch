"""HTTP parsing errors."""

from __future__ import annotations


class HttpError(Exception):
    """Base class for HTTP protocol errors that carry a status code."""

    status_code: int
    reason: str

    def __init__(self, status_code: int, reason: str, message: str | None = None) -> None:
        super().__init__(message or f"{status_code} {reason}")
        self.status_code = status_code
        self.reason = reason


class BadRequestError(HttpError):
    def __init__(self, message: str | None = None) -> None:
        super().__init__(400, "Bad Request", message)


class RequestTimeoutError(HttpError):
    def __init__(self, message: str | None = None) -> None:
        super().__init__(408, "Request Timeout", message)


class ContentTooLargeError(HttpError):
    def __init__(self, message: str | None = None) -> None:
        super().__init__(413, "Content Too Large", message)


class UriTooLongError(HttpError):
    def __init__(self, message: str | None = None) -> None:
        super().__init__(414, "URI Too Long", message)


class RequestHeaderFieldsTooLargeError(HttpError):
    def __init__(self, message: str | None = None) -> None:
        super().__init__(431, "Request Header Fields Too Large", message)


class NotFoundError(HttpError):
    def __init__(self, message: str | None = None) -> None:
        super().__init__(404, "Not Found", message)


class MethodNotAllowedError(HttpError):
    """405 with Allow header support."""

    allowed: tuple[str, ...]

    def __init__(
        self, message: str | None = None, *, allowed: list[str] | tuple[str, ...] | None = None
    ) -> None:
        super().__init__(405, "Method Not Allowed", message)
        self.allowed = tuple(allowed) if allowed is not None else ()

    @property
    def allow_header(self) -> str:
        return ", ".join(sorted(self.allowed))


class InternalServerError(HttpError):
    def __init__(self, message: str | None = None) -> None:
        super().__init__(500, "Internal Server Error", message)


class NotImplementedError(HttpError):  # noqa: A001
    def __init__(self, message: str | None = None) -> None:
        super().__init__(501, "Not Implemented", message)


class HttpVersionNotSupportedError(HttpError):
    def __init__(self, message: str | None = None) -> None:
        super().__init__(505, "HTTP Version Not Supported", message)
