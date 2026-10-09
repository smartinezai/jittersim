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
import ssl
import subprocess
import tempfile
import threading
import time
from collections import deque
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
def dashboard(monkeypatch, *, input_seconds=INPUT_SECONDS, tls=False):
    monkeypatch.setattr(api, "REQUEST_INPUT_TIMEOUT_SECONDS", input_seconds)
    with tempfile.TemporaryDirectory(prefix="js70-", dir="/tmp") as temporary:
        directory = Path(temporary).resolve()
        certificates = {}
        context = None
        if tls:
            certificate, key = directory / "dashboard.crt", directory / "dashboard.key"
            subprocess.run(
                ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-nodes", "-days", "1",
                 "-keyout", str(key), "-out", str(certificate), "-subj", "/CN=127.0.0.1",
                 "-addext", "subjectAltName=IP:127.0.0.1"],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            certificates = {"tls_certfile": certificate, "tls_keyfile": key}
            context = ssl.create_default_context(cafile=str(certificate))
        with served_helper(directory) as (service, client, _), ConfigRepository(":memory:") as repo:
            repo.save_profile(Profile(id="normal", name="Normal"))
            scheduler = RuntimeScheduler(database_path=":memory:", helper_client=client)
            server = api.ControlServer(
                ("127.0.0.1", 0), repo, admin_token=TOKEN, helper_client=client, scheduler=scheduler,
                **certificates,
            )
            serving = threading.Thread(target=server.serve_forever, daemon=True)
            serving.start()
            clients = []

            def request(method, path, body=None):
                connection = (
                    http.client.HTTPSConnection(*server.server_address[:2], timeout=10, context=context)
                    if tls else http.client.HTTPConnection(*server.server_address[:2], timeout=10))
                if tls:
                    # Under a flood the kernel's listen queue can overflow, and macOS
                    # then resets the TCP connect before the server ever sees it.
                    # Nothing was sent, so only that step is retried. A reset during
                    # the TLS handshake or the request still fails the test.
                    deadline = time.monotonic() + 5
                    while True:
                        try:
                            raw = socket.create_connection(server.server_address[:2], timeout=10)
                            break
                        except (ConnectionResetError, ConnectionRefusedError):
                            if time.monotonic() > deadline:
                                raise
                    connection.sock = context.wrap_socket(raw, server_hostname="127.0.0.1")
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


def test_stalled_clients_cannot_take_the_panic_reserve_and_capacity_recovers(monkeypatch):
    # Changed on purpose (was: the reserve fills with stalled clients and Panic gets 503).
    # A reserved slot is now handed out only for a complete, authorized Panic request,
    # so clients that stall after a few bytes wait in the bounded pending queue instead.
    with dashboard(monkeypatch) as env:
        running_experiment(env.service)
        env.stall(ORDINARY + RESERVE)
        assert wait_until(lambda: env.server.active_workers == ORDINARY, 2)
        assert wait_until(lambda: len(env.server._pending_connections) == RESERVE, 2)
        assert env.server._reserved_requests == set()

        # An ordinary request beyond the 16 workers is still refused at once, never queued.
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
        assert env.server.active_workers == ORDINARY

        # A Panic request without the token does not get the reserve either.
        unauthorized = socket.create_connection(env.server.server_address[:2], timeout=2)
        try:
            unauthorized.sendall(b"POST /api/normal-access HTTP/1.1\r\nHost: x\r\n"
                                 b"Authorization: Bearer wrong\r\nContent-Length: 2\r\n\r\n{}")
            assert unauthorized.recv(4096).startswith(b"HTTP/1.1 503 ")
        finally:
            unauthorized.close()
        assert env.service._panic_stop_latched is False

        # Panic gets through while the stalled clients are still there.
        status, _, body = env.request("POST", "/api/normal-access", {})
        assert status == 200, body
        assert env.service._panic_stop_latched is True
        assert env.service._active_plan is None

        # Every stalled client is cut off at the input deadline and capacity comes back whole.
        assert wait_until(lambda: env.server.active_workers == 0, INPUT_SECONDS + 3)
        assert wait_until(lambda: not env.server._pending_connections, 2)
        assert env.server._reserved_requests == set()
        assert env.request("GET", "/api/profiles")[0] == 200
        assert env.request("POST", "/api/normal-access", {})[0] == 200


def test_panic_gets_through_while_one_byte_stalls_are_renewed(monkeypatch):
    # Eighteen connections that send one byte and stall, renewed as soon as one ends.
    with dashboard(monkeypatch) as env:
        running_experiment(env.service)
        stop = threading.Event()
        renewed = [0]

        def staller():
            open_ = []
            while not stop.is_set():
                open_ = [c for c in open_ if not _closed_by_server(c)]
                while len(open_) < ORDINARY + RESERVE and not stop.is_set():
                    try:
                        connection = socket.create_connection(env.server.server_address[:2], timeout=2)
                        connection.sendall(b"P")
                    except OSError:
                        break
                    open_.append(connection)
                    renewed[0] += 1
                time.sleep(0.01)
            for connection in open_:
                connection.close()

        thread = threading.Thread(target=staller, daemon=True)
        thread.start()
        try:
            assert wait_until(lambda: env.server.active_workers == ORDINARY, 3)
            assert wait_until(lambda: renewed[0] >= ORDINARY + RESERVE, 3)
            for _ in range(3):
                status, _, body = env.request("POST", "/api/normal-access", {})
                assert status == 200, body
            assert env.server._reserved_requests == set()
            assert len(env.server._pending_connections) <= api.MAX_PENDING_DASHBOARD_CONNECTIONS
        finally:
            stop.set()
            thread.join(5)
        assert env.service._panic_stop_latched is True
        assert wait_until(lambda: env.server.active_workers == 0, INPUT_SECONDS + 3)
        assert env.request("GET", "/api/profiles")[0] == 200


def _closed_by_server(connection):
    connection.setblocking(False)
    try:
        return connection.recv(1) == b""
    except BlockingIOError:
        return False
    except OSError:
        return True
    finally:
        connection.setblocking(True)


def test_tls_panic_completes_its_handshake_during_a_continuing_flood(monkeypatch):
    with dashboard(monkeypatch, tls=True) as env:
        running_experiment(env.service)
        stop = threading.Event()
        opened = [0]

        def flood():
            held = deque()
            while not stop.is_set():
                try:
                    held.append(socket.create_connection(env.server.server_address[:2], timeout=1))
                except OSError:
                    continue
                opened[0] += 1
                if len(held) > 40:
                    held.popleft().close()
                # About a thousand connections a second in all: far more than the 16 a
                # Panic handshake can afford to see, but within what the kernel's listen
                # queue holds. Beyond that macOS resets connections before accept.
                time.sleep(0.002)
            for connection in held:
                connection.close()

        threads = [threading.Thread(target=flood, daemon=True) for _ in range(2)]
        for thread in threads:
            thread.start()
        try:
            limit = api.MAX_PENDING_DASHBOARD_CONNECTIONS
            assert wait_until(lambda: len(env.server._pending_connections) == limit, 3)
            assert wait_until(lambda: opened[0] >= 4 * limit, 5)
            for _ in range(3):
                before = opened[0]
                status, _, body = env.request("POST", "/api/normal-access", {})
                assert status == 200, body
                # The flood kept going while Panic made its handshake.
                assert opened[0] > before
            assert len(env.server._pending_connections) <= limit
        finally:
            stop.set()
            for thread in threads:
                thread.join(5)
        assert env.service._panic_stop_latched is True


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


def test_silent_flood_evicts_the_oldest_cleanly_and_stays_within_the_bound(monkeypatch):
    with dashboard(monkeypatch) as env:
        running_experiment(env.service)
        sizes = []
        accept = env.server._accept_connection

        def recording_accept():
            accept()
            sizes.append(len(env.server._pending_connections))

        monkeypatch.setattr(env.server, "_accept_connection", recording_accept)
        limit = api.MAX_PENDING_DASHBOARD_CONNECTIONS
        oldest = env.stall(limit, data=b"")
        assert wait_until(lambda: len(env.server._pending_connections) == limit, 2)
        newest = []
        for _ in range(8):
            # One at a time, so the order of arrival is the order of acceptance.
            accepted = len(sizes)
            newest.extend(env.stall(1, data=b""))
            assert wait_until(lambda: len(sizes) > accepted, 2)

        # Each newcomer replaced the oldest silent connection, which saw a clean close.
        for connection in oldest[:8]:
            connection.settimeout(2)
            assert connection.recv(1) == b""
        for connection in oldest[8:] + newest:
            connection.settimeout(0.05)
            with pytest.raises(socket.timeout):
                connection.recv(1)
        assert max(sizes) <= limit
        assert len(env.server._pending_connections) == limit
        assert env.server.active_workers == 0

        status, _, body = env.request("POST", "/api/normal-access", {})
        assert status == 200, body
        assert env.service._panic_stop_latched is True
        assert max(sizes) <= limit

        # The survivors expire at the input deadline and capacity comes back whole.
        assert wait_until(lambda: not env.server._pending_connections, INPUT_SECONDS + 3)
        assert wait_until(lambda: env.server.active_workers == 0, 2)
        assert env.server._reserved_requests == set()
        assert env.request("GET", "/api/profiles")[0] == 200


def stall_helper(path, count):
    sockets = []
    for _ in range(count):
        # A busy machine can fill the small listen queue before the helper accepts.
        # Such a refused connect never reached the helper, so it is simply repeated.
        deadline = time.monotonic() + 5
        while True:
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                connection.connect(str(path))
                break
            except ConnectionRefusedError:
                connection.close()
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.02)
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
