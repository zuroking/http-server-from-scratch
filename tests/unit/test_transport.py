"""Tests for transport abstraction (Phase 7)."""

import socket
import pytest

from http_server.transport.base import Transport
from http_server.transport.plain import PlainTransport


def test_plain_transport_recv_send():
    a, b = socket.socketpair()
    try:
        t_a = PlainTransport(a)
        t_b = PlainTransport(b)
        # Non-blocking: need to handle BlockingIOError may occur, but socketpair typically ready
        # Send from a, recv on b
        sent = t_a.send(b"hello")
        assert sent == 5
        # B may need to handle BlockingIOError loop; but for small data it's available
        data = b""
        # Try recv until data or BlockingIOError
        import time
        for _ in range(10):
            try:
                data = t_b.recv(4096)
                if data:
                    break
            except BlockingIOError:
                time.sleep(0.01)
        assert data == b"hello"

        assert t_a.fileno() == a.fileno()
        assert not t_a.closed
        t_a.close()
        assert t_a.closed
        assert t_b.fileno() == b.fileno()
    finally:
        try:
            a.close()
        except: pass
        try:
            b.close()
        except: pass


def test_plain_transport_nonblocking():
    a, b = socket.socketpair()
    try:
        t = PlainTransport(a)
        # socket should be non-blocking
        assert not a.getblocking()
        # recv with no data should raise BlockingIOError
        # b hasn't sent yet, so recv should block
        with pytest.raises(BlockingIOError):
            t.recv(4096)
    finally:
        a.close()
        b.close()


def test_plain_transport_send_blocking():
    a, b = socket.socketpair()
    try:
        t_a = PlainTransport(a)
        # fill up buffer to cause BlockingIOError? Hard to test reliably,
        # but ensure send of empty returns 0
        assert t_a.send(b"") == 0
    finally:
        a.close()
        b.close()


def test_transport_is_abstract():
    assert issubclass(PlainTransport, Transport)
    # Transport is abstract
    with pytest.raises(TypeError):
        Transport()  # type: ignore


def test_plain_transport_close_idempotent():
    a, b = socket.socketpair()
    t = PlainTransport(a)
    t.close()
    t.close()  # second close should not raise
    assert t.closed
    b.close()


def test_plain_transport_invalid_sock():
    with pytest.raises(TypeError):
        PlainTransport("not a socket")  # type: ignore
