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
