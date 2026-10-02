"""Exercise the disposable fixture's authority and resource limits over HTTP."""

import http.client
import importlib.util
import json
import socket
import time
from contextlib import ExitStack
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "experiments/homepage-bounce-reference/fixture.py"
SPEC = importlib.util.spec_from_file_location("homepage_reference_fixture", SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.fixture
def fixture():
    server = MODULE.Fixture()
    server.start()
    try:
        yield server
    finally:
        server.close()


def post(fixture, path, payload, authorization=""):
    connection = http.client.HTTPConnection("127.0.0.1", fixture.server.server_port, timeout=2)
    try:
        connection.request("POST", path, json.dumps(payload), {
            "Content-Type": "application/json", "Authorization": authorization,
        })
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def pair(fixture, code=None):
    status, result = post(fixture, "/reference/pair", {"code": code or fixture.code})
    assert status == 200
    return "Reference " + result["capability"]


def policy(fixture, credential):
    return post(fixture, "/reference/policy", {
        "origin": fixture.origin, "request_id": "fixture-check",
    }, credential)


def test_pairing_replay_and_replacement(fixture):
    code = fixture.code
    old_credential = pair(fixture)
    assert post(fixture, "/reference/pair", {"code": code})[0] == 400
    status, replacement = post(fixture, "/__reference/control", {"event": "pairing-code"},
                               "Harness " + fixture.harness_key)
    assert status == 200
    new_credential = pair(fixture, replacement["code"])
    assert policy(fixture, old_credential)[0] == 401
    assert policy(fixture, new_credential)[0] == 200


def test_elapsed_pairing_and_session_deadlines(fixture):
    with fixture.lock:
        fixture.code_deadline = time.monotonic() - 1
    assert post(fixture, "/reference/pair", {"code": fixture.code})[0] == 400
    with fixture.lock:
        fixture.code_deadline = time.monotonic() + 120
    credential = pair(fixture)
    with fixture.lock:
        fixture.session_deadline = time.monotonic() - 1
    assert policy(fixture, credential)[0] == 401


def test_browser_credential_cannot_control_the_harness(fixture):
    credential = pair(fixture)
    assert post(fixture, "/__reference/control", {"event": "restart"}, credential)[0] == 400
    assert policy(fixture, credential)[0] == 200


@pytest.mark.parametrize("path, payload, authorization", [
    ("/reference/pair", {"code": "é" * 32}, ""),
    ("/reference/pair", {"code": "x" * 9000}, ""),
    ("/reference/pair", [], ""),
    ("/reference/policy", {}, "Reference é"),
])
def test_bad_requests_are_bounded_and_do_not_break_the_server(fixture, path, payload,
                                                              authorization):
    assert post(fixture, path, payload, authorization)[0] in (400, 401)
    assert policy(fixture, pair(fixture))[0] == 200


def test_four_stalled_handlers_refuse_more_work_then_recover(fixture):
    address = ("127.0.0.1", fixture.server.server_port)
    with ExitStack() as sockets:
        for _ in range(4):
            connection = sockets.enter_context(socket.create_connection(address, timeout=1))
            connection.sendall(b"POST /reference/pair HTTP/1.0\r\nContent-Length: 10\r\n\r\n")
        deadline = time.monotonic() + 1
        while fixture.server.slots._value and time.monotonic() < deadline:
            time.sleep(0.01)
        assert fixture.server.slots._value == 0
        with socket.create_connection(address, timeout=1) as refused:
            refused.sendall(b"GET / HTTP/1.0\r\n\r\n")
            try:
                assert refused.recv(1) == b""
            except ConnectionResetError:
                pass
    deadline = time.monotonic() + 1
    while fixture.server.slots._value != 4 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert fixture.server.slots._value == 4
    assert policy(fixture, pair(fixture))[0] == 200
