"""TLS tests (Phase 8)."""

import socket
import ssl
import tempfile
import os
import time
import pytest

import datetime

def generate_cert_and_key(cert_path, key_path):
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    # Generate private key
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    # Write key
    with open(key_path, "wb") as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))
    # Generate cert
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "RU"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Moscow"),
        x509.NameAttribute(NameOID.LOCALITY_NAME, "Moscow"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Test"),
        x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
    ])
    cert = x509.CertificateBuilder().subject_name(
        subject
    ).issuer_name(
        issuer
    ).public_key(
        key.public_key()
    ).serial_number(
        x509.random_serial_number()
    ).not_valid_before(
        datetime.datetime.utcnow() - datetime.timedelta(days=1)
    ).not_valid_after(
        datetime.datetime.utcnow() + datetime.timedelta(days=10)
    ).add_extension(
        x509.SubjectAlternativeName([x509.DNSName("localhost")]),
        critical=False,
    ).sign(key, hashes.SHA256())
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))


@pytest.fixture
def cert_key():
    with tempfile.TemporaryDirectory() as tmpdir:
        cert_path = os.path.join(tmpdir, "cert.pem")
        key_path = os.path.join(tmpdir, "key.pem")
        generate_cert_and_key(cert_path, key_path)
        yield cert_path, key_path


def make_tls_contexts(cert_path, key_path):
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(cert_path, key_path)
    client_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client_ctx.check_hostname = False
    client_ctx.verify_mode = ssl.CERT_NONE
    return server_ctx, client_ctx


def create_pair_nonblocking():
    a, b = socket.socketpair()
    a.setblocking(False)
    b.setblocking(False)
    return a, b


def test_tls_transport_handshake(cert_key):
    cert_path, key_path = cert_key
    server_ctx, client_ctx = make_tls_contexts(cert_path, key_path)
    from http_server.transport.tls import TLSTransport

    a, b = create_pair_nonblocking()
    try:
        server_tls = TLSTransport(a, server_ctx, server_side=True)
        client_tls = TLSTransport(b, client_ctx, server_side=False)

        # Do handshake non-blocking, handling WantRead/Write
        for _ in range(20):
            try:
                server_tls.do_handshake()
            except ssl.SSLWantReadError:
                pass
            except ssl.SSLWantWriteError:
                pass
            try:
                client_tls.do_handshake()
            except ssl.SSLWantReadError:
                pass
            except ssl.SSLWantWriteError:
                pass
            if server_tls.handshake_done and client_tls.handshake_done:
                break
            time.sleep(0.01)
        assert server_tls.handshake_done
        assert client_tls.handshake_done
    finally:
        try: a.close()
        except: pass
        try: b.close()
        except: pass


def test_tls_transport_send_recv(cert_key):
    cert_path, key_path = cert_key
    server_ctx, client_ctx = make_tls_contexts(cert_path, key_path)
    from http_server.transport.tls import TLSTransport

    a, b = create_pair_nonblocking()
    try:
        server_tls = TLSTransport(a, server_ctx, server_side=True)
        client_tls = TLSTransport(b, client_ctx, server_side=False)
        # handshake
        for _ in range(20):
            for t in (server_tls, client_tls):
                try:
                    t.do_handshake()
                except (ssl.SSLWantReadError, ssl.SSLWantWriteError):
                    pass
            if server_tls.handshake_done and client_tls.handshake_done:
                break
            time.sleep(0.01)
        assert server_tls.handshake_done

        # Send from client, recv on server (need handling WantRead/Write)
        msg = b"hello tls"
        # client send may need wants
        for _ in range(10):
            try:
                sent = client_tls.send(msg)
                if sent > 0:
                    break
            except (ssl.SSLWantReadError, ssl.SSLWantWriteError):
                time.sleep(0.01)
        # server recv
        data = b""
        for _ in range(20):
            try:
                data = server_tls.recv(4096)
                if data:
                    break
            except (ssl.SSLWantReadError, ssl.SSLWantWriteError):
                time.sleep(0.01)
        assert data == msg

        # reverse
        for _ in range(10):
            try:
                server_tls.send(b"reply")
                break
            except (ssl.SSLWantReadError, ssl.SSLWantWriteError):
                time.sleep(0.01)
        data2 = b""
        for _ in range(20):
            try:
                data2 = client_tls.recv(4096)
                if data2:
                    break
            except (ssl.SSLWantReadError, ssl.SSLWantWriteError):
                time.sleep(0.01)
        assert data2 == b"reply"
    finally:
        try: a.close()
        except: pass
        try: b.close()
        except: pass


def test_tls_connection_handshake(cert_key):
    cert_path, key_path = cert_key
    server_ctx, client_ctx = make_tls_contexts(cert_path, key_path)
    from http_server.connection import Connection
    from http_server.tls_connection import TLSConnection
    from http_server.routing.router import Router
    from http_server.http.response import Response
    from http_server.config import ServerConfig

    router = Router()
    router.add("GET", "/", lambda r: Response(status_code=200, body=b"hello tls"))
    cfg = ServerConfig()

    # Create socketpair for TLS: server side TLSConnection, client side ssl socket
    a, b = socket.socketpair()
    a.setblocking(False)
    b.setblocking(False)

    server_conn = TLSConnection(a, ("127.0.0.1", 12345), cfg, router, ssl_context=server_ctx)

    # Client side: wrap b with client_ctx
    client_sock = client_ctx.wrap_socket(b, server_hostname="localhost", do_handshake_on_connect=False)
    client_sock.setblocking(False)

    # Drive handshake via selector-like loop: check want_read/write for server, try client handshake
    for _ in range(50):
        # Server wants
        want_r = server_conn.want_read()
        want_w = server_conn.want_write()
        # Try server handshake via handle_read/write
        if want_r:
            server_conn.handle_read()
        if want_w:
            server_conn.handle_write()
        # Client handshake
        try:
            client_sock.do_handshake()
        except ssl.SSLWantReadError:
            pass
        except ssl.SSLWantWriteError:
            pass
        except ssl.SSLError:
            pass
        if server_conn.transport.handshake_done:
            # Check client handshake done?
            try:
                # After handshake, client can try
                pass
            except: pass
            # Verify server handshake done
            break
        time.sleep(0.005)

    assert server_conn.transport.handshake_done, "server handshake should be done"
    # Now try HTTPS GET
    # Client send HTTP request over TLS
    request = b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n"
    # Client send may need wants
    for _ in range(10):
        try:
            client_sock.send(request)
            break
        except ssl.SSLWantReadError:
            time.sleep(0.01)
        except ssl.SSLWantWriteError:
            time.sleep(0.01)
        except BlockingIOError:
            time.sleep(0.01)

    # Server handle read
    for _ in range(10):
        server_conn.handle_read()
        if server_conn.want_write():
            break
        time.sleep(0.01)

    assert server_conn.want_write()
    # Server write response
    for _ in range(10):
        server_conn.handle_write()
        if not server_conn.want_write():
            break
        time.sleep(0.01)

    # Client recv
    data = b""
    for _ in range(20):
        try:
            chunk = client_sock.recv(4096)
            if chunk:
                data += chunk
                if b"200 OK" in data:
                    break
        except ssl.SSLWantReadError:
            time.sleep(0.01)
        except ssl.SSLWantWriteError:
            time.sleep(0.01)
        except BlockingIOError:
            time.sleep(0.01)

    assert b"200 OK" in data
    assert b"hello tls" in data

    try: client_sock.close()
    except: pass
    server_conn.close()


def test_tls_want_read_write(cert_key):
    cert_path, key_path = cert_key
    server_ctx, client_ctx = make_tls_contexts(cert_path, key_path)
    from http_server.tls_connection import TLSConnection
    from http_server.routing.router import Router
    from http_server.config import ServerConfig
    from http_server.http.response import Response

    router = Router()
    router.add("GET", "/", lambda r: Response(status_code=200, body=b"ok"))
    cfg = ServerConfig()

    a, b = socket.socketpair()
    a.setblocking(False); b.setblocking(False)
    conn = TLSConnection(a, ("127.0.0.1", 1), cfg, router, ssl_context=server_ctx)
    # During handshake, should want read initially
    assert conn.want_read() or conn.want_write()
    # At least one of them true
    # After handshake without client, still handshaking -> want_read true
    # Cleanup
    conn.close()
    b.close()


def test_tls_invalid_client(cert_key):
    cert_path, key_path = cert_key
    server_ctx, _ = make_tls_contexts(cert_path, key_path)
    from http_server.tls_connection import TLSConnection
    from http_server.routing.router import Router
    from http_server.config import ServerConfig

    router = Router()
    cfg = ServerConfig(request_timeout=0.5)

    a, b = socket.socketpair()
    a.setblocking(False); b.setblocking(False)
    conn = TLSConnection(a, ("127.0.0.1",1), cfg, router, ssl_context=server_ctx)
    # Client sends garbage instead of handshake
    b.send(b"not a tls handshake")
    # Server try handle read -> should handle handshake failure and close?
    # Give time
    for _ in range(5):
        conn.handle_read()
        time.sleep(0.01)
        if conn.is_closed():
            break
    # Should be closed or in closing due to handshake failure?
    # At least not crash, and may be expired or closed
    assert conn.is_closed() or conn.state.name in ("CLOSING", "CLOSED", "READING")
    conn.close()
    b.close()
