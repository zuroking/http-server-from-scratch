"""Server with listening socket, accept and dispatch to event loop (Phase 10)."""

from __future__ import annotations

import logging
import socket
import ssl
import threading
import time

from http_server.config import ServerConfig
from http_server.connection import Connection
from http_server.event_loop import EventLoop
from http_server.routing.router import Router
from http_server.tls_connection import TLSConnection

logger = logging.getLogger(__name__)


class Server:
    """HTTP server managing listening socket and event loop."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8080,
        router: Router | None = None,
        config: ServerConfig | None = None,
        ssl_context: ssl.SSLContext | None = None,
        middlewares: list | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.router = router or Router()
        self.config = config or ServerConfig(host=host, port=port)
        self.ssl_context = ssl_context
        self.middlewares = middlewares or []

        self._listen_sock: socket.socket | None = None
        self.event_loop = EventLoop(self.config)
        self._thread: threading.Thread | None = None
        self._running = False

    def _setup_listener(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # For quick restart
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass
        sock.setblocking(False)
        sock.bind((self.host, self.port))
        sock.listen(128)
        # Update port if 0 (random)
        self.host, self.port = sock.getsockname()[:2]
        self._listen_sock = sock
        self.event_loop.register_listener(sock, self._accept)
        logger.info("Listening on %s:%s %s", self.host, self.port, "TLS" if self.ssl_context else "plain")

    def _accept(self) -> None:
        assert self._listen_sock is not None
        try:
            while True:
                try:
                    client_sock, addr = self._listen_sock.accept()
                except BlockingIOError:
                    break
                except OSError as exc:
                    logger.debug("accept error: %s", exc)
                    break
                client_sock.setblocking(False)
                # Set TCP_NODELAY?
                try:
                    client_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except OSError:
                    pass

                # Create connection
                try:
                    if self.ssl_context is not None:
                        conn = TLSConnection(client_sock, addr, self.config, self.router, ssl_context=self.ssl_context, middlewares=self.middlewares)
                    else:
                        conn = Connection(client_sock, addr, self.config, self.router, middlewares=self.middlewares)
                except Exception as exc:
                    logger.exception("Failed to create connection for %s: %s", addr, exc)
                    try:
                        client_sock.close()
                    except: pass
                    continue

                self.event_loop.add_connection(conn)
        except Exception as exc:
            logger.exception("Accept loop error: %s", exc)

    def start(self) -> None:
        """Start server synchronously (blocks) running event loop."""
        if self._running:
            return
        self._setup_listener()
        self._running = True
        try:
            self.event_loop.run_forever()
        finally:
            self.stop()

    def start_in_thread(self, daemon: bool = True) -> threading.Thread:
        """Start server in background thread for testing. Returns thread."""
        if self._running:
            assert self._thread is not None
            return self._thread

        self._setup_listener()
        self.event_loop._running = True
        self._running = True

        def run() -> None:
            logger.info("Server thread running")
            while self._running and self.event_loop._running:
                self.event_loop.run_once(timeout=0.5)
            logger.info("Server thread exiting")

        self._thread = threading.Thread(target=run, daemon=daemon, name="http-server")
        self._thread.start()
        # Wait a bit for listen
        time.sleep(0.1)
        return self._thread

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self.event_loop.stop()
        if self._listen_sock is not None:
            try:
                self._listen_sock.close()
            except: pass
            self._listen_sock = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        logger.info("Server stopped")

    def get_address(self) -> tuple[str, int]:
        if self._listen_sock is not None:
            return self._listen_sock.getsockname()[:2]
        return (self.host, self.port)

    @property
    def running(self) -> bool:
        return self._running
