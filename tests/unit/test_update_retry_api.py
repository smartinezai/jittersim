"""The explicit retry route uses the existing token and device boundary."""
import http.client
import json
import threading
from types import SimpleNamespace

import pytest

from jittersim.api.server import ControlServer
from jittersim.config.storage import ConfigRepository
from jittersim.networking.helper import HelperClientError

TOKEN = "retry-test-token"
RETRY = "a" * 64
REQUEST = "b" * 32


class RetryHelper:
    def __init__(self):
        self.calls = []
        self.error = None
        self.result = {"request_id": REQUEST, "request_kind": "install", "phase": "queued", "message": "Retry queued."}

    def retry_update(self, *, retry_id):
        self.calls.append(retry_id)
        if self.error:
            raise self.error
        return self.result


@pytest.fixture
def stack():
    repository = ConfigRepository(":memory:")
    helper = RetryHelper()
    server = ControlServer(("127.0.0.1", 0), repository, admin_token=TOKEN)
    original_scheduler = server.scheduler
    server.helper_client = helper
    server.dashboard_access = lambda peer: {"allowed": True}
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server, helper
    finally:
        # This route fixture never starts the scheduler used by the real server.
        server.scheduler = original_scheduler
        server.shutdown()
        worker.join(timeout=2)
        server.server_close()
        repository.close()


def post(server, body, token=TOKEN):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    try:
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["Authorization"] = "Bearer " + token
        connection.request("POST", "/api/updates/retry", json.dumps(body), headers)
        reply = connection.getresponse()
        return reply.status, json.loads(reply.read())
    finally:
        connection.close()


def test_identified_retry_dispatches_once_and_returns_same_request(stack):
    server, helper = stack
    status, reply = post(server, {"retry_id": RETRY})
    assert status == 202
    assert reply["status"]["request_id"] == REQUEST
    assert reply["status"]["phase"] == "queued"
    assert helper.calls == [RETRY]


@pytest.mark.parametrize("payload", [{}, {"retry_id": None}, {"retry_id": True},
    {"retry_id": "a" * 63}, {"retry_id": "a" * 65}, {"retry_id": "A" * 64},
    {"retry_id": "../" + "a" * 61}, {"retry_id": RETRY, "version": "2.0.0"},
    {"retry_id": RETRY, "rollback": True}, {"retry_id": RETRY, "token": "private"}])
def test_invalid_retry_never_reaches_helper(stack, payload):
    server, helper = stack
    status, _ = post(server, payload)
    assert status == 400
    assert not helper.calls


@pytest.mark.parametrize("token", [None, "wrong"])
def test_retry_requires_the_current_admin_token(stack, token):
    server, helper = stack
    assert post(server, {"retry_id": RETRY}, token)[0] == 401
    assert not helper.calls


def test_retry_requires_accepted_device_access(stack):
    server, helper = stack
    server.dashboard_access = lambda peer: {"allowed": False}
    status, reply = post(server, {"retry_id": RETRY})
    assert status == 403 and reply["registration_required"] is True
    assert not helper.calls


@pytest.mark.parametrize("reason", ["helper-busy", "retry-refused", "update-retry-refused"])
def test_stale_busy_or_unknown_admission_does_not_queue_again(stack, reason):
    server, helper = stack
    helper.error = HelperClientError("fixture refusal", reason=reason)
    status, _ = post(server, {"retry_id": RETRY})
    assert status == 409
    assert helper.calls == [RETRY]


@pytest.mark.parametrize("helper", [None, SimpleNamespace()])
def test_older_or_absent_helper_cannot_retry(stack, helper):
    server, _ = stack
    server.helper_client = helper
    assert post(server, {"retry_id": RETRY})[0] == 503


def test_retry_is_an_action_outside_the_scheduler_configuration_lock(stack):
    server, helper = stack
    class Scheduler:
        def exclusive(self):
            pytest.fail("queue admission must not hold the configuration lock")
    server.scheduler = Scheduler()
    status, _ = post(server, {"retry_id": RETRY})
    assert status == 202 and helper.calls == [RETRY]


def test_unknown_helper_failure_returns_safe_unavailable_without_replay(stack):
    server, helper = stack
    helper.error = HelperClientError("private fixture detail", reason="unknown-code")
    status, reply = post(server, {"retry_id": RETRY})
    assert status == 503
    assert "private fixture" not in json.dumps(reply)
    assert helper.calls == [RETRY]


@pytest.mark.parametrize("result", [None, [], "private", {"phase": "queued"},
    {"request_id": REQUEST + "\n", "request_kind": "install"},
    {"request_id": REQUEST, "request_kind": "restore"}])
def test_unidentified_helper_reply_is_not_queue_confirmation(stack, result):
    server, helper = stack
    helper.result = result
    status, reply = post(server, {"retry_id": RETRY})
    assert status == 503
    assert reply == {"error": "update retry is unavailable"}
    assert helper.calls == [RETRY]
