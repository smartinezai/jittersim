"""The dashboard page is sent compressed and a reload of an unchanged page gets 304."""

import gzip
import io
import json

from jittersim.api import server


class _Connection:
    def settimeout(self, value):
        pass


def _respond(request_headers, *, body=None, content_type="text/html; charset=utf-8"):
    handler = object.__new__(server.ControlRequestHandler)
    handler.connection = _Connection()
    handler.wfile = io.BytesIO()
    handler.request_version = "HTTP/1.1"
    handler.requestline = "GET / HTTP/1.1"
    handler.command = "GET"
    handler.headers = request_headers
    handler.log_request = lambda *args, **kwargs: None
    if body is None:
        body = server.dashboard_page_bytes()
    handler._send_response(status=200, content_type=content_type, body=body)
    head, _, payload = handler.wfile.getvalue().partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    headers = {}
    for line in lines[1:]:
        name, _, value = line.partition(": ")
        headers[name.lower()] = value
    return int(lines[0].split()[1]), headers, payload


def test_the_page_is_compressed_when_the_browser_accepts_gzip():
    status, headers, payload = _respond({"Accept-Encoding": "gzip, deflate, br"})
    assert status == 200
    assert headers["content-encoding"] == "gzip"
    assert headers["vary"] == "Accept-Encoding"
    assert int(headers["content-length"]) == len(payload)
    assert gzip.decompress(payload) == server.DASHBOARD_HTML.encode("utf-8")
    assert len(payload) < len(server.DASHBOARD_HTML.encode("utf-8")) / 3
    assert headers["cache-control"] == "no-cache"
    assert headers["etag"].startswith('W/"') and headers["etag"].endswith('"')
    assert "content-security-policy" in headers


def test_the_page_is_sent_plain_without_gzip_or_with_gzip_refused():
    for accept in ({}, {"Accept-Encoding": "identity"}, {"Accept-Encoding": "gzip;q=0"}):
        status, headers, payload = _respond(accept)
        assert status == 200
        assert "content-encoding" not in headers
        assert payload == server.DASHBOARD_HTML.encode("utf-8")


def test_a_reload_of_the_same_page_gets_304_without_a_body():
    _, first, _ = _respond({"Accept-Encoding": "gzip"})
    status, headers, payload = _respond({"If-None-Match": first["etag"], "Accept-Encoding": "gzip"})
    assert status == 304
    assert payload == b""
    assert headers["etag"] == first["etag"]
    assert "content-security-policy" in headers


def test_a_different_etag_gets_the_full_page():
    status, _, payload = _respond({"If-None-Match": '"something-else"'})
    assert status == 200
    assert payload == server.DASHBOARD_HTML.encode("utf-8")


def test_a_changed_page_gets_a_new_etag(monkeypatch):
    _, before, _ = _respond({})
    monkeypatch.setattr(server, "DASHBOARD_HTML", server.DASHBOARD_HTML + "<!-- changed -->")
    status, after, payload = _respond({"If-None-Match": before["etag"]})
    assert status == 200
    assert after["etag"] != before["etag"]
    assert payload.endswith(b"<!-- changed -->")


def test_other_answers_keep_no_store_and_get_no_etag():
    body = json.dumps({"ok": True}).encode("utf-8")
    status, headers, payload = _respond({"Accept-Encoding": "gzip"}, body=body,
                                        content_type="application/json")
    assert status == 200
    assert headers["cache-control"] == "no-store"
    assert "etag" not in headers and "content-encoding" not in headers
    assert payload == body


def test_explicit_gzip_refusal_wins_over_wildcard_in_either_order():
    for value in ("*;q=1, gzip;q=0", "gzip;q=0, *;q=1"):
        status, headers, payload = _respond({"Accept-Encoding": value})
        assert status == 200
        assert "content-encoding" not in headers
        assert payload == server.dashboard_page_bytes()


def test_gzip_quality_is_bounded_and_accepts_parameter_spacing():
    for value in ("gzip;q=2", "gzip;q=inf", "gzip;q=NaN", "gzip;q=-1"):
        assert not server._accepts_gzip(value)
    assert server._accepts_gzip("gzip; q = 0.5")


def test_page_variants_share_only_a_weak_validator_and_use_weak_comparison():
    _, plain, _ = _respond({})
    _, compressed, _ = _respond({"Accept-Encoding": "gzip"})
    assert plain["etag"] == compressed["etag"]
    assert plain["etag"].startswith("W/")
    for tag in (plain["etag"], plain["etag"][2:]):
        status, headers, body = _respond({"If-None-Match": tag, "Accept-Encoding": "gzip"})
        assert status == 304
        assert body == b""
        assert headers["vary"] == "Accept-Encoding"
        assert headers["cache-control"] == "no-cache"
        assert headers["x-content-type-options"] == "nosniff"
        assert "content-security-policy" in headers
