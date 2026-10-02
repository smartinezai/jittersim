"""Check the helper client that the browser journeys use."""

import pytest

from jittersim.networking.helper import HelperClient, HelperClientError
from tests.browser import fixture


def script_requests(monkeypatch, answers):
    """Make the base client answer from a list. An exception in the list is raised."""

    calls = []

    def request(self, payload, *, timeout_seconds=None):
        calls.append(payload["action"])
        answer = answers[min(len(calls), len(answers)) - 1]
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(HelperClient, "_request", request)
    monkeypatch.setattr(fixture, "BUSY_WAIT_SECONDS", 0)
    return calls


def client():
    return fixture.PatientHelperClient(socket_path="unused.sock")


def busy():
    return HelperClientError("busy", reason="helper-busy")


def test_a_busy_helper_is_asked_again_until_it_answers(monkeypatch):
    # The page polls the helper while a journey presses Apply. Without the wait the Apply
    # lost that race now and then on a slow machine and left the network in an error state.
    calls = script_requests(monkeypatch, [busy(), busy(), busy(), {"ok": True}])

    assert client()._request({"action": "apply"}) == {"ok": True}
    assert calls == ["apply"] * 4


def test_any_other_failure_is_not_hidden(monkeypatch):
    calls = script_requests(monkeypatch, [HelperClientError("apply failed"), {"ok": True}])

    with pytest.raises(HelperClientError, match="apply failed"):
        client()._request({"action": "apply"})
    assert calls == ["apply"]


def test_a_helper_that_stays_busy_still_fails_in_the_end(monkeypatch):
    calls = script_requests(monkeypatch, [busy()])

    with pytest.raises(HelperClientError) as error:
        client()._request({"action": "apply"})
    assert error.value.reason == "helper-busy"
    assert len(calls) == fixture.BUSY_ATTEMPTS
