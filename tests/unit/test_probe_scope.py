"""An echo probe may only target a host of the test Wi-Fi network, never another local network."""

import ipaddress
import socket
from types import SimpleNamespace
from unittest import mock

import pytest

from jittersim import speed_test as speed
from jittersim.api import speed_tests as routes
from jittersim.api.speed_tests import probe_network
from jittersim.speed_test import NoProber
from tests.unit.test_device_speed_test import ManagerBase

TEST_NETWORK = ipaddress.ip_network("192.168.50.0/24")


class FakeIcmpSocket:
    """A datagram socket stand-in that records what is connected and sent."""

    def __init__(self, local):
        self.local = local
        self.connected = None
        self.sent = []

    def setblocking(self, flag):
        pass

    def connect(self, address):
        self.connected = address

    def getsockname(self):
        return (self.local, 0)

    def send(self, data):
        self.sent.append(data)
        return len(data)

    def close(self):
        pass


def make_prober(target, local, network=TEST_NETWORK):
    built = []

    def factory():
        built.append(FakeIcmpSocket(local))
        return built[-1]

    return speed.IcmpProber(target, socket_factory=factory, network=network), built


def test_a_host_of_the_test_network_is_probed():
    prober, built = make_prober("192.168.50.10", "192.168.50.1")
    assert prober.status == "measured"
    prober.send()
    assert len(built[0].sent) == 1


@pytest.mark.parametrize("target,local", [
    ("192.0.2.1", "192.0.2.20"),          # a peer on another local network, same /24 as the Pi there
    ("192.168.1.1", "192.168.1.5"),       # the household router on the uplink
    ("10.0.0.7", "10.0.0.2"),
])
def test_an_address_outside_the_test_network_gets_no_socket_and_no_packet(target, local):
    prober, built = make_prober(target, local)
    assert prober.status == "unavailable"
    assert built == []
    prober.send()
    assert prober.probes == {}


@pytest.mark.parametrize("target", ["192.168.50.0", "192.168.50.255"])
def test_the_network_and_broadcast_addresses_are_never_probed(target):
    prober, built = make_prober(target, "192.168.50.1")
    assert prober.status == "unavailable"
    assert built == []


def test_the_pi_own_address_is_never_probed():
    prober, built = make_prober("192.168.50.1", "192.168.50.1")
    assert prober.status == "unavailable"
    assert built[0].sent == []


def test_a_route_that_leaves_the_test_network_is_refused_after_connect():
    # The target is a test network address but the route picked an address outside it.
    prober, built = make_prober("192.168.50.10", "192.0.2.20")
    assert prober.status == "unavailable"
    prober.send()
    assert built[0].sent == []


def test_without_a_named_network_the_old_subnet_rule_still_applies():
    prober, _ = make_prober("192.0.2.1", "192.0.2.20", network=None)
    assert prober.status == "measured"
    refused, _ = make_prober("192.168.50.255", "192.168.50.1", network=None)
    assert refused.status == "unavailable"


def test_the_real_socket_factory_is_not_used_when_the_target_is_outside_the_network():
    with mock.patch.object(socket, "socket") as make_socket:
        prober = speed.IcmpProber("192.168.1.1", network=TEST_NETWORK)
    assert prober.status == "unavailable"
    make_socket.assert_not_called()


def test_probe_network_reads_the_dashboard_test_network():
    assert probe_network(SimpleNamespace(_tls_wifi_network=TEST_NETWORK)) == TEST_NETWORK
    assert probe_network(SimpleNamespace()) is None


class DefaultProberScopeTests(ManagerBase):
    def test_the_default_prober_of_a_speed_test_is_given_the_test_wifi_network(self) -> None:
        del self.server.speed_test_prober_factory
        built = {}

        class Recorder(NoProber):
            def __init__(self, target, **options):
                super().__init__("unavailable")
                built.update(target=target, **options)

        with mock.patch.object(routes, "IcmpProber", Recorder):
            self.start_short()
        self.assertEqual(built["target"], "192.168.50.10")
        self.assertEqual(built["network"], TEST_NETWORK)
