"""Only unfinished identity checks may be shared by concurrent HTTP workers."""

import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from jittersim.api.server import MAX_DASHBOARD_WORKERS, ControlServer
from jittersim.config.storage import ConfigRepository
from jittersim.networking.deadlines import deadline_scope
from jittersim.networking.helper import HelperClientError


PEER = "192.168.50.20"


def identity(allowed=True):
    return {"allowed": allowed, "can_register": not allowed, "network": "test",
            "grants": ["manual"] if allowed else []}


class WaitingEvent:
    """Observe admission to a wait without depending on thread scheduling delays."""

    def __init__(self):
        self.event = threading.Event()
        self.condition = threading.Condition()
        self.waiters = 0

    def wait(self, timeout=None):
        with self.condition:
            self.waiters += 1
            self.condition.notify_all()
        return self.event.wait(timeout)

    def set(self):
        self.event.set()

    def joined(self, count):
        with self.condition:
            assert self.condition.wait_for(lambda: self.waiters >= count, timeout=2)


class Pending:
    def __init__(self, operation, peer, values):
        self.operation, self.peer, self.values = operation, peer, values
        self.release = threading.Event()
        self.answer = identity()

    def finish(self, answer=None):
        if answer is not None:
            self.answer = answer
        self.release.set()


class ControlledHelper:
    timeout_seconds = 2

    def __init__(self):
        self.calls = queue.Queue()
        self.pending = []
        self.lock = threading.Lock()

    def dashboard_access(self, operation, *, peer, **values):
        return self.call(operation, peer, values)

    def call(self, operation, peer, values):
        pending = Pending(operation, peer, values)
        with self.lock:
            self.pending.append(pending)
        self.calls.put(pending)
        assert pending.release.wait(4), "test did not release its synthetic helper read"
        if isinstance(pending.answer, BaseException):
            raise pending.answer
        return pending.answer

    def next(self, operation="status", peer=PEER):
        call = self.calls.get(timeout=2)
        assert (call.operation, call.peer) == (operation, peer)
        return call


@pytest.fixture
def setup():
    helper = ControlledHelper()
    repository = ConfigRepository(":memory:")
    server = ControlServer(("127.0.0.1", 0), repository, helper_client=helper)
    pool = ThreadPoolExecutor(max_workers=MAX_DASHBOARD_WORKERS + 3)
    try:
        yield server, helper, pool
    finally:
        with helper.lock:
            for pending in helper.pending:
                pending.release.set()
        pool.shutdown(wait=True)
        server.server_close()
        repository.close()


def observe(server, peer=PEER):
    with server._device_access_lock:
        event = WaitingEvent()
        server._device_access_flights[peer].done = event
        return event


def test_eight_concurrent_checks_share_one_read_then_recheck_revocation(setup):
    server, helper, pool = setup
    leader = pool.submit(server.dashboard_access, PEER)
    first = helper.next()
    event = observe(server)
    followers = [pool.submit(server.dashboard_access, PEER) for _ in range(7)]
    event.joined(7)
    assert helper.calls.empty()
    first.finish()
    answers = [future.result(timeout=2) for future in [leader, *followers]]
    assert all(answer["allowed"] for answer in answers)
    answers[0]["grants"].clear()
    assert all(answer["grants"] == ["manual"] for answer in answers[1:])
    assert not server._device_access_flights
    later = pool.submit(server.dashboard_access, PEER)
    helper.next().finish(identity(False))
    assert later.result(timeout=2)["allowed"] is False
    assert not server._device_access_flights


@pytest.mark.parametrize("answer", [
    identity(False),
    HelperClientError("Device verification is busy.", reason="helper-busy"),
    HelperClientError("helper unavailable"),
    RuntimeError("unexpected helper failure"),
    {"allowed": "yes", "can_register": False, "network": "test"},
])
def test_denial_invalid_answers_and_errors_reach_every_waiter_and_are_not_kept(setup, answer):
    server, helper, pool = setup
    leader = pool.submit(server.dashboard_access, PEER)
    call = helper.next()
    event = observe(server)
    waiter = pool.submit(server.dashboard_access, PEER)
    event.joined(1)
    call.finish(answer)
    for future in (leader, waiter):
        if isinstance(answer, BaseException):
            with pytest.raises(type(answer), match=str(answer)) as failure:
                future.result(timeout=2)
            if isinstance(answer, HelperClientError):
                assert failure.value.reason == answer.reason
        elif type(answer["allowed"]) is not bool:
            with pytest.raises(HelperClientError, match="invalid response"):
                future.result(timeout=2)
        else:
            assert future.result(timeout=2)["allowed"] is False
    assert not server._device_access_flights
    fresh = pool.submit(server.dashboard_access, PEER)
    helper.next().finish()
    assert fresh.result(timeout=2)["allowed"] is True


def test_peers_and_servers_do_not_share_reads(setup):
    server, helper, pool = setup
    with ConfigRepository(":memory:") as repository:
        with ControlServer(("127.0.0.1", 0), repository, helper_client=helper) as other:
            first = pool.submit(server.dashboard_access, PEER)
            call1 = helper.next()
            second = pool.submit(server.dashboard_access, "192.168.50.21")
            call2 = helper.next(peer="192.168.50.21")
            third = pool.submit(other.dashboard_access, PEER)
            call3 = helper.next()
            for call in (call1, call2, call3):
                call.finish()
            assert all(f.result(timeout=2)["allowed"] for f in (first, second, third))


@pytest.mark.parametrize("write_error", [None, HelperClientError("remove failed")])
def test_reads_cross_neither_write_boundary_and_old_completion_keeps_new_flight(setup, write_error):
    server, helper, pool = setup
    old = pool.submit(server.dashboard_access, PEER)
    old_call = helper.next()
    write = pool.submit(server.dashboard_access, PEER, "remove", mac_address="02:00:00:00:00:01")
    write_call = helper.next("remove")
    during = pool.submit(server.dashboard_access, PEER)
    during_call = helper.next()
    write_call.finish(write_error or identity(False))
    if write_error:
        with pytest.raises(HelperClientError, match="remove failed"):
            write.result(timeout=2)
    else:
        write.result(timeout=2)
    after = pool.submit(server.dashboard_access, PEER)
    after_call = helper.next()
    event = observe(server)
    old_call.finish()
    during_call.finish()
    old.result(timeout=2)
    during.result(timeout=2)
    waiter = pool.submit(server.dashboard_access, PEER)
    event.joined(1)
    assert helper.calls.empty()
    after_call.finish(identity(False))
    assert not after.result(timeout=2)["allowed"]
    assert not waiter.result(timeout=2)["allowed"]
    assert not server._device_access_flights


@pytest.mark.parametrize("sync_error", [False, True])
def test_protected_sync_invalidates_both_boundaries_even_on_failure(setup, sync_error):
    server, helper, pool = setup
    helper.dashboard_access_protected_sync = lambda devices: helper.call("protected-sync", PEER, {})
    old = pool.submit(server.dashboard_access, PEER)
    old_call = helper.next()
    sync = pool.submit(server.sync_protected_access)
    sync_call = helper.next("protected-sync")
    during = pool.submit(server.dashboard_access, PEER)
    during_call = helper.next()
    sync_call.finish(HelperClientError("sync failed") if sync_error else
                     {"protected_synced": True, "protected_count": 0})
    assert sync.result(timeout=2)["synced"] is not sync_error
    after = pool.submit(server.dashboard_access, PEER)
    after_call = helper.next()
    for call in (old_call, during_call, after_call):
        call.finish(identity(False))
    assert not any(f.result(timeout=2)["allowed"] for f in (old, during, after))


@pytest.mark.parametrize("mode", ["emergency", "deadline", "values", "loopback"])
def test_emergency_scoped_reads_values_and_loopback_bypass_sharing(setup, mode):
    server, helper, pool = setup
    ordinary = pool.submit(server.dashboard_access, PEER)
    ordinary_call = helper.next()

    def bypass():
        if mode == "deadline":
            with deadline_scope(time.monotonic() + 2):
                return server.dashboard_access(PEER)
        if mode == "loopback":
            return server.dashboard_access("127.0.0.1")
        return server.dashboard_access(PEER, "emergency-status" if mode == "emergency" else "status",
                                       **({"name": "different"} if mode == "values" else {}))

    special = pool.submit(bypass)
    if mode != "loopback":
        helper.next("emergency-status" if mode == "emergency" else "status").finish(identity(False))
    assert special.result(timeout=2)["allowed"] is (mode == "loopback")
    ordinary_call.finish()
    assert ordinary.result(timeout=2)["allowed"]


def test_waiter_timeout_fails_closed_without_losing_the_leader(setup):
    server, helper, pool = setup
    leader = pool.submit(server.dashboard_access, PEER)
    call = helper.next()
    helper.timeout_seconds = 0.01
    with pytest.raises(HelperClientError) as failure:
        server.dashboard_access(PEER)
    assert failure.value.reason == "helper-busy"
    assert len(server._device_access_flights) == 1
    call.finish()
    assert leader.result(timeout=2)["allowed"]
    assert not server._device_access_flights


def test_flight_table_is_bounded_and_overflow_keeps_the_existing_read_path(setup):
    server, helper, pool = setup
    futures, calls = [], []
    for index in range(MAX_DASHBOARD_WORKERS + 1):
        peer = f"192.168.50.{index + 30}"
        futures.append(pool.submit(server.dashboard_access, peer))
        calls.append(helper.next(peer=peer))
    assert len(server._device_access_flights) == MAX_DASHBOARD_WORKERS
    for call in calls:
        call.finish()
    assert all(f.result(timeout=2)["allowed"] for f in futures)
    assert not server._device_access_flights
