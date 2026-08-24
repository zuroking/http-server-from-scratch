"""Connection state machine for plain TCP (Phase 6).

Implements non-blocking connection lifecycle: READING -> PROCESSING -> WRITING -> KEEP_ALIVE -> READING,
with support for keep-alive, timeouts, leftover buffer preservation, and error handling.

Transport abstraction is used via PlainTransport (Phase 7), but for Phase 6 direct socket wrapping is allowed.
"""

from __future__ import annotations

import logging
import socket
import time
from enum import Enum, auto
from typing import Any, Callable

import ssl

from http_server.config import ServerConfig
from http_server.http.errors import HttpError, MethodNotAllowedError, NotFoundError
from http_server.http.headers import Headers
from http_server.http.parser import HttpParser, ParseResult
from http_server.http.request import Request
from http_server.http.response import Response
from http_server.middleware.base import Middleware, chain_middleware
from http_server.routing.router import Router
from http_server.transport.plain import PlainTransport

logger = logging.getLogger(__name__)


class ConnectionState(Enum):
    READING = auto()
    PROCESSING = auto()
    WRITING = auto()
    KEEP_ALIVE = auto()
    CLOSING = auto()
    CLOSED = auto()


def _should_keep_alive(request: Request | None, should_close: bool) -> bool:
    """Decide keep-alive per ARCHITECTURE 15.

    - If should_close True (protocol error) -> False
    - If request is None -> False
    - Checks Connection header and HTTP version.
    """
    if should_close:
        return False
    if request is None:
        return False
    conn_header = request.headers.get("connection")
    conn_val = conn_header.lower().strip() if isinstance(conn_header, str) else None

    http_version = request.http_version

    if http_version == "HTTP/1.1":
        # HTTP/1.1 defaults to keep-alive
        if conn_val == "close":
            return False
        return True  # includes keep-alive or no header
    elif http_version == "HTTP/1.0":
        # HTTP/1.0 defaults to close
        if conn_val == "keep-alive":
            return True
        return False
    else:
        return False


class Connection:
    """Per-connection state machine.

    Handles one client socket, its parser, request/response lifecycle, keep-alive and timeouts.
    Transport-agnostic via PlainTransport, but accepts raw socket for convenience.
    """

    def __init__(
        self,
        sock: socket.socket,
        addr: tuple[str, int],
        config: ServerConfig | None = None,
        router: Router | None = None,
        middlewares: list[Middleware] | None = None,
        *,
        transport: Any | None = None,
    ) -> None:
        self.addr = addr
        self.config: ServerConfig = config or ServerConfig()
        self.router: Router = router or Router()
        self.middlewares: list[Middleware] = middlewares or []

        if transport is not None:
            self.transport = transport  # expected to have recv/send/fileno/close
        else:
            self.transport = PlainTransport(sock)

        self.parser: HttpParser = HttpParser(self.config)
        self.state: ConnectionState = ConnectionState.READING
        self.last_activity: float = time.monotonic()

        self.request: Request | None = None
        self.response: Response | None = None

        self._output: bytes = b""
        self._output_offset: int = 0
        self._should_close: bool = False
        self._closed: bool = False

    # -- selector helpers --

    def fileno(self) -> int:
        return self.transport.fileno()

    def want_read(self) -> bool:
        if self._closed or self.state == ConnectionState.CLOSED:
            return False
        # We want read when expecting request data and not closed
        # Even during WRITING we keep read to buffer pipelined data for next request (but not process)
        # However to avoid busy, keep read during READING and KEEP_ALIVE
        return self.state in (ConnectionState.READING, ConnectionState.KEEP_ALIVE)

    def want_write(self) -> bool:
        if self._closed or self.state == ConnectionState.CLOSED:
            return False
        return self._output_offset < len(self._output)

    def is_closed(self) -> bool:
        return self._closed or self.state == ConnectionState.CLOSED

    # -- timeout --

    def is_expired(self, now: float | None = None) -> bool:
        if self._closed:
            return True
        now = now if now is not None else time.monotonic()
        elapsed = now - self.last_activity
        if self.state == ConnectionState.KEEP_ALIVE:
            return elapsed > self.config.keep_alive_timeout
        # READING, PROCESSING, WRITING share request_timeout
        if self.state in (ConnectionState.READING, ConnectionState.PROCESSING, ConnectionState.WRITING):
            return elapsed > self.config.request_timeout
        return False

    # -- lifecycle --

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self.state = ConnectionState.CLOSED
            try:
                self.transport.close()
            except Exception:
                pass
            logger.debug("Connection closed %s", self.addr)

    def handle_read(self) -> None:
        """Called when socket is readable (EVENT_READ)."""
        if self.state == ConnectionState.CLOSED or self._closed:
            return
        # If we are in CLOSING/CLOSED, ignore reads
        if self.state == ConnectionState.CLOSING:
            return

        try:
            data = self.transport.recv(8192)
        except BlockingIOError:
            return
        except ssl.SSLWantReadError:
            raise
        except ssl.SSLWantWriteError:
            raise
        except OSError as exc:
            logger.debug("recv error %s: %s", self.addr, exc)
            self._should_close = True
            self.state = ConnectionState.CLOSING
            self.close()
            return

        if not data:
            # Peer closed
            logger.debug("Peer closed %s", self.addr)
            self.state = ConnectionState.CLOSING
            self.close()
            return

        self.last_activity = time.monotonic()

        # Feed parser
        result = self.parser.feed(data)

        if result == ParseResult.NEED_MORE:
            # Need more data, stay in READING
            # Check timeout will be handled by event loop
            return
        elif result == ParseResult.COMPLETE:
            self.request = self.parser.request
            # Assign client address for future use
            if self.request is not None:
                self.request.client_address = self.addr
            self._process_request()
        elif result == ParseResult.ERROR:
            # Parser error -> build error response
            err = self.parser.error
            assert err is not None
            logger.debug("Parser error %s: %s", self.addr, err)
            # Create request placeholder if possible: try to keep version for response
            http_version = "HTTP/1.1"
            if self.request is not None:
                http_version = self.request.http_version
            # For malformed request, we may not have request; default to HTTP/1.1
            # Protocol errors must close after response
            self._should_close = True
            self._build_error_response(err.status_code, err.reason, http_version=http_version)
            self.state = ConnectionState.WRITING

    def _process_request(self) -> None:
        self.state = ConnectionState.PROCESSING
        assert self.request is not None
        req = self.request

        try:
            handler, params = self.router.match(req.method, req.path)
            req.route_params = params
            # Wrap handler with middleware chain if present
            effective_handler = handler
            if self.middlewares:
                effective_handler = chain_middleware(self.middlewares, handler)
            # Invoke handler
            try:
                result = effective_handler(req)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Handler exception for %s %s: %s", req.method, req.path, exc)
                self._should_close = True
                self._build_error_response(500, "Internal Server Error", http_version=req.http_version)
                self.state = ConnectionState.WRITING
                return

            # Normalize handler result to Response
            if isinstance(result, Response):
                resp = result
            elif isinstance(result, (bytes, bytearray)):
                resp = Response(status_code=200, body=bytes(result))
            elif isinstance(result, str):
                resp = Response(status_code=200, body=result.encode("utf-8"), headers=Headers([("Content-Type", "text/plain; charset=utf-8")]))
            elif result is None:
                resp = Response(status_code=200, body=b"")
            else:
                logger.warning("Handler returned unexpected type %s", type(result))
                resp = Response(status_code=500, body=b"Internal Server Error")

            self.response = resp
            # Decide keep-alive
            self._should_close = not _should_keep_alive(req, False)
            # For protocol errors, should_close already True; for now keep default
            # Prepare output
            send_body = req.method.upper() != "HEAD"
            http_version = req.http_version if req.http_version in ("HTTP/1.0", "HTTP/1.1") else "HTTP/1.1"
            # Ensure Connection header reflects keep-alive decision if not already present
            if "connection" not in resp.headers:
                if self._should_close:
                    resp.headers.set("Connection", "close")
                else:
                    # For HTTP/1.1 keep-alive is implicit, but we can set keep-alive for clarity; for HTTP/1.0 we set keep-alive if needed
                    if http_version == "HTTP/1.0" and not self._should_close:
                        resp.headers.set("Connection", "keep-alive")
                    elif http_version == "HTTP/1.1" and self._should_close:
                        resp.headers.set("Connection", "close")
            data = resp.serialize(http_version=http_version, send_body=send_body)
            self._output = data
            self._output_offset = 0
            self.state = ConnectionState.WRITING

        except NotFoundError as e:
            http_version = req.http_version if req.http_version in ("HTTP/1.0", "HTTP/1.1") else "HTTP/1.1"
            self._should_close = not _should_keep_alive(req, False)
            self._build_error_response(404, "Not Found", http_version=http_version)
            self.state = ConnectionState.WRITING
        except MethodNotAllowedError as e:
            http_version = req.http_version if req.http_version in ("HTTP/1.0", "HTTP/1.1") else "HTTP/1.1"
            self._should_close = not _should_keep_alive(req, False)
            headers = Headers([("Allow", e.allow_header)])
            resp = Response(status_code=405, headers=headers, body=b"Method Not Allowed")
            self.response = resp
            data = resp.serialize(http_version=http_version, send_body=True)
            self._output = data
            self._output_offset = 0
            self.state = ConnectionState.WRITING
        except HttpError as e:
            http_version = req.http_version if req.http_version in ("HTTP/1.0", "HTTP/1.1") else "HTTP/1.1"
            # For some HttpErrors, we should close
            self._should_close = True
            self._build_error_response(e.status_code, e.reason, http_version=http_version)
            self.state = ConnectionState.WRITING
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unexpected route error: %s", exc)
            self._should_close = True
            try:
                http_version = req.http_version
            except Exception:
                http_version = "HTTP/1.1"
            self._build_error_response(500, "Internal Server Error", http_version=http_version)
            self.state = ConnectionState.WRITING

    def _build_error_response(self, status_code: int, reason: str | None = None, *, http_version: str = "HTTP/1.1") -> None:
        # Build minimal error response
        body = f"{status_code} {reason or ''}".strip().encode("utf-8")
        # Use a simple text/plain body
        headers = Headers([("Content-Type", "text/plain; charset=utf-8")])
        # Connection close for protocol errors
        if self._should_close:
            headers.set("Connection", "close")
        resp = Response(status_code=status_code, headers=headers, body=body, reason=reason)
        self.response = resp
        try:
            data = resp.serialize(http_version=http_version if http_version in ("HTTP/1.0", "HTTP/1.1") else "HTTP/1.1", send_body=True)
        except Exception:
            # Fallback minimal
            data = f"{http_version} {status_code} {reason or ''}\r\nContent-Length: {len(body)}\r\n\r\n".encode("latin-1") + body
        self._output = data
        self._output_offset = 0

    def handle_write(self) -> None:
        """Called when socket is writable (EVENT_WRITE)."""
        if self.state == ConnectionState.CLOSED or self._closed:
            return
        if self._output_offset >= len(self._output):
            # Nothing to write
            return
        try:
            sent = self.transport.send(self._output[self._output_offset :])
        except BlockingIOError:
            return
        except ssl.SSLWantReadError:
            raise
        except ssl.SSLWantWriteError:
            raise
        except OSError as exc:
            logger.debug("send error %s: %s", self.addr, exc)
            self.close()
            return

        self._output_offset += sent
        self.last_activity = time.monotonic()

        if self._output_offset >= len(self._output):
            # Fully sent
            self._output = b""
            self._output_offset = 0

            if self._should_close:
                self.state = ConnectionState.CLOSING
                self.close()
                return

            # Preserve leftover for next request (pipelined data)
            leftover = self.parser.get_remaining()

            # Reset parser for next request
            self.parser = HttpParser(self.config)
            self.request = None
            self.response = None

            if leftover:
                # Feed leftover immediately
                result = self.parser.feed(leftover)
                if result == ParseResult.COMPLETE:
                    self.request = self.parser.request
                    if self.request is not None:
                        self.request.client_address = self.addr
                    self._process_request()
                    # If processing resulted in WRITING, we will send next response on next writable event
                    # If it resulted in error, also WRITING
                    return
                elif result == ParseResult.ERROR:
                    err = self.parser.error
                    assert err is not None
                    self._should_close = True
                    self._build_error_response(err.status_code, err.reason)
                    self.state = ConnectionState.WRITING
                    return
                # else NEED_MORE: keep leftover in parser buffer, wait for more data
                # Note: leftover may be partial next request, keep it
                self.state = ConnectionState.KEEP_ALIVE
            else:
                self.state = ConnectionState.KEEP_ALIVE
                # For keep-alive, we transition to READING for next request
                # But keep KEEP_ALIVE state to differentiate timeout
                # Next handle_read will process
                # Immediately set to READING? Keep KEEP_ALIVE for timeout handling
                # Event loop will watch for READ
                pass

            # After keep-alive, if no immediate complete request, go to READING-like waiting
            # We keep state KEEP_ALIVE until data arrives, then handle_read will process
            # For simplicity, keep state as KEEP_ALIVE; handle_read will transition to PROCESSING when complete
            # But to allow want_read to be true, we keep KEEP_ALIVE
            # No automatic transition to READING needed; want_read handles both
            # However some implementations transition to READING right away; we keep KEEP_ALIVE
            # If we want to immediately be readable, state KEEP_ALIVE is readable via want_read

    # -- helpers for event loop --

    def should_close(self) -> bool:
        return self._should_close

    def get_output_pending(self) -> int:
        return len(self._output) - self._output_offset

    def get_state(self) -> ConnectionState:
        return self.state
