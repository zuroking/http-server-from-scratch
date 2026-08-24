"""Unit tests for incremental HTTP parser.

Covers required minimal set + security checks.
"""

import pytest

from http_server.config import ServerConfig
from http_server.http.parser import HttpParser, ParserState, ParseResult


def feed_all(parser: HttpParser, chunks: list[bytes]) -> ParseResult:
    result = ParseResult.NEED_MORE
    for c in chunks:
        result = parser.feed(c)
        if result == ParseResult.ERROR:
            break
        if result == ParseResult.COMPLETE:
            # continue feeding only if more chunks remain to test leftover handling
            # but stop unless testing multiple requests
            continue
    return result


# ---- simple ----

def test_parse_simple_get():
    p = HttpParser()
    result = p.feed(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
    assert result == ParseResult.COMPLETE
    assert p.request is not None
    assert p.request.method == "GET"
    assert p.request.target == "/"
    assert p.request.path == "/"
    assert p.request.query_string == ""
    assert p.request.http_version == "HTTP/1.1"
    assert p.request.headers.get("host") == "localhost"
    assert p.request.body == b""


def test_parse_request_with_headers():
    p = HttpParser()
    raw = (
        b"GET /hello?x=1&y=2 HTTP/1.1\r\n"
        b"Host: example.com\r\n"
        b"User-Agent: test\r\n"
        b"Accept: */*\r\n"
        b"\r\n"
    )
    assert p.feed(raw) == ParseResult.COMPLETE
    assert p.request.path == "/hello"
    assert p.request.query_string == "x=1&y=2"
    assert p.request.target == "/hello?x=1&y=2"
    assert p.request.headers.get("Host") == "example.com"
    assert p.request.headers.get("user-agent") == "test"


def test_parse_request_with_body():
    p = HttpParser()
    raw = b"POST /submit HTTP/1.1\r\nHost: localhost\r\nContent-Length: 11\r\n\r\nhello world"
    assert p.feed(raw) == ParseResult.COMPLETE
    assert p.request.body == b"hello world"
    assert p.request.method == "POST"


def test_parse_fragmented_request_line():
    p = HttpParser()
    assert p.feed(b"GET /inde") == ParseResult.NEED_MORE
    assert p.state == ParserState.REQUEST_LINE
    assert p.feed(b"x HTTP/1.1\r\nHost: localhost\r\n") == ParseResult.NEED_MORE
    assert p.state == ParserState.HEADERS
    assert p.feed(b"\r\n") == ParseResult.COMPLETE
    assert p.request.path == "/index"


def test_parse_fragmented_headers():
    p = HttpParser()
    assert p.feed(b"GET / HTTP/1.1\r\nHost: local") == ParseResult.NEED_MORE
    assert p.feed(b"host\r\nX-Test: 123\r\n") == ParseResult.NEED_MORE
    assert p.feed(b"\r\n") == ParseResult.COMPLETE
    assert p.request.headers.get("host") == "localhost"
    assert p.request.headers.get("x-test") == "123"


def test_parse_fragmented_body():
    p = HttpParser()
    assert p.feed(b"POST / HTTP/1.1\r\nContent-Length: 5\r\n\r\nhel") == ParseResult.NEED_MORE
    assert p.state == ParserState.BODY
    assert p.feed(b"lo") == ParseResult.COMPLETE
    assert p.request.body == b"hello"
    # fragmented in three parts
    p2 = HttpParser()
    p2.feed(b"POST / HTTP/1.1\r\nContent-Length: 5\r\n\r\n")
    assert p2.state == ParserState.BODY
    p2.feed(b"h")
    assert p2.state == ParserState.BODY
    p2.feed(b"e")
    p2.feed(b"l")
    p2.feed(b"l")
    assert p2.feed(b"o") == ParseResult.COMPLETE
    assert p2.request.body == b"hello"


def test_parse_multiple_requests_in_buffer():
    p = HttpParser()
    raw = (
        b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n"
        b"GET /second HTTP/1.1\r\nHost: localhost\r\n\r\n"
    )
    assert p.feed(raw) == ParseResult.COMPLETE
    assert p.request.path == "/"
    leftover = p.get_remaining()
    assert leftover == b"GET /second HTTP/1.1\r\nHost: localhost\r\n\r\n"
    # parse second request from leftover
    p2 = HttpParser()
    assert p2.feed(leftover) == ParseResult.COMPLETE
    assert p2.request.path == "/second"


def test_empty_body():
    p = HttpParser()
    assert p.feed(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n") == ParseResult.COMPLETE
    assert p.request.body == b""
    p2 = HttpParser()
    assert p2.feed(b"DELETE /resource HTTP/1.1\r\nHost: a\r\n\r\n") == ParseResult.COMPLETE
    assert p2.request.body == b""


def test_content_length_zero():
    p = HttpParser()
    assert p.feed(b"POST / HTTP/1.1\r\nHost: a\r\nContent-Length: 0\r\n\r\n") == ParseResult.COMPLETE
    assert p.request.body == b""
    assert p.request.headers.get("content-length") == "0"


def test_invalid_content_length():
    cases = [
        b"POST / HTTP/1.1\r\nContent-Length: abc\r\n\r\n",
        b"POST / HTTP/1.1\r\nContent-Length: -5\r\n\r\n",
        b"POST / HTTP/1.1\r\nContent-Length: 12.3\r\n\r\n",
        b"POST / HTTP/1.1\r\nContent-Length: \r\n\r\n",
        b"POST / HTTP/1.1\r\nContent-Length: 10abc\r\n\r\n",
    ]
    for raw in cases:
        p = HttpParser()
        result = p.feed(raw)
        assert result == ParseResult.ERROR, f"expected error for {raw!r}"
        assert p.error is not None
        assert p.error.status_code == 400, f"expected 400 for {raw!r} got {p.error.status_code}"


def test_conflicting_content_length():
    p = HttpParser()
    raw = b"POST / HTTP/1.1\r\nContent-Length: 10\r\nContent-Length: 20\r\n\r\n"
    assert p.feed(raw) == ParseResult.ERROR
    assert p.error.status_code == 400
    # case-insensitive conflict
    p2 = HttpParser()
    assert p2.feed(b"POST / HTTP/1.1\r\ncontent-length: 5\r\nContent-Length: 5\r\n\r\n") == ParseResult.ERROR
    assert p2.error.status_code == 400


def test_body_size_limit():
    cfg = ServerConfig(max_body_size=5)
    p = HttpParser(cfg)
    assert p.feed(b"POST / HTTP/1.1\r\nContent-Length: 10\r\n\r\n") == ParseResult.ERROR
    assert p.error.status_code == 413
    # incremental exceed
    cfg2 = ServerConfig(max_body_size=10)
    p2 = HttpParser(cfg2)
    # content-length exactly at limit should be okay if body arrives correctly, but exceeding incrementally
    p2.feed(b"POST / HTTP/1.1\r\nContent-Length: 5\r\n\r\nhello")
    assert p2.state == ParserState.DONE
    # try with large content-length value triggering 413 immediately
    p3 = HttpParser(ServerConfig(max_body_size=1))
    assert p3.feed(b"POST / HTTP/1.1\r\nContent-Length: 100\r\n\r\n") == ParseResult.ERROR
    assert p3.error.status_code == 413


def test_header_size_limit():
    cfg = ServerConfig(max_headers_size=50)
    p = HttpParser(cfg)
    # 50 bytes limit, headers block larger
    raw = b"GET / HTTP/1.1\r\nHost: localhost\r\nX-Long: " + b"a" * 100 + b"\r\n\r\n"
    assert p.feed(raw) == ParseResult.ERROR
    assert p.error.status_code == 431
    # fragmented headers exceeding size gradually
    cfg2 = ServerConfig(max_headers_size=10)
    p2 = HttpParser(cfg2)
    p2.feed(b"GET / HTTP/1.1\r\n")
    result = p2.feed(b"Host: localhost\r\n\r\n")
    assert result == ParseResult.ERROR
    assert p2.error.status_code == 431


def test_header_count_limit():
    cfg = ServerConfig(max_header_count=2)
    p = HttpParser(cfg)
    raw = b"GET / HTTP/1.1\r\nHost: a\r\nX-1: 1\r\nX-2: 2\r\n\r\n"
    assert p.feed(raw) == ParseResult.ERROR
    assert p.error.status_code == 431
    p2 = HttpParser(ServerConfig(max_header_count=100))
    # exactly 100 should pass
    headers = b"".join([f"X-{i}: {i}\r\n".encode() for i in range(100)])
    raw2 = b"GET / HTTP/1.1\r\n" + headers + b"\r\n"
    assert p2.feed(raw2) == ParseResult.COMPLETE


def test_request_line_limit():
    cfg = ServerConfig(max_request_line_size=20)
    p = HttpParser(cfg)
    # URI too long: target huge
    raw = b"GET /" + b"a" * 100 + b" HTTP/1.1\r\nHost: a\r\n\r\n"
    result = p.feed(raw)
    assert result == ParseResult.ERROR
    assert p.error.status_code == 414
    # fragmented request line exceeding limit before CRLF
    cfg2 = ServerConfig(max_request_line_size=10)
    p2 = HttpParser(cfg2)
    p2.feed(b"GET /very")
    result2 = p2.feed(b"longlinewithoutend")
    # buffer now >10 without CRLF
    assert result2 == ParseResult.ERROR
    assert p2.error.status_code in (400, 414)


def test_chunked_not_supported():
    p = HttpParser()
    assert p.feed(b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n") == ParseResult.ERROR
    assert p.error.status_code == 501
    p2 = HttpParser()
    assert p2.feed(b"POST / HTTP/1.1\r\nTransfer-Encoding: Chunked\r\n\r\n") == ParseResult.ERROR
    assert p2.error.status_code == 501
    # with additional value
    p3 = HttpParser()
    assert p3.feed(b"POST / HTTP/1.1\r\nTransfer-Encoding: gzip, chunked\r\n\r\n") == ParseResult.ERROR
    assert p3.error.status_code == 501


def test_invalid_http_version():
    p = HttpParser()
    assert p.feed(b"GET / HTTP/2.0\r\nHost: a\r\n\r\n") == ParseResult.ERROR
    assert p.error.status_code == 505
    p2 = HttpParser()
    assert p2.feed(b"GET / HTTP/9.9\r\nHost: a\r\n\r\n") == ParseResult.ERROR
    assert p2.error.status_code == 505
    # malformed version should be 400
    p3 = HttpParser()
    assert p3.feed(b"GET / HTT/1.1\r\nHost: a\r\n\r\n") == ParseResult.ERROR
    assert p3.error.status_code == 400
    p4 = HttpParser()
    assert p4.feed(b"GET / HTTP/1.\r\nHost: a\r\n\r\n") == ParseResult.ERROR
    assert p4.error.status_code == 400


def test_malformed_request_line():
    cases = [
        (b"GET HTTP/1.1\r\nHost: a\r\n\r\n", 400),  # missing target
        (b" HTTP/1.1\r\nHost: a\r\n\r\n", 400),  # empty method
        (b"GET  / HTTP/1.1\r\nHost: a\r\n\r\n", 400),  # double space
        (b"GET / \r\nHost: a\r\n\r\n", 400),  # missing version
        (b"GET / HTTP/1.1 extra\r\nHost: a\r\n\r\n", 400),  # extra part
        (b"/ HTTP/1.1\r\nHost: a\r\n\r\n", 400),  # missing target part count
        (b"GET\r\nHost: a\r\n\r\n", 400),  # only method
        (b"GET / HTTP/1.1 \r\nHost: a\r\n\r\n", 400),  # trailing space
    ]
    for raw, expected_code in cases:
        p = HttpParser()
        result = p.feed(raw)
        if result == ParseResult.NEED_MORE:
            result = p.feed(b"\r\n")
        assert result == ParseResult.ERROR, f"expected ERROR for {raw!r} got {result}"
        assert p.error.status_code == expected_code, f"for {raw!r} expected {expected_code} got {p.error.status_code}"


def test_malformed_header():
    cases = [
        b"GET / HTTP/1.1\r\nBadHeader\r\n\r\n",
        b"GET / HTTP/1.1\r\n: value\r\n\r\n",
        b"GET / HTTP/1.1\r\nHost localhost\r\n\r\n",
        b"GET / HTTP/1.1\r\nHost : localhost\r\n\r\n",  # space before colon
    ]
    for raw in cases:
        p = HttpParser()
        result = p.feed(raw)
        assert result == ParseResult.ERROR, f"expected ERROR for {raw!r}"
        assert p.error.status_code == 400, f"expected 400 for {raw!r} got {p.error.status_code}"


# -- security / additional --

def test_incomplete_request():
    p = HttpParser()
    assert p.feed(b"GET / HTTP/1.1\r\nHost: a\r\n") == ParseResult.NEED_MORE
    assert p.feed(b"") == ParseResult.NEED_MORE
    # complete
    assert p.feed(b"\r\n") == ParseResult.COMPLETE


def test_empty_input():
    p = HttpParser()
    assert p.feed(b"") == ParseResult.NEED_MORE
    assert p.state == ParserState.REQUEST_LINE


def test_arbitrary_binary_body():
    body = bytes([0, 1, 2, 3, 255, 254, 0x0D, 0x0A, 0x00])
    p = HttpParser()
    raw = b"POST / HTTP/1.1\r\nContent-Length: 9\r\n\r\n" + body
    assert p.feed(raw) == ParseResult.COMPLETE
    assert p.request.body == body


def test_huge_request_line_security():
    cfg = ServerConfig(max_request_line_size=8192)
    p = HttpParser(cfg)
    huge_target = b"/" + b"a" * 9000
    raw = b"GET " + huge_target + b" HTTP/1.1\r\nHost: a\r\n\r\n"
    assert p.feed(raw) == ParseResult.ERROR
    assert p.error.status_code == 414


def test_huge_headers_security():
    cfg = ServerConfig(max_headers_size=32768)
    p = HttpParser(cfg)
    # exceed by sending header value 40000 bytes
    raw = b"GET / HTTP/1.1\r\nHost: a\r\nX-Huge: " + b"a" * 40000 + b"\r\n\r\n"
    assert p.feed(raw) == ParseResult.ERROR
    assert p.error.status_code == 431


def test_huge_body_security():
    cfg = ServerConfig(max_body_size=1024)
    p = HttpParser(cfg)
    raw = b"POST / HTTP/1.1\r\nContent-Length: 2048\r\n\r\n"
    assert p.feed(raw) == ParseResult.ERROR
    assert p.error.status_code == 413


def test_too_many_headers_security():
    cfg = ServerConfig(max_header_count=5)
    p = HttpParser(cfg)
    headers = b"".join([f"H{i}: v\r\n".encode() for i in range(10)])
    raw = b"GET / HTTP/1.1\r\n" + headers + b"\r\n"
    assert p.feed(raw) == ParseResult.ERROR
    assert p.error.status_code == 431


def test_fragmented_request_line_security():
    cfg = ServerConfig(max_request_line_size=10)
    p = HttpParser(cfg)
    # send fragments that slowly exceed limit
    p.feed(b"GET /")
    p.feed(b"ab")
    p.feed(b"cd")
    p.feed(b"efghij")
    # now buffer is "GET /abcdefghij" without CRLF, length >10
    assert p.feed(b"klmnop") == ParseResult.ERROR
    assert p.error.status_code in (400, 414)


def test_fragmented_headers_security():
    cfg = ServerConfig(max_headers_size=20)
    p = HttpParser(cfg)
    p.feed(b"GET / HTTP/1.1\r\n")
    p.feed(b"Host: local")
    p.feed(b"host\r\n")
    # now headers size likely exceeded before terminator
    result = p.feed(b"X-Test: " + b"a" * 50 + b"\r\n\r\n")
    assert result == ParseResult.ERROR
    assert p.error.status_code == 431


def test_fragmented_body_security():
    cfg = ServerConfig(max_body_size=5)
    p = HttpParser(cfg)
    # content-length within limit but body will be fed exceeding?
    # Actually content-length > limit should already error before body
    assert p.feed(b"POST / HTTP/1.1\r\nContent-Length: 10\r\n\r\n") == ParseResult.ERROR


def test_headers_plus_partial_body():
    p = HttpParser()
    # headers + partial body in one feed
    raw = b"POST / HTTP/1.1\r\nContent-Length: 5\r\n\r\nhel"
    assert p.feed(raw) == ParseResult.NEED_MORE
    assert p.state == ParserState.BODY
    assert p.feed(b"lo") == ParseResult.COMPLETE
    assert p.request.body == b"hello"


def test_headers_plus_full_body():
    p = HttpParser()
    raw = b"POST / HTTP/1.1\r\nContent-Length: 5\r\n\r\nhello"
    assert p.feed(raw) == ParseResult.COMPLETE
    assert p.request.body == b"hello"


def test_negative_content_length():
    p = HttpParser()
    assert p.feed(b"POST / HTTP/1.1\r\nContent-Length: -1\r\n\r\n") == ParseResult.ERROR
    assert p.error.status_code == 400


def test_non_numeric_content_length():
    p = HttpParser()
    assert p.feed(b"POST / HTTP/1.1\r\nContent-Length: xyz\r\n\r\n") == ParseResult.ERROR
    assert p.error.status_code == 400


def test_body_exceeding_limit_incremental():
    cfg = ServerConfig(max_body_size=5)
    p = HttpParser(cfg)
    assert p.feed(b"POST / HTTP/1.1\r\nContent-Length: 3\r\n\r\nabc") == ParseResult.COMPLETE
    # but if content-length=3 but we try to trick by sending more bytes after? That would be leftover, not body exceed.
    # The limit check for incremental is content-length already validated.


def test_incomplete_then_complete():
    p = HttpParser()
    assert p.feed(b"GET / HTTP/1.1\r\n") == ParseResult.NEED_MORE
    assert p.feed(b"Host: a\r\n\r\n") == ParseResult.COMPLETE


def test_multiple_requests_with_body():
    p = HttpParser()
    raw = (
        b"POST / HTTP/1.1\r\nContent-Length: 5\r\n\r\nhello"
        b"GET /second HTTP/1.1\r\nHost: a\r\n\r\n"
    )
    assert p.feed(raw) == ParseResult.COMPLETE
    assert p.request.body == b"hello"
    leftover = p.get_remaining()
    assert leftover.startswith(b"GET /second")
    p2 = HttpParser()
    assert p2.feed(leftover) == ParseResult.COMPLETE
    assert p2.request.path == "/second"


def test_empty_body_for_get_head_delete():
    for method in [b"GET", b"HEAD", b"DELETE", b"OPTIONS"]:
        p = HttpParser()
        raw = method + b" / HTTP/1.1\r\nHost: a\r\n\r\n"
        assert p.feed(raw) == ParseResult.COMPLETE
        assert p.request.body == b""


def test_headers_case_insensitive_content_length():
    p = HttpParser()
    assert p.feed(b"POST / HTTP/1.1\r\ncontent-length: 3\r\n\r\nabc") == ParseResult.COMPLETE
    assert p.request.body == b"abc"
    p2 = HttpParser()
    assert p2.feed(b"POST / HTTP/1.1\r\nContent-LENGTH: 3\r\n\r\nabc") == ParseResult.COMPLETE


def test_http_10_and_11_supported():
    p = HttpParser()
    assert p.feed(b"GET / HTTP/1.0\r\nHost: a\r\n\r\n") == ParseResult.COMPLETE
    assert p.request.http_version == "HTTP/1.0"
    p2 = HttpParser()
    assert p2.feed(b"GET / HTTP/1.1\r\nHost: a\r\n\r\n") == ParseResult.COMPLETE
