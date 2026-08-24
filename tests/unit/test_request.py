from http_server.http.headers import Headers
from http_server.http.request import Request


def test_request_basic():
    req = Request(
        method="GET",
        target="/hello?x=1",
        path="/hello",
        query_string="x=1",
        http_version="HTTP/1.1",
        headers=Headers([("Host", "localhost")]),
        body=b"",
    )
    assert req.method == "GET"
    assert req.target == "/hello?x=1"
    assert req.path == "/hello"
    assert req.query_string == "x=1"
    assert req.query == "x=1"
    assert req.http_version == "HTTP/1.1"
    assert req.headers.get("host") == "localhost"
    assert req.body == b""


def test_request_body():
    req = Request(
        method="POST",
        target="/submit",
        path="/submit",
        query_string="",
        http_version="HTTP/1.1",
        headers=Headers([("Content-Length", "5")]),
        body=b"hello",
    )
    assert req.body == b"hello"


def test_request_extensible_fields():
    req = Request(
        method="GET",
        target="/",
        path="/",
        query_string="",
        http_version="HTTP/1.1",
        headers=Headers(),
        body=b"",
    )
    # future fields should exist with defaults
    assert req.client_address is None
    assert req.route_params == {}
    assert req.context == {}
    req.route_params["id"] = "42"
    req.context["user"] = "alice"
    assert req.route_params["id"] == "42"


def test_request_dict_headers_coercion():
    # allow dict initialization
    req = Request(
        method="GET",
        target="/",
        path="/",
        query_string="",
        http_version="HTTP/1.1",
        headers={"Host": "localhost"},  # type: ignore[arg-type]
        body=b"",
    )
    assert req.headers.get("host") == "localhost"


def test_request_query_string_empty():
    req = Request(
        method="GET",
        target="/path",
        path="/path",
        query_string="",
        http_version="HTTP/1.1",
        headers=Headers(),
        body=b"",
    )
    assert req.query_string == ""
