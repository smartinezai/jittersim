"""Defects the QA run of 2026-10-09 found in the dashboard API, now fixed and held by these tests.

Each one made the server answer 500 and write a traceback to the journal where it should answer 400
with a sentence a person can read. None was a security hole: each needs the admin token. The
standard library's HTML error pages for refused requests (finding 6) are JSON now too. The tests
keep what was fixed from coming back.

Run one by hand against a local fixture, for example:

    python -m tests.browser.fixture      # prints its port
    curl -s -X POST -H 'Authorization: Bearer browser-test' \\
         -d '{"id": ["x"]}' http://127.0.0.1:<port>/api/time-quotas
"""

import json

import pytest

from tests.qa.api_support import POST_ROUTES, call
from tests.qa.dashboard_support import running_dashboard


@pytest.fixture(scope="module")
def dashboard():
    with running_dashboard(observations=True) as running:
        yield running


def post_status(dashboard, route, body, raw=None):
    status, headers, data, first = call(
        dashboard, "POST", route, raw if raw is not None else body,
        headers={"Content-Type": "application/json"})
    return status, data


def assert_client_error(dashboard, route, body, raw=None):
    status, data = post_status(dashboard, route, body, raw)
    assert 400 <= status < 500, f"{route} answered {status}: {data[:100]!r}"
    assert "error" in json.loads(data)


def test_an_integer_literal_over_4300_digits_is_a_bad_request_on_every_route(dashboard):
    body = b'{"version": ' + b"9" * 4301 + b"}"
    wrong = []
    for route in POST_ROUTES:
        status, data = post_status(dashboard, route, None, raw=body)
        if status != 400 or json.loads(data).get("error") != "a number in the request body has too many digits":
            wrong.append((route, status))
    assert not wrong, f"{len(wrong)} of {len(POST_ROUTES)} routes: {wrong[:5]}"


def test_an_integer_literal_of_4300_digits_is_still_read(dashboard):
    # The edge below the limit works: the defect above starts exactly one digit later.
    status, _ = post_status(dashboard, "/api/profiles", None, raw=b'{"version": ' + b"9" * 4300 + b"}")
    assert status == 400


LONE_SURROGATE_DELETES = (
    ("/api/devices/delete", "id"), ("/api/profiles/delete", "id"), ("/api/scenarios/delete", "id"),
    ("/api/scenarios/stop", "id"), ("/api/schedules/delete", "id"), ("/api/service-assignments/delete", "id"),
    ("/api/service-lists/delete", "id"), ("/api/service-quotas/delete", "id"), ("/api/time-quotas/delete", "id"),
    ("/api/data-limits/delete", "device_id"), ("/api/group-data-limits/delete", "group_id"),
    ("/api/everyone-else", "profile_id"), ("/api/router/status", "admin_password"),
)


def test_a_lone_surrogate_in_an_identifier_is_a_bad_request(dashboard):
    wrong = []
    for route, key in LONE_SURROGATE_DELETES:
        status, data = post_status(dashboard, route, None, raw=('{"%s": "\\ud800"}' % key).encode())
        if status != 400 or "unpaired surrogate" not in json.loads(data)["error"]:
            wrong.append((route, status))
    assert not wrong, f"{len(wrong)} of {len(LONE_SURROGATE_DELETES)} routes: {wrong}"


@pytest.mark.parametrize("raw", [
    b'{"id": "x", "extra": ["a", {"deep": "\\udc00"}]}',
    b'{"id": "x", "\\ud800": 1}',
    b'{"id": "\\ud83d"}',
    b'{"id": "\\ude00\\ud83d"}',
])
def test_a_lone_surrogate_is_found_in_a_key_and_in_a_nested_value(dashboard, raw):
    status, data = post_status(dashboard, "/api/profiles/delete", None, raw=raw)
    assert status == 400 and "unpaired surrogate" in json.loads(data)["error"]


def test_a_valid_surrogate_pair_is_still_read(dashboard):
    # \ud83d\ude00 is one character (an emoji). It is not a lone surrogate.
    status, data = post_status(dashboard, "/api/profiles/delete", None, raw=b'{"id": "\\ud83d\\ude00"}')
    assert "surrogate" not in json.loads(data)["error"]
    assert status in (400, 404)


NON_STRING_IDENTIFIERS = (
    ("/api/data-limits", "device_id", {"device_id": "alice", "profile_id": "slow", "limit_bytes": 1000}),
    ("/api/group-data-limits", "group_id", {"group_id": "family", "profile_id": "slow", "limit_bytes": 1000}),
    ("/api/service-quotas", "id", {"id": "q", "device_id": "alice", "name": "Q", "domains": ["gateway.icloud.com"],
                                   "profile_id": "slow", "limit_bytes": 1000}),
    ("/api/service-quotas", "device_id", {"id": "q", "device_id": "alice", "name": "Q",
                                         "domains": ["gateway.icloud.com"], "profile_id": "slow", "limit_bytes": 1000}),
    ("/api/time-quotas", "id", {"id": "t", "device_id": "alice", "name": "T", "domains": ["gateway.icloud.com"],
                                "profile_id": "slow", "limit_seconds": 60}),
    ("/api/connected-devices/group", "group_id", {"group_id": "family", "mac_address": "02:00:00:00:00:01"}),
)


@pytest.mark.parametrize("route,field,body", NON_STRING_IDENTIFIERS,
                         ids=[f"{route}:{field}" for route, field, _ in NON_STRING_IDENTIFIERS])
def test_a_list_or_object_as_an_identifier_is_a_bad_request(dashboard, route, field, body):
    wrong = []
    for bad in (["x"], {"a": 1}, 2**63, -2**63 - 1, True, None):
        status, data = post_status(dashboard, route, {**body, field: bad})
        if status != 400 or f"{field} must be text" not in json.loads(data)["error"]:
            wrong.append((json.dumps(bad)[:20], status))
    assert not wrong, f"{route} {field}: {wrong}"


@pytest.mark.parametrize("route", ["/api/history/preview", "/api/history/restore"])
def test_a_settings_version_id_beyond_sqlite_range_is_a_bad_request(dashboard, route):
    for identifier in (2**63, -2**63 - 1, 10**30):
        status, data = post_status(dashboard, route, {"id": identifier, "current_version": 1})
        assert status == 400 and json.loads(data)["error"] == "settings version no longer exists", (identifier, status)


def test_the_edge_of_the_range_is_already_answered_correctly(dashboard):
    assert_client_error(dashboard, "/api/history/preview", {"id": 2**63 - 1})


RAW_ERRORS = {
    "request line too long": (
        b"GET /api/" + b"a" * 70000 + b" HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n", 414),
    "header too large": (
        b"GET /api/health HTTP/1.1\r\nHost: x\r\nX-A: " + b"a" * 70000 + b"\r\nConnection: close\r\n\r\n", 431),
    "too many headers": (
        b"GET /api/health HTTP/1.1\r\nHost: x\r\n" + b"X-A: a\r\n" * 120 + b"Connection: close\r\n\r\n", 431),
    "OPTIONS": (b"OPTIONS /api/profiles HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n", 405),
    "PATCH": (b"PATCH /api/profiles HTTP/1.1\r\nHost: x\r\nContent-Length: 0\r\nConnection: close\r\n\r\n", 405),
    "DELETE": (b"DELETE /api/profiles HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n", 405),
    "unknown method": (b"FOO /api/profiles HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n", 501),
    "HTTP version": (b"GET /api/health HTTP/9.9\r\nHost: x\r\nConnection: close\r\n\r\n", 505),
    "bad request line": (b"GET /api/health HTTP/1.1 extra\r\nHost: x\r\nConnection: close\r\n\r\n", 400),
}


def split_reply(reply):
    head, _, body = reply.partition(b"\r\n\r\n")
    status_line, *header_lines = head.split(b"\r\n")
    headers = {line.split(b":", 1)[0].decode().lower(): line.split(b":", 1)[1].strip().decode()
               for line in header_lines if b":" in line}
    return status_line, headers, body


def test_every_refusal_under_api_is_json(dashboard):
    wrong = []
    for name, (raw, wanted) in RAW_ERRORS.items():
        status_line, headers, body = split_reply(dashboard.raw(raw, timeout=15))
        try:
            message = json.loads(body)["error"]
            is_json = headers.get("content-type") == "application/json" and bool(message)
        except (ValueError, KeyError):
            is_json = False
        if not is_json or f" {wanted} ".encode() not in status_line + b" ":
            wrong.append((name, status_line[:40], body[:30]))
    assert not wrong, wrong


def test_a_refusal_by_the_standard_library_echoes_nothing_of_the_request(dashboard):
    secret = b"<script>alert(1)</script>"
    for raw in (b"GET /api/health HTTP/1.1 " + secret + b"\r\nHost: x\r\n\r\n",
                b"GET /api/health HTTP/" + secret + b"\r\nHost: x\r\n\r\n",
                secret + b" /api/health HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n"):
        reply = dashboard.raw(raw, timeout=15)
        assert b"script" not in reply and b"alert" not in reply, reply[:200]
        assert json.loads(split_reply(reply)[2])["error"]


def test_methods_the_dashboard_does_not_answer_say_which_it_does(dashboard):
    for method in ("OPTIONS", "PATCH", "DELETE"):
        status, headers, data = dashboard.request(method, "/api/profiles")
        assert status == 405, method
        assert {item.strip() for item in headers["Allow"].split(",")} == {"GET", "HEAD", "POST", "PUT"}
        assert json.loads(data)["error"]


def test_head_answers_the_health_check_with_headers_and_no_body(dashboard):
    status, headers, data = dashboard.request("HEAD", "/api/health", token=None)
    get_status, get_headers, get_data = dashboard.request("GET", "/api/health", token=None)
    assert status == get_status == 200 and data == b""
    assert headers["Content-Type"] == "application/json"
    assert int(headers["Content-Length"]) == len(get_data) > 0


def test_head_answers_nothing_else(dashboard):
    for path in ("/api/profiles", "/", "/api/nothing-here"):
        status, headers, data = dashboard.request("HEAD", path)
        assert status == 405 and data == b"" and "Allow" in headers, path
