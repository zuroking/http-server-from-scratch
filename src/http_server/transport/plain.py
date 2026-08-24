"""Plain TCP transport over non-blocking socket."""

from __future__ import annotations

import socket

from http_server.transport.base import Transport


class PlainTransport(Transport):
    """Plain non-blocking socket transport."""

    def __init__(self, sock: socket.socket) -> None:
        if not isinstance(sock, socket.socket):
            raise TypeError("sock must be socket.socket")
        self._sock = sock
        # Ensure non-blocking as required by architecture invariant 7
        self._sock.setblocking(False)
        self._closed = False

    def recv(self, n: int) -> bytes:
        if self._closed:
            return b""
        try:
            data = self._sock.recv(n)
            return data
        except BlockingIOError:
            raise
        except OSError:
            # Treat other socket errors as closed
            raise

    def send(self, data: bytes) -> int:
        if self._closed:
            return 0
        if not data:
            return 0
        try:
            sent = self._sock.send(data)
            return sent
        except BlockingIOError:
            raise
        except OSError:
            raise

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._sock.close()
            except OSError:
                pass

    def fileno(self) -> int:
        return self._sock.fileno()

    @property
    def closed(self) -> bool:
        return self._closed

    def get_sock(self) -> socket.socket:
        return self._sock
