"""Deliberately stalled clients cannot exhaust capacity for good, and Panic gets through.

A disposable dashboard and a real helper run on loopback and a Unix socket in a
temporary folder. Stalled clients send a few bytes and then nothing. The tests
use the real limits: 16 ordinary dashboard workers plus 2 kept for Panic, and 8
helper connections. Only the input deadline of the dashboard is shortened so
the timeouts can be observed without waiting ten seconds.
"""

import http.client
import json
import socket
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from jittersim.api import server as api
from jittersim.config.models import Profile
from jittersim.config.storage import ConfigRepository
from jittersim.engine.access import AccessPlan
from jittersim.engine.runtime import RuntimeScheduler
from jittersim.networking import helper as helper_module
from jittersim.networking.access import AccessManager
from jittersim.networking.applier import NetworkApplier
from jittersim.networking.executor import FakeExecutor
from jittersim.networking.helper import (
    MAX_HELPER_CONNECTIONS,
    HelperClient,
    HelperClientError,
    HelperService,
    UnixHelperServer,
)
from jittersim.networking.topology import Topology
from tests.access_support import AccessExecutor
from tests.unit.test_helper_access import apply_payload

TOKEN = "capacity-test"
# Long enough for the checks that follow a fill, short enough to observe the expiry.
INPUT_SECONDS = 3.0
PARTIAL_REQUEST = b"GET /api/profiles HTTP/1.1\r\nHost:"
ORDINARY = api.MAX_DASHBOARD_WORKERS
RESERVE = api.RESERVED_PANIC_WORKERS


def wait_until(predicate, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


@contextmanager
def served_helper(directory):
    """A real helper on a Unix socket. Yields the service, a client and the server."""

    topology = Topology("eth0", "wlan0")
    manager = AccessManager(
        executor=AccessExecutor(directory),
        topology=topology,
        directory=directory,
        runtime_path=directory / "access-runtime.json",
    )
    service = HelperService(
        NetworkApplier(executor=FakeExecutor(), topology=topology),
        active_plan_path=directory / "active-plan.json",
        access_manager=manager,
        gateway_state_path=directory / "gateway.json",
    )
    server = UnixHelperServer(directory / "helper.sock", service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield service, HelperClient(socket_path=directory / "helper.sock"), server
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()
        assert not thread.is_alive()


def running_experiment(service):
    started = service.handle_payload({**apply_payload(AccessPlan()), "explicit_apply": True})
    assert started["ok"] is True
    assert service._active_plan is not None


@contextmanager
def dashboard(monkeypatch, *, input_seconds=INPUT_SECONDS):
    monkeypatch.setattr(api, "REQUEST_INPUT_TIMEOUT_SECONDS", input_seconds)
    with tempfile.TemporaryDirectory(prefix="js70-", dir="/tmp") as temporary:
        directory = Path(temporary).resolve()
        with served_helper(directory) as (service, client, _), ConfigRepository(":memory:") as repo:
            repo.save_profile(Profile(id="normal", name="Normal"))
            scheduler = RuntimeScheduler(database_path=":memory:", helper_client=client)
            server = api.ControlServer(
                ("127.0.0.1", 0), repo, admin_token=TOKEN, helper_client=client, scheduler=scheduler
            )
            serving = threading.Thread(target=server.serve_forever, daemon=True)
            serving.start()
            clients = []

            def request(method, path, body=None):
                connection = http.client.HTTPConnection(*server.server_address[:2], timeout=10)
                try:
                    headers = {"Authorization": "Bearer " + TOKEN}
                    payload = None if body is None else json.dumps(body)
                    if payload is not None:
                        headers["Content-Type"] = "application/json"
                    connection.request(method, path, body=payload, headers=headers)
                    response = connection.getresponse()
                    return response.status, dict(response.getheaders()), response.read()
                finally:
                    connection.close()

            def stall(count, data=PARTIAL_REQUEST):
                opened = []
                for _ in range(count):
                    connection = socket.create_connection(server.server_address[:2], timeout=2)
                    if data:
                        connection.sendall(data)
                    opened.append(connection)
                clients.extend(opened)
                return opened

            try:
                yield SimpleNamespace(
                    server=server,
                    service=service,
                    request=request,
                    stall=stall,
                    directory=directory,
                )
            finally:
                for connection in clients:
                    connection.close()
                server.shutdown()
                serving.join(5)
                server.server_close()
                scheduler.stop()


def test_audited_limits_are_the_limits_in_force():
    assert api.MAX_DASHBOARD_WORKERS == 16
    assert api.RESERVED_PANIC_WORKERS == 2
    assert api.PRIORITY_WRITE_PATHS == frozenset({"/api/normal-access"})
    assert api.REQUEST_INPUT_TIMEOUT_SECONDS == 10.0
    assert api.ControlServer.request_timeout_seconds == 5.0
    assert api.MAX_PENDING_DASHBOARD_CONNECTIONS == 16
    assert helper_module.MAX_HELPER_CONNECTIONS == 8
    assert helper_module.WRITE_LOCK_WAIT_SECONDS == 3.0
    assert helper_module.MAX_MESSAGE_BYTES == 256 * 1024


def test_sixteen_stalled_clients_still_leave_the_reserve_for_panic_alone(monkeypatch):
    with dashboard(monkeypatch) as env:
        running_experiment(env.service)
        env.stall(ORDINARY)
        assert wait_until(lambda: env.server.active_workers == ORDINARY, 2)
        # Every stalled client holds an ordinary slot. None of them sits in the reserve.
        assert env.server._reserved_requests == set()

        status, headers, body = env.request("GET", "/api/profiles")
        assert status == 503
        assert headers["Retry-After"] == "1"
        assert json.loads(body) == {"error": "the dashboard is busy, try again"}
        assert wait_until(lambda: env.server.active_workers == ORDINARY, 2)
        # The refused request left no helper effect behind.
        assert env.service._active_plan is not None

        status, _, body = env.request("POST", "/api/normal-access", {})
        assert status == 200, body
        assert env.service._panic_stop_latched is True
        assert env.service._active_plan is None
        assert wait_until(lambda: env.server.active_workers == ORDINARY, 2)

        # Capacity returns when the stalled clients time out.
        assert wait_until(lambda: env.server.active_workers == 0, INPUT_SECONDS + 3)
        status, _, _ = env.request("GET", "/api/profiles")
        assert status == 200
        assert env.request("POST", "/api/normal-access", {})[0] == 200


def test_every_worker_stalled_refuses_new_connections_then_capacity_recovers(monkeypatch):
    with dashboard(monkeypatch) as env:
        running_experiment(env.service)
        env.stall(ORDINARY + RESERVE)
        assert wait_until(lambda: env.server.active_workers == ORDINARY + RESERVE, 2)
        assert len(env.server._reserved_requests) == RESERVE

        # Beyond 16+2 the answer is an immediate bare 503, never a wait or a new worker.
        started = time.monotonic()
        refused = socket.create_connection(env.server.server_address[:2], timeout=2)
        try:
            refused.sendall(b"GET /api/profiles HTTP/1.1\r\nHost: x\r\n\r\n")
            reply = refused.recv(4096)
        finally:
            refused.close()
        assert reply.startswith(b"HTTP/1.1 503 ")
        assert b"Retry-After: 1" in reply
        assert time.monotonic() - started < 1.0
        assert env.server.active_workers == ORDINARY + RESERVE

        # Panic is refused too while the reserve is full of stalled clients.
        status, _, _ = env.request("POST", "/api/normal-access", {})
        assert status == 503
        assert env.service._panic_stop_latched is False
        assert env.service._active_plan is not None

        # Every stalled client is cut off at the input deadline and capacity comes back whole.
        assert wait_until(lambda: env.server.active_workers == 0, INPUT_SECONDS + 3)
        assert env.server._reserved_requests == set()
        assert env.request("GET", "/api/profiles")[0] == 200
        assert env.request("POST", "/api/normal-access", {})[0] == 200
        assert env.service._panic_stop_latched is True
        assert env.service._active_plan is None


def test_silent_connections_take_no_worker_slot_and_expire_before_panic_goes_through(monkeypatch):
    with dashboard(monkeypatch) as env:
        running_experiment(env.service)
        # Fewer than the pending limit, so ordinary traffic is still accepted.
        env.stall(api.MAX_PENDING_DASHBOARD_CONNECTIONS - 2, data=b"")
        assert wait_until(lambda: len(env.server._pending_connections) == 14, 2)
        assert env.server.active_workers == 0
        assert env.request("GET", "/api/profiles")[0] == 200
        assert env.request("POST", "/api/normal-access", {})[0] == 200
        assert env.service._panic_stop_latched is True

        assert wait_until(lambda: not env.server._pending_connections, INPUT_SECONDS + 3)
        assert env.server.active_workers == 0


@pytest.mark.xfail(
    strict=True,
    reason=(
        "ControlServer._accept_connection (src/jittersim/api/server.py:1146) closes every new "
        "connection once MAX_PENDING_DASHBOARD_CONNECTIONS (16, server.py:161) silent clients are "
        "waiting for their first byte. The 2 Panic workers are not consulted at that stage, so "
        "Panic is reset instead of answered, and nothing is returned to ordinary clients either. "
        "The slots free only at REQUEST_INPUT_TIMEOUT_SECONDS (10 s, 5 s for TLS handshakes)."
    ),
)
def test_panic_gets_through_while_silent_connections_fill_the_pending_queue(monkeypatch):
    with dashboard(monkeypatch) as env:
        running_experiment(env.service)
        env.stall(api.MAX_PENDING_DASHBOARD_CONNECTIONS, data=b"")
        assert wait_until(lambda: len(env.server._pending_connections) == 16, 2)
        try:
            status, _, _ = env.request("POST", "/api/normal-access", {})
        except (http.client.HTTPException, OSError):
            status = None
        assert status == 200
        assert env.service._panic_stop_latched is True


def stall_helper(path, count):
    sockets = []
    for _ in range(count):
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.connect(str(path))
        connection.sendall(b"{")
        sockets.append(connection)
    return sockets


def test_eight_stalled_helper_connections_are_the_limit_and_capacity_recovers():
    with tempfile.TemporaryDirectory(prefix="js70-", dir="/tmp") as temporary:
        directory = Path(temporary).resolve()
        with served_helper(directory) as (service, client, server):
            running_experiment(service)
            stalled = stall_helper(directory / "helper.sock", MAX_HELPER_CONNECTIONS)
            try:
                assert wait_until(lambda: server._request_slots._value == 0, 2)
                # All eight were admitted. None was answered with a busy reply.
                for connection in stalled:
                    connection.settimeout(0.05)
                    with pytest.raises(socket.timeout):
                        connection.recv(1)

                for call in (client.ping, client.normal_access):
                    started = time.monotonic()
                    with pytest.raises(HelperClientError) as busy:
                        call()
                    assert busy.value.reason == "helper-busy"
                    assert time.monotonic() - started < 1.0
                # The helper owns no reserve of its own. Panic waits for the input deadline.
                assert service._panic_stop_latched is False
                assert service._active_plan is not None

                # The three second input deadline frees every connection without any dispatch.
                # Finished workers hand their slot back when the next request reaps them.
                assert wait_until(lambda: not server._request_workers.has_workers, 6)
                assert server._request_slots._value == MAX_HELPER_CONNECTIONS
                assert service._active_plan is not None
                assert client.ping()["ok"] is True
                assert client.normal_access()["ok"] is True
                assert service._panic_stop_latched is True
                assert service._active_plan is None
            finally:
                for connection in stalled:
                    connection.close()
