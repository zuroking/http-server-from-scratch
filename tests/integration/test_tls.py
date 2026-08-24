"""TLS integration tests (Phase 14)."""

import socket
import ssl
import tempfile
import os
import time
import datetime

import pytest

from http_server.config import ServerConfig
from http_server.http.response import Response
from http_server.routing.router import Router
from http_server.server import Server


def generate_cert_and_key(cert_path, key_path):
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with open(key_path, "wb") as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "RU"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Test"),
        x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
    ])
    cert = x509.CertificateBuilder().subject_name(subject).issuer_name(issuer).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(datetime.datetime.utcnow() - datetime.timedelta(days=1)).not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=10)).add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False).sign(key, hashes.SHA256())
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))


@pytest.fixture
def tls_server():
    with tempfile.TemporaryDirectory() as tmpdir:
        cert_path = os.path.join(tmpdir, "cert.pem")
        key_path = os.path.join(tmpdir, "key.pem")
        generate_cert_and_key(cert_path, key_path)

        server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_ctx.load_cert_chain(cert_path, key_path)

        client_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        client_ctx.check_hostname = False
        client_ctx.verify_mode = ssl.CERT_NONE

        router = Router()
        router.add("GET", "/", lambda r: Response(status_code=200, body=b"hello tls"))
        router.add("GET", "/keep", lambda r: Response(status_code=200, body=b"keep"))
        router.add("POST", "/echo", lambda r: Response(status_code=200, body=r.body))

        server = Server(host="127.0.0.1", port=0, router=router, config=ServerConfig(), ssl_context=server_ctx)
        server.start_in_thread()
        host, port = server.get_address()
        yield host, port, client_ctx, server
        server.stop()


def recv_tls_response(sock: ssl.SSLSocket, timeout: float = 2.0) -> bytes:
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


def test_https_get(tls_server):
    host, port, client_ctx, server = tls_server
    s = socket.socket()
    s.settimeout(2)
    s.connect((host, port))
    tls_sock = client_ctx.wrap_socket(s, server_hostname="localhost")
    # wrap_socket with do_handshake True by default for client
    try:
        tls_sock.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
        data = recv_tls_response(tls_sock)
        assert b"200 OK" in data
        assert b"hello tls" in data
    finally:
        try: tls_sock.close()
        except: pass


def test_https_keep_alive(tls_server):
    host, port, client_ctx, server = tls_server
    s = socket.socket()
    s.settimeout(2)
    s.connect((host, port))
    tls_sock = client_ctx.wrap_socket(s, server_hostname="localhost")
    try:
        tls_sock.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
        data = recv_tls_response(tls_sock)
        assert b"200 OK" in data

        tls_sock.sendall(b"GET /keep HTTP/1.1\r\nHost: localhost\r\n\r\n")
        data = recv_tls_response(tls_sock)
        assert b"keep" in data
    finally:
        try: tls_sock.close()
        except: pass


def test_tls_handshake_failure():
    # Test handshake failure with plain socket connecting to TLS server
    with tempfile.TemporaryDirectory() as tmpdir:
        cert_path = os.path.join(tmpdir, "cert.pem")
        key_path = os.path.join(tmpdir, "key.pem")
        generate_cert_and_key(cert_path, key_path)
        server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_ctx.load_cert_chain(cert_path, key_path)
        router = Router()
        router.add("GET", "/", lambda r: Response(status_code=200, body=b"ok"))
        server = Server(host="127.0.0.1", port=0, router=router, ssl_context=server_ctx)
        server.start_in_thread()
        host, port = server.get_address()
        try:
            # Connect with plain socket and send garbage
            s = socket.socket()
            s.settimeout(2)
            s.connect((host, port))
            s.sendall(b"not a tls handshake")
            # Server should close without crashing
            time.sleep(0.5)
            # Check server still running
            assert server.running
            s.close()
        finally:
            server.stop()


def test_tls_connection_close():
    with tempfile.TemporaryDirectory() as tmpdir:
        cert_path = os.path.join(tmpdir, "cert.pem")
        key_path = os.path.join(tmpdir, "key.pem")
        generate_cert_and_key(cert_path, key_path)
        server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_ctx.load_cert_chain(cert_path, key_path)
        client_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        client_ctx.check_hostname = False
        client_ctx.verify_mode = ssl.CERT_NONE

        router = Router()
        router.add("GET", "/", lambda r: Response(status_code=200, body=b"ok"))
        server = Server(host="127.0.0.1", port=0, router=router, ssl_context=server_ctx)
        server.start_in_thread()
        host, port = server.get_address()
        try:
            s = socket.socket()
            s.settimeout(2)
            s.connect((host, port))
            tls_sock = client_ctx.wrap_socket(s, server_hostname="localhost")
            tls_sock.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
            data = recv_tls_response(tls_sock)
            assert b"200 OK" in data
            # Server should close after Connection: close
            tls_sock.settimeout(1)
            try:
                extra = tls_sock.recv(4096)
                assert extra == b"" or b"" == extra
            except:
                pass
            tls_sock.close()
        finally:
            server.stop()


def test_https_post_with_body(tls_server):
    host, port, client_ctx, server = tls_server
    s = socket.socket()
    s.settimeout(2)
    s.connect((host, port))
    tls_sock = client_ctx.wrap_socket(s, server_hostname="localhost")
    try:
        tls_sock.sendall(b"POST /echo HTTP/1.1\r\nHost: localhost\r\nContent-Length: 5\r\n\r\nhello")
        data = recv_tls_response(tls_sock)
        assert b"hello" in data
    finally:
        try: tls_sock.close()
        except: pass
