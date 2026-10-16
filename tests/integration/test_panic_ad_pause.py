"""A briefly busy ad-blocking lock does not fail Panic (gap audit 2026-10-09, finding 7).

Panic pauses ad blocking first. The settings lock gave up after three seconds, so a
slow ad-blocking change made Panic report failure although profiles were off.
"""

import fcntl
import threading
import time

from jittersim.networking.access import AccessManager
from jittersim.networking.adblocking import AdRouting, AdStore
from jittersim.networking.executor import FakeExecutor
from jittersim.networking.helper import HelperService
from jittersim.networking.topology import Topology
from tests.access_support import AccessExecutor
from tests.adblocking_support import provision_guard


def panic_service(tmp_path):
    store = AdStore(
        AdRouting("wlan0", FakeExecutor()), path=tmp_path / "policy",
        lock_path=tmp_path / "lock", active_path=tmp_path / "active",
        status_path=tmp_path / "status", intent_path=tmp_path / "intent",
        panic_guard=provision_guard(tmp_path),
    )
    manager = AccessManager(
        executor=AccessExecutor(tmp_path), topology=Topology("eth0", "wlan0"),
        directory=tmp_path / "access", runtime_path=tmp_path / "access-policy",
        ad_blocking_store=store,
    )
    from jittersim.networking.applier import NetworkApplier

    service = HelperService(
        NetworkApplier(executor=FakeExecutor(), topology=Topology("eth0", "wlan0")),
        access_manager=manager, ad_blocking_store=store,
        gateway_state_path=tmp_path / "gateway.json",
    )
    return service, store


def hold_lock(path, seconds):
    held = threading.Event()

    def holder():
        with path.open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            held.set()
            time.sleep(seconds)
            fcntl.flock(stream, fcntl.LOCK_UN)

    thread = threading.Thread(target=holder, daemon=True)
    thread.start()
    assert held.wait(5)
    return thread


def test_panic_waits_out_a_slow_ad_blocking_change(tmp_path):
    service, store = panic_service(tmp_path)
    holder = hold_lock(store.lock_path, 4.0)
    try:
        answer = service.handle_payload({"action": "normal-access"})
    finally:
        holder.join(10)
    assert answer["ok"] is True, answer
    assert answer["experiments_stopped"] is True
    assert store.read()["running"] is False


def test_a_stuck_ad_blocking_lock_still_fails_panic_honestly(tmp_path, monkeypatch):
    from jittersim.networking import helper as helper_module

    monkeypatch.setattr(helper_module, "AD_PAUSE_PANIC_WAIT_SECONDS", 0.5)
    service, store = panic_service(tmp_path)
    holder = hold_lock(store.lock_path, 2.0)
    try:
        answer = service.handle_payload({"action": "normal-access"})
    finally:
        holder.join(10)
    assert answer["ok"] is False
    assert answer["experiments_stopped"] is False
