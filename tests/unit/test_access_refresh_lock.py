"""The background address refresh takes turns with Panic (gap audit 2026-10-09, finding 3).

The refresh ran a full access transaction outside the helper's locks, so it could
change household rules while Panic released them and make Panic wait on the
access lock. It now runs only when the network lock is free and never after a
household stop.
"""

from types import SimpleNamespace

from jittersim.networking.applier import NetworkApplier
from jittersim.networking.executor import FakeExecutor
from jittersim.networking.helper import HelperService
from jittersim.networking.topology import Topology

DEVICES = [{"mac_address": "02:00:00:00:00:02", "ipv4_address": "192.168.50.11"}]


class SpyAccess:
    def __init__(self):
        self.applied = SimpleNamespace(has_dns_rules=True)
        self.refreshed = []
        self.health_checks = 0

    def check_health(self):
        self.health_checks += 1

    def refresh_addresses(self, devices):
        self.refreshed.append(devices)


def service():
    helper = HelperService(NetworkApplier(executor=FakeExecutor(), topology=Topology("eth0", "wlan0")))
    helper._access_manager = SpyAccess()
    helper._handle_clients = lambda: {"ok": True, "devices": DEVICES}
    return helper


def test_the_refresh_runs_when_the_network_is_idle():
    helper = service()
    helper.refresh_access_addresses()
    assert helper._access_manager.refreshed == [DEVICES]


def test_the_refresh_skips_a_round_while_panic_or_an_apply_holds_the_network_lock():
    helper = service()
    assert helper._network_lock.acquire(blocking=False)
    try:
        helper.refresh_access_addresses()
    finally:
        helper._network_lock.release()
    assert helper._access_manager.refreshed == []
    # The health check needs no network lock and still runs.
    assert helper._access_manager.health_checks == 1


def test_the_refresh_never_runs_after_a_household_stop():
    helper = service()
    helper._household_stop_latched = True
    helper.refresh_access_addresses()
    assert helper._access_manager.refreshed == []


def test_the_refresh_does_not_hold_the_network_lock_while_it_scans_clients():
    helper = service()
    held = []
    helper._handle_clients = lambda: held.append(helper._network_lock.locked()) or {
        "ok": True, "devices": DEVICES}
    helper.refresh_access_addresses()
    assert held == [False]
    assert not helper._network_lock.locked()
