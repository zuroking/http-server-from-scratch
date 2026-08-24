"""Unit tests for HTTP response serializer (Phase 4)."""

import pytest

from http_server.http.headers import Headers
from http_server.http.response import Response


def test_simple_200_response():
    resp = Response(status_code=200, headers=Headers(), body=b"Hello")
    data = resp.serialize()
    # Must start with status line
    assert data.startswith(b"HTTP/1.1 200 OK\r\n")
    # Must contain CRLF CRLF
    assert b"\r\n\r\n" in data
    # Body after header section
    assert data.endswith(b"Hello")
    # Must not use lone \n
    # Ensure no "\n" without preceding "\r"
    # Check that every \n is preceded by \r
    for i, c in enumerate(data):
        if c == 10:  # \n
            assert i > 0 and data[i - 1] == 13, "found lone \\n"
    # Content-Length correct
    assert b"Content-Length: 5\r\n" in data


def test_custom_status():
    resp = Response(status_code=404, body=b"Not found")
    data = resp.serialize()
    assert b"HTTP/1.1 404 Not Found\r\n" in data

    resp2 = Response(status_code=201, body=b"created")
    assert b"HTTP/1.1 201 Created\r\n" in resp2.serialize()

    resp3 = Response(status_code=500, body=b"err")
    assert b"HTTP/1.1 500 Internal Server Error\r\n" in resp3.serialize()


def test_custom_headers():
    headers = Headers([("Content-Type", "text/plain"), ("X-Custom", "1")])
    resp = Response(status_code=200, headers=headers, body=b"hi")
    data = resp.serialize()
    assert b"Content-Type: text/plain\r\n" in data
    assert b"X-Custom: 1\r\n" in data


def test_body_serialization():
    body = b"Hello World"
    resp = Response(status_code=200, body=body)
    data = resp.serialize()
    # Body exactly after header terminator
    _, _, after = data.partition(b"\r\n\r\n")
    assert after == body

    # Binary body
    binary = bytes([0, 1, 2, 255, 254, 13, 10, 0])
    resp2 = Response(status_code=200, body=binary)
    data2 = resp2.serialize()
    _, _, after2 = data2.partition(b"\r\n\r\n")
    assert after2 == binary


def test_automatic_content_length():
    resp = Response(status_code=200, body=b"12345")
    data = resp.serialize()
    assert b"Content-Length: 5\r\n" in data

    resp2 = Response(status_code=200, headers=Headers([("Content-Length", "5")]), body=b"12345")
    # Explicit matching should pass and not duplicate
    data2 = resp2.serialize()
    # Only one Content-Length
    assert data2.count(b"Content-Length:") == 1 or data2.count(b"content-length:") == 1 or data2.lower().count(b"content-length:") == 1

    resp3 = Response(status_code=200, body=b"")
    assert b"Content-Length: 0\r\n" in resp3.serialize()


def test_unicode_body_content_length():
    text = "Привет"
    body = text.encode("utf-8")
    # len("Привет") == 6, but utf-8 bytes == 12
    assert len(body) == 12
    assert len(text) == 6
    resp = Response(status_code=200, body=body)
    data = resp.serialize()
    assert b"Content-Length: 12\r\n" in data
    assert b"Content-Length: 6\r\n" not in data
    _, _, after = data.partition(b"\r\n\r\n")
    assert after == body


def test_empty_body():
    resp = Response(status_code=200, body=b"")
    data = resp.serialize()
    assert b"Content-Length: 0\r\n" in data
    assert data.endswith(b"\r\n\r\n")

    resp2 = Response(status_code=204, body=b"")
    data2 = resp2.serialize()
    # 204 must not include body, header terminator at end
    assert data2.endswith(b"\r\n\r\n")
    _, _, after = data2.partition(b"\r\n\r\n")
    assert after == b""


def test_multiple_headers():
    headers = Headers([("Content-Type", "text/html"), ("Cache-Control", "no-cache"), ("X-Test", "value")])
    resp = Response(status_code=200, headers=headers, body=b"hi")
    data = resp.serialize()
    assert b"Content-Type: text/html\r\n" in data
    assert b"Cache-Control: no-cache\r\n" in data
    assert b"X-Test: value\r\n" in data
    # Ensure order preservation? Headers preserves insertion order via dict
    # All headers end with \r\n
    assert data.count(b"\r\n") >= 5  # status + 3 headers + content-length + blank


def test_case_insensitive_content_length():
    # Explicit header with different casing should be recognized
    headers = Headers([("content-length", "3")])
    resp = Response(status_code=200, headers=headers, body=b"abc")
    data = resp.serialize()
    # Should not add duplicate, should keep original casing?
    # Lower count
    assert data.lower().count(b"content-length:") == 1
    assert b"abc" in data

    headers2 = Headers([("CONTENT-LENGTH", "3")])
    resp2 = Response(status_code=200, headers=headers2, body=b"abc")
    assert resp2.serialize().lower().count(b"content-length:") == 1

    # Explicit mismatched case should raise
    headers3 = Headers([("Content-Length", "5")])
    resp3 = Response(status_code=200, headers=headers3, body=b"abc")
    with pytest.raises(ValueError):
        resp3.serialize()

    # Case-insensitive lookup works in Headers, so injection via lower should be detected
    headers4 = Headers([("Content-length", "3")])
    resp4 = Response(status_code=200, headers=headers4, body=b"ab")  # body len 2 != 3
    with pytest.raises(ValueError):
        resp4.serialize()


def test_content_length_mismatch():
    # Explicit too small
    with pytest.raises(ValueError):
        Response(status_code=200, headers=Headers([("Content-Length", "10")]), body=b"short").serialize()
    # Explicit too large
    with pytest.raises(ValueError):
        Response(status_code=200, headers=Headers([("Content-Length", "2")]), body=b"hello").serialize()
    # Non-numeric
    with pytest.raises(ValueError):
        Response(status_code=200, headers=Headers([("Content-Length", "abc")]), body=b"hello").serialize()
    # Empty
    # Headers allows empty? But serializer should reject
    h = Headers()
    # Bypass validation for empty check: directly store empty via _store
    h._store["content-length"] = ("Content-Length", "")
    resp = Response(status_code=200, headers=h, body=b"hi")
    with pytest.raises(ValueError):
        resp.serialize()


def test_header_injection_name():
    # Headers.set should already reject CRLF in name
    with pytest.raises(ValueError):
        Headers([("X-Test\r\nInjected: true", "value")])

    with pytest.raises(ValueError):
        h = Headers()
        h.set("Bad\r\nName", "value")

    # Bypass Headers validation via direct _store to test serializer defense
    h2 = Headers()
    h2._store["x-test\r\ninjected"] = ("X-Test\r\nInjected: true", "value")
    resp = Response(status_code=200, headers=h2, body=b"hi")
    with pytest.raises(ValueError):
        resp.serialize()

    # Also test \n alone
    h3 = Headers()
    h3._store["x-inj"] = ("X-Test\nInjected", "value")
    resp3 = Response(status_code=200, headers=h3, body=b"hi")
    with pytest.raises(ValueError):
        resp3.serialize()


def test_header_injection_value():
    # Headers now validates value as well
    with pytest.raises(ValueError):
        Headers([("X-Test", "hello\r\nInjected: true")])

    with pytest.raises(ValueError):
        h = Headers()
        h.set("X-Test", "val\ninjected")

    # Bypass to test serializer depth
    h2 = Headers()
    h2._store["x-test"] = ("X-Test", "hello\r\nInjected: true")
    resp = Response(status_code=200, headers=h2, body=b"hi")
    with pytest.raises(ValueError):
        resp.serialize()

    h3 = Headers()
    h3._store["x-test"] = ("X-Test", "hello\nworld")
    resp3 = Response(status_code=200, headers=h3, body=b"hi")
    with pytest.raises(ValueError):
        resp3.serialize()


def test_http_10_response():
    resp = Response(status_code=200, body=b"ok")
    data = resp.serialize(http_version="HTTP/1.0")
    assert data.startswith(b"HTTP/1.0 200 OK\r\n")
    assert b"Content-Length: 2\r\n" in data


def test_http_11_response():
    resp = Response(status_code=200, body=b"ok")
    data = resp.serialize(http_version="HTTP/1.1")
    assert data.startswith(b"HTTP/1.1 200 OK\r\n")

    # Default is HTTP/1.1
    resp2 = Response(status_code=200, body=b"ok")
    assert resp2.serialize().startswith(b"HTTP/1.1")


def test_head_no_body():
    body = b"Hello World"  # 11 bytes
    resp = Response(status_code=200, body=body)
    data_get = resp.serialize(send_body=True)
    data_head = resp.serialize(send_body=False)

    # HEAD must have same headers (including Content-Length) but no body
    assert b"Content-Length: 11\r\n" in data_get
    assert b"Content-Length: 11\r\n" in data_head

    _, _, after_get = data_get.partition(b"\r\n\r\n")
    _, _, after_head = data_head.partition(b"\r\n\r\n")

    assert after_get == body
    assert after_head == b""
    # Ensure HEAD response length is just headers
    assert len(data_head) == len(data_get) - len(body)

    # Also test with explicit Content-Length matching
    resp2 = Response(status_code=200, headers=Headers([("Content-Length", "5")]), body=b"hello")
    data_head2 = resp2.serialize(send_body=False)
    assert b"Content-Length: 5\r\n" in data_head2
    assert data_head2.endswith(b"\r\n\r\n")


def test_204_no_body():
    resp = Response(status_code=204, body=b"should not be sent")
    data = resp.serialize()
    # Must end with header terminator, no body
    assert data.endswith(b"\r\n\r\n")
    _, _, after = data.partition(b"\r\n\r\n")
    assert after == b""
    # Content-Length must be 0 (auto)
    assert b"Content-Length: 0\r\n" in data

    # Even with send_body=True, body suppressed
    resp2 = Response(status_code=204, body=b"hello")
    assert resp2.serialize(send_body=True).partition(b"\r\n\r\n")[2] == b""

    # Explicit non-zero Content-Length for 204 should raise
    with pytest.raises(ValueError):
        Response(status_code=204, headers=Headers([("Content-Length", "5")]), body=b"hello").serialize()


def test_304_no_body():
    resp = Response(status_code=304, body=b"should not be sent")
    data = resp.serialize()
    assert data.endswith(b"\r\n\r\n")
    _, _, after = data.partition(b"\r\n\r\n")
    assert after == b""
    assert b"Content-Length: 0\r\n" in data

    with pytest.raises(ValueError):
        Response(status_code=304, headers=Headers([("Content-Length", "10")]), body=b"hello").serialize()


def test_1xx_no_body():
    for code in [100, 101, 102, 103]:
        resp = Response(status_code=code, body=b"should not be sent")
        data = resp.serialize()
        _, _, after = data.partition(b"\r\n\r\n")
        assert after == b"", f"1xx {code} should not have body"
        assert b"Content-Length: 0\r\n" in data

    # 1xx with explicit non-zero should raise
    with pytest.raises(ValueError):
        Response(status_code=100, headers=Headers([("Content-Length", "5")]), body=b"hi").serialize()


def test_status_reason_phrase():
    cases = {
        200: b"OK",
        400: b"Bad Request",
        404: b"Not Found",
        405: b"Method Not Allowed",
        408: b"Request Timeout",
        413: b"Content Too Large",
        414: b"URI Too Long",
        431: b"Request Header Fields Too Large",
        500: b"Internal Server Error",
        501: b"Not Implemented",
        505: b"HTTP Version Not Supported",
    }
    for code, phrase in cases.items():
        resp = Response(status_code=code, body=b"")
        data = resp.serialize()
        expected_line = b"HTTP/1.1 " + str(code).encode() + b" " + phrase
        assert expected_line in data

    # Custom reason override
    resp2 = Response(status_code=200, body=b"", reason="Super OK")
    assert b"HTTP/1.1 200 Super OK\r\n" in resp2.serialize()

    # Any 100-599 should be serializable, even if not in table
    resp3 = Response(status_code=418, body=b"teapot")
    data3 = resp3.serialize()
    assert b"HTTP/1.1 418" in data3

    resp4 = Response(status_code=599, body=b"")
    assert b"HTTP/1.1 599" in resp4.serialize()


def test_exact_output_bytes():
    # Check full exact output for simple case
    resp = Response(
        status_code=200,
        headers=Headers([("Content-Type", "text/plain")]),
        body=b"Hello",
    )
    data = resp.serialize()
    # Content-Length auto =5, order: provided headers then auto Content-Length?
    # Our implementation adds Content-Length at end if missing
    expected = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 5\r\n\r\nHello"
    assert data == expected


def test_no_extra_lf():
    resp = Response(status_code=200, body=b"hi")
    data = resp.serialize()
    # Count \r\n must be at least status + headers + blank
    # Ensure no isolated \n
    # Already checked in simple test but repeat
    assert b"\n" in data
    assert data.count(b"\r\n") >= 3
    # Replace all \r\n and check no \n remains
    without_crlf = data.replace(b"\r\n", b"")
    assert b"\n" not in without_crlf
    assert b"\r" not in without_crlf


def test_headers_crlf_correctness():
    resp = Response(status_code=200, headers=Headers([("A", "1"), ("B", "2")]), body=b"hi")
    data = resp.serialize()
    header_section, _, body = data.partition(b"\r\n\r\n")
    # Header section must end with no extra CRLF beyond separator handled
    # header_section contains status + headers (no trailing blank line)
    lines = header_section.split(b"\r\n")
    # First line status
    assert lines[0].startswith(b"HTTP/1.1 200")
    # Subsequent lines are headers
    for line in lines[1:]:
        assert b":" in line
        assert not line.endswith(b"\r")
        assert b"\r" not in line
        assert b"\n" not in line


def test_invalid_http_version():
    resp = Response(status_code=200, body=b"hi")
    with pytest.raises(ValueError):
        resp.serialize(http_version="HTTP/1.3")
    with pytest.raises(ValueError):
        resp.serialize(http_version="HTTP/2.0")
    with pytest.raises(ValueError):
        resp.serialize(http_version="garbage")
    with pytest.raises(ValueError):
        resp.serialize(http_version="HTTP/1.1\r\nInjected: true")
    with pytest.raises(ValueError):
        resp.serialize(http_version="HTTP/1.1\n")


def test_invalid_status_code():
    with pytest.raises(ValueError):
        Response(status_code=99, body=b"").serialize()
    with pytest.raises(ValueError):
        Response(status_code=600, body=b"").serialize()
    with pytest.raises(ValueError):
        Response(status_code=0, body=b"").serialize()
    with pytest.raises(ValueError):
        Response(status_code="200", body=b"").serialize()  # type: ignore


def test_header_injection_via_reason():
    with pytest.raises(ValueError):
        Response(status_code=200, body=b"", reason="OK\r\nInjected: true").serialize()


def test_unicode_headers_rejected():
    # Headers with non-latin1 should be rejected
    h = Headers()
    h._store["x-test"] = ("X-Test", "Привет")
    resp = Response(status_code=200, headers=h, body=b"hi")
    with pytest.raises(ValueError):
        resp.serialize()


def test_body_suppression_for_head_with_204():
    # HEAD + 204: still no body, Content-Length 0
    resp = Response(status_code=204, body=b"hello")
    data = resp.serialize(send_body=False)
    _, _, after = data.partition(b"\r\n\r\n")
    assert after == b""
    assert b"Content-Length: 0\r\n" in data
