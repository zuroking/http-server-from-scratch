"""TLS transport over non-blocking ssl.SSLSocket."""

from __future__ import annotations

import socket
import ssl

from http_server.transport.base import Transport


class TLSTransport(Transport):
    """Non-blocking TLS transport via ssl.SSLContext.

    Wraps a plain TCP socket with SSLContext.wrap_socket(..., do_handshake_on_connect=False)
    and handles SSLWantReadError / SSLWantWriteError for handshake and I/O.
    """

    def __init__(self, sock: socket.socket, ctx: ssl.SSLContext, *, server_side: bool = True) -> None:
        if not isinstance(sock, socket.socket):
            raise TypeError("sock must be socket.socket")
        if not isinstance(ctx, ssl.SSLContext):
            raise TypeError("ctx must be ssl.SSLContext")
        # Underlying socket must be non-blocking; wrap_socket will preserve
        sock.setblocking(False)
        self._raw_sock = sock
        self._ctx = ctx
        self._ssl_sock = self._ctx.wrap_socket(
            sock,
            server_side=server_side,
            do_handshake_on_connect=False,
        )
        self._ssl_sock.setblocking(False)
        self._closed = False
        self._handshake_done = False

    # -- handshake --

    @property
    def handshake_done(self) -> bool:
        return self._handshake_done

    def do_handshake(self) -> bool:
        """Try non-blocking handshake.

        Returns True if completed, else raises SSLWantReadError / SSLWantWriteError.
        """
        if self._handshake_done:
            return True
        try:
            self._ssl_sock.do_handshake()
            self._handshake_done = True
            return True
        except ssl.SSLWantReadError:
            raise
        except ssl.SSLWantWriteError:
            raise
        except OSError:
            raise

    # -- Transport API --

    def recv(self, n: int) -> bytes:
        if self._closed:
            return b""
        # Handshake must be done before app data
        if not self._handshake_done:
            self.do_handshake()
        try:
            data = self._ssl_sock.recv(n)
            return data
        except ssl.SSLWantReadError:
            raise
        except ssl.SSLWantWriteError:
            raise
        except OSError:
            raise

    def send(self, data: bytes) -> int:
        if self._closed or not data:
            return 0
        if not self._handshake_done:
            self.do_handshake()
        try:
            sent = self._ssl_sock.send(data)
            return sent
        except ssl.SSLWantReadError:
            raise
        except ssl.SSLWantWriteError:
            raise
        except OSError:
            raise

    def shutdown(self) -> None:
        """TLS shutdown (try to send close_notify)."""
        if self._closed:
            return
        try:
            self._ssl_sock.unwrap()
        except (ssl.SSLError, OSError):
            pass
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._ssl_sock.close()
        except OSError:
            pass
        # raw_sock is owned by ssl_sock, but ensure closed
        try:
            self._raw_sock.close()
        except OSError:
            pass

    def fileno(self) -> int:
        return self._ssl_sock.fileno()

    @property
    def closed(self) -> bool:
        return self._closed

    def get_extra_info(self, name: str):  # type: ignore[no-untyped-def]
        if name == "ssl_object":
            return self._ssl_sock
        if name == "cipher":
            try:
                return self._ssl_sock.cipher()
            except Exception:
                return None
        return None
