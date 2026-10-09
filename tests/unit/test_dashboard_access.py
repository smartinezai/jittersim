"""Saved access must reject ambiguous identities and incomplete state changes."""

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from jittersim.networking.dashboard_access import AccessError, AccessStore, identify_peer
from jittersim.networking.helper import HelperService

MAC = "02:00:00:00:00:01"
PEER = "192.168.50.184"


class PeerApplier:
    test_interface = "wlan0"
    uplink_interface = "eth0"

    def __init__(self):
        self.route = {"dev": "wlan0"}
        self.neighbors = [{"dst": PEER, "dev": "wlan0", "lladdr": MAC, "state": ["REACHABLE"]}]
        self.stations = "Station " + MAC + " (on wlan0)\n"
        self.calls = 0

    def dashboard_peer_route(self, peer):
        self.calls += 1
        return self.route

    def dashboard_peer_neighbors(self, peer, interface):
        return self.neighbors

    def list_connected_stations(self):
        return self.stations


@pytest.mark.parametrize("state", ["REACHABLE", "STALE", "DELAY", "PROBE"])
def test_saved_identity_survives_restart_and_ip_changes(tmp_path, state):
    store = AccessStore(tmp_path / "access.json")
    applier = PeerApplier()
    service = HelperService(applier, access_store=store)
    request = {"action": "dashboard-access", "operation": "register-self", "peer": PEER, "name": "Mac"}
    assert service.handle_payload(request)["allowed"] is True
    assert store.path.stat().st_mode & 0o777 == 0o640
    restarted = HelperService(applier, access_store=AccessStore(store.path))
    new_ip = "192.168.50.180"
    applier.neighbors[0]["dst"] = new_ip
    applier.neighbors[0]["state"] = [state]
    assert restarted.handle_payload({**request, "operation": "status", "peer": new_ip})["allowed"]
    # Reusing the old IP does not inherit the saved hardware address.
    applier.neighbors[0].update(dst=PEER, lladdr="02:00:00:00:00:02")
    applier.stations = "Station 02:00:00:00:00:02 (on wlan0)\n"
    assert not restarted.handle_payload({**request, "operation": "status"})["allowed"]


@pytest.mark.parametrize("change", ["stale", "missing", "duplicate", "unassociated", "relay", "bad-route"])
def test_identity_requires_one_current_direct_device(change):
    applier = PeerApplier()
    if change == "stale":
        applier.neighbors[0]["state"] = ["STALE"]
    elif change == "missing":
        applier.neighbors = []
    elif change == "duplicate":
        applier.neighbors *= 2
    elif change == "unassociated":
        applier.stations = ""
    elif change == "relay":
        applier.route["gateway"] = "192.168.50.100"
    else:
        applier.route = {}
    if change == "bad-route":
        with pytest.raises(AccessError):
            identify_peer(applier, PEER)
    else:
        identity = identify_peer(applier, PEER)
        assert identity["network"] == "test"
        assert not identity["can_register"]
        assert identity["mac_address"] is None


def test_routed_management_caller_cannot_enroll_its_gateway():
    applier = PeerApplier()
    applier.route = {"dev": "eth0", "gateway": "192.168.0.1"}
    identity = identify_peer(applier, "10.20.30.40")
    assert identity["network"] == "management"
    assert not identity["can_register"]


@pytest.mark.parametrize("state", ["STALE", "DELAY", "PROBE"])
def test_saved_wifi_access_survives_neighbor_refresh_without_enrollment(tmp_path, state):
    applier = PeerApplier()
    applier.neighbors[0]["state"] = [state]
    store = AccessStore(tmp_path / "access.json")
    store.add(MAC)
    service = HelperService(applier, access_store=store)
    request = {"action": "dashboard-access", "peer": PEER}

    status = service.handle_payload(request)
    assert status["allowed"] is True
    assert status["can_register"] is False
    assert status["mac_address"] == MAC
    assert not service.handle_payload({**request, "operation": "register-self"})["ok"]
    assert service.handle_payload({**request, "operation": "remove", "mac_address": MAC})["ok"]
    assert not service.handle_payload(request)["allowed"]


@pytest.mark.parametrize("state", ["STALE", "DELAY", "PROBE"])
def test_neighbor_refresh_does_not_grant_unsaved_or_unassociated_access(tmp_path, state):
    applier = PeerApplier()
    applier.neighbors[0]["state"] = [state]
    store = AccessStore(tmp_path / "access.json")
    service = HelperService(applier, access_store=store)
    request = {"action": "dashboard-access", "peer": PEER}

    with patch("jittersim.networking.helper.time.monotonic", return_value=10):
        assert not service.handle_payload(request)["allowed"]
    store.add(MAC)
    applier.stations = ""
    with patch("jittersim.networking.helper.time.monotonic", return_value=11.1):
        assert not service.handle_payload(request)["allowed"]


@pytest.mark.parametrize(
    "state", [["FAILED"], ["INCOMPLETE"], ["PERMANENT"], ["NOARP"], [], None,
              ["REACHABLE", "FAILED"]],
)
def test_saved_wifi_access_rejects_unusable_neighbor_states(tmp_path, state):
    applier = PeerApplier()
    applier.neighbors[0]["state"] = state
    store = AccessStore(tmp_path / "access.json")
    store.add(MAC)
    service = HelperService(applier, access_store=store)
    assert not service.handle_payload({"action": "dashboard-access", "peer": PEER})["allowed"]


def test_registration_revalidates_and_removal_is_immediate(tmp_path):
    applier = PeerApplier()
    service = HelperService(applier, access_store=AccessStore(tmp_path / "access.json"))
    request = {"action": "dashboard-access", "peer": PEER}
    assert not service.handle_payload(request)["allowed"]
    applier.stations = ""
    assert not service.handle_payload({**request, "operation": "register-self"})["ok"]
    applier.stations = "Station " + MAC + " (on wlan0)\n"
    assert service.handle_payload({**request, "operation": "register-self"})["allowed"]
    assert service.handle_payload(request)["allowed"]
    assert service.handle_payload({**request, "operation": "remove", "mac_address": MAC})["ok"]
    assert not service.handle_payload(request)["allowed"]
    # Ordinary checks reuse identity for at most a second but always reread permissions.
    assert applier.calls == 4


def test_overlapping_status_reads_wait_briefly_and_reuse_only_identity(tmp_path):
    applier = PeerApplier()
    store = AccessStore(tmp_path / "access.json")
    store.add(MAC)
    service = HelperService(applier, access_store=store)
    identity_started = threading.Event()
    release_identity = threading.Event()
    all_checks_at_lock = threading.Event()
    attempts_lock = threading.Lock()
    attempts = 0
    original_identify = identify_peer

    class TrackingLock:
        def __init__(self):
            self.lock = threading.Lock()

        def acquire(self, *args, **kwargs):
            nonlocal attempts
            with attempts_lock:
                attempts += 1
                if attempts == 4:
                    all_checks_at_lock.set()
            return self.lock.acquire(*args, **kwargs)

        def release(self):
            self.lock.release()

    service._access_lock = TrackingLock()

    def slow_identify(*args, **kwargs):
        identity_started.set()
        assert release_identity.wait(timeout=1)
        return original_identify(*args, **kwargs)

    request = {"action": "dashboard-access", "peer": PEER}
    start = threading.Barrier(5)

    def check_status():
        start.wait(timeout=1)
        return service.handle_payload(request)

    with patch("jittersim.networking.helper.identify_peer", side_effect=slow_identify):
        with ThreadPoolExecutor(max_workers=4) as executor:
            checks = [executor.submit(check_status) for _ in range(4)]
            start.wait(timeout=1)
            assert identity_started.wait(timeout=1)
            assert all_checks_at_lock.wait(timeout=1)
            release_identity.set()
            results = [check.result(timeout=1) for check in checks]

    assert all(result["ok"] and result["allowed"] for result in results)
    assert applier.calls == 1
    assert service.handle_payload({**request, "operation": "remove", "mac_address": MAC})["ok"]
    assert not service.handle_payload(request)["allowed"]


def test_status_read_wait_is_bounded_and_access_writes_remain_serialized(tmp_path):
    service = HelperService(PeerApplier(), access_store=AccessStore(tmp_path / "access.json"))
    service._access_lock.acquire()
    try:
        with patch("jittersim.networking.helper.ACCESS_STATUS_LOCK_WAIT_SECONDS", 0.01):
            status = service.handle_payload({"action": "dashboard-access", "peer": PEER})
            remove = service.handle_payload({
                "action": "dashboard-access", "operation": "remove",
                "peer": PEER, "mac_address": MAC,
            })
    finally:
        service._access_lock.release()
    assert status["reason"] == "helper-busy"
    assert remove["reason"] == "helper-busy"


def test_concurrent_changes_duplicates_limits_and_failed_replace(tmp_path):
    store = AccessStore(tmp_path / "access.json")
    addresses = ["02:00:00:00:00:" + f"{number:02x}" for number in range(64)]
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(store.add, addresses))
    assert len(store.list_devices()) == 64
    store.add(addresses[0])
    with pytest.raises(AccessError):
        store.add("02:00:00:00:01:01")
    previous = store.path.read_bytes()
    with patch("jittersim.networking.dashboard_access.os.replace", side_effect=OSError):
        with pytest.raises(AccessError):
            store.remove(addresses[0])
    assert store.path.read_bytes() == previous
    assert not list(tmp_path.glob(".dashboard-access-*"))
    store.remove(addresses[0])
    assert len(store.list_devices()) == 63


@pytest.mark.parametrize("damage", ["invalid", "oversized", "permissions", "symlink", "duplicate", "fifo"])
def test_invalid_storage_never_grants_access(tmp_path, damage):
    store = AccessStore(tmp_path / "access.json")
    store.add(MAC)
    if damage == "invalid":
        store.path.write_text("invalid")
    elif damage == "oversized":
        store.path.write_bytes(b" " * 40000)
    elif damage == "permissions":
        store.path.chmod(0o666)
    elif damage == "symlink":
        target = tmp_path / "other.json"
        store.path.rename(target)
        store.path.symlink_to(target)
    elif damage == "fifo":
        store.path.unlink()
        os.mkfifo(store.path, 0o640)
    else:
        data = json.loads(store.path.read_bytes())
        data["devices"] *= 2
        store.path.write_text(json.dumps(data))
    with pytest.raises(AccessError):
        store.list_devices()


def test_names_and_addresses_are_validated_before_writing(tmp_path):
    store = AccessStore(tmp_path / "access.json")
    for mac in ("ff:ff:ff:ff:ff:ff", "00:00:00:00:00:00", "bad", None):
        with pytest.raises(AccessError):
            store.add(mac)
    with pytest.raises(AccessError):
        store.add(MAC, "name\nwith control character")
    assert not store.path.exists()


def test_installation_initializes_once_and_preserves_saved_access(tmp_path):
    store = AccessStore(tmp_path / "access.json")
    store.initialize()
    assert store.list_devices() == []
    store.add(MAC, "My Mac")
    previous = store.path.read_bytes()
    AccessStore(store.path).initialize()
    assert store.path.read_bytes() == previous
    store.path.write_text("damaged")
    with pytest.raises(AccessError):
        store.initialize()
    assert store.path.read_text() == "damaged"


def test_management_recovery_remains_available_with_a_damaged_list(tmp_path):
    applier = PeerApplier()
    applier.route = {"dev": "eth0", "gateway": "192.168.0.1"}
    store = AccessStore(tmp_path / "access.json")
    store.initialize()
    store.path.write_text("damaged")
    service = HelperService(applier, access_store=store)
    result = service.handle_payload({"action": "dashboard-access", "peer": "10.20.30.40"})
    assert result["allowed"]
    assert not result["can_register"]


def test_identity_cache_expires_without_caching_permissions(tmp_path):
    applier = PeerApplier()
    store = AccessStore(tmp_path / "access.json")
    store.add(MAC)
    service = HelperService(applier, access_store=store)
    request = {"action": "dashboard-access", "peer": PEER}
    with patch("jittersim.networking.helper.time.monotonic", return_value=10):
        assert service.handle_payload(request)["allowed"]
    applier.stations = ""
    with patch("jittersim.networking.helper.time.monotonic", return_value=11.1):
        assert not service.handle_payload(request)["allowed"]


def test_maximum_unicode_names_fit_the_bounded_file(tmp_path):
    store = AccessStore(tmp_path / "access.json")
    for number in range(64):
        store.add("02:00:00:00:00:" + f"{number:02x}", "😀" * 64)
    assert len(AccessStore(store.path).list_devices()) == 64
    assert store.path.stat().st_size <= 32 * 1024
