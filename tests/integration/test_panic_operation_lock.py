"""Panic does not wait behind a long helper read (gap audit 2026-10-09, finding 1)."""

import tempfile
import threading
import time
from pathlib import Path

from jittersim.networking.applier import NetworkApplier
from tests.integration.test_capacity_stalled_clients import running_experiment, served_helper


def test_normal_access_succeeds_while_a_dns_sample_holds_the_operation_lock(monkeypatch):
    capturing, release = threading.Event(), threading.Event()

    def slow_capture(self, excluded_macs=()):
        # A quiet network: tcpdump runs into its five second limit.
        capturing.set()
        release.wait(5)
        return ""

    monkeypatch.setattr(NetworkApplier, "capture_dns_queries", slow_capture)
    with tempfile.TemporaryDirectory(prefix="js-oplock-", dir="/tmp") as temporary:
        directory = Path(temporary).resolve()
        with served_helper(directory) as (service, client, server):
            running_experiment(service)
            sample = threading.Thread(target=lambda: client._request(
                {"action": "observe-dns", "excluded_macs": []}, timeout_seconds=10))
            sample.start()
            try:
                assert capturing.wait(5)
                assert server.operation_lock.locked()
                started = time.monotonic()
                result = client.normal_access()
                elapsed = time.monotonic() - started
                assert result["ok"] is True, result
                assert service._panic_stop_latched is True
                assert service._active_plan is None
                # Panic did not wait for the sample.
                assert elapsed < 3.0
                assert not release.is_set()
            finally:
                release.set()
                sample.join(10)
