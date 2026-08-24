"""Configuration layer for http-server-from-scratch.

Stores network settings, parser limits and timeouts with safe defaults.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ServerConfig:
    """Typed server configuration.

    Attributes:
        host: Address to bind listening socket.
        port: Port to bind listening socket.
        max_request_line_size: Maximum size of request line in bytes.
        max_headers_size: Maximum total size of headers block in bytes.
        max_header_count: Maximum number of header fields.
        max_body_size: Maximum request body size in bytes (Content-Length).
        request_timeout: Timeout for receiving a complete request (seconds).
        keep_alive_timeout: Idle timeout for keep-alive connections (seconds).
    """

    host: str = "127.0.0.1"
    port: int = 8080

    max_request_line_size: int = 8192  # 8 KiB
    max_headers_size: int = 32768  # 32 KiB
    max_header_count: int = 100
    max_body_size: int = 1_048_576  # 1 MiB

    request_timeout: float = 5.0
    keep_alive_timeout: float = 5.0

    def __post_init__(self) -> None:
        if not isinstance(self.host, str) or not self.host:
            raise ValueError("host must be a non-empty string")
        if not 0 <= self.port <= 65535:
            raise ValueError("port must be in range 0..65535")
        if self.max_request_line_size <= 0:
            raise ValueError("max_request_line_size must be > 0")
        if self.max_headers_size <= 0:
            raise ValueError("max_headers_size must be > 0")
        if self.max_header_count <= 0:
            raise ValueError("max_header_count must be > 0")
        if self.max_body_size <= 0:
            raise ValueError("max_body_size must be > 0")
        if self.request_timeout <= 0:
            raise ValueError("request_timeout must be > 0")
        if self.keep_alive_timeout <= 0:
            raise ValueError("keep_alive_timeout must be > 0")
