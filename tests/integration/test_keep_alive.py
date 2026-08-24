"""Keep-alive integration tests (Phase 13)."""

import socket
import time

from http_server.config import ServerConfig
from http_server.http.response import Response
from http_server.routing.router import Router
from http_server.server import Server


def recv_response(sock: socket.socket, timeout: float = 2.0) -> bytes:
    sock.settimeout(timeout)
    data = b""
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
    cl = 0
    for line in header.split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            try:
                cl = int(line.split(b":", 1)[1].strip())
            except:
                cl = 0
    while len(body_start) < cl:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            break
        if not chunk:
            break
        body_start += chunk
    return header + b"\r\n\r\n" + body_start


def test_http11_keep_alive_default():
    router = Router()
    router.add("GET", "/", lambda r: Response(status_code=200, body=b"ok"))
    server = Server(host="127.0.0.1", port=0, router=router)
    server.start_in_thread()
    host, port = server.get_address()
    try:
        s = socket.socket()
        s.settimeout(2)
        s.connect((host, port))
        s.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
        data = recv_response(s)
        assert b"200 OK" in data
        # Connection should stay open for keep-alive
        # Second request on same connection should succeed
        s.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
        data2 = recv_response(s)
        assert b"200 OK" in data2
        s.close()
    finally:
        server.stop()


def test_http11_keep_alive_explicit():
    router = Router()
    router.add("GET", "/", lambda r: Response(status_code=200, body=b"ok"))
    server = Server(host="127.0.0.1", port=0, router=router)
    server.start_in_thread()
    host, port = server.get_address()
    try:
        s = socket.socket()
        s.settimeout(2)
        s.connect((host, port))
        s.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: keep-alive\r\n\r\n")
        data = recv_response(s)
        assert b"200 OK" in data
        # No close header expected for keep-alive HTTP/1.1
        # Second request
        s.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
        data2 = recv_response(s)
        assert b"200 OK" in data2
        s.close()
    finally:
        server.stop()


def test_http11_close():
    router = Router()
    router.add("GET", "/", lambda r: Response(status_code=200, body=b"ok"))
    server = Server(host="127.0.0.1", port=0, router=router)
    server.start_in_thread()
    host, port = server.get_address()
    try:
        s = socket.socket()
        s.settimeout(2)
        s.connect((host, port))
        s.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        data = recv_response(s)
        assert b"200 OK" in data
        assert b"Connection: close" in data
        # Connection should be closed by server
        # Try to recv more - should be 0 or closed
        s.settimeout(1)
        try:
            extra = s.recv(4096)
            # If server closed, recv returns b"" or raises
            assert extra == b"" or b"" == extra
        except socket.timeout:
            # Also acceptable if server closed and client times out?
            pass
        s.close()
    finally:
        server.stop()


def test_http10_default_close():
    router = Router()
    router.add("GET", "/", lambda r: Response(status_code=200, body=b"ok"))
    server = Server(host="127.0.0.1", port=0, router=router)
    server.start_in_thread()
    host, port = server.get_address()
    try:
        s = socket.socket()
        s.settimeout(2)
        s.connect((host, port))
        s.sendall(b"GET / HTTP/1.0\r\nHost: localhost\r\n\r\n")
        data = recv_response(s)
        assert b"200 OK" in data
        # HTTP/1.0 default close
        assert b"Connection: close" in data
        s.settimeout(1)
        try:
            extra = s.recv(4096)
            assert extra == b""
        except socket.timeout:
            pass
        s.close()
    finally:
        server.stop()


def test_http10_keep_alive():
    router = Router()
    router.add("GET", "/", lambda r: Response(status_code=200, body=b"ok"))
    server = Server(host="127.0.0.1", port=0, router=router)
    server.start_in_thread()
    host, port = server.get_address()
    try:
        s = socket.socket()
        s.settimeout(2)
        s.connect((host, port))
        s.sendall(b"GET / HTTP/1.0\r\nHost: localhost\r\nConnection: keep-alive\r\n\r\n")
        data = recv_response(s)
        assert b"200 OK" in data
        assert b"Connection: keep-alive" in data or b"keep-alive" in data.lower()
        # Second request should work
        s.sendall(b"GET / HTTP/1.0\r\nHost: localhost\r\nConnection: keep-alive\r\n\r\n")
        data2 = recv_response(s)
        assert b"200 OK" in data2
        s.close()
    finally:
        server.stop()


def test_multiple_requests_on_one_connection():
    router = Router()
    router.add("GET", "/", lambda r: Response(status_code=200, body=b"ok"))
    router.add("GET", "/two", lambda r: Response(status_code=200, body=b"two"))
    router.add("GET", "/three", lambda r: Response(status_code=200, body=b"three"))
    server = Server(host="127.0.0.1", port=0, router=router)
    server.start_in_thread()
    host, port = server.get_address()
    try:
        s = socket.socket()
        s.settimeout(2)
        s.connect((host, port))
        for path, expected in [("/", b"ok"), ("/two", b"two"), ("/three", b"three")]:
            s.sendall(f"GET {path} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode())
            data = recv_response(s)
            assert expected in data
        s.close()
    finally:
        server.stop()


def test_keep_alive_with_body():
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

        s.sendall(b"POST /echo HTTP/1.1\r\nHost: localhost\r\nContent-Length: 5\r\n\r\nworld")
        data = recv_response(s)
        assert b"world" in data
        s.close()
    finally:
        server.stop()
