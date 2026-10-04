import threading

import pytest

from jittersim.networking.worker_slots import start_admitted_worker


def admitted():
    slots = threading.BoundedSemaphore(1)
    assert slots.acquire(blocking=False)
    return slots


def test_partial_start_retains_capacity_until_physical_worker_finishes():
    slots = admitted()
    entered, release, done = threading.Event(), threading.Event(), threading.Event()

    class InterruptedThread(threading.Thread):
        def start(self):
            super().start()
            assert entered.wait(1)
            raise KeyboardInterrupt("native worker already started")

    def action():
        entered.set()
        release.wait(2)
        done.set()

    with pytest.raises(KeyboardInterrupt):
        start_admitted_worker(slots, action, thread_factory=InterruptedThread)
    assert not slots.acquire(blocking=False)
    release.set()
    assert done.wait(1)
    # Joining is unnecessary for correctness. Acquire waits for the release finally.
    assert slots.acquire(timeout=1)
    assert not slots.acquire(blocking=False)
    slots.release()


def test_known_native_creation_failure_returns_admission():
    slots = admitted()

    class FailedThread(threading.Thread):
        def start(self):
            raise RuntimeError("can't start new thread")

    with pytest.raises(RuntimeError):
        start_admitted_worker(slots, lambda: None, thread_factory=FailedThread)
    assert slots.acquire(blocking=False)
    assert not slots.acquire(blocking=False)


def test_ambiguous_failure_before_callback_retains_admission_fail_closed():
    slots = admitted()

    class AmbiguousThread(threading.Thread):
        def start(self):
            raise KeyboardInterrupt("cannot prove whether a native worker exists")

    with pytest.raises(KeyboardInterrupt):
        start_admitted_worker(slots, lambda: None, thread_factory=AmbiguousThread)
    assert not slots.acquire(blocking=False)


def test_ordinary_worker_is_registered_until_finished_and_can_be_joined():
    slots = admitted()
    release = threading.Event()
    workers = set()
    worker = start_admitted_worker(
        slots,
        lambda: release.wait(1),
        daemon=False,
        register=workers.add,
        unregister=workers.discard,
    )
    assert not worker.daemon
    assert workers == {worker}
    release.set()
    worker.join(1)
    assert not worker.is_alive()
    assert not workers
    assert slots.acquire(blocking=False)


def test_creation_failure_removes_registration():
    slots = admitted()
    workers = set()

    class FailedThread(threading.Thread):
        def start(self):
            raise RuntimeError("can't start new thread")

    with pytest.raises(RuntimeError):
        start_admitted_worker(
            slots,
            lambda: None,
            thread_factory=FailedThread,
            register=workers.add,
            unregister=workers.discard,
        )
    assert not workers
    assert slots.acquire(blocking=False)


@pytest.mark.parametrize("surface", ["https", "policy-unix", "control-unix"])
def test_each_server_holds_partial_start_admission(surface, monkeypatch):
    import ssl
    import tempfile
    from pathlib import Path

    import jittersim.networking.worker_slots as ownership
    from jittersim.api.homepage import PolicyServer
    from jittersim.networking.helper import UnixHelperServer

    entered, release = threading.Event(), threading.Event()
    physical_workers = []

    class InterruptedThread(threading.Thread):
        def start(self):
            physical_workers.append(self)
            super().start()
            assert entered.wait(1)
            raise KeyboardInterrupt("physical worker already started")

    original = ownership.start_admitted_worker

    def interrupted(*args, **kwargs):
        return original(*args, thread_factory=InterruptedThread, **kwargs)

    if surface == "https":
        monkeypatch.setattr(ownership, "start_admitted_worker", interrupted)
    else:
        import jittersim.request_workers as requests
        monkeypatch.setattr(requests.threading, "Thread", InterruptedThread)
    with tempfile.TemporaryDirectory(prefix="admit-", dir="/tmp") as directory:
        if surface == "https":
            server = PolicyServer(("127.0.0.1", 0), None, ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER))
            slots, capacity = server.slots, 4
        else:
            policy = surface == "policy-unix"
            server = UnixHelperServer(Path(directory) / "s", None, policy_only=policy)
            slots, capacity = server._request_slots, 2 if policy else 8

        def blocked(*_):
            entered.set()
            release.wait(2)

        monkeypatch.setattr(server, "process_request_thread", blocked)
        try:
            with pytest.raises(KeyboardInterrupt):
                server.process_request(object(), None)
            for _ in range(capacity - 1):
                assert slots.acquire(blocking=False)
            assert not slots.acquire(blocking=False)
            release.set()
            for worker in physical_workers:
                worker.join(1)
                assert not worker.is_alive()
            if surface != "https":
                server._request_workers.reap()
            assert slots.acquire(timeout=1)
            assert not slots.acquire(blocking=False)
        finally:
            release.set()
            server.server_close()
