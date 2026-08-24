"""TLS connection handling via non-blocking handshake."""

from __future__ import annotations

import logging
import socket
import ssl
import time
from enum import Enum, auto

from http_server.config import ServerConfig
from http_server.connection import Connection, ConnectionState
from http_server.http.parser import ParseResult
from http_server.routing.router import Router
from http_server.transport.tls import TLSTransport

logger = logging.getLogger(__name__)


class TLSHandshakeState(Enum):
    NEED_READ = auto()
    NEED_WRITE = auto()
    DONE = auto()


class TLSConnection(Connection):
    """Connection with non-blocking TLS handshake.

    State machine extends plain Connection with HANDSHAKING phase:

        ACCEPTED -> HANDSHAKING -> READING -> PROCESSING -> WRITING -> KEEP_ALIVE -> READING
                               -> CLOSING
    """

    def __init__(
        self,
        sock: socket.socket,
        addr: tuple[str, int],
        config: ServerConfig | None = None,
        router: Router | None = None,
        ssl_context: ssl.SSLContext | None = None,
        middlewares: list | None = None,
    ) -> None:
        if ssl_context is None:
            raise ValueError("ssl_context is required for TLSConnection")
        # Create TLS transport upfront
        tls_transport = TLSTransport(sock, ssl_context, server_side=True)
        # Initialize base Connection with transport injection
        super().__init__(sock, addr, config, router, middlewares=middlewares, transport=tls_transport)
        # Override state to HANDSHAKING
        self.state = ConnectionState.READING  # we reuse READING but track handshake separately
        # Use custom handshake flag
        self._tls_handshake_state: TLSHandshakeState | None = None
        self._handshake_want_read = False
        self._handshake_want_write = False
        self._tls_want_read = False
        self._tls_want_write = False
        # Start handshake attempt
        self._try_handshake()

    def _try_handshake(self) -> None:
        transport: TLSTransport = self.transport  # type: ignore[assignment]
        if transport.handshake_done:
            self._tls_handshake_state = TLSHandshakeState.DONE
            self._handshake_want_read = False
            self._handshake_want_write = False
            # Transition to normal READING if not already
            if self.state not in (ConnectionState.WRITING, ConnectionState.CLOSING, ConnectionState.CLOSED):
                self.state = ConnectionState.READING
            return
        try:
            transport.do_handshake()
            self._tls_handshake_state = TLSHandshakeState.DONE
            self._handshake_want_read = False
            self._handshake_want_write = False
            self.state = ConnectionState.READING
            self.last_activity = time.monotonic()
            logger.debug("TLS handshake done for %s", self.addr)
        except ssl.SSLWantReadError:
            self._tls_handshake_state = TLSHandshakeState.NEED_READ
            self._handshake_want_read = True
            self._handshake_want_write = False
            self.last_activity = time.monotonic()
        except ssl.SSLWantWriteError:
            self._tls_handshake_state = TLSHandshakeState.NEED_WRITE
            self._handshake_want_read = False
            self._handshake_want_write = True
            self.last_activity = time.monotonic()
        except (ssl.SSLError, OSError) as exc:
            logger.debug("TLS handshake failed for %s: %s", self.addr, exc)
            self._tls_handshake_state = None
            self._handshake_want_read = False
            self._handshake_want_write = False
            self._should_close = True
            self.state = ConnectionState.CLOSING
            self.close()

    # Override selector interests to handle handshake wants + TLS I/O wants
    def want_read(self) -> bool:
        if self.is_closed():
            return False
        transport: TLSTransport = self.transport  # type: ignore
        if not transport.handshake_done:
            # During handshake, readiness depends on handshake want
            if self._tls_handshake_state == TLSHandshakeState.NEED_READ:
                return True
            if self._tls_handshake_state == TLSHandshakeState.NEED_WRITE:
                return False
            # Initial or unknown -> want read to start handshake
            return True
        # After handshake, incorporate TLS WantRead (e.g., send wants read)
        if self._tls_want_read:
            return True
        return super().want_read()

    def want_write(self) -> bool:
        if self.is_closed():
            return False
        transport: TLSTransport = self.transport  # type: ignore
        if not transport.handshake_done:
            if self._tls_handshake_state == TLSHandshakeState.NEED_WRITE:
                return True
            if self._tls_handshake_state == TLSHandshakeState.NEED_READ:
                return False
            # Initial unknown -> no write yet, will be determined after first try
            return False
        if self._tls_want_write:
            return True
        if super().want_write():
            return True
        return False

    def handle_read(self) -> None:
        transport: TLSTransport = self.transport  # type: ignore
        if not transport.handshake_done:
            self._try_handshake()
            if not transport.handshake_done:
                # Still handshaking, not yet ready for HTTP
                return
            # Handshake completed, fall through to normal read handling
        # Clear previous TLS I/O wants before attempt
        self._tls_want_read = False
        self._tls_want_write = False
        try:
            super().handle_read()
        except ssl.SSLWantReadError:
            self._tls_want_read = True
            self._tls_want_write = False
        except ssl.SSLWantWriteError:
            self._tls_want_read = False
            self._tls_want_write = True

    def handle_write(self) -> None:
        transport: TLSTransport = self.transport  # type: ignore
        if not transport.handshake_done:
            self._try_handshake()
            if not transport.handshake_done:
                return
        self._tls_want_read = False
        self._tls_want_write = False
        try:
            super().handle_write()
        except ssl.SSLWantReadError:
            self._tls_want_read = True
            self._tls_want_write = False
        except ssl.SSLWantWriteError:
            self._tls_want_read = False
            self._tls_want_write = True

    def is_expired(self, now: float | None = None) -> bool:
        # Use same timeout logic as base, but handshake also respects request_timeout
        return super().is_expired(now)
