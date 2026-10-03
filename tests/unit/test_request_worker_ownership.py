"""A started request keeps its socket and capacity until its worker finishes."""

import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pytest

from jittersim.api.server import ControlServer
from jittersim.config.storage import ConfigRepository
from jittersim.networking.helper import MAX_HELPER_CONNECTIONS, UnixHelperServer
from jittersim.request_workers import WorkersStillRunning


class StartInterrupted(BaseException):
    pass


class Request:
    def __init__(self):
        self.closed = 0

    def shutdown(self, how):
        pass

    def close(self):
        self.closed += 1


@pytest.fixture
def socket_folder():
    with TemporaryDirectory(prefix="js70-", dir="/tmp") as folder:
        yield Path(folder)


@pytest.mark.parametrize("kind", ["dashboard", "helper"])
@pytest.mark.parametrize("immediate", [False, True])
@pytest.mark.parametrize("error_type", [StartInterrupted, RuntimeError])
def test_partial_start_keeps_one_socket_and_capacity_owner(socket_folder, monkeypatch, kind, immediate, error_type):
    repository = ConfigRepository(":memory:")
    server = (ControlServer(("127.0.0.1", 0), repository)
              if kind == "dashboard" else UnixHelperServer(socket_folder / "helper.sock", SimpleNamespace()))
    entered = threading.Event()
    release = threading.Event()
    threads = []
    original_start = threading.Thread.start

    def finish_request(request, address):
        entered.set()
        if not immediate:
            assert release.wait(3), "test never released the active request"

    def interrupted_start(thread):
        threads.append(thread)
        original_start(thread)
        assert entered.wait(1), "request worker never began"
        if immediate:
            thread.join(1)
            assert not thread.is_alive()
        raise error_type("can't start new thread")

    monkeypatch.setattr(server, "finish_request", finish_request)
    monkeypatch.setattr(threading.Thread, "start", interrupted_start)
    request = Request()
    try:
        if error_type is StartInterrupted:
            with pytest.raises(StartInterrupted):
                server.process_request(request, ("127.0.0.1", 1))
        else:
            server.process_request(request, ("127.0.0.1", 1))
        if kind == "dashboard":
            assert server.active_workers == (0 if immediate else 1)
        else:
            assert server._request_slots._value == MAX_HELPER_CONNECTIONS - (0 if immediate else 1)
        assert request.closed == (1 if immediate else 0)
    finally:
        release.set()
        for thread in threads:
            thread.join(2)
            assert not thread.is_alive()
        server.server_close()
        repository.close()
    assert request.closed == 1
    if kind == "dashboard":
        assert server.active_workers == 0
    else:
        assert server._request_slots._value == MAX_HELPER_CONNECTIONS


@pytest.mark.parametrize("kind", ["dashboard", "helper"])
def test_constructor_failure_can_release_proven_unstarted_ownership(socket_folder, monkeypatch, kind):
    repository = ConfigRepository(":memory:")
    server = (ControlServer(("127.0.0.1", 0), repository)
              if kind == "dashboard" else UnixHelperServer(socket_folder / "helper.sock", SimpleNamespace()))
    request = Request()

    def fail_constructor(*args, **kwargs):
        raise RuntimeError("constructor failed")

    monkeypatch.setattr(threading, "Thread", fail_constructor)
    try:
        server.process_request(request, ("127.0.0.1", 1))
        assert request.closed == 1
        if kind == "dashboard":
            assert server.active_workers == 0
        else:
            assert server._request_slots._value == MAX_HELPER_CONNECTIONS
    finally:
        server.server_close()
        repository.close()


@pytest.mark.parametrize("kind", ["dashboard", "helper"])
def test_unknown_start_retains_ownership_until_child_really_exits(socket_folder, monkeypatch, kind):
    repository = ConfigRepository(":memory:")
    server = (ControlServer(("127.0.0.1", 0), repository)
              if kind == "dashboard" else UnixHelperServer(socket_folder / "helper.sock", SimpleNamespace()))
    original_start = threading.Thread.start
    delayed = []
    handled = []

    def unknown_start(thread):
        delayed.append(thread)
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(threading.Thread, "start", unknown_start)
    monkeypatch.setattr(server, "finish_request", lambda *args: handled.append(args))
    request = Request()
    try:
        server.process_request(request, ("127.0.0.1", 1))
        assert request.closed == 0
        if kind == "dashboard":
            assert server.active_workers == 1
        else:
            assert server._request_slots._value == MAX_HELPER_CONNECTIONS - 1
        with pytest.raises(WorkersStillRunning):
            server._request_workers.close(timeout=0.01)
        assert request.closed == 0
        assert repository.get_runtime_state() is not None
    finally:
        for thread in delayed:
            original_start(thread)
            thread.join(1)
            assert not thread.is_alive()
        server.server_close()
        repository.close()
    assert request.closed == 1
    assert handled == [], "uncertain startup must fence later mutation admission"
