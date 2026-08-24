"""Event loop with selectors.DefaultSelector (Phase 9)."""

from __future__ import annotations

import logging
import selectors
import socket
import time

from http_server.config import ServerConfig
from http_server.connection import Connection

logger = logging.getLogger(__name__)


class EventLoop:
    """Single-threaded selector-based event loop.

    Owns selector and connection lifecycle, handles dynamic EVENT_READ/WRITE and O(n) timeout polling.
    """

    def __init__(self, config: ServerConfig | None = None) -> None:
        self.config = config or ServerConfig()
        self.selector = selectors.DefaultSelector()
        # fd -> Connection
        self.connections: dict[int, Connection] = {}
        self._running = False
        # Listening socket (optional, managed by server)
        self._listen_sock: socket.socket | None = None
        self._listen_handler = None  # callable for accept

    # -- listening socket --

    def register_listener(self, sock: socket.socket, handler):  # type: ignore[no-untyped-def]
        """Register listening socket for accept."""
        sock.setblocking(False)
        self._listen_sock = sock
        self._listen_handler = handler
        self.selector.register(sock, selectors.EVENT_READ, data="__listen__")

    def unregister_listener(self) -> None:
        if self._listen_sock is not None:
            try:
                self.selector.unregister(self._listen_sock)
            except Exception:
                pass
            self._listen_sock = None
            self._listen_handler = None

    # -- connections --

    def add_connection(self, conn: Connection) -> None:
        fd = conn.fileno()
        # Handle fd reuse: if old connection with same fd exists and is closed, clean it
        if fd in self.connections:
            old = self.connections[fd]
            if old.is_closed() or old.fileno() != fd:
                try:
                    self.selector.unregister(fd)
                except Exception:
                    pass
                self.connections.pop(fd, None)
        self.connections[fd] = conn
        events = 0
        if conn.want_read():
            events |= selectors.EVENT_READ
        if conn.want_write():
            events |= selectors.EVENT_WRITE
        if events == 0:
            events = selectors.EVENT_READ
        try:
            self.selector.register(fd, events, data=conn)
        except KeyError:
            try:
                self.selector.modify(fd, events, data=conn)
            except Exception:
                # If modify fails due to invalid fd, try unregister and register
                try:
                    self.selector.unregister(fd)
                except Exception:
                    pass
                try:
                    self.selector.register(fd, events, data=conn)
                except Exception as exc:
                    logger.debug("Failed to register fd %s: %s", fd, exc)
        except Exception as exc:
            logger.debug("Failed to register fd %s: %s", fd, exc)
        logger.debug("Added connection fd=%s addr=%s events=%s", fd, conn.addr, events)

    def remove_connection(self, conn: Connection) -> None:
        fd = conn.fileno()
        try:
            self.selector.unregister(fd)
        except Exception:
            pass
        self.connections.pop(fd, None)
        try:
            conn.close()
        except Exception:
            pass
        logger.debug("Removed connection fd=%s", fd)

    def modify_connection(self, conn: Connection) -> None:
        """Update selector interest based on want_read/want_write."""
        if conn.is_closed():
            self.remove_connection(conn)
            return
        events = 0
        if conn.want_read():
            events |= selectors.EVENT_READ
        if conn.want_write():
            events |= selectors.EVENT_WRITE
        if events == 0:
            # At least watch for READ to detect peer close; but per spec, WRITE only when needed
            # If no interest, keep READ to detect close? However KEEP_ALIVE wants read, so events>0 normally
            # If both false, unregister? Keep at least READ to detect close? But spec says WRITE only when needed, READ when capable
            # If neither, we can still watch READ to detect close/timeout
            events = selectors.EVENT_READ
        try:
            self.selector.modify(conn.fileno(), events, data=conn)
        except KeyError:
            try:
                self.selector.register(conn.fileno(), events, data=conn)
            except Exception:
                pass
        except Exception as exc:
            logger.debug("modify failed %s: %s", conn.addr, exc)

    # -- timeout handling --

    def _next_timeout(self) -> float | None:
        now = time.monotonic()
        min_timeout: float | None = None
        for conn in list(self.connections.values()):
            if conn.is_closed():
                continue
            # Determine timeout for this conn
            if conn.state.name == "KEEP_ALIVE":
                timeout = conn.config.keep_alive_timeout
            else:
                timeout = conn.config.request_timeout
            elapsed = now - conn.last_activity
            remaining = timeout - elapsed
            if remaining <= 0:
                return 0
            if min_timeout is None or remaining < min_timeout:
                min_timeout = remaining
        return min_timeout

    def _check_timeouts(self) -> None:
        now = time.monotonic()
        for conn in list(self.connections.values()):
            if conn.is_expired(now):
                logger.debug("Connection timeout %s state=%s", conn.addr, conn.state)
                # Try to send 408 if still reading and no response sent?
                # For simplicity, just close; if in READING and no response, we could send 408 but transport not yet writable
                # Architecture says request timeout should be handled
                # We will attempt to send 408 if not already writing
                if conn.state.name in ("READING", "KEEP_ALIVE") and not conn.want_write():
                    try:
                        conn._build_error_response(408, "Request Timeout")
                        conn.state = conn.state.__class__.WRITING  # type: ignore
                        # Try to send immediately? Event loop will handle write on next iteration
                        self.modify_connection(conn)
                        # But if timeout is immediate, we may close after trying to send? Keep for one more loop
                    except Exception:
                        pass
                # If still expired after, close
                if conn.is_expired(now):
                    # If we just queued 408, give one chance to send; check again next loop
                    # For now close if still expired and not writing
                    if not conn.want_write():
                        self.remove_connection(conn)
                    # If want_write, keep for one more iteration to send timeout response

    # -- main loop --

    def run_once(self, timeout: float | None = None) -> None:
        """Run single iteration: select, dispatch, check timeouts."""
        if timeout is None:
            timeout = self._next_timeout()
            # Default selector timeout if no connections
            if timeout is None:
                timeout = 1.0
            # Clamp to reasonable
            timeout = max(0, min(timeout, 1.0))

        try:
            events = self.selector.select(timeout)
        except OSError as exc:
            logger.debug("select error: %s", exc)
            # Clean up invalid fds
            for fd, conn in list(self.connections.items()):
                try:
                    # Test if fd is still valid
                    import os

                    os.fstat(fd)
                except OSError:
                    logger.debug("Cleaning up invalid fd %s", fd)
                    try:
                        self.selector.unregister(fd)
                    except Exception:
                        pass
                    self.connections.pop(fd, None)
                    try:
                        conn.close()
                    except Exception:
                        pass
            return

        for key, mask in events:
            if key.data == "__listen__":
                # Accept
                if self._listen_handler:
                    try:
                        self._listen_handler()
                    except Exception as exc:
                        logger.exception("Accept error: %s", exc)
                continue

            conn: Connection = key.data
            if conn.is_closed():
                self.remove_connection(conn)
                continue

            try:
                # Always try to handle read if readable or if connection wants read and data may be buffered
                # For TLS and pipelined leftover, we want to be aggressive
                if mask & selectors.EVENT_READ:
                    conn.handle_read()
                    # After read, if connection now wants write, try to write immediately without waiting for next select
                    if conn.want_write() and not conn.is_closed():
                        try:
                            conn.handle_write()
                        except Exception as exc:
                            logger.exception("Immediate write after read error %s: %s", conn.addr, exc)
                            self.remove_connection(conn)
                            continue
                if mask & selectors.EVENT_WRITE:
                    # If we already handled write immediately after read, avoid double
                    # Only handle write if still wants write and not already handled
                    if conn.want_write() and not conn.is_closed():
                        conn.handle_write()
                # For TLS where read may need write and vice versa, try opposite if needed and not already handled
                # If connection wants read but we only had write event, try read
                if not (mask & selectors.EVENT_READ) and conn.want_read() and not conn.is_closed():
                    # Check if data may be available without blocking: try handle_read
                    # We do optimistic read if want_read and not is_closed, but handle_read will catch BlockingIOError quickly
                    try:
                        # Peek if data available? For non-blocking, handle_read will return immediately if no data
                        # To avoid busy loop, only try if we suspect data: for TLS, want_read due to WantRead on send
                        # So we try
                        if getattr(conn, "_tls_want_read", False) or getattr(conn, "_handshake_want_read", False):
                            conn.handle_read()
                    except Exception:
                        pass
                if not (mask & selectors.EVENT_WRITE) and conn.want_write() and not conn.is_closed():
                    try:
                        if getattr(conn, "_tls_want_write", False) or getattr(conn, "_handshake_want_write", False):
                            conn.handle_write()
                    except Exception:
                        pass
            except Exception as exc:
                logger.exception("Connection handler error %s: %s", conn.addr, exc)
                self.remove_connection(conn)
                continue

            # Update selector interest
            if conn.is_closed():
                self.remove_connection(conn)
            else:
                self.modify_connection(conn)

        # After handling all ready events, also handle any connections that have pending write but were not selected
        # This handles case where read generated write needing immediate send
        for conn in list(self.connections.values()):
            if conn.is_closed():
                continue
            if conn.want_write():
                try:
                    # Try to drain output without waiting for select if socket is writable
                    conn.handle_write()
                    self.modify_connection(conn)
                    if conn.is_closed():
                        self.remove_connection(conn)
                except Exception as exc:
                    logger.exception("Pending write error %s: %s", conn.addr, exc)
                    self.remove_connection(conn)

        # Check timeouts after handling events
        self._check_timeouts()

    def run_forever(self) -> None:
        self._running = True
        logger.info("Event loop running")
        try:
            while self._running:
                self.run_once()
        except KeyboardInterrupt:
            logger.info("Event loop interrupted")
        finally:
            self.stop()

    def stop(self) -> None:
        self._running = False
        # Close all connections
        for conn in list(self.connections.values()):
            self.remove_connection(conn)
        self.unregister_listener()
        try:
            self.selector.close()
        except Exception:
            pass

    def is_running(self) -> bool:
        return self._running
