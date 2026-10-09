"""The bounded queue of dashboard connections that have not been dispatched yet.

The accept loop is driven by hand on loopback, so the order of events is exact.
"""

import logging
import select
import socket
import time

import pytest

from jittersim.api import server as api
from jittersim.config.storage import ConfigRepository

TOKEN = "queue-test"
PANIC = (b"POST /api/normal-access HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer " + TOKEN.encode()
         + b"\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}")


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setattr(api, "REQUEST_INPUT_TIMEOUT_SECONDS", 1.0)
    repository = ConfigRepository(":memory:")
    instance = api.ControlServer(("127.0.0.1", 0), repository, admin_token=TOKEN)
    clients = []

    def connect():
        client = socket.create_connection(instance.server_address, timeout=2)
        clients.append(client)
        return client

    def accept(client):
        assert select.select([instance.socket], [], [], 2)[0]
        instance._accept_connection()
        connection, pending = next(reversed(instance._pending_connections.items()))
        assert pending.address == client.getsockname()
        return connection

    instance.connect = connect
    instance.accept = accept
    try:
        yield instance
    finally:
        for client in clients:
            client.close()
        instance.server_close()
        repository.close()


def readable(connection):
    return bool(select.select([connection], [], [], 2)[0])


def closed_cleanly(client):
    client.settimeout(2)
    return client.recv(1) == b""


def still_open(client):
    client.settimeout(0.05)
    try:
        client.recv(1)
    except socket.timeout:
        return True
    return False


def test_a_ready_oldest_connection_is_served_before_the_listener_is_accepted(server, monkeypatch):
    monkeypatch.setattr(api, "MAX_PENDING_DASHBOARD_CONNECTIONS", 2)
    oldest, other = server.connect(), server.connect()
    oldest_side = server.accept(oldest)
    server.accept(other)
    oldest.sendall(b"GET /api/health HTTP/1.0\r\n\r\n")
    newcomer = server.connect()
    assert readable(oldest_side)
    assert readable(server.socket)

    connection, address = server.get_request()
    try:
        assert address == oldest.getsockname()
        assert server._request_inputs[connection][0] == b"G"
        assert still_open(other) and still_open(newcomer)
    finally:
        server.shutdown_request(connection)


def test_a_connection_with_input_waiting_is_never_the_one_replaced(server, monkeypatch):
    monkeypatch.setattr(api, "MAX_PENDING_DASHBOARD_CONNECTIONS", 2)
    oldest, other = server.connect(), server.connect()
    oldest_side = server.accept(oldest)
    server.accept(other)
    oldest.sendall(PANIC)
    assert readable(oldest_side)
    newcomer = server.connect()
    server.accept(newcomer)

    assert oldest_side in server._pending_connections
    assert closed_cleanly(other)
    assert len(server._pending_connections) == 2


def test_when_every_waiting_connection_has_input_the_newcomer_is_closed(server, monkeypatch):
    monkeypatch.setattr(api, "MAX_PENDING_DASHBOARD_CONNECTIONS", 1)
    first = server.connect()
    first_side = server.accept(first)
    first.sendall(PANIC)
    assert readable(first_side)
    newcomer = server.connect()
    assert select.select([server.socket], [], [], 2)[0]
    server._accept_connection()

    assert list(server._pending_connections) == [first_side]
    assert closed_cleanly(newcomer)


def test_the_peer_holding_the_most_waiting_connections_loses_its_oldest(server, monkeypatch):
    monkeypatch.setattr(api, "MAX_PENDING_DASHBOARD_CONNECTIONS", 3)
    owner, flood_old, flood_new = server.connect(), server.connect(), server.connect()
    sides = [server.accept(client) for client in (owner, flood_old, flood_new)]
    server._pending_connections[sides[0]].address = ("192.168.50.20", 50000)
    server._pending_connections[sides[1]].address = ("192.168.50.66", 50001)
    server._pending_connections[sides[2]].address = ("192.168.50.66", 50002)
    server.accept(server.connect())

    assert sides[0] in server._pending_connections
    assert sides[1] not in server._pending_connections
    assert closed_cleanly(flood_old)
    assert still_open(owner) and still_open(flood_new)


def test_within_one_peer_a_silent_connection_goes_before_one_that_made_progress(server, monkeypatch):
    monkeypatch.setattr(api, "MAX_PENDING_DASHBOARD_CONNECTIONS", 2)
    started, silent = server.connect(), server.connect()
    started_side = server.accept(started)
    server.accept(silent)
    # For example a TLS handshake that is under way.
    server._pending_connections[started_side].progressed = True
    server.accept(server.connect())

    assert started_side in server._pending_connections
    assert closed_cleanly(silent)


def test_replacements_are_reported_in_a_rate_limited_warning_with_a_count(server, monkeypatch, caplog):
    monkeypatch.setattr(api, "MAX_PENDING_DASHBOARD_CONNECTIONS", 1)
    caplog.set_level(logging.WARNING, logger=api.LOGGER.name)
    server.accept(server.connect())

    def replace_one():
        server.accept(server.connect())

    def warnings():
        return [record.getMessage() for record in caplog.records
                if "waiting dashboard connection" in record.getMessage()]

    replace_one()
    assert len(warnings()) == 1 and " 1 " in warnings()[0]
    replace_one()
    replace_one()
    assert len(warnings()) == 1
    server._eviction_warned_at -= api.EVICTION_WARNING_INTERVAL_SECONDS + 1
    replace_one()
    assert len(warnings()) == 2 and " 3 " in warnings()[1]
    # No client address or request content is logged.
    assert "127.0.0.1" not in warnings()[1]


@pytest.mark.parametrize("data, verdict", [
    (PANIC, True),
    (PANIC[:-1], None),
    (PANIC[:PANIC.index(b"\r\n\r\n")], None),
    (PANIC.replace(TOKEN.encode(), b"wrong"), False),
    (PANIC.replace(b"Authorization: Bearer " + TOKEN.encode() + b"\r\n", b""), False),
    (PANIC.replace(b"POST", b"GET"), False),
    (PANIC.replace(b"/api/normal-access", b"/api/apply"), False),
    (PANIC.replace(b"Content-Length: 2", b"Transfer-Encoding: chunked"), False),
    (PANIC.replace(b"Content-Length: 2", b"Content-Length: 999999"), False),
    (PANIC.replace(b"Content-Length: 2", b"Content-Length: x"), False),
    (PANIC.replace(b"Content-Length: 2\r\n", b"")[:-2], True),
    (b"\xff\xfe garbage\r\n\r\n", False),
])
def test_only_a_complete_authorized_panic_request_qualifies_for_the_reserve(server, data, verdict):
    assert server._waiting_request_verdict(data) is verdict


def test_waiting_input_stays_bounded(server, monkeypatch):
    monkeypatch.setattr(api, "MAX_DASHBOARD_WORKERS", 0)
    client = server.connect()
    side = server.accept(client)
    client.sendall(b"POST /api/normal-access HTTP/1.1\r\nX: " + b"a" * (api.MAX_WAITING_REQUEST_BYTES + 10))
    deadline = time.monotonic() + 2
    while side in server._pending_connections and time.monotonic() < deadline:
        with pytest.raises(TimeoutError):
            server.get_request()
    assert side not in server._pending_connections
    client.settimeout(2)
    assert client.recv(4096).startswith(b"HTTP/1.1 503 ")
