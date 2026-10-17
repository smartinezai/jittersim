"""A status poll must not fail a client scan that is running (found by tests/qa/test_browser_explore.py).

The page reads the clients and the update status in the same breath. HelperClient treats every
action that is not in READ_ONLY_ACTIONS as a possible network change and invalidates the running
scan, so the scan reports "network changed during client scan" (helper-busy) and the dashboard
answers 502 to GET /api/connected-devices. With GET /api/updates/check-online polled in a tight
loop beside it, the QA dashboard answered 502 to 2447 of 7796 reads of the clients and to none of
15417 without that poll.

READ_ONLY_ACTIONS lives in src/jittersim/networking/helper.py, one of the three files the updater
only accepts as an approved bundle. The fix is two names in that set and goes in with the next
Panic batch. Until then the browser fixture's PatientHelperClient asks again (see
tests/unit/test_browser_fixture.py) and this module keeps the cause as an executable record.
"""

import threading
from pathlib import Path

import pytest

from jittersim.networking.helper import HelperClient, HelperClientError
from tests.unit.test_helper_snapshot_cache import CLIENT


def overlap_a_scan_with_a_status_poll(helper_client, monkeypatch, poll):
    """Start a client scan, run POLL while it is in flight, then let the scan finish.

    A status poll is not a network change, but the client cannot tell: every action that is
    not on its read-only list invalidates the scan that is running. Returns the scan's result
    or its error, and the number of scans the helper was asked for.
    """

    entered, release = threading.Event(), threading.Event()
    scans = []

    def send(payload, timeout_seconds=None):
        if payload["action"] != "clients":
            return {"ok": True}
        scans.append(payload)
        if len(scans) == 1:
            entered.set()
            assert release.wait(5)
        return {"ok": True, "association_status_available": True, "devices": [dict(CLIENT)]}

    monkeypatch.setattr(helper_client, "_send", send)
    outcome = []

    def scan():
        try:
            outcome.append(helper_client.connected_device_snapshot())
        except HelperClientError as error:
            outcome.append(error)

    reader = threading.Thread(target=scan, daemon=True)
    reader.start()
    assert entered.wait(5)
    poll(helper_client)
    release.set()
    reader.join(5)
    assert not reader.is_alive()
    return outcome[0], len(scans)


def new_client():
    return HelperClient(socket_path=Path("/tmp/not-used.sock"))


STILL_FAILS = (
    "update-online-status and update-online-settings are missing from READ_ONLY_ACTIONS in "
    "networking/helper.py (Panic-gated, hash-pinned). Add both there, add a bundle to "
    "PANIC_APPROVED_SOURCES, then remove this marker and PatientHelperClient.connected_device_snapshot."
)


@pytest.mark.xfail(strict=True, reason=STILL_FAILS)
@pytest.mark.parametrize("poll", [
    pytest.param(lambda client: client.online_status(), id="online-status"),
    pytest.param(lambda client: client.online_settings(), id="online-settings-read"),
])
def test_a_status_poll_does_not_fail_a_running_scan(monkeypatch, poll):
    outcome, scans = overlap_a_scan_with_a_status_poll(new_client(), monkeypatch, poll)

    assert not isinstance(outcome, Exception), outcome
    assert scans == 1


def test_a_network_change_still_fails_a_running_scan(monkeypatch):
    # The guard exists for this case. Whoever adds the status reads to READ_ONLY_ACTIONS must
    # not let a real change through with them.
    outcome, _ = overlap_a_scan_with_a_status_poll(
        new_client(), monkeypatch, lambda client: client._request({"action": "apply"}))

    assert isinstance(outcome, HelperClientError)
    assert outcome.reason == "helper-busy"
