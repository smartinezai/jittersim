"""Defects the QA run of 2026-10-09 found in the dashboard API, each pinned by a failing test.

Every test is marked xfail(strict=True): it fails today and says why. When the defect is fixed the
test passes, strict mode reports that as a failure, and the marker is removed. None of these is a
security hole: each needs the admin token. Each makes the server answer 500 and write a traceback to
the journal where it should answer 400 with a sentence a person can read.

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


@pytest.mark.xfail(strict=True, reason=(
    "server.py _read_json_body catches json.JSONDecodeError but json.loads raises a plain ValueError "
    "for an integer literal of more than 4300 digits (Python's int/str limit), so every POST route "
    "answers 500 and logs a traceback instead of 400 request body must be valid JSON"))
def test_an_integer_literal_over_4300_digits_is_a_bad_request_on_every_route(dashboard):
    body = b'{"version": ' + b"9" * 4301 + b"}"
    wrong = []
    for route in POST_ROUTES:
        status, _ = post_status(dashboard, route, None, raw=body)
        if not 400 <= status < 500:
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


@pytest.mark.xfail(strict=True, reason=(
    "a lone UTF-16 surrogate in a JSON string (\"\\ud800\") passes json.loads and reaches sqlite3 or "
    "str.encode('utf-8') unchecked: storage.py lines 1192, 1671, 1924, 2224, 2879, 2946 and "
    "router_operations.py:38 raise UnicodeEncodeError, so the route answers 500"))
def test_a_lone_surrogate_in_an_identifier_is_a_bad_request(dashboard):
    wrong = []
    for route, key in LONE_SURROGATE_DELETES:
        status, _ = post_status(dashboard, route, None, raw=('{"%s": "\\ud800"}' % key).encode())
        if not 400 <= status < 500:
            wrong.append((route, status))
    assert not wrong, f"{len(wrong)} of {len(LONE_SURROGATE_DELETES)} routes: {wrong}"


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


@pytest.mark.xfail(strict=True, reason=(
    "these handlers pass an identifier from the body to repository.get_* without checking that it is a "
    "string: a list or object raises sqlite3.ProgrammingError (storage.py 1206, 1935, 2465, 2618, 2761, "
    "3023) and an integer of 2**63 or more raises OverflowError, so the route answers 500. Other routes "
    "with the same field answer 400 invalid ... configuration"))
@pytest.mark.parametrize("route,field,body", NON_STRING_IDENTIFIERS,
                         ids=[f"{route}:{field}" for route, field, _ in NON_STRING_IDENTIFIERS])
def test_a_list_or_object_as_an_identifier_is_a_bad_request(dashboard, route, field, body):
    wrong = []
    for bad in (["x"], {"a": 1}, 2**63, -2**63 - 1):
        status, _ = post_status(dashboard, route, {**body, field: bad})
        if not 400 <= status < 500:
            wrong.append((json.dumps(bad)[:20], status))
    assert not wrong, f"{route} {field}: {wrong}"


@pytest.mark.xfail(strict=True, reason=(
    "config/workflows.py:147 binds the settings version id to SQLite without a range check, so an id of "
    "2**63 or more raises OverflowError: /api/history/preview and /api/history/restore answer 500. "
    "9223372036854775807 correctly answers 400 settings version no longer exists"))
@pytest.mark.parametrize("route", ["/api/history/preview", "/api/history/restore"])
def test_a_settings_version_id_beyond_sqlite_range_is_a_bad_request(dashboard, route):
    assert_client_error(dashboard, route, {"id": 2**63})
    assert_client_error(dashboard, route, {"id": -2**63 - 1})


def test_the_edge_of_the_range_is_already_answered_correctly(dashboard):
    assert_client_error(dashboard, "/api/history/preview", {"id": 2**63 - 1})


RAW_ERRORS = {
    "request line too long (414)": b"GET /api/" + b"a" * 70000 + b" HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n",
    "header too large (431)": b"GET /api/health HTTP/1.1\r\nHost: x\r\nX-A: " + b"a" * 70000 + b"\r\nConnection: close\r\n\r\n",
    "OPTIONS (501)": b"OPTIONS /api/profiles HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n",
    "PATCH (501)": b"PATCH /api/profiles HTTP/1.1\r\nHost: x\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
    "HTTP version (505)": b"GET /api/health HTTP/9.9\r\nHost: x\r\nConnection: close\r\n\r\n",
}


@pytest.mark.xfail(strict=True, reason=(
    "requests that http.server refuses before the handler runs (too long a request line or header, a "
    "method with no do_ function, an unknown HTTP version) get the standard library's HTML error page, "
    "not the JSON {\"error\": ...} every other refusal uses. OPTIONS and PATCH answer 501 where 405 "
    "with an Allow header is the usual answer. A client that parses every error as JSON breaks on them."))
def test_every_refusal_under_api_is_json(dashboard):
    wrong = []
    for name, raw in RAW_ERRORS.items():
        reply = dashboard.raw(raw, timeout=15)
        head, _, body = reply.partition(b"\r\n\r\n")
        try:
            json.loads(body)
            is_json = b"application/json" in head
        except ValueError:
            is_json = False
        if not is_json:
            wrong.append((name, body[:30]))
    assert not wrong, wrong
