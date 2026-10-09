"""Dashboard Panic has its own device check (review T5 of PR 91).

Panic and Reset used the same `dashboard-access` status operation as ordinary
polling. That waits 0.5 s for the access lock, while an ordinary verification can
hold the lock for five seconds (`iw station dump`), so Panic got HTTP 503 without
ever calling normal_access. The emergency check keeps bearer authentication and
the saved-device check and does not wait for that lock. These tests go through
the real HTTP path, the helper client and the helper socket.
"""

import threading
import time

from jittersim.networking.access import AccessManager
from jittersim.networking.helper import HelperService
from jittersim.networking.topology import Topology
from tests.access_support import AccessExecutor
from tests.integration.test_dashboard_access_http import dashboard
from tests.unit.test_dashboard_access import MAC, PEER, PeerApplier

OTHER_MAC = "02:00:00:00:00:99"


class PanicApplier(PeerApplier):
    """A peer applier that can also stop experiments."""

    def __init__(self):
        super().__init__()
        self.slow_stations = 0.0

    def reset(self):
        return 0

    def list_connected_stations(self):
        if self.slow_stations:
            time.sleep(self.slow_stations)
        return super().list_connected_stations()


def panic_service(tmp_path, created):
    def build(applier, access_store):
        service = HelperService(
            PanicApplier(), access_store=access_store,
            access_manager=AccessManager(
                executor=AccessExecutor(tmp_path), topology=Topology("eth0", "wlan0"),
                directory=tmp_path / "household", runtime_path=tmp_path / "household.json"),
        )
        service._applier.route = applier.route
        service._applier.neighbors = applier.neighbors
        created.append(service)
        return service

    (tmp_path / "household").mkdir()
    return build


def hold_access_lock(service, seconds):
    held = threading.Event()

    def holder():
        with service._access_lock:
            held.set()
            time.sleep(seconds)

    thread = threading.Thread(target=holder, daemon=True)
    thread.start()
    assert held.wait(5)
    return thread


def test_panic_gets_through_while_an_ordinary_verification_holds_the_access_lock(tmp_path):
    created = []
    with dashboard(tmp_path, service_class=panic_service(tmp_path, created)) as (request, store, _):
        service = created[0]
        assert request("/api/dashboard-access/register-self", "POST", {"name": "Phone"})[0] == 200
        holder = hold_access_lock(service, 5.0)
        try:
            # The ordinary check still waits only briefly and refuses.
            assert request("/api/profiles")[0] in (409, 503)
            started = time.monotonic()
            status, body = request("/api/normal-access", "POST", {})
            elapsed = time.monotonic() - started
        finally:
            holder.join(10)
        assert status == 200, body
        assert elapsed < 4.0
        assert service._panic_stop_latched is True


def test_the_emergency_check_still_refuses_a_device_that_is_not_saved(tmp_path):
    created = []
    with dashboard(tmp_path, service_class=panic_service(tmp_path, created)) as (request, store, _):
        service = created[0]
        assert request("/api/dashboard-access/register-self", "POST", {"name": "Phone"})[0] == 200
        store.remove(MAC)
        holder = hold_access_lock(service, 3.0)
        try:
            status, body = request("/api/normal-access", "POST", {})
        finally:
            holder.join(10)
        assert status == 403, body
        assert service._panic_stop_latched is False


def test_the_emergency_check_needs_the_bearer_token(tmp_path):
    created = []
    with dashboard(tmp_path, service_class=panic_service(tmp_path, created)) as (request, store, _):
        assert request("/api/dashboard-access/register-self", "POST", {"name": "Phone"})[0] == 200
        assert request("/api/normal-access", "POST", {}, token="wrong")[0] == 401
        assert request("/api/normal-access", "POST", {}, token=None)[0] == 401
        assert created[0]._panic_stop_latched is False


def test_a_cached_verification_is_not_reused_for_another_device_on_the_same_address(tmp_path):
    created = []
    with dashboard(tmp_path, service_class=panic_service(tmp_path, created)) as (request, store, _):
        service = created[0]
        assert request("/api/dashboard-access/register-self", "POST", {"name": "Phone"})[0] == 200
        # Another device now answers on the verified address. Its MAC is not saved and
        # it is not a confirmed Wi-Fi station.
        service._applier.neighbors[:] = [
            {"dst": PEER, "dev": "wlan0", "lladdr": OTHER_MAC, "state": ["REACHABLE"]}]
        holder = hold_access_lock(service, 3.0)
        try:
            status, body = request("/api/normal-access", "POST", {})
        finally:
            holder.join(10)
        assert status == 403, body
        assert service._panic_stop_latched is False


def test_without_a_recent_verification_the_emergency_check_runs_a_bounded_full_check(tmp_path):
    created = []
    with dashboard(tmp_path, service_class=panic_service(tmp_path, created)) as (request, store, _):
        service = created[0]
        store.add(MAC, "Phone")  # saved, but never verified by this helper
        service._applier.slow_stations = 1.0
        holder = hold_access_lock(service, 5.0)
        try:
            started = time.monotonic()
            status, body = request("/api/normal-access", "POST", {})
            elapsed = time.monotonic() - started
        finally:
            holder.join(10)
        assert status == 200, body
        assert 1.0 <= elapsed < 4.5
        assert service._panic_stop_latched is True
