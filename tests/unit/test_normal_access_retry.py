"""Normal access retries a busy helper for a bounded time (gap audit 2026-10-09, finding 1).

`jittersim normal-access` is the recovery the installer recommends. It used to fail
on the first helper-busy answer.
"""

import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest

from jittersim.cli import main
from jittersim.networking import helper as helper_module
from jittersim.networking.helper import HelperClient, HelperClientError


def busy():
    return HelperClientError("The networking helper is busy. Try again later.", reason="helper-busy")


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    fake = Clock()
    monkeypatch.setattr(helper_module.time, "monotonic", fake.monotonic)
    monkeypatch.setattr(helper_module.time, "sleep", fake.sleep)
    return fake


def client():
    return HelperClient(socket_path=Path("/nonexistent/helper.sock"), timeout_seconds=5)


def test_a_busy_helper_is_retried_until_panic_gets_through(clock):
    answers = [busy(), busy(), {"ok": True, "action": "normal-access"}]
    calls = []

    def request(self, payload, **kwargs):
        calls.append((payload, self.timeout_seconds))
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    with patch.object(HelperClient, "_request", request):
        assert client().normal_access()["ok"] is True
    assert [payload for payload, _ in calls] == [{"action": "normal-access"}] * 3
    assert all(0 < timeout <= 60 for _, timeout in calls)
    assert clock.sleeps and all(0 < seconds <= 1 for seconds in clock.sleeps)


def test_the_retry_is_bounded(clock):
    calls = []

    def request(self, payload, **kwargs):
        calls.append(clock.now)
        raise busy()

    with patch.object(HelperClient, "_request", request), pytest.raises(HelperClientError) as error:
        client().normal_access()
    assert error.value.reason == "helper-busy"
    assert len(calls) > 1
    assert calls[-1] - calls[0] <= helper_module.NORMAL_ACCESS_BUSY_RETRY_SECONDS


def test_other_errors_are_not_retried(clock):
    calls = []

    def request(self, payload, **kwargs):
        calls.append(payload)
        raise HelperClientError("normal access was not fully restored")

    with patch.object(HelperClient, "_request", request), pytest.raises(HelperClientError):
        client().normal_access()
    assert len(calls) == 1


def test_the_recovery_command_rides_out_a_busy_helper(clock, tmp_path):
    answers = [busy(), {"ok": True, "action": "normal-access", "experiments_stopped": True,
                        "access_disabled": True}]

    def request(self, payload, **kwargs):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    output, errors = io.StringIO(), io.StringIO()
    with patch.object(HelperClient, "_request", request), redirect_stdout(output), redirect_stderr(errors):
        code = main(["normal-access", "--helper-socket", str(tmp_path / "helper.sock"),
                     "--database", str(tmp_path / "missing.db")])
    assert code == 0, errors.getvalue()
    assert answers == []


def test_the_dashboard_panic_failure_names_what_the_helper_could_not_do(tmp_path):
    # Review O-b: the dashboard used to drop the helper's reason for a failed Panic.
    import json

    from jittersim.api.server import ControlServer
    from jittersim.config.storage import ConfigRepository
    from tests.unit.test_api import PlanRecordingHelper

    reason = ("normal access was not fully restored. Household rules are off, but the "
              "Wi-Fi join limits could not be cleared. Retry Panic.")

    class IncompleteRelease(PlanRecordingHelper):
        def normal_access(self):
            raise HelperClientError(reason)

    with ConfigRepository(tmp_path / "settings.db") as repository:
        server = ControlServer(("127.0.0.1", 0), repository, helper_client=IncompleteRelease())
        try:
            status, _, body = server.write_response_for("/api/normal-access", {})
        finally:
            server.server_close()
        error = json.loads(body)["error"]
        assert status == 503
        assert error.startswith("Normal access was not fully restored. Automation is paused. "
                                "Retry the panic button.")
        assert "the Wi-Fi join limits could not be cleared" in error
        assert repository.get_runtime_state().last_error == error


def test_a_refusal_after_the_full_network_lock_wait_is_retried(clock):
    # Review S2: the helper answers helper-busy only after waiting the whole
    # PANIC_NETWORK_LOCK_WAIT_SECONDS. An access change or a DNS restart can hold the
    # network lock for about 8 s, so the attempt after that refusal must still be made.
    answers = [busy(), {"ok": True, "action": "normal-access"}]
    calls = []

    def request(self, payload, **kwargs):
        calls.append((clock.now, self.timeout_seconds))
        clock.now += helper_module.PANIC_NETWORK_LOCK_WAIT_SECONDS + 0.5  # the wait and some overhead
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    with patch.object(HelperClient, "_request", request):
        assert client().normal_access()["ok"] is True
    assert answers == [] and len(calls) == 2
    # The retry still leaves the helper its whole answer time.
    assert calls[1][1] >= helper_module.PANIC_ANSWER_SECONDS


def test_full_lock_wait_refusals_are_retried_a_bounded_number_of_times(clock):
    calls = []

    def request(self, payload, **kwargs):
        calls.append((clock.now, self.timeout_seconds))
        clock.now += helper_module.PANIC_NETWORK_LOCK_WAIT_SECONDS
        raise busy()

    started = clock.now
    with patch.object(HelperClient, "_request", request), pytest.raises(HelperClientError) as error:
        client().normal_access()
    assert error.value.reason == "helper-busy"
    assert 2 <= len(calls) <= 3
    assert calls[-1][0] - started <= helper_module.NORMAL_ACCESS_BUSY_RETRY_SECONDS
    assert clock.now - started <= helper_module.NORMAL_ACCESS_TIMEOUT_SECONDS
