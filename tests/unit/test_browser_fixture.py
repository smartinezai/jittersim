"""Check the helper client that the browser journeys use."""

import json

import pytest

from jittersim.networking.commands import Command
from jittersim.networking.helper import HelperClient, HelperClientError
from tests.browser import fixture
from tests.unit.test_helper_snapshot_cache import CLIENT
from tests.unit.test_snapshot_status_poll import overlap_a_scan_with_a_status_poll


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


def test_a_scan_that_overlaps_a_status_poll_is_asked_again_not_failed(monkeypatch):
    # The page reads the clients and the update status in the same breath. The status read
    # invalidates the running scan, the scan reports "network changed" and the dashboard
    # answered 502. A person reloads. tests/qa/test_browser_explore.py failed 1 run in 10 on that.
    monkeypatch.setattr(fixture, "BUSY_WAIT_SECONDS", 0)

    outcome, scans = overlap_a_scan_with_a_status_poll(
        client(), monkeypatch, lambda helper_client: helper_client.online_status())

    assert not isinstance(outcome, Exception), outcome
    assert outcome["devices"][0]["mac_address"] == CLIENT["mac_address"]
    assert scans == 2


def test_a_scan_that_keeps_losing_the_race_still_fails_in_the_end(monkeypatch):
    calls = []

    def always_changed(self):
        calls.append("scan")
        raise HelperClientError("network changed during client scan", reason="helper-busy")

    monkeypatch.setattr(HelperClient, "connected_device_snapshot", always_changed)
    monkeypatch.setattr(fixture, "BUSY_WAIT_SECONDS", 0)

    with pytest.raises(HelperClientError) as error:
        client().connected_device_snapshot()
    assert error.value.reason == "helper-busy"
    assert len(calls) == fixture.BUSY_ATTEMPTS


def test_a_scan_that_fails_for_another_reason_is_not_asked_again(monkeypatch):
    calls = []

    def broken(self):
        calls.append("scan")
        raise HelperClientError("client scan timed out")

    monkeypatch.setattr(HelperClient, "connected_device_snapshot", broken)
    monkeypatch.setattr(fixture, "BUSY_WAIT_SECONDS", 0)

    with pytest.raises(HelperClientError, match="timed out"):
        client().connected_device_snapshot()
    assert calls == ["scan"]


class FakeClock:
    def __init__(self):
        self.now = 1_000.0

    def __call__(self):
        return self.now


def read_queue(executor, interface="wlan0"):
    command = Command("tc", ("-s", "-j", "qdisc", "show", "dev", interface), "read queue counters")
    queues = json.loads(executor.run(command).stdout)
    return next(queue for queue in queues if queue["kind"] == "netem")


@pytest.mark.parametrize("gap_seconds", [4.0, 5.0, 5.5, 8.0])
def test_queue_bytes_follow_the_clock_so_the_rate_ignores_the_poll_gap(gap_seconds):
    # The impact card divides the byte change by the time between two samples and calls the
    # cap reached from 90% of it. A fixed amount per read gave 240 kbit/s for a 5 second gap
    # but only 218 kbit/s for 5.5 seconds, so the journey failed on a slow CI machine.
    clock = FakeClock()
    executor = fixture.DashboardWifiExecutor(clock=clock)
    first = read_queue(executor)
    clock.now += gap_seconds
    second = read_queue(executor)

    kbit = (second["bytes"] - first["bytes"]) * 8 / 1000 / gap_seconds
    assert kbit == pytest.approx(fixture.QUEUE_KBIT_PER_SECOND)


def test_each_read_still_adds_the_same_packets_and_drops_whatever_the_gap():
    # The 10% loss line (11 dropped of 111) must not depend on timing either.
    clock = FakeClock()
    executor = fixture.DashboardWifiExecutor(clock=clock)
    first = read_queue(executor)
    clock.now += 7.3
    second = read_queue(executor)
    clock.now += 0.4
    third = read_queue(executor)

    assert second["packets"] - first["packets"] == third["packets"] - second["packets"] == 100
    assert second["drops"] - first["drops"] == third["drops"] - second["drops"] == 11


def test_a_new_root_queue_starts_the_counters_again():
    clock = FakeClock()
    executor = fixture.DashboardWifiExecutor(clock=clock)
    read_queue(executor)
    clock.now += 6
    assert read_queue(executor)["bytes"] > 0

    executor.run(Command("tc", ("qdisc", "del", "dev", "wlan0", "root"), "remove the root queue"))
    clock.now += 6
    again = read_queue(executor)
    assert (again["bytes"], again["packets"], again["drops"]) == (0, 100, 11)
