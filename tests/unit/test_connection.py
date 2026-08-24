"""Unit tests for Connection state machine (Phase 6) + keep-alive & timeouts."""

import socket
import time
import pytest

from http_server.config import ServerConfig
from http_server.connection import Connection, ConnectionState
from http_server.routing.router import Router
from http_server.http.response import Response
from http_server.http.headers import Headers


def make_router():
    router = Router()
    def hello(req):
        return Response(status_code=200, body=b"Hello", headers=Headers([("Content-Type","text/plain")]))
    router.add("GET", "/", hello)
    router.add("GET", "/users/{id}", lambda r: Response(status_code=200, body=f"user-{r.route_params['id']}".encode()))
    router.add("POST", "/echo", lambda r: Response(status_code=200, body=r.body))
    return router


def make_conn_pair(router=None, config=None):
    if router is None:
        router = make_router()
    if config is None:
        config = ServerConfig(request_timeout=5, keep_alive_timeout=5)
    c_sock, s_sock = socket.socketpair()
    c_sock.setblocking(False)
    s_sock.setblocking(False)
    conn = Connection(s_sock, ("127.0.0.1", 12345), config, router)
    return c_sock, s_sock, conn


def recv_all(sock):
    data = b""
    try:
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
            # try non-blocking read may raise BlockingIOError after data
            # peek if more available via blocking check? For test we just break if less than 4096
            if len(chunk) < 4096:
                break
    except BlockingIOError:
        pass
    return data


def test_simple_get():
    c_sock, s_sock, conn = make_conn_pair()
    try:
        c_sock.send(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
        conn.handle_read()
        assert conn.state == ConnectionState.WRITING
        assert conn.want_write()
        conn.handle_write()
        assert conn.state == ConnectionState.KEEP_ALIVE
        data = recv_all(c_sock)
        assert b"200 OK" in data
        assert b"Hello" in data
        assert b"Content-Length: 5" in data
    finally:
        c_sock.close()
        conn.close()


def test_fragmented_request():
    c_sock, s_sock, conn = make_conn_pair()
    try:
        c_sock.send(b"GET /inde")
        conn.handle_read()
        assert conn.state == ConnectionState.READING
        c_sock.send(b"x HTTP/1.1\r\nHost: localhost\r\n\r\n")
        conn.handle_read()
        assert conn.state == ConnectionState.WRITING
        conn.handle_write()
        data = recv_all(c_sock)
        # /index not found -> 404, but request parsed as /index
        assert b"404" in data
    finally:
        c_sock.close(); conn.close()


def test_keep_alive_two_requests():
    c_sock, s_sock, conn = make_conn_pair()
    try:
        c_sock.send(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
        conn.handle_read()
        conn.handle_write()
        data1 = recv_all(c_sock)
        assert b"Hello" in data1

        # second request on same connection
        c_sock.send(b"GET /users/42 HTTP/1.1\r\nHost: localhost\r\n\r\n")
        conn.handle_read()
        conn.handle_write()
        data2 = recv_all(c_sock)
        assert b"user-42" in data2
    finally:
        c_sock.close(); conn.close()


def test_pipelined_leftover():
    c_sock, s_sock, conn = make_conn_pair()
    try:
        c_sock.send(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\nGET /users/99 HTTP/1.1\r\nHost: localhost\r\n\r\n")
        conn.handle_read()
        # First request should be processed, second buffered as leftover
        assert conn.state == ConnectionState.WRITING
        conn.handle_write()
        data1 = recv_all(c_sock)
        assert b"Hello" in data1
        # After first write, connection should have queued second response via leftover handling
        assert conn.want_write()
        conn.handle_write()
        data2 = recv_all(c_sock)
        assert b"user-99" in data2
    finally:
        c_sock.close(); conn.close()


def test_head_no_body():
    c_sock, s_sock, conn = make_conn_pair()
    try:
        c_sock.send(b"HEAD / HTTP/1.1\r\nHost: localhost\r\n\r\n")
        conn.handle_read()
        conn.handle_write()
        data = recv_all(c_sock)
        assert b"200 OK" in data
        assert b"Content-Length: 5" in data
        # Body must not be present
        _, _, body = data.partition(b"\r\n\r\n")
        assert body == b""
    finally:
        c_sock.close(); conn.close()


def test_404():
    c_sock, s_sock, conn = make_conn_pair()
    try:
        c_sock.send(b"GET /notfound HTTP/1.1\r\nHost: localhost\r\n\r\n")
        conn.handle_read()
        conn.handle_write()
        data = recv_all(c_sock)
        assert b"404 Not Found" in data
    finally:
        c_sock.close(); conn.close()


def test_405_and_allow():
    c_sock, s_sock, conn = make_conn_pair()
    try:
        # / only has GET, POST to /echo, so POST to / should be 405
        c_sock.send(b"POST / HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\n\r\n")
        conn.handle_read()
        conn.handle_write()
        data = recv_all(c_sock)
        assert b"405 Method Not Allowed" in data
        assert b"Allow:" in data
        assert b"GET" in data
    finally:
        c_sock.close(); conn.close()


def test_http11_keepalive_default():
    router = make_router()
    cfg = ServerConfig()
    c_sock, s_sock, conn = make_conn_pair(router, cfg)
    try:
        c_sock.send(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
        conn.handle_read()
        assert not conn._should_close
        conn.handle_write()
        assert conn.state == ConnectionState.KEEP_ALIVE
        assert not conn.is_closed()
    finally:
        c_sock.close(); conn.close()


def test_http11_close():
    router = make_router()
    cfg = ServerConfig()
    c_sock, s_sock, conn = make_conn_pair(router, cfg)
    try:
        c_sock.send(b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        conn.handle_read()
        assert conn._should_close
        conn.handle_write()
        assert conn.is_closed()
    finally:
        c_sock.close()
        try: conn.close()
        except: pass


def test_http10_default_close():
    router = make_router()
    cfg = ServerConfig()
    c_sock, s_sock, conn = make_conn_pair(router, cfg)
    try:
        c_sock.send(b"GET / HTTP/1.0\r\nHost: localhost\r\n\r\n")
        conn.handle_read()
        assert conn._should_close
        conn.handle_write()
        assert conn.is_closed()
    finally:
        c_sock.close(); conn.close()


def test_http10_keepalive():
    router = make_router()
    cfg = ServerConfig()
    c_sock, s_sock, conn = make_conn_pair(router, cfg)
    try:
        c_sock.send(b"GET / HTTP/1.0\r\nHost: localhost\r\nConnection: keep-alive\r\n\r\n")
        conn.handle_read()
        assert not conn._should_close
        conn.handle_write()
        assert conn.state == ConnectionState.KEEP_ALIVE
    finally:
        c_sock.close(); conn.close()


def test_parser_error_400_closes():
    c_sock, s_sock, conn = make_conn_pair()
    try:
        c_sock.send(b"BAD REQUEST\r\n\r\n")
        conn.handle_read()
        assert conn.state == ConnectionState.WRITING
        assert conn._should_close
        conn.handle_write()
        data = recv_all(c_sock)
        assert b"400 Bad Request" in data
        assert conn.is_closed()
    finally:
        c_sock.close(); conn.close()


def test_oversized_body_413():
    cfg = ServerConfig(max_body_size=5)
    router = make_router()
    c_sock, s_sock, conn = make_conn_pair(router, cfg)
    try:
        c_sock.send(b"POST /echo HTTP/1.1\r\nHost: localhost\r\nContent-Length: 10\r\n\r\n")
        conn.handle_read()
        conn.handle_write()
        data = recv_all(c_sock)
        assert b"413" in data
        assert conn.is_closed()
    finally:
        c_sock.close(); conn.close()


def test_chunked_501():
    c_sock, s_sock, conn = make_conn_pair()
    try:
        c_sock.send(b"POST / HTTP/1.1\r\nHost: localhost\r\nTransfer-Encoding: chunked\r\n\r\n")
        conn.handle_read()
        conn.handle_write()
        data = recv_all(c_sock)
        assert b"501 Not Implemented" in data
    finally:
        c_sock.close(); conn.close()


def test_timeout_request():
    cfg = ServerConfig(request_timeout=0.2, keep_alive_timeout=0.2)
    c_sock, s_sock, conn = make_conn_pair(config=cfg)
    try:
        # Don't send anything, wait
        time.sleep(0.3)
        assert conn.is_expired()
    finally:
        c_sock.close(); conn.close()


def test_timeout_keepalive():
    cfg = ServerConfig(request_timeout=5, keep_alive_timeout=0.2)
    c_sock, s_sock, conn = make_conn_pair(config=cfg)
    try:
        c_sock.send(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
        conn.handle_read()
        conn.handle_write()
        # Now in KEEP_ALIVE, wait
        time.sleep(0.3)
        assert conn.is_expired()
    finally:
        c_sock.close(); conn.close()


def test_fragmented_body():
    router = Router()
    router.add("POST", "/echo", lambda r: Response(status_code=200, body=r.body))
    cfg = ServerConfig()
    c_sock, s_sock, conn = make_conn_pair(router, cfg)
    try:
        c_sock.send(b"POST /echo HTTP/1.1\r\nHost: localhost\r\nContent-Length: 11\r\n\r\nhello")
        conn.handle_read()
        assert conn.state == ConnectionState.READING  # body not yet complete
        c_sock.send(b" world")
        conn.handle_read()
        assert conn.state == ConnectionState.WRITING
        conn.handle_write()
        data = recv_all(c_sock)
        assert b"hello world" in data
    finally:
        c_sock.close(); conn.close()


def test_handler_exception_500():
    router = Router()
    def bad_handler(req):
        raise RuntimeError("boom")
    router.add("GET", "/bad", bad_handler)
    cfg = ServerConfig()
    c_sock, s_sock, conn = make_conn_pair(router, cfg)
    try:
        c_sock.send(b"GET /bad HTTP/1.1\r\nHost: localhost\r\n\r\n")
        conn.handle_read()
        conn.handle_write()
        data = recv_all(c_sock)
        assert b"500 Internal Server Error" in data
    finally:
        c_sock.close(); conn.close()


def test_handler_returning_string():
    router = Router()
    router.add("GET", "/str", lambda r: "hello string")
    cfg = ServerConfig()
    c_sock, s_sock, conn = make_conn_pair(router, cfg)
    try:
        c_sock.send(b"GET /str HTTP/1.1\r\nHost: localhost\r\n\r\n")
        conn.handle_read()
        conn.handle_write()
        data = recv_all(c_sock)
        assert b"hello string" in data
    finally:
        c_sock.close(); conn.close()


def test_want_read_write_transitions():
    router = make_router()
    cfg = ServerConfig()
    c_sock, s_sock, conn = make_conn_pair(router, cfg)
    try:
        assert conn.want_read() and not conn.want_write()
        c_sock.send(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
        conn.handle_read()
        assert conn.want_write() and not conn.want_read()
        conn.handle_write()
        # After write keep-alive, want read again
        assert conn.want_read() and not conn.want_write()
    finally:
        c_sock.close(); conn.close()


def test_peer_close():
    c_sock, s_sock, conn = make_conn_pair()
    try:
        # Close client without sending
        c_sock.close()
        # Give time for peer close detection? handle_read will recv 0
        # Need to trigger handle_read after close: s_sock will see EOF
        # Simulate by calling handle_read
        conn.handle_read()
        assert conn.is_closed() or conn.state == ConnectionState.CLOSING
    finally:
        try: s_sock.close()
        except: pass
        try: conn.close()
        except: pass


def test_output_partial_send_via_mock():
    # Test partial send handling via mock transport that only sends part
    class PartialTransport:
        def __init__(self, sock):
            self.sock = sock
            self.sock.setblocking(False)
            self._closed=False
        def recv(self, n): return self.sock.recv(n)
        def send(self, data):
            # Only send half
            half = max(1, len(data)//2)
            return self.sock.send(data[:half])
        def close(self):
            self._closed=True
            try: self.sock.close()
            except: pass
        def fileno(self): return self.sock.fileno()
        @property
        def closed(self): return self._closed

    router = make_router()
    cfg = ServerConfig()
    c_sock, s_sock = socket.socketpair()
    c_sock.setblocking(False); s_sock.setblocking(False)
    transport = PartialTransport(s_sock)
    conn = Connection(s_sock, ("127.0.0.1",1), cfg, router, transport=transport)
    try:
        c_sock.send(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
        conn.handle_read()
        assert conn.want_write()
        # First write will only send half
        conn.handle_write()
        assert conn.want_write()  # still pending
        conn.handle_write()
        # Eventually fully sent - partial mock sends half each time, need ~10 iterations for 69 bytes
        for _ in range(10):
            if conn.want_write():
                conn.handle_write()
        assert not conn.want_write()
        data = recv_all(c_sock)
        assert b"Hello" in data
    finally:
        c_sock.close()
        conn.close()


def test_multiple_keepalive_sequential():
    router = make_router()
    cfg = ServerConfig()
    c_sock, s_sock, conn = make_conn_pair(router, cfg)
    try:
        for path in ["/", "/users/1", "/users/2"]:
            c_sock.send(f"GET {path} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode())
            conn.handle_read()
            conn.handle_write()
            data = recv_all(c_sock)
            assert b"200 OK" in data
            assert conn.state == ConnectionState.KEEP_ALIVE
        # After loop, connection still open
        assert not conn.is_closed()
    finally:
        c_sock.close(); conn.close()


def test_body_binary():
    router = Router()
    router.add("POST", "/echo", lambda r: Response(status_code=200, body=r.body))
    cfg = ServerConfig()
    c_sock, s_sock, conn = make_conn_pair(router, cfg)
    try:
        body = bytes([0,1,2,255,254,13,10])
        c_sock.send(b"POST /echo HTTP/1.1\r\nHost: localhost\r\nContent-Length: 7\r\n\r\n" + body)
        conn.handle_read()
        conn.handle_write()
        data = recv_all(c_sock)
        _, _, after = data.partition(b"\r\n\r\n")
        assert after == body
    finally:
        c_sock.close(); conn.close()
