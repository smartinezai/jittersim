"""Throw malformed, hostile and random requests at every route of a local dashboard (QA, 2026-10-09).

The dashboard is the browser fixture's: the real server, repository and helper over a unix socket
with a fake network executor. For every route the answer must be a 4xx or 2xx JSON document: never
a 500, a traceback in the body, a hang, a token in a response or in the log, or a dead server.
Server errors that the known defects cause are tolerated here and pinned in
test_api_known_defects.py. A new kind of server error fails these tests.
"""

import json
import re

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tests.qa.api_support import (
    GET_ROUTES,
    NASTY_NUMBERS,
    NASTY_STRINGS,
    POST_ROUTES,
    PUBLIC,
    call,
    check,
    judge,
)
from tests.qa.dashboard_support import TOKEN, running_dashboard

FUZZ = settings(
    max_examples=300, deadline=None, database=None, derandomize=True,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large, HealthCheck.function_scoped_fixture],
)


@pytest.fixture(scope="module")
def dashboard():
    with running_dashboard(observations=True) as running:
        yield running
        # Whatever was thrown at it, the server still answers and nothing leaked.
        status, _, body = running.request("GET", "/api/health")
        assert status == 200 and json.loads(body)["status"] == "ok"
        assert not [r for r in running.logs.records if TOKEN in r.getMessage()]
        assert running.outside_connections == []


KEYS = (
    "id", "name", "profile_id", "device_id", "device_ids", "group_id", "scenario_id", "ipv4_address", "mac_address",
    "address", "domains", "domain", "hostnames", "services", "enabled", "version", "ssid", "password",
    "admin_password", "download", "upload", "steps", "mode", "limit_bytes", "limit_seconds", "reset_mode",
    "reset_interval_seconds", "reset_times", "weight_percent", "window_duration_seconds", "days_of_week",
    "start_minute", "end_minute", "start_profile_id", "end_profile_id", "duration_minutes", "duration_seconds",
    "loop", "persistence_enabled", "confirm", "confirmed", "force", "format", "policies", "options", "site_blocks",
    "everyone_else", "general_profile_id", "ad_blocking_devices", "ad_blocking_allowances", "profiles", "devices",
    "groups", "schedules", "scenarios", "delay_ms", "jitter_ms", "loss_percent", "bandwidth_kbit", "reorder_percent",
    "duplicate_percent", "corrupt_percent", "loss_burst_packets", "delay_randomness", "random_seed", "random_mode",
    "reroll_interval_seconds", "outage_probability", "outage_duration_seconds", "website_mode", "website_domains",
    "homepage_bounce_percent", "from_mac", "expected_fingerprint", "request_id", "operation_id", "display_name",
    "variation_percent", "enabled_probability", "minimum", "maximum", "allowed", "blocked", "limit", "target",
)
# Surrogate halves cannot be written to the database. A lone one in an id is the confirmed
# defect (test_api_known_defects.py), so the generic strategy leaves them out.
text = st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=80)
scalars = st.one_of(
    st.none(), st.booleans(), st.sampled_from(NASTY_NUMBERS), st.integers(-2**40, 2**40),
    st.floats(allow_nan=False, allow_infinity=False), text, st.sampled_from(NASTY_STRINGS),
)
values = st.recursive(
    scalars, lambda inner: st.one_of(st.lists(inner, max_size=4), st.dictionaries(st.sampled_from(KEYS), inner, max_size=5)),
    max_leaves=14,
)
payloads = st.one_of(
    st.dictionaries(st.sampled_from(KEYS), values, max_size=8),
    st.dictionaries(text, values, max_size=4),
)


@FUZZ
@given(route=st.sampled_from(POST_ROUTES), payload=payloads)
def test_any_json_object_on_any_post_route_is_answered_cleanly(dashboard, route, payload):
    problems, status = check(dashboard, "POST", route, payload)

    assert not problems, (route, json.dumps(payload)[:300], status, problems)


@FUZZ
@given(route=st.sampled_from(POST_ROUTES), body=st.one_of(
    st.binary(max_size=300), st.sampled_from([b"", b"null", b"[]", b'"s"', b"1", b"true", b"{", b'{"a":', b"\xff\xfe{}"]),
    st.text(max_size=100).map(lambda value: value.encode("utf-8", "surrogatepass")),
))
def test_any_bytes_on_any_post_route_are_answered_cleanly(dashboard, route, body):
    problems, status = check(dashboard, "POST", route, body, headers={"Content-Type": "application/json"})

    assert not problems, (route, body[:120], status, problems)
    if route not in ("/api/apply", "/api/reset", "/api/observations/scan", "/api/access-point/reset-name",
                     "/api/updates/check-online", "/api/updates/retry", "/api/update-preferences"):
        # A body that is not a JSON object is the caller's mistake, never a success or a crash.
        try:
            parsed = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            parsed = None
        if not isinstance(parsed, dict):
            assert 400 <= status < 500, (route, body[:80], status)


@pytest.mark.parametrize("route", POST_ROUTES)
def test_structured_hostile_bodies_on_every_route(dashboard, route):
    """A fixed corpus on every route. Each body is the kind of thing a client really sends wrong."""

    keys = ("id", "name", "profile_id", "device_id", "ipv4_address", "mac_address", "domains", "enabled",
            "version", "ssid", "password", "limit_bytes", "download", "upload", "steps")
    corpus = [
        {}, {"id": None}, {"id": []}, {"id": {}}, {"id": True}, {"id": 0}, {"id": -1}, {"id": 1.5},
        {"id": "x" * 65}, {"id": "A"}, {"id": "a b"}, {"id": "\x00"}, {"id": "‮"},
        {key: value for key in keys for value in ("x",)},
        {key: [] for key in keys}, {key: {} for key in keys}, {key: None for key in keys},
        {key: True for key in keys}, {key: -1 for key in keys}, {key: 2**31 for key in keys},
        {key: 1e308 for key in keys}, {key: "x" * 4000 for key in keys}, {key: [None] * 300 for key in keys},
        {"download": {"delay_ms": 10**9}, "upload": {"loss_percent": -5}, "id": "p", "name": "p"},
        {"download": "fast", "upload": [], "id": "p", "name": "p"},
        {"domains": ["a.com"] * 400}, {"domains": ["é.com", "a..b", "-a.com", "a.com."]},
        {"ipv4_address": "999.1.1.1", "mac_address": "zz"}, {"ipv4_address": 3232235777, "mac_address": 1},
        {"ipv4_address": "192.168.050.001", "mac_address": "02-00-00-00-00-01"},
        {"limit_bytes": 0, "limit_seconds": 0, "weight_percent": 101, "reset_mode": "never"},
    ]
    corpus.append(json.loads('{"a":' * 90 + "1" + "}" * 90))  # deep but not absurd
    for body in corpus:
        problems, status = check(dashboard, "POST", route, body)
        assert not problems, (route, json.dumps(body)[:200], status, problems)


@pytest.mark.parametrize("route", POST_ROUTES + ("/api/nope", "/api/../etc/passwd", "/api/profiles/x"))
def test_without_a_valid_token_every_write_is_a_401(dashboard, route):
    for headers, token in (({}, None), ({"Authorization": "Bearer wrong"}, None), ({"Authorization": "Bearer "}, None),
                           ({"Authorization": "Basic " + "Zm9vOmJhcg=="}, None), ({"Authorization": TOKEN}, None),
                           ({"Authorization": "Bearer " + TOKEN + " "}, None), ({"Authorization": "Bearer " + TOKEN[:-1]}, None)):
        status, response_headers, body, first = call(dashboard, "POST", route, {"id": "x"}, headers=headers, token=token)
        if headers.get("Authorization") == "Bearer " + TOKEN + " ":
            # A trailing space is not the token.
            assert status == 401, (route, status, body[:80])
        problems = judge(dashboard, "POST", route, status, response_headers, body, first)
        assert not problems, (route, headers.keys(), status, problems)
        assert status in (401, 431), (route, headers.get("Authorization", "")[:20], status)
        assert json.loads(body) == {"error": "valid bearer authentication is required"} or status == 431


@pytest.mark.parametrize("route", [route for route in GET_ROUTES if route not in PUBLIC])
def test_without_a_valid_token_every_private_read_is_a_401(dashboard, route):
    for authorization in (None, "Bearer wrong", "Bearer", "bearer", "Token " + TOKEN, TOKEN):
        headers = {"Authorization": authorization} if authorization else {}
        status, response_headers, body, first = call(dashboard, "GET", route, headers=headers, token=None)
        assert status == 401, (route, authorization, status)
        assert not judge(dashboard, "GET", route, status, response_headers, body, first)


@pytest.mark.parametrize("route", GET_ROUTES)
def test_reads_survive_hostile_query_strings(dashboard, route):
    queries = ("", "?", "?id=", "?id=%00", "?id=%ff", "?id=%ED%A0%80", "?id=" + "9" * 5000, "?id=-1", "?id=1e999",
               "?limit=-1", "?limit=99999999999999999999", "?id=a&id=b", "?" + "a=b&" * 3000, "?days=0", "?days=31",
               "?days=%EF%BC%91", "?days=1_0", "?days=" + "9" * 5000, "?device_id=" + "d" * 65, "?device_id=%00&days=1",
               "?days=1&device_id=a&x=1&y=2&z=3", "?q=%E0%A4%A", "?id=%F0%9F%98%80")
    for query in queries:
        problems, status = check(dashboard, "GET", route + query)
        assert not problems, (route + query[:40], status, problems)


# The routes that take a name or an id after a fixed prefix. The fixed lists above never put anything
# odd after the prefix, and the server reads that part itself.
FAMILIES = (
    "/api/router/", "/api/homepage/", "/api/dashboard-access/", "/api/device-workspace/", "/api/test-sessions/",
    "/api/updates/check-online/", "/api/wifi-operation/", "/api/updates/", "/api/profiles/", "/api/activity/",
    "/api/observations/", "/api/access-point/", "/api/connected-devices/", "/api/",
)
SEGMENTS = (
    "", "x", "%00", "%ff", "%ED%A0%80", "..", "%2e%2e", "%2e%2e%2f", "a%2fb", "a/b/c", "x" * 5000, "%", "%zz", "a%20b",
    "ALICE", "%F0%9F%98%80", "%E2%80%AE", "-1", "1e999", "alice", "alice/", "alice/extra", "alice%00", "status",
    "undefined", "..;", "%252e", "alice?days=1", "x" * 70,
)


@pytest.mark.parametrize("family", FAMILIES)
def test_odd_names_after_a_route_prefix_and_odd_methods_are_answered_cleanly(dashboard, family):
    for segment in SEGMENTS:
        path = family + segment
        for method in ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
            body = {} if method in ("POST", "PUT", "PATCH") else None
            status, headers, data, first = call(dashboard, method, path, body)
            where = (method, path[:60], status)
            if method in ("HEAD", "OPTIONS"):
                # The standard library answers a method it does not know with 501, and that is fine.
                assert status < 500 or status == 501, where
                assert not [r for r in dashboard.logs.records[first:] if r.exc_info], where
                continue
            problems = judge(dashboard, method, path, status, headers, data, first)
            # A method the server does not implement is answered by the standard library as HTML.
            problems = [p for p in problems if not (status == 501 and p.startswith("non-JSON"))]
            assert not problems, (where, problems)
            assert status != 501 or method in ("PUT", "DELETE", "PATCH"), where


def test_random_website_analytics_filters_are_validated(dashboard):
    for query, expected in (("?days=0", 400), ("?days=31", 400), ("?days=abc", 400), ("?days=1&days=2", 400),
                            ("?device_id=" + "d" * 65, 400), ("?days=30", 200), ("", 200), ("?days=1&device_id=alice", 200),
                            ("?a=1&b=2&c=3&d=4&e=5", 400)):
        status, _, body, _first = call(dashboard, "GET", "/api/random-website-analytics" + query)
        assert status == expected, (query, status, body[:100])


RAW = {
    "negative length": b"POST /api/profiles HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer %s\r\nContent-Length: -5\r\nConnection: close\r\n\r\n{}",
    "non-numeric length": b"POST /api/profiles HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer %s\r\nContent-Length: abc\r\nConnection: close\r\n\r\n{}",
    "huge length": b"POST /api/profiles HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer %s\r\nContent-Length: 99999999999999999999\r\nConnection: close\r\n\r\n{}",
    "chunked": b"POST /api/profiles HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer %s\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n2\r\n{}\r\n0\r\n\r\n",
    "length with plus sign": b"POST /api/profiles HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer %s\r\nContent-Length: +2\r\nConnection: close\r\n\r\n{}",
    "arabic digit length": "POST /api/profiles HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer %s\r\nContent-Length: ٢\r\nConnection: close\r\n\r\n{}".encode(),
    "oversized body": b"POST /api/profiles HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer %s\r\nContent-Length: 70000\r\nConnection: close\r\n\r\n" + b"x" * 70000,
    "very long url": b"GET /api/" + b"a" * 70000 + b" HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n",
    "very long header": b"GET /api/health HTTP/1.1\r\nHost: x\r\nX-A: " + b"a" * 70000 + b"\r\nConnection: close\r\n\r\n",
    "many headers": b"GET /api/health HTTP/1.1\r\nHost: x\r\n" + b"".join(b"X-%d: a\r\n" % i for i in range(500)) + b"Connection: close\r\n\r\n",
    "binary garbage": b"\x00\x01\x02garbage\r\n\r\n",
    "tls hello on plain port": b"\x16\x03\x01\x02\x00\x01\x00\x01\xfc\x03\x03" + b"\x00" * 64 + b"\r\n\r\n",
    "bare newlines": b"GET /api/health HTTP/1.1\nHost: x\nConnection: close\n\n",
    "duplicate content-length": b"POST /api/profiles HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer %s\r\nContent-Length: 2\r\nContent-Length: 9\r\nConnection: close\r\n\r\n{}",
    "unknown version": b"GET /api/health HTTP/9.9\r\nHost: x\r\nConnection: close\r\n\r\n",
    "absolute uri": b"GET http://evil.example/api/health HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n",
    "double slash": b"GET //api/profiles HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer %s\r\nConnection: close\r\n\r\n",
    "encoded traversal": b"GET /api/%2e%2e/%2e%2e/etc/passwd HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer %s\r\nConnection: close\r\n\r\n",
}


@pytest.mark.parametrize("name", sorted(RAW))
def test_malformed_requests_are_refused_and_the_server_lives_on(dashboard, name):
    raw = RAW[name]
    raw = raw.replace(b"%s", TOKEN.encode()) if b"%s" in raw else raw
    reply = dashboard.raw(raw, timeout=15)
    if reply.startswith(b"HTTP/"):
        status = int(re.match(rb"HTTP/1\.[01] (\d+)", reply).group(1))
    else:
        # A request line that cannot be read is answered the way the standard library does: the
        # error page alone, as an HTTP/0.9 reply has no status line. The code is in the page.
        assert reply.startswith(b"<!DOCTYPE HTML>"), f"no HTTP answer to {name}: {reply[:60]!r}"
        status = int(re.search(rb"Error code: (\d+)", reply).group(1))
    assert status < 500 or status in (501, 505), (name, status)
    assert TOKEN.encode() not in reply
    assert b"Traceback" not in reply
    health = dashboard.request("GET", "/api/health")
    assert health[0] == 200


def test_the_double_slash_path_does_not_bypass_anything(dashboard):
    # //api/profiles is read as /api/profiles by the standard library. The token is still needed.
    reply = dashboard.raw(b"GET //api/profiles HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
    assert reply.startswith(b"HTTP/1.0 401") or reply.startswith(b"HTTP/1.1 401")


def test_a_slow_body_is_dropped_instead_of_holding_a_worker_for_ever(dashboard):
    import socket
    import time

    with socket.create_connection(("127.0.0.1", dashboard.port), timeout=30) as connection:
        connection.sendall(b"POST /api/profiles HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer "
                           + TOKEN.encode() + b"\r\nContent-Length: 50\r\n\r\n{")
        started = time.monotonic()
        data = b"1"
        try:
            while data:
                data = connection.recv(4096)
        except socket.timeout:
            pytest.fail("the server kept a half-sent request open for 30 seconds")
        assert time.monotonic() - started < 20
    assert dashboard.request("GET", "/api/health")[0] == 200


def test_sixteen_stalled_connections_do_not_block_the_health_check(dashboard):
    """The server has a fixed number of workers. Idle sockets must not take them all."""

    import socket

    stalled = [socket.create_connection(("127.0.0.1", dashboard.port)) for _ in range(20)]
    try:
        status, _, _ = dashboard.request("GET", "/api/health", timeout=15)
        assert status in (200, 503)
    finally:
        for connection in stalled:
            connection.close()
    assert dashboard.request("GET", "/api/health")[0] == 200
