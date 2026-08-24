"""Unit tests for routing (Phase 5)."""

import pytest

from http_server.http.errors import MethodNotAllowedError, NotFoundError
from http_server.http.request import Request
from http_server.http.headers import Headers
from http_server.routing.router import Router
from http_server.routing.route import Route


def dummy_handler(request):  # type: ignore
    return "ok"


def dummy_handler2(request):  # type: ignore
    return "ok2"


# -- basic --

def test_exact_route():
    router = Router()
    router.add("GET", "/", dummy_handler)
    handler, params = router.match("GET", "/")
    assert handler is dummy_handler
    assert params == {}


def test_exact_route_with_different_path():
    router = Router()
    router.add("GET", "/hello", dummy_handler)
    handler, params = router.match("GET", "/hello")
    assert handler is dummy_handler
    with pytest.raises(NotFoundError) as exc:
        router.match("GET", "/world")
    assert exc.value.status_code == 404


def test_named_param_single():
    router = Router()
    router.add("GET", "/users/{id}", dummy_handler)
    handler, params = router.match("GET", "/users/42")
    assert handler is dummy_handler
    assert params == {"id": "42"}

    handler, params = router.match("GET", "/users/abc")
    assert params == {"id": "abc"}


def test_named_param_multiple():
    router = Router()
    router.add("GET", "/users/{id}/posts/{post_id}", dummy_handler)
    handler, params = router.match("GET", "/users/42/posts/99")
    assert params == {"id": "42", "post_id": "99"}


def test_param_vs_exact_precedence():
    # Exact should be preferred if both could match? insertion order matters.
    router = Router()
    router.add("GET", "/users/me", dummy_handler)
    router.add("GET", "/users/{id}", dummy_handler2)
    handler, params = router.match("GET", "/users/me")
    # First added exact should match
    assert handler is dummy_handler
    assert params == {}

    # param for other id
    handler, params = router.match("GET", "/users/42")
    assert handler is dummy_handler2
    assert params == {"id": "42"}


def test_404():
    router = Router()
    router.add("GET", "/hello", dummy_handler)
    with pytest.raises(NotFoundError) as exc:
        router.match("GET", "/notfound")
    assert exc.value.status_code == 404

    # Empty router
    router2 = Router()
    with pytest.raises(NotFoundError):
        router2.match("GET", "/")


def test_405():
    router = Router()
    router.add("GET", "/hello", dummy_handler)
    router.add("POST", "/hello", dummy_handler2)
    # GET exists, POST exists, but DELETE should be 405
    with pytest.raises(MethodNotAllowedError) as exc:
        router.match("DELETE", "/hello")
    assert exc.value.status_code == 405
    assert set(exc.value.allowed) == {"GET", "POST"}
    assert "GET" in exc.value.allow_header
    assert "POST" in exc.value.allow_header

    # Path matched but method not allowed, not 404
    router2 = Router()
    router2.add("GET", "/users/{id}", dummy_handler)
    with pytest.raises(MethodNotAllowedError) as exc2:
        router2.match("POST", "/users/42")
    assert exc2.value.status_code == 405

    # No path match -> 404 not 405
    with pytest.raises(NotFoundError):
        router2.match("GET", "/other/42")


def test_allow_header_sorted():
    router = Router()
    router.add("GET", "/", dummy_handler)
    router.add("POST", "/", dummy_handler2)
    router.add("DELETE", "/", dummy_handler)
    try:
        router.match("PUT", "/")
        assert False, "should have raised"
    except MethodNotAllowedError as e:
        # Allow should be sorted
        assert e.allow_header == "DELETE, GET, POST"
        assert e.allowed == tuple(sorted(e.allowed))


def test_allowed_methods_helper():
    router = Router()
    router.add("GET", "/resource", dummy_handler)
    router.add("POST", "/resource", dummy_handler2)
    allowed = router.allowed_methods("/resource")
    assert allowed == {"GET", "POST"}
    assert router.allowed_methods("/nope") == set()


def test_query_string_ignored():
    router = Router()
    router.add("GET", "/hello", dummy_handler)
    # match should strip query string
    handler, params = router.match("GET", "/hello?x=1&y=2")
    assert handler is dummy_handler


def test_trailing_slash_normalized():
    router = Router()
    router.add("GET", "/hello", dummy_handler)
    # Both "/hello" and "/hello/" should match due to normalization
    handler, _ = router.match("GET", "/hello/")
    assert handler is dummy_handler
    handler2, _ = router.match("GET", "/hello")
    assert handler2 is dummy_handler

    router2 = Router()
    router2.add("GET", "/hello/", dummy_handler)
    handler3, _ = router2.match("GET", "/hello")
    assert handler3 is dummy_handler


def test_case_insensitive_method():
    router = Router()
    router.add("GET", "/hello", dummy_handler)
    handler, _ = router.match("get", "/hello")
    assert handler is dummy_handler
    handler, _ = router.match("GeT", "/hello")
    assert handler is dummy_handler


def test_head_fallback_to_get():
    router = Router()
    router.add("GET", "/hello", dummy_handler)
    # HEAD should fallback to GET if HEAD not explicitly registered
    handler, params = router.match("HEAD", "/hello")
    assert handler is dummy_handler

    # If HEAD explicitly registered, it should take precedence
    router2 = Router()
    router2.add("GET", "/hello", dummy_handler)
    router2.add("HEAD", "/hello", dummy_handler2)
    handler2, _ = router2.match("HEAD", "/hello")
    assert handler2 is dummy_handler2


def test_resolve_with_request_object():
    router = Router()
    router.add("GET", "/users/{id}", dummy_handler)
    req = Request(
        method="GET",
        target="/users/42",
        path="/users/42",
        query_string="",
        http_version="HTTP/1.1",
        headers=Headers(),
        body=b"",
    )
    match = router.resolve(req)
    assert match.handler is dummy_handler
    assert match.params == {"id": "42"}
    assert match.route.pattern == "/users/{id}"


def test_duplicate_route_raises():
    router = Router()
    router.add("GET", "/hello", dummy_handler)
    with pytest.raises(ValueError):
        router.add("GET", "/hello", dummy_handler2)
    # Different method same path is allowed
    router.add("POST", "/hello", dummy_handler2)
    assert len(router) == 2


def test_invalid_route_pattern():
    router = Router()
    with pytest.raises(ValueError):
        router.add("GET", "hello", dummy_handler)  # missing leading slash
    with pytest.raises(ValueError):
        router.add("GET", "/users/{123id}", dummy_handler)  # invalid param name
    with pytest.raises(ValueError):
        router.add("GET", "/users/{id}/{id}", dummy_handler)  # duplicate param
    with pytest.raises(ValueError):
        router.add("GET", "/users//profile", dummy_handler)


def test_invalid_method():
    router = Router()
    with pytest.raises(ValueError):
        router.add("", "/hello", dummy_handler)
    with pytest.raises(ValueError):
        router.add("GET BAD", "/hello", dummy_handler)


def test_route_match_direct():
    route = Route(method="GET", pattern="/users/{id}", handler=dummy_handler)
    assert route.match("/users/42") == {"id": "42"}
    assert route.match("/users/") is None
    assert route.match("/users/42/posts") is None
    assert route.match("/other/42") is None
    assert route.method == "GET"
    assert route.param_names == ("id",)


def test_exact_vs_param_edge():
    router = Router()
    router.add("GET", "/", dummy_handler)
    handler, params = router.match("GET", "/")
    assert params == {}
    with pytest.raises(NotFoundError):
        router.match("GET", "/extra")


def test_options_returns_allow():
    router = Router()
    router.add("GET", "/hello", dummy_handler)
    router.add("POST", "/hello", dummy_handler2)
    # OPTIONS not registered -> should be 405 with Allow
    with pytest.raises(MethodNotAllowedError) as exc:
        router.match("OPTIONS", "/hello")
    assert set(exc.value.allowed) == {"GET", "POST"}

    # If OPTIONS explicitly registered, it should match
    router2 = Router()
    router2.add("OPTIONS", "/hello", dummy_handler)
    router2.add("GET", "/hello", dummy_handler2)
    handler, _ = router2.match("OPTIONS", "/hello")
    assert handler is dummy_handler


def test_router_len_and_iter():
    router = Router()
    assert len(router) == 0
    router.add("GET", "/", dummy_handler)
    router.add("POST", "/", dummy_handler2)
    assert len(router) == 2
    methods = [r.method for r in router]
    assert "GET" in methods
    assert "POST" in methods


def test_lookup_returns_none_for_404():
    router = Router()
    router.add("GET", "/hello", dummy_handler)
    assert router.lookup("GET", "/hello") is not None
    assert router.lookup("GET", "/notfound") is None


def test_find_all():
    router = Router()
    router.add("GET", "/users/{id}", dummy_handler)
    router.add("POST", "/users/{id}", dummy_handler2)
    router.add("GET", "/posts/{id}", dummy_handler)
    found = router.find_all("/users/42")
    assert len(found) == 2
    assert all(r.pattern == "/users/{id}" for r in found)


def test_multiple_params_with_same_handler():
    router = Router()
    router.add("GET", "/a/{x}/b/{y}/c/{z}", dummy_handler)
    handler, params = router.match("GET", "/a/1/b/2/c/3")
    assert params == {"x": "1", "y": "2", "z": "3"}


def test_route_pattern_root_vs_empty():
    route_root = Route(method="GET", pattern="/", handler=dummy_handler)
    assert route_root.match("/") == {}
    assert route_root.match("") is None

    route_a = Route(method="GET", pattern="/a", handler=dummy_handler)
    assert route_a.match("/a") == {}
    assert route_a.match("/a/") == {}
    assert route_a.match("/a/b") is None
