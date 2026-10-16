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


def test_panic_from_a_protection_only_device_resyncs_through_the_panic_reserve(tmp_path, monkeypatch):
    # N1: after a helper restart the emergency check of a protection-only device
    # needs a protection resync. With the eight operation slots busy and the
    # input pool stalled, only the Panic reserve is free, and the resync must get it.
    from jittersim.config.exemptions import UnaffectedDevice
    from jittersim.networking import helper as helper_module
    from jittersim.networking.dashboard_access import AccessStore
    from jittersim.networking.helper import MAX_HELPER_CONNECTIONS, HelperClient
    from jittersim.networking.helper import HelperClientError
    from tests.integration.test_capacity_stalled_clients import stall_helper

    release, entered = threading.Event(), threading.Semaphore(0)
    original = HelperService.handle_payload

    def held(self, payload):
        if isinstance(payload, dict) and payload.get("action") == "ping":
            entered.release()
            release.wait(30)
        return original(self, payload)

    def ping():
        # A busy machine can fill the small listen queue: a refused connect never
        # reached the helper and is simply repeated.
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not release.is_set():
            try:
                HelperClient(socket_path=socket_path, timeout_seconds=40).ping()
                return
            except HelperClientError:
                time.sleep(0.05)

    created = []
    with dashboard(tmp_path, service_class=panic_service(tmp_path, created)) as (request, store, _):
        service = created[0]
        server = request.server
        server.repository.save_unaffected_device(UnaffectedDevice(MAC, "Phone", 1))
        assert server.sync_protected_access()["synced"] is True
        # The root helper restarts and forgets the protection grants.
        service._access_store = AccessStore(store.path)
        socket_path = server.helper_client.socket_path
        monkeypatch.setattr(HelperService, "handle_payload", held)
        workers = [threading.Thread(target=ping, daemon=True)
                   for _ in range(MAX_HELPER_CONNECTIONS)]
        for worker in workers:
            worker.start()
        stalled = []
        try:
            for _ in workers:
                assert entered.acquire(timeout=30)
            stalled = stall_helper(socket_path, helper_module.MAX_PENDING_HELPER_INPUT)
            # The page retries a 503. The single kept slot is freed when the worker of
            # the previous call exits, which a loaded machine can delay past the next
            # call, so a few retries are allowed. Without the fix every one is refused.
            for _ in range(5):
                status, body = request("/api/normal-access", "POST", {})
                if status != 503:
                    break
                time.sleep(0.2)
        finally:
            for connection in stalled:
                connection.close()
            release.set()
            for worker in workers:
                worker.join(10)
        assert status == 200, body
        assert service._panic_stop_latched is True
        assert service._access_store.protected_synced is True


def hold_repository_lock(repository):
    """A database backup: it holds the repository lock for its whole duration."""
    held, stop = threading.Event(), threading.Event()

    def backup():
        with repository._lock:
            held.set()
            stop.wait(30)

    thread = threading.Thread(target=backup, daemon=True)
    thread.start()
    assert held.wait(5)
    return thread, stop


def test_panic_reaches_the_helper_while_a_backup_holds_the_repository(tmp_path):
    # N3: Panic's bookkeeping waits a bounded time for the repository, then the
    # helper still stops everything and the answer says the stop was not saved.
    from jittersim.api import server as server_module
    from jittersim.engine.runtime import RuntimeScheduler

    created, schedulers = [], []

    def configure(server):
        scheduler = RuntimeScheduler(database_path=tmp_path / "config.db",
                                     helper_client=server.helper_client)
        server.scheduler = scheduler
        schedulers.append(scheduler)

    with dashboard(tmp_path, configure=configure,
                   service_class=panic_service(tmp_path, created)) as (request, store, _):
        service = created[0]
        scheduler = schedulers[0]
        repository = request.server.repository
        assert request("/api/dashboard-access/register-self", "POST", {"name": "Phone"})[0] == 200
        backup, stop = hold_repository_lock(repository)
        try:
            started = time.monotonic()
            status, body = request("/api/normal-access", "POST", {})
            elapsed = time.monotonic() - started
            # The physical stop happened while the repository was still held.
            assert service._panic_stop_latched is True
        finally:
            stop.set()
            backup.join(10)
        assert status == 503, body
        assert b"could not be saved" in body
        assert elapsed < server_module.PANIC_DATABASE_ALLOWANCE_SECONDS + 2
        # Recovery stays unconfirmed until a Panic saves its result.
        assert scheduler._recovery_required is True
        assert scheduler.can_apply(scheduler.apply_generation) is False
        status, body = request("/api/normal-access", "POST", {})
        assert status == 200, body
        assert scheduler._recovery_required is False
        assert repository.access_settings().access_options.enabled is False
        assert repository.get_runtime_state().observed_state == "reset"
    for scheduler in schedulers:
        scheduler.stop()


def test_panic_saves_its_stop_after_the_helper_when_the_backup_ends_in_time(tmp_path):
    # N3: the stop intent could not be saved first, so it is saved with the result
    # after the helper call. A backup that ends meanwhile costs nothing.
    created = []
    with dashboard(tmp_path, service_class=panic_service(tmp_path, created)) as (request, store, _):
        service = created[0]
        repository = request.server.repository
        assert request("/api/dashboard-access/register-self", "POST", {"name": "Phone"})[0] == 200
        backup, stop = hold_repository_lock(repository)
        original = service.handle_payload

        def end_backup_during_cleanup(payload):
            if isinstance(payload, dict) and payload.get("action") == "normal-access":
                stop.set()
                backup.join(5)
            return original(payload)

        service.handle_payload = end_backup_during_cleanup
        try:
            status, body = request("/api/normal-access", "POST", {})
        finally:
            stop.set()
            backup.join(10)
        assert status == 200, body
        assert service._panic_stop_latched is True
        assert repository.access_settings().access_options.enabled is False
        assert repository.get_runtime_state().observed_state == "reset"


def test_panic_protection_lookup_is_retryable_while_a_backup_holds_the_repository(tmp_path):
    # N3: a protection-only device after a helper restart needs saved protection.
    # Without a snapshot inside the emergency budget the answer is a retryable 503,
    # never access and never an unbounded wait.
    from jittersim.config.exemptions import UnaffectedDevice
    from jittersim.networking.dashboard_access import AccessStore

    created = []
    with dashboard(tmp_path, service_class=panic_service(tmp_path, created)) as (request, store, _):
        service = created[0]
        server = request.server
        server.repository.save_unaffected_device(UnaffectedDevice(MAC, "Phone", 1))
        assert server.sync_protected_access()["synced"] is True
        service._access_store = AccessStore(store.path)  # the helper restarted
        backup, stop = hold_repository_lock(server.repository)
        try:
            started = time.monotonic()
            status, body = request("/api/normal-access", "POST", {})
            elapsed = time.monotonic() - started
        finally:
            stop.set()
            backup.join(10)
        assert status == 503, body
        assert elapsed < 4.0
        assert service._panic_stop_latched is False
        status, body = request("/api/normal-access", "POST", {})
        assert status == 200, body
        assert service._panic_stop_latched is True
