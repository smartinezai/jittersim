"""A shutdown timeout must retain shared state and prevent worker restart."""

import json
import os
import signal
import threading
import time

import pytest

from jittersim.api import server as api
from jittersim.api.router_operations import RouterOperations
from jittersim.api.wifi_operations import WifiOperations, WifiOutcome
from jittersim.config.storage import ConfigRepository
from jittersim.engine.runtime import RuntimeScheduler
from jittersim.engine.service_resolver import ServiceAddressRefresher
from jittersim.request_workers import WorkersStillRunning


@pytest.mark.parametrize("kind", ["router", "wifi"])
def test_background_close_retains_admitted_work_until_actual_completion(kind):
    entered, release = threading.Event(), threading.Event()

    def block():
        entered.set()
        assert release.wait(3)

    if kind == "router":
        def execute(path, payload):
            block()
            return 200, "application/json", json.dumps({"result": {"enabled": True}}).encode()
        operations = RouterOperations(execute, "fixture")
        operations.start("/api/router/wifi", {"admin_password": "test"})
    else:
        def execute():
            block()
            return WifiOutcome(200, "Done.")
        operations = WifiOperations(delay=0)
        operations.start("reset-name", execute)
    assert entered.wait(1)
    try:
        with pytest.raises(WorkersStillRunning):
            operations.close(timeout=0.01)
        if kind == "router":
            assert operations.status("test")["busy"]
            assert operations.start("/api/router/wifi", {"admin_password": "test"})[0] == 409
        else:
            assert operations.busy()
            assert operations.start("reset-name", execute) is None
    finally:
        release.set()
        operations.close()


@pytest.mark.parametrize("kind", ["scheduler", "service-addresses"])
def test_stopped_background_owner_keeps_live_thread_and_cannot_restart(kind):
    entered, release = threading.Event(), threading.Event()
    if kind == "scheduler":
        owner = RuntimeScheduler(database_path=":memory:", helper_client=object())
    else:
        owner = ServiceAddressRefresher(applied_plan=lambda: None, helper_client=object())

    def blocked():
        entered.set()
        assert release.wait(3)

    thread = threading.Thread(target=blocked, daemon=True)
    owner._thread = thread
    thread.start()
    assert entered.wait(1)
    try:
        with pytest.raises(WorkersStillRunning):
            owner.stop(timeout=0.01)
        assert owner._thread is thread
        owner.start()
        assert owner._thread is thread
    finally:
        release.set()
        thread.join(1)
        assert not thread.is_alive()
        owner.stop()


def test_run_server_keeps_repository_open_when_request_shutdown_is_unresolved(monkeypatch):
    repository = ConfigRepository(":memory:")

    class UnresolvedServer:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.server_close()

        def recover_saved_network_state(self):
            pass

        def serve_forever(self):
            raise KeyboardInterrupt()

        def server_close(self):
            raise WorkersStillRunning("request still owns repository")

    monkeypatch.setattr(api, "ConfigRepository", lambda path: repository)
    monkeypatch.setattr(api, "ControlServer", UnresolvedServer)
    try:
        with pytest.raises(WorkersStillRunning):
            api.run_server(database_path=":memory:")
        assert repository.get_runtime_state() is not None
    finally:
        repository.close()


@pytest.mark.parametrize("kind", ["scheduler", "service-addresses"])
def test_background_start_exception_cannot_drop_or_replace_started_worker(kind, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    owner = (RuntimeScheduler(database_path=":memory:", helper_client=object())
             if kind == "scheduler" else
             ServiceAddressRefresher(applied_plan=lambda: None, helper_client=object()))

    def blocked():
        entered.set()
        assert release.wait(3)

    original_start = threading.Thread.start

    def partial_start(thread):
        original_start(thread)
        assert entered.wait(1)
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(owner, "_run", blocked)
    monkeypatch.setattr(threading.Thread, "start", partial_start)
    try:
        with pytest.raises(RuntimeError):
            owner.start()
        thread = owner._thread
        assert thread is not None and thread.is_alive()
        with pytest.raises(WorkersStillRunning):
            owner.stop(timeout=0.01)
        owner.start()
        assert owner._thread is thread
    finally:
        release.set()
        owner.stop()


class _StopsItself:
    """A dashboard server that is told to stop by the service manager while it serves."""

    closed = 0

    def __init__(self, *args, **kwargs):
        pass

    def recover_saved_network_state(self):
        pass

    def serve_forever(self):
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(5)
        raise AssertionError("SIGTERM did not stop the server")

    def server_close(self):
        # A second SIGTERM must not cut into the shutdown that the first one began.
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(0.05)
        type(self).closed += 1


def test_sigterm_takes_the_ctrl_c_exit_path_and_exits_zero(monkeypatch):
    repository = ConfigRepository(":memory:")
    _StopsItself.closed = 0
    before = signal.getsignal(signal.SIGTERM)
    monkeypatch.setattr(api, "ConfigRepository", lambda path: repository)
    monkeypatch.setattr(api, "ControlServer", _StopsItself)
    stopped = []
    monkeypatch.setattr(RuntimeScheduler, "stop", lambda self, *a, **k: stopped.append(self))
    try:
        started = time.monotonic()
        assert api.main(["--database", ":memory:"]) == 0
        assert time.monotonic() - started < 3
        assert _StopsItself.closed == 1 and len(stopped) == 1
        # The process leaves the signal as it found it.
        assert signal.getsignal(signal.SIGTERM) is before
    finally:
        signal.signal(signal.SIGTERM, before)
        repository.close()


def test_sigterm_with_unresolved_workers_still_exits_nonzero(monkeypatch):
    repository = ConfigRepository(":memory:")
    before = signal.getsignal(signal.SIGTERM)

    class Unresolved(_StopsItself):
        def server_close(self):
            raise WorkersStillRunning("request still owns repository")

    monkeypatch.setattr(api, "ConfigRepository", lambda path: repository)
    monkeypatch.setattr(api, "ControlServer", Unresolved)
    try:
        with pytest.raises(WorkersStillRunning):
            api.main(["--database", ":memory:"])
        assert repository.get_runtime_state() is not None
        assert signal.getsignal(signal.SIGTERM) is before
    finally:
        signal.signal(signal.SIGTERM, before)
        repository.close()


def test_sigterm_while_the_server_is_still_starting_exits_zero(monkeypatch):
    repository = ConfigRepository(":memory:")
    before = signal.getsignal(signal.SIGTERM)

    def starting(*args, **kwargs):
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(5)
        raise AssertionError("SIGTERM did not stop the start")

    monkeypatch.setattr(api, "ConfigRepository", lambda path: repository)
    monkeypatch.setattr(api, "ControlServer", starting)
    try:
        assert api.main(["--database", ":memory:"]) == 0
        assert signal.getsignal(signal.SIGTERM) is before
    finally:
        signal.signal(signal.SIGTERM, before)
        repository.close()
