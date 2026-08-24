import pytest
from http_server.http.headers import Headers


def test_case_insensitive_lookup():
    h = Headers([("Host", "example.com")])
    assert h.get("Host") == "example.com"
    assert h.get("host") == "example.com"
    assert h.get("HOST") == "example.com"
    assert h.get("HoSt") == "example.com"
    assert "host" in h
    assert "HOST" in h
    assert "HoSt" in h


def test_set_and_get():
    h = Headers()
    h.set("Content-Type", "text/plain")
    assert h.get("content-type") == "text/plain"
    assert h["Content-Type"] == "text/plain"
    assert h["content-type"] == "text/plain"


def test_overwrite():
    h = Headers()
    h.set("Host", "a.com")
    h.set("host", "b.com")
    assert h.get("HOST") == "b.com"
    assert len(h) == 1


def test_add_alias():
    h = Headers()
    h.add("X-Test", "1")
    assert h.get("x-test") == "1"


def test_contains():
    h = Headers([("Content-Length", "5")])
    assert "content-length" in h
    assert "Content-Length" in h
    assert "missing" not in h


def test_len_and_iter():
    h = Headers([("Host", "a"), ("Content-Length", "5")])
    assert len(h) == 2
    names = list(h)
    assert "Host" in names
    assert "Content-Length" in names


def test_items():
    h = Headers([("Host", "example.com")])
    items = list(h.items())
    assert ("Host", "example.com") in items


def test_equality_case_insensitive():
    h1 = Headers([("Host", "a.com")])
    h2 = Headers([("host", "a.com")])
    h3 = Headers([("Host", "b.com")])
    assert h1 == h2
    assert h1 != h3


def test_invalid_name_empty():
    h = Headers()
    with pytest.raises(ValueError):
        h.set("", "value")


def test_invalid_name_with_colon():
    h = Headers()
    with pytest.raises(ValueError):
        h.set("Bad:Name", "value")


def test_invalid_name_with_space():
    h = Headers()
    with pytest.raises(ValueError):
        h.set("Bad Name", "value")


def test_preserve_original_casing():
    h = Headers()
    h.set("Content-Type", "text/html")
    assert h.get_original_name("content-type") == "Content-Type"
    h.set("content-type", "application/json")
    # last set should overwrite original casing? Actually we preserve last set's casing
    assert h.get_original_name("CONTENT-TYPE") == "content-type"


def test_get_with_default():
    h = Headers()
    assert h.get("Missing", "default") == "default"
    assert h.get("Missing") is None


def test_key_error():
    h = Headers()
    with pytest.raises(KeyError):
        _ = h["Missing"]


def test_to_dict():
    h = Headers([("Host", "a"), ("X-Custom", "1")])
    d = h.to_dict()
    assert d["Host"] == "a"
    assert d["X-Custom"] == "1"
