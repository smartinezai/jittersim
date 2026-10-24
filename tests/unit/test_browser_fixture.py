"""Check the helper client that the browser journeys use."""

import json
import socket
import subprocess
from types import SimpleNamespace

import pytest

from jittersim.networking.commands import Command
from jittersim.networking.helper import HelperClient, HelperClientError
from tests.browser import fixture


def script_requests(monkeypatch, answers):
    """Make the base client answer from a list. An exception in the list is raised."""

    calls = []

    def request(self, payload, *, timeout_seconds=None):
        calls.append(payload["action"])
        answer = answers[min(len(calls), len(answers)) - 1]
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(HelperClient, "_request", request)
    monkeypatch.setattr(fixture, "BUSY_WAIT_SECONDS", 0)
    return calls


def client():
    return fixture.PatientHelperClient(socket_path="unused.sock")


def busy():
    return HelperClientError("busy", reason="helper-busy")


def test_a_busy_helper_is_asked_again_until_it_answers(monkeypatch):
    # The page polls the helper while a journey presses Apply. Without the wait the Apply
    # lost that race now and then on a slow machine and left the network in an error state.
    calls = script_requests(monkeypatch, [busy(), busy(), busy(), {"ok": True}])

    assert client()._request({"action": "apply"}) == {"ok": True}
    assert calls == ["apply"] * 4


def test_any_other_failure_is_not_hidden(monkeypatch):
    calls = script_requests(monkeypatch, [HelperClientError("apply failed"), {"ok": True}])

    with pytest.raises(HelperClientError, match="apply failed"):
        client()._request({"action": "apply"})
    assert calls == ["apply"]


def test_a_helper_that_stays_busy_still_fails_in_the_end(monkeypatch):
    calls = script_requests(monkeypatch, [busy()])

    with pytest.raises(HelperClientError) as error:
        client()._request({"action": "apply"})
    assert error.value.reason == "helper-busy"
    assert len(calls) == fixture.BUSY_ATTEMPTS


class FakeClock:
    def __init__(self):
        self.now = 1_000.0

    def __call__(self):
        return self.now


def read_queue(executor, interface="wlan0"):
    command = Command("tc", ("-s", "-j", "qdisc", "show", "dev", interface), "read queue counters")
    queues = json.loads(executor.run(command).stdout)
    return next(queue for queue in queues if queue["kind"] == "netem")


@pytest.mark.parametrize("gap_seconds", [4.0, 5.0, 5.5, 8.0])
def test_queue_bytes_follow_the_clock_so_the_rate_ignores_the_poll_gap(gap_seconds):
    # The impact card divides the byte change by the time between two samples and calls the
    # cap reached from 90% of it. A fixed amount per read gave 240 kbit/s for a 5 second gap
    # but only 218 kbit/s for 5.5 seconds, so the journey failed on a slow CI machine.
    clock = FakeClock()
    executor = fixture.DashboardWifiExecutor(clock=clock)
    first = read_queue(executor)
    clock.now += gap_seconds
    second = read_queue(executor)

    kbit = (second["bytes"] - first["bytes"]) * 8 / 1000 / gap_seconds
    assert kbit == pytest.approx(fixture.QUEUE_KBIT_PER_SECOND)


def test_each_read_still_adds_the_same_packets_and_drops_whatever_the_gap():
    # The 10% loss line (11 dropped of 111) must not depend on timing either.
    clock = FakeClock()
    executor = fixture.DashboardWifiExecutor(clock=clock)
    first = read_queue(executor)
    clock.now += 7.3
    second = read_queue(executor)
    clock.now += 0.4
    third = read_queue(executor)

    assert second["packets"] - first["packets"] == third["packets"] - second["packets"] == 100
    assert second["drops"] - first["drops"] == third["drops"] - second["drops"] == 11


def test_a_new_root_queue_starts_the_counters_again():
    clock = FakeClock()
    executor = fixture.DashboardWifiExecutor(clock=clock)
    read_queue(executor)
    clock.now += 6
    assert read_queue(executor)["bytes"] > 0

    executor.run(Command("tc", ("qdisc", "del", "dev", "wlan0", "root"), "remove the root queue"))
    clock.now += 6
    again = read_queue(executor)
    assert (again["bytes"], again["packets"], again["drops"]) == (0, 100, 11)


@pytest.fixture
def no_release_side_effects(monkeypatch):
    """Any escape to production dispatch, process execution or the network must fail."""
    from jittersim import release_uploads, updates
    from jittersim.networking import helper

    def forbidden(*args, **kwargs):
        pytest.fail("The browser fixture escaped to a production release operation")

    monkeypatch.setattr(helper.HelperService, "_handle_payload", forbidden)
    for name in ("update_status", "request_update", "request_online_check", "online_overview",
                 "set_online_settings", "stage_upload"):
        monkeypatch.setattr(helper, name, forbidden)
    monkeypatch.setattr(updates, "retry_update", forbidden)
    monkeypatch.setattr(release_uploads, "receiving", forbidden)
    monkeypatch.setattr(fixture.server_module, "handle_release_upload", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


@pytest.mark.parametrize("seed", ["0", "1"])
def test_release_status_and_settings_do_not_use_host_state(monkeypatch, no_release_side_effects, seed):
    monkeypatch.setenv("JITTERSIM_BROWSER_TEST_SEED", seed)
    service = fixture.DashboardHelperService(None)
    status = service._handle_payload({"action": "update-status"})
    assert status["ok"] is True
    assert status["installed"] == ("test-release" if seed == "1" else "Unversioned installation")
    assert status["installable"] is (seed == "1")
    assert status["available"] == ("candidate-release" if seed == "1" else None)
    assert status["token_set"] is False
    assert status["retry"] is None
    for action in ("update-online-status", "update-online-settings"):
        view = service._handle_payload({"action": action})
        assert view["state"] == "none"
        assert view["token_set"] is False
        assert view["settings"] == {"daily_check": False, "install_automatically": False}
    changed = service._handle_payload({"action": "update-online-settings",
                                       "changes": {"install_automatically": True}})
    assert changed["settings"]["install_automatically"] is True
    changed["settings"]["daily_check"] = True
    assert service._handle_payload({"action": "update-online-status"})["settings"] == {
        "daily_check": False, "install_automatically": True}
    other = fixture.DashboardHelperService(None)
    assert other._handle_payload({"action": "update-online-settings"})["settings"] == {
        "daily_check": False, "install_automatically": False}


@pytest.mark.parametrize("seed", ["0", "1"])
@pytest.mark.parametrize("action", ["update-install", "update-rollback", "update-retry",
                                   "update-online-check", "update-stage-upload",
                                   "update-stage-identified-upload", "update-future-action"])
def test_release_operations_fail_closed(monkeypatch, no_release_side_effects, seed, action):
    monkeypatch.setenv("JITTERSIM_BROWSER_TEST_SEED", seed)
    reply = fixture.DashboardHelperService(None)._handle_payload({
        "action": action, "version": "candidate-release", "force": True,
        "retry_id": "a" * 64, "upload_id": "a" * 32})
    assert reply["ok"] is False
    assert reply["reason"] == "release-refused"
    assert "browser fixture" in reply["error"]


@pytest.mark.parametrize("changes", [[], {}, {"future_setting": True}, {"daily_check": "yes"}])
def test_fixture_settings_refuse_invalid_changes(no_release_side_effects, changes):
    reply = fixture.DashboardHelperService(None)._handle_payload({
        "action": "update-online-settings", "changes": changes})
    assert reply["ok"] is False


def test_every_direct_server_has_private_upload_paths_and_cleans_them(monkeypatch,
                                                                    no_release_side_effects):
    roots = []

    def initialize(server, *args, **kwargs):
        root = server.release_spool.parent
        assert root.is_dir()
        assert server.release_upload_lock.parent == root
        roots.append(root)
        server._speed_test_manager = SimpleNamespace(start=lambda *args, **kwargs: None)

    monkeypatch.setattr(fixture.ControlServer, "__init__", initialize)
    monkeypatch.setattr(fixture.ControlServer, "server_close", lambda server: None)
    with fixture.FixtureServer() as first, fixture.FixtureServer() as second:
        assert first.RequestHandlerClass is fixture.FixtureRequestHandler
        assert second.RequestHandlerClass is fixture.FixtureRequestHandler
        assert first.release_spool != second.release_spool
        assert first.release_spool != fixture.ControlServer.release_spool
        assert first.release_upload_lock != fixture.ControlServer.release_upload_lock
    assert all(not root.exists() for root in roots)


def test_server_initialization_failure_cleans_upload_directory(monkeypatch, no_release_side_effects):
    roots = []

    def fail(server, *args, **kwargs):
        roots.append(server.release_spool.parent)
        raise ValueError("fixture initialization failed")

    monkeypatch.setattr(fixture.ControlServer, "__init__", fail)
    with pytest.raises(ValueError, match="fixture initialization failed"):
        fixture.FixtureServer()
    assert not roots[0].exists()


@pytest.mark.parametrize("method", ["do_POST", "do_PUT"])
@pytest.mark.parametrize("authorized,device_allowed,expected", [
    (False, True, 401), (True, False, None), (True, True, 503)])
def test_http_upload_is_refused_before_body_storage(no_release_side_effects, method,
                                                   authorized, device_allowed, expected):
    class UnreadBody:
        def read(self, *args):
            pytest.fail("The fixture must refuse uploads before reading or storing the body")

    handler = object.__new__(fixture.FixtureRequestHandler)
    handler.path = "/api/updates/upload?attempt=1"
    handler.headers = {"Authorization": "Bearer browser-test", "Content-Length": "2",
                       "X-Jittersim-Archive-Bytes": "1"}
    handler.server = SimpleNamespace(admin_token="browser-test", is_authorized=lambda _: authorized)
    handler.rfile = UnreadBody()
    handler.close_connection = False
    checks = []

    def check_device(*, emergency):
        checks.append(emergency)
        return device_allowed

    handler._check_device_access = check_device
    responses = []
    handler._send_response = lambda **reply: responses.append(reply)
    getattr(handler, method)()
    assert checks == ([False] if authorized else [])
    if expected is None:
        assert responses == []
    else:
        assert responses[0]["status"] == expected
    if expected == 503:
        assert handler.close_connection is True
        assert responses[0]["headers"] == {"Connection": "close"}
        assert "disabled in the browser fixture" in json.loads(responses[0]["body"])["error"]


@pytest.mark.parametrize("seed", ["0", "1"])
def test_soak_helper_keeps_release_status_isolated(monkeypatch, no_release_side_effects, seed):
    # The soak target's CountingService inherits this QA helper.
    from tests.qa.dashboard_support import QAHelperService

    monkeypatch.setenv("JITTERSIM_BROWSER_TEST_SEED", seed)
    service = QAHelperService(None)
    assert service._handle_payload({"action": "update-status"})["installable"] is (seed == "1")
    assert service._handle_payload({"action": "update-install"})["ok"] is False
