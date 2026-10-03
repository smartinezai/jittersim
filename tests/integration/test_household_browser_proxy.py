"""Check applied household MAC policy through real browser tunnels."""

import json
import socket
import socketserver
import threading
from ipaddress import IPv4Address

import pytest

from jittersim.config.models import AccessOptions, Device, MacPolicy, Profile
from jittersim.engine.access import AccessPlan
from jittersim.engine.codec import plan_to_payload
from jittersim.engine.compiler import compile_plan
from jittersim.engine.plan import GeneralProfileRule
from jittersim.networking.access import atomic_write
from jittersim.networking.browser_proxy import BrowserProxyServer
from tests.integration.test_browser_proxy import _EchoHandler

AFFECTED = "02:00:00:00:00:02"
PROTECTED = "02:00:00:00:00:01"
ADDRESS = IPv4Address("192.168.50.10")


@pytest.fixture
def tunnel(tmp_path):
    active = tmp_path / "active.json"
    access = tmp_path / "access-policy.json"
    echo = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _EchoHandler)
    echo.daemon_threads = True
    echo_thread = threading.Thread(target=echo.serve_forever, daemon=True)
    echo_thread.start()
    identity = [AFFECTED]
    marks = []
    proxy = BrowserProxyServer(
        ("127.0.0.1", 0), active_plan_path=active,
        access_policy_path=access, require_access_policy=True,
        client_network="127.0.0.0/8", identify_client=lambda _ip: ADDRESS,
        identify_mac=lambda _ip: identity[0],
        mark_socket=lambda _socket, mark: marks.append(mark),
        resolve_addresses=lambda _host: [
            (socket.AF_INET, socket.SOCK_STREAM, 0, echo.server_address)
        ],
    )
    proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    proxy_thread.start()
    try:
        yield active, access, proxy, identity, marks
    finally:
        proxy.shutdown()
        proxy.server_close()
        proxy_thread.join(timeout=2)
        echo.shutdown()
        echo.server_close()
        echo_thread.join(timeout=2)


def publish_profile(active, embedded, general):
    profile = Profile("slow", "Slow")
    plan = compile_plan(
        devices=() if general else (Device("alice", "Alice", ADDRESS, "slow"),),
        profiles={"slow": profile}, protected_mac_addresses=(PROTECTED,),
        general_profile=GeneralProfileRule("slow") if general else None,
        mac_policy=embedded.mac, access=embedded,
    )
    atomic_write(active, json.dumps(plan_to_payload(plan)))


def connect(proxy, expected):
    with socket.create_connection(proxy.server_address, timeout=3) as client:
        client.sendall(b"CONNECT example.com:443 HTTP/1.1\r\n\r\n")
        response = client.recv(4096)
        assert expected in response
        if b"200" in expected:
            client.sendall(b"household-access-body")
            assert client.recv(4096) == b"household-access-body"


@pytest.mark.parametrize("general", (False, True))
@pytest.mark.parametrize("client_mac", (AFFECTED, PROTECTED))
def test_live_disable_releases_cached_mac_blocks_without_reapplying_profile(
    tunnel, general, client_mac
):
    active, access, proxy, identity, marks = tunnel
    identity[0] = client_mac
    embedded = AccessPlan(mac=MacPolicy("denylist", (), (client_mac,)))
    publish_profile(active, embedded, general)
    before = active.read_bytes()
    atomic_write(access, json.dumps(embedded.payload()))
    connect(proxy, b"503 Service Unavailable")
    disabled = AccessPlan(options=AccessOptions(enabled=False), mac=embedded.mac)
    atomic_write(access, json.dumps(disabled.payload()))

    connect(proxy, b"200 Connection Established")

    assert active.read_bytes() == before
    expected_mark = 0 if client_mac == PROTECTED else 1
    assert marks == [expected_mark, expected_mark]


@pytest.mark.parametrize("general", (False, True))
@pytest.mark.parametrize("client_mac", (AFFECTED, PROTECTED))
def test_live_blocks_win_over_disabled_or_pending_profile_policy(tunnel, general, client_mac):
    active, access, proxy, identity, marks = tunnel
    identity[0] = client_mac
    embedded = AccessPlan(options=AccessOptions(enabled=False))
    publish_profile(active, embedded, general)
    applied = AccessPlan(mac=MacPolicy("denylist", (), (client_mac,)))
    atomic_write(access, json.dumps(applied.payload()))

    connect(proxy, b"503 Service Unavailable")

    assert marks == []


@pytest.mark.parametrize("client_mac", (AFFECTED, None))
@pytest.mark.parametrize("mode", ("denylist", "allowlist"))
def test_active_household_mac_rules_apply_with_no_experiment(tunnel, client_mac, mode):
    _, access, proxy, identity, marks = tunnel
    identity[0] = client_mac
    applied = AccessPlan(mac=MacPolicy(mode, (PROTECTED,), (AFFECTED,)))
    atomic_write(access, json.dumps(applied.payload()))

    connect(proxy, b"503 Service Unavailable")

    assert marks == []


def test_applied_allowlist_ignores_inactive_saved_denylist(tunnel):
    active, access, proxy, _, marks = tunnel
    embedded = AccessPlan(mac=MacPolicy("denylist", (), (AFFECTED,)))
    publish_profile(active, embedded, general=True)
    applied = AccessPlan(mac=MacPolicy("allowlist", (AFFECTED,), (AFFECTED,)))
    atomic_write(access, json.dumps(applied.payload()))

    connect(proxy, b"200 Connection Established")

    assert marks == [1, 1]


@pytest.mark.parametrize("content", (None, "invalid policy"))
def test_unavailable_applied_policy_keeps_browser_access_closed(tunnel, content):
    active, access, proxy, _, marks = tunnel
    publish_profile(active, AccessPlan(options=AccessOptions(enabled=False)), general=True)
    if content is not None:
        atomic_write(access, content)

    connect(proxy, b"503 Service Unavailable")

    assert marks == []
