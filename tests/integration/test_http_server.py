"""Integration tests for plain HTTP lifecycle (Phase 13)."""

import socket
import time
import threading

import pytest

from http_server.config import ServerConfig
from http_server.http.headers import Headers
from http_server.http.response import Response
from http_server.routing.router import Router
from http_server.server import Server
from http_server.middleware.logging import LoggingMiddleware


def recv_response(sock: socket.socket, timeout: float = 2.0) -> bytes:
    sock.settimeout(timeout)
    data = b""
    # Read until header terminator
    while b"\r\n\r\n" not in data:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            break
        if not chunk:
            break
        data += chunk
    if b"\r\n\r\n" not in data:
        return data
    header, _, body_start = data.partition(b"\r\n\r\n")
    # Parse Content-Length
    cl = 0
    for line in header.split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            try:
                cl = int(line.split(b":", 1)[1].strip())
            except:  # noqa
                cl = 0
    # Read remaining body
    while len(body_start) < cl:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            break
        if not chunk:
            break
        body_start += chunk
        data = header + b"\r\n\r\n" + body_start
        if len(body_start) >= cl:
            break
    return data


def test_connection_establishment():
    router = Router()
    router.add("GET", "/", lambda r: Response(status_code=200, body=b"ok"))
    server = Server(host="127.0.0.1", port=0, router=router, config=ServerConfig(request_timeout=5, keep_alive_timeout=5))
    server.start_in_thread()
    host, port = server.get_address()
    try:
        s = socket.socket()
        s.settimeout(2)
        s.connect((host, port))
        # Should be connected
        s.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
        data = recv_response(s)
        assert b"200 OK" in data
        s.close()
    finally:
        server.stop()


def test_http_request_response():
    router = Router()
    router.add("GET", "/hello", lambda r: Response(status_code=200, body=b"hello world", headers=Headers([("Content-Type", "text/plain")])))
    server = Server(host="127.0.0.1", port=0, router=router)
    server.start_in_thread()
    host, port = server.get_address()
    try:
        s = socket.socket()
        s.settimeout(2)
        s.connect((host, port))
        s.sendall(b"GET /hello HTTP/1.1\r\nHost: localhost\r\n\r\n")
        data = recv_response(s)
        assert b"200 OK" in data
        assert b"hello world" in data
        assert b"Content-Type: text/plain" in data
        s.close()
    finally:
        server.stop()


def test_multiple_connections():
    router = Router()
    router.add("GET", "/", lambda r: Response(status_code=200, body=b"ok"))
    server = Server(host="127.0.0.1", port=0, router=router)
    server.start_in_thread()
    host, port = server.get_address()
    try:
        sockets = []
        for _ in range(5):
            s = socket.socket()
            s.settimeout(2)
            s.connect((host, port))
            s.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
            sockets.append(s)
        for s in sockets:
            data = recv_response(s)
            assert b"200 OK" in data
            s.close()
    finally:
        server.stop()


def test_multiple_requests_on_one_connection():
    router = Router()
    router.add("GET", "/", lambda r: Response(status_code=200, body=b"first"))
    router.add("GET", "/second", lambda r: Response(status_code=200, body=b"second"))
    server = Server(host="127.0.0.1", port=0, router=router)
    server.start_in_thread()
    host, port = server.get_address()
    try:
        s = socket.socket()
        s.settimeout(2)
        s.connect((host, port))
        s.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
        data = recv_response(s)
        assert b"first" in data

        s.sendall(b"GET /second HTTP/1.1\r\nHost: localhost\r\n\r\n")
        data = recv_response(s)
        assert b"second" in data
        s.close()
    finally:
        server.stop()


def test_leftover_bytes_after_parsing():
    # Two requests in one recv (pipelined data must be preserved)
    router = Router()
    router.add("GET", "/", lambda r: Response(status_code=200, body=b"one"))
    router.add("GET", "/two", lambda r: Response(status_code=200, body=b"two"))
    server = Server(host="127.0.0.1", port=0, router=router)
    server.start_in_thread()
    host, port = server.get_address()
    try:
        s = socket.socket()
        s.settimeout(2)
        s.connect((host, port))
        # Send two requests together
        s.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\nGET /two HTTP/1.1\r\nHost: localhost\r\n\r\n")
        # First response may already contain both due to TCP coalescing
        data = recv_response(s)
        assert b"one" in data
        # Second response should be available without new send; handle coalesced case
        if b"two" in data:
            # Both responses coalesced in first recv (valid)
            data2 = b"two"
        else:
            data2 = recv_response(s)
            assert b"two" in data2
        s.close()
    finally:
        server.stop()


def test_malformed_request_returns_400():
    router = Router()
    router.add("GET", "/", lambda r: Response(status_code=200, body=b"ok"))
    server = Server(host="127.0.0.1", port=0, router=router)
    server.start_in_thread()
    host, port = server.get_address()
    try:
        s = socket.socket()
        s.settimeout(2)
        s.connect((host, port))
        s.sendall(b"BAD REQUEST\r\n\r\n")
        data = recv_response(s)
        assert b"400" in data
        s.close()
    finally:
        server.stop()


def test_oversized_request_returns_error():
    # Use small limits to trigger oversized
    cfg = ServerConfig(max_request_line_size=20, max_headers_size=50, max_header_count=10, max_body_size=10, request_timeout=5, keep_alive_timeout=5)
    router = Router()
    router.add("GET", "/", lambda r: Response(status_code=200, body=b"ok"))
    # Oversized request line - separate server
    server = Server(host="127.0.0.1", port=0, router=router, config=cfg)
    server.start_in_thread()
    host, port = server.get_address()
    try:
        s = socket.socket()
        s.settimeout(2)
        s.connect((host, port))
        s.sendall(b"GET /" + b"a" * 100 + b" HTTP/1.1\r\nHost: localhost\r\n\r\n")
        data = recv_response(s)
        assert b"414" in data or b"400" in data
        s.close()
    finally:
        server.stop()

    # Oversized headers - fresh server to avoid fd reuse issues
    server2 = Server(host="127.0.0.1", port=0, router=router, config=cfg)
    server2.start_in_thread()
    host, port = server2.get_address()
    try:
        s2 = socket.socket()
        s2.settimeout(2)
        s2.connect((host, port))
        s2.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\nX-Long: " + b"a" * 100 + b"\r\n\r\n")
        data = recv_response(s2)
        assert b"431" in data
        s2.close()
    finally:
        server2.stop()


def test_routing_404_and_405():
    router = Router()
    router.add("GET", "/hello", lambda r: Response(status_code=200, body=b"hi"))
    # 404 - separate server
    server = Server(host="127.0.0.1", port=0, router=router)
    server.start_in_thread()
    host, port = server.get_address()
    try:
        s = socket.socket()
        s.settimeout(2)
        s.connect((host, port))
        s.sendall(b"GET /notfound HTTP/1.1\r\nHost: localhost\r\n\r\n")
        data = recv_response(s)
        assert b"404" in data
        s.close()
    finally:
        server.stop()

    # 405 - fresh server
    server2 = Server(host="127.0.0.1", port=0, router=router)
    server2.start_in_thread()
    host, port = server2.get_address()
    try:
        s2 = socket.socket()
        s2.settimeout(2)
        s2.connect((host, port))
        s2.sendall(b"POST /hello HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\n\r\n")
        data = recv_response(s2)
        assert b"405" in data
        assert b"Allow:" in data
        s2.close()
    finally:
        server2.stop()


def test_middleware_logging():
    router = Router()
    router.add("GET", "/", lambda r: Response(status_code=200, body=b"ok"))
    mw = LoggingMiddleware()
    server = Server(host="127.0.0.1", port=0, router=router, middlewares=[mw])
    server.start_in_thread()
    host, port = server.get_address()
    try:
        s = socket.socket()
        s.settimeout(2)
        s.connect((host, port))
        s.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
        data = recv_response(s)
        assert b"200 OK" in data
        s.close()
    finally:
        server.stop()


def test_routing_path_params():
    router = Router()
    router.add("GET", "/users/{id}", lambda r: Response(status_code=200, body=r.route_params["id"].encode()))
    server = Server(host="127.0.0.1", port=0, router=router)
    server.start_in_thread()
    host, port = server.get_address()
    try:
        s = socket.socket()
        s.settimeout(2)
        s.connect((host, port))
        s.sendall(b"GET /users/123 HTTP/1.1\r\nHost: localhost\r\n\r\n")
        data = recv_response(s)
        assert b"123" in data
        s.close()
    finally:
        server.stop()


def test_post_with_body():
    router = Router()
    router.add("POST", "/echo", lambda r: Response(status_code=200, body=r.body))
    server = Server(host="127.0.0.1", port=0, router=router)
    server.start_in_thread()
    host, port = server.get_address()
    try:
        s = socket.socket()
        s.settimeout(2)
        s.connect((host, port))
        s.sendall(b"POST /echo HTTP/1.1\r\nHost: localhost\r\nContent-Length: 5\r\n\r\nhello")
        data = recv_response(s)
        assert b"hello" in data
        s.close()
    finally:
        server.stop()
