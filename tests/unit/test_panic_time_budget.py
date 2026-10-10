"""Panic answers inside one time budget even when every wait hits its bound (review G4).

Each wait on the Panic path had its own bound (network lock, Panic stop lock, ad
pause, household release lock, ad transition, reset and release commands), but
nothing bounded their sum. Stacked, they took far longer than the dashboard waits
for an answer, so the browser gave up and retried while the helper still held the
network lock. These tests use a fake clock: every lock wait lasts its full allowance
before it succeeds or fails, and every command takes its full timeout.
"""

import contextlib
import re
import subprocess
import threading
import time
from email.message import Message
from pathlib import Path

import pytest

from jittersim.api import server as server_module
from jittersim.api.server import ControlRequestHandler, ControlServer
from jittersim.config.storage import ConfigRepository
from jittersim.networking import helper as helper_module
from jittersim.networking import adblocking as adblocking_module
from jittersim.networking import executor as executor_module
from jittersim.networking.access import AccessManager
from jittersim.networking.adblocking import AdRouting
from jittersim.networking.applier import NetworkApplier
from jittersim.networking.executor import SubprocessExecutor
from jittersim.networking.helper import HelperClient, HelperClientError, HelperService
from jittersim.networking.topology import Topology
from jittersim import panic_guard as panic_guard_module

DASHBOARD_JS = Path(server_module.__file__).with_name("dashboard.js")


class Clock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(0.0, seconds)


@pytest.fixture
def clock(monkeypatch):
    fake = Clock()
    monkeypatch.setattr(time, "monotonic", fake.monotonic)
    monkeypatch.setattr(time, "sleep", fake.sleep)
    return fake


class SlowCommands:
    """Stands in for subprocess.run: every command uses its whole timeout."""

    def __init__(self, clock, *, time_out):
        self.clock = clock
        self.time_out = time_out
        self.calls = []

    def __call__(self, argv, *, input=None, timeout=None, **_):
        assert timeout is not None and timeout > 0, argv
        self.calls.append((tuple(argv), input or "", timeout))
        self.clock.now += timeout
        if self.time_out:
            raise subprocess.TimeoutExpired(argv, timeout)
        stdout = ""
        if tuple(argv[:3]) == ("tc", "-j", "qdisc"):
            stdout = '[{"handle": "10:", "kind": "prio"}]'  # a project queue to remove
        elif argv[0] == "hostapd_cli":
            stdout = "OK\n"
        return subprocess.CompletedProcess(argv, 0, stdout, "")


class SlowLock:
    """A lock another owner releases only when the waiter's allowance runs out."""

    def __init__(self, clock):
        self.clock = clock
        self.owner = None
        self.count = 0
        self.waits = []

    def acquire(self, blocking=True, timeout=-1):
        if self.owner == threading.get_ident():
            self.count += 1
            return True
        assert blocking and timeout is not None and timeout >= 0, "an unbounded wait on the Panic path"
        self.waits.append(timeout)
        self.clock.now += timeout
        self.owner, self.count = threading.get_ident(), 1
        return True

    def release(self):
        self.count -= 1
        if not self.count:
            self.owner = None

    def __enter__(self):
        self.acquire(timeout=0)
        return self

    def __exit__(self, *_):
        self.release()


class SlowAdStore:
    """The ad-blocking settings lock is busy for the whole allowance of each wait."""

    def __init__(self, clock, routing):
        self.clock = clock
        self.routing = routing
        self.waits = []

    def pause(self, *, wait=3.0):
        self.waits.append(("pause", wait))
        self.clock.now += wait
        self.routing.clear()

    @contextlib.contextmanager
    def access_transition(self, *, wait=3.0):
        self.waits.append(("transition", wait))
        self.clock.now += wait
        self.routing.clear()
        try:
            yield
        finally:
            # Like AdStore: the end waits at least TRANSITION_END_WAIT_SECONDS (review O-c).
            wait = max(wait, adblocking_module.TRANSITION_END_WAIT_SECONDS)
            self.waits.append(("transition end", wait))
            self.clock.now += wait
            self.routing.clear()


class SlowGuard:
    """The Panic stop lock stays busy for each whole wait, so every save fails."""

    def __init__(self, clock):
        self.clock = clock
        self.waits = []

    def write(self, state, *, initialize_only=False, wait=None):
        wait = panic_guard_module.GUARD_LOCK_WAIT_SECONDS if wait is None else wait
        self.waits.append(wait)
        self.clock.now += wait
        raise OSError("the Panic stop lock stayed busy")


def slow_panic_service(tmp_path, clock, monkeypatch, *, time_out):
    commands = SlowCommands(clock, time_out=time_out)
    monkeypatch.setattr(executor_module.subprocess, "run", commands)
    topology = Topology("eth0", "wlan0")
    store = SlowAdStore(clock, AdRouting("wlan0", SubprocessExecutor(timeout_seconds=2)))
    # The same executors and timeouts as run_helper.
    manager = AccessManager(
        executor=SubprocessExecutor(timeout_seconds=20), topology=topology,
        directory=tmp_path / "access", runtime_path=tmp_path / "access-policy.json",
        hostapd_path=tmp_path / "hostapd", ad_blocking_store=store,
    )
    manager.lock = SlowLock(clock)
    monkeypatch.setattr(manager, "admission_supported", lambda: True)
    service = HelperService(
        NetworkApplier(executor=SubprocessExecutor(timeout_seconds=10), topology=topology),
        access_manager=manager, ad_blocking_store=store,
        gateway_state_path=tmp_path / "gateway.json",
    )
    service._network_lock = SlowLock(clock)
    service._panic_guard = SlowGuard(clock)
    return service, commands


@pytest.mark.parametrize("time_out", [False, True], ids=["slow-commands", "timed-out-commands"])
def test_stacked_waits_stay_inside_the_panic_budget(tmp_path, clock, monkeypatch, time_out):
    service, commands = slow_panic_service(tmp_path, clock, monkeypatch, time_out=time_out)
    started = clock.now
    answer = service.handle_payload({"action": "normal-access"})
    elapsed = clock.now - started

    assert elapsed <= helper_module.PANIC_ANSWER_SECONDS, (elapsed, commands.calls)
    # Recovery is not confirmed, and Panic says so.
    assert answer["ok"] is False
    assert service._panic_recovery_failed is True
    # Nothing that opens the network was skipped to save time.
    ran = [argv for argv, _, _ in commands.calls]
    if time_out:
        assert any(argv[:3] == ("tc", "-j", "qdisc") for argv in ran)  # the reset was tried
    else:
        assert ("nft", "delete", "table", "inet", "jittersim") in ran
    loaded = [text for argv, text, _ in commands.calls if argv == ("nft", "-f", "-")]
    assert any("table inet jittersim_access" in text and "ether saddr" not in text
               for text in loaded), "the open access firewall was not tried"
    assert any("delete table inet jittersim_access_transition" in text for text in loaded)


def test_without_contention_panic_keeps_the_usual_waits(tmp_path, clock, monkeypatch):
    # The deadline only shortens waits once time is short. At the start the network
    # lock, the ad pause and the release lock keep their usual allowances.
    service, _ = slow_panic_service(tmp_path, clock, monkeypatch, time_out=False)
    service.handle_payload({"action": "normal-access"})
    assert service._network_lock.waits[0] == helper_module.PANIC_NETWORK_LOCK_WAIT_SECONDS


def test_a_full_network_lock_wait_leaves_real_time_for_the_release(tmp_path, clock, monkeypatch):
    # Review O-a: the deadline starts before the network lock wait. A wait for the
    # lock's whole allowance must still leave the work after it more than the floors.
    service, _ = slow_panic_service(tmp_path, clock, monkeypatch, time_out=False)
    instant = SlowCommands(clock, time_out=False)
    instant.clock = type("Frozen", (), {"now": 0.0})()  # commands take no time here
    monkeypatch.setattr(executor_module.subprocess, "run", instant)
    service.handle_payload({"action": "normal-access"})
    assert service._network_lock.waits == [helper_module.PANIC_NETWORK_LOCK_WAIT_SECONDS]
    after_lock = helper_module.PANIC_DEADLINE_SECONDS - helper_module.PANIC_NETWORK_LOCK_WAIT_SECONDS
    assert after_lock >= helper_module.PANIC_POST_LOCK_SECONDS >= 6.0
    # The first Panic stop save may use its whole wait. The household release lock
    # still gets what is left of the post-lock time, not the 0.5 s floor.
    (release_wait,) = service._access_manager.lock.waits
    assert release_wait >= (helper_module.PANIC_POST_LOCK_SECONDS
                            - panic_guard_module.GUARD_LOCK_WAIT_SECONDS)
    assert release_wait > helper_module.PANIC_STEP_FLOOR_SECONDS


def test_the_client_never_starts_an_attempt_the_helper_cannot_finish(clock):
    attempts = []

    def request(self, payload, **kwargs):
        attempts.append((clock.now, self.timeout_seconds))
        clock.now += 0.2
        raise HelperClientError("busy", reason="helper-busy")

    client = HelperClient(socket_path=Path("/nonexistent/helper.sock"), timeout_seconds=5)
    started = clock.now
    with pytest.MonkeyPatch.context() as patch, pytest.raises(HelperClientError):
        patch.setattr(HelperClient, "_request", request)
        client.normal_access()
    assert len(attempts) > 1
    for _, timeout in attempts:
        assert timeout >= helper_module.PANIC_ANSWER_SECONDS + 1
    assert attempts[-1][0] - started + attempts[-1][1] <= helper_module.NORMAL_ACCESS_TIMEOUT_SECONDS


def emergency_handler(tmp_path, peer="192.168.50.20"):
    repository = ConfigRepository(tmp_path / "settings.db")
    server = ControlServer(("127.0.0.1", 0), repository, admin_token="admin-token")
    server.helper_client = HelperClient(socket_path=tmp_path / "helper.sock", timeout_seconds=8)
    handler = object.__new__(ControlRequestHandler)
    handler.server = server
    handler.client_address = (peer, 45000)
    handler.headers = Message()
    responses = []
    handler._send_response = lambda **values: responses.append(values)
    return server, repository, handler, responses


def test_the_dashboard_device_check_for_panic_has_one_bounded_budget(tmp_path, clock, monkeypatch):
    sent = []

    def send(self, payload, *, timeout_seconds=None):
        seconds = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        sent.append((payload.get("operation"), seconds))
        clock.now += seconds
        raise HelperClientError("could not contact the networking helper")

    monkeypatch.setattr(HelperClient, "_send", send)
    server, repository, handler, responses = emergency_handler(tmp_path)
    try:
        started = clock.now
        assert handler._check_device_access(emergency=True) is False
        elapsed = clock.now - started
    finally:
        server.server_close()
        repository.close()
    assert elapsed <= server_module.EMERGENCY_CHECK_BUDGET_SECONDS, sent
    assert sent[0][0] == "emergency-status"
    # No decision means 503 (the page retries Panic), never access.
    assert responses and responses[-1]["status"] == 503


def test_the_browser_waits_longer_than_the_dashboard_needs_for_panic():
    source = DASHBOARD_JS.read_text(encoding="utf-8")
    browser = int(re.search(r"const PANIC_REQUEST_TIMEOUT_MS = (\d+);", source).group(1)) / 1000
    assert re.search(r'request\(path, "POST", \{\}, \{timeoutMs: PANIC_REQUEST_TIMEOUT_MS\}\)', source)
    # The device check, then the helper client with its busy retries. The rest is
    # a few small database writes (docs/architecture.md, "Panic time budget").
    server_bound = (server_module.EMERGENCY_CHECK_BUDGET_SECONDS
                    + helper_module.NORMAL_ACCESS_TIMEOUT_SECONDS)
    assert server_bound + server_module.PANIC_DATABASE_ALLOWANCE_SECONDS <= browser
    assert (helper_module.PANIC_ANSWER_SECONDS + helper_module.NORMAL_ACCESS_BUSY_RETRY_SECONDS + 1
            <= helper_module.NORMAL_ACCESS_TIMEOUT_SECONDS)
    # Review S2: a refusal after the whole network lock wait (plus a second for the
    # request around it) still leaves room for one more attempt in the retry window.
    assert (helper_module.PANIC_NETWORK_LOCK_WAIT_SECONDS + 1
            + helper_module.NORMAL_ACCESS_BUSY_RETRY_INTERVAL
            <= helper_module.NORMAL_ACCESS_BUSY_RETRY_SECONDS)
    assert server_bound + server_module.PANIC_DATABASE_ALLOWANCE_SECONDS == 48


def test_a_full_lock_wait_refusal_is_retried_inside_the_client_budget(clock):
    attempts = []

    def request(self, payload, **kwargs):
        attempts.append((clock.now, self.timeout_seconds))
        if len(attempts) == 1:
            clock.now += helper_module.PANIC_NETWORK_LOCK_WAIT_SECONDS + 0.5
            raise HelperClientError("busy", reason="helper-busy")
        clock.now += helper_module.PANIC_ANSWER_SECONDS  # the retry uses its whole answer time
        return {"ok": True}

    client = HelperClient(socket_path=Path("/nonexistent/helper.sock"), timeout_seconds=5)
    started = clock.now
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(HelperClient, "_request", request)
        assert client.normal_access() == {"ok": True}
    assert len(attempts) == 2
    assert attempts[1][1] >= helper_module.PANIC_ANSWER_SECONDS + 1
    assert clock.now - started <= helper_module.NORMAL_ACCESS_TIMEOUT_SECONDS


def test_panic_bookkeeping_waits_fit_the_database_allowance():
    # N3: each repository step of Panic waits a bounded time for a backup to end,
    # and the steps together stay inside the allowance that the browser timeout has.
    from jittersim.api import access as access_module

    worst = (access_module.PANIC_REPOSITORY_WAIT_SECONDS * access_module.PANIC_REPOSITORY_STEPS
             + access_module.PANIC_RESULT_WAIT_SECONDS)
    assert worst <= server_module.PANIC_DATABASE_ALLOWANCE_SECONDS
    assert server_module.EMERGENCY_REPOSITORY_WAIT_SECONDS < server_module.EMERGENCY_CHECK_BUDGET_SECONDS


def test_a_held_repository_lock_is_waited_for_only_boundedly(tmp_path):
    from jittersim.config.storage import RepositoryBusy

    repository = ConfigRepository(tmp_path / "config.db")
    held, stop = threading.Event(), threading.Event()

    def backup():
        with repository._lock:
            held.set()
            stop.wait(10)

    thread = threading.Thread(target=backup)
    thread.start()
    try:
        assert held.wait(5)
        started = time.monotonic()
        with pytest.raises(RepositoryBusy):
            with repository.locked_within(0.2):
                pass
        assert time.monotonic() - started < 2
    finally:
        stop.set()
        thread.join(5)
    with repository.locked_within(0.2):
        assert repository.get_runtime_state() is not None
    repository.close()
