"""Release operations never hold the lock that network operations wait for (review U1 of PR 110).

An online update check starts `systemctl start --no-block jittersim-update-fetch.service`.
It ran while the helper held the request operation lock, which Apply and Reset wait
for only three seconds (and Panic waited for before finding 1 was fixed), so a slow
systemd start made them answer helper-busy.
"""

import tempfile
import threading
import time
from pathlib import Path

import pytest

from jittersim.networking import helper as helper_module
from tests.integration.test_capacity_stalled_clients import running_experiment, served_helper


@pytest.mark.parametrize("action", ["update-online-check", "update-status", "update-install"])
def test_a_slow_release_operation_blocks_neither_reset_nor_panic(monkeypatch, action):
    entered, release = threading.Event(), threading.Event()

    def slow_systemd(*args, **kwargs):
        # systemd takes longer than any network write waits.
        entered.set()
        release.wait(10)
        return {}

    monkeypatch.setattr(helper_module, "request_online_check", slow_systemd)
    monkeypatch.setattr(helper_module, "update_status", slow_systemd)
    monkeypatch.setattr(helper_module, "request_update", lambda *args, **kwargs: None)
    monkeypatch.setattr(helper_module, "online_overview", lambda job=None: {})
    with tempfile.TemporaryDirectory(prefix="js-u1-", dir="/tmp") as temporary:
        directory = Path(temporary).resolve()
        with served_helper(directory) as (service, client, server):
            running_experiment(service)
            worker = threading.Thread(target=lambda: client._request(
                {"action": action, "force": True, "version": "1.0.0"}, timeout_seconds=15), daemon=True)
            worker.start()
            try:
                assert entered.wait(5)
                assert not server.operation_lock.locked()
                started = time.monotonic()
                assert client.reset()["ok"] is True
                assert client.normal_access()["ok"] is True
                assert time.monotonic() - started < 3.0
                assert service._panic_stop_latched is True
            finally:
                release.set()
                worker.join(15)
