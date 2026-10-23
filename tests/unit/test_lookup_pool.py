import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from concurrent.futures import wait

import pytest

from jittersim.engine.lookup_pool import BoundedLookupPool, MAX_UNFINISHED_LOOKUPS


def test_cancelled_queue_items_keep_capacity_until_consumed():
    pool = BoundedLookupPool(workers=2, name="bounded-pool-test")
    release = threading.Event()
    started = threading.Barrier(3)
    calls = []

    def blocked():
        started.wait(timeout=5)
        release.wait(timeout=5)

    running = [pool.submit(blocked) for _ in range(2)]
    queued = []
    try:
        started.wait(timeout=5)
        queued = [pool.submit(lambda: calls.append("queued"))
                  for _ in range(MAX_UNFINISHED_LOOKUPS - 2)]
        assert all(future is not None for future in queued)
        assert pool.submit(lambda: None) is None
        assert all(future.cancel() for future in queued)
        for _ in range(100):
            assert pool.submit(lambda: calls.append("unexpected")) is None
    finally:
        release.set()
        wait(running + queued, timeout=5)
        deadline = time.monotonic() + 5
        while pool._unfinished and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not pool._unfinished
    assert calls == []
    assert pool.submit(lambda: "recovered").result(timeout=2) == "recovered"


def test_completed_and_failed_work_release_capacity():
    pool = BoundedLookupPool(workers=1, name="bounded-pool-result-test")

    def fails():
        raise OSError("lookup failed")

    assert pool.submit(lambda: "answer").result(timeout=2) == "answer"
    error = pool.submit(fails)
    assert isinstance(error.exception(timeout=2), OSError)
    assert pool.submit(lambda: "next").result(timeout=2) == "next"


@pytest.mark.parametrize("layer", ["pool", "website", "service", "nested-service"])
def test_stuck_lookup_does_not_delay_interpreter_exit(layer):
    # A thread assertion cannot catch concurrent.futures' interpreter-exit join.
    # Every fake resolver blocks forever, without DNS or other network access.
    program = r"""
import sys
import threading
from unittest.mock import patch
from jittersim.engine import service_resolver, website
from jittersim.engine.lookup_pool import BoundedLookupPool

started = threading.Event()
never_release = threading.Event()

def stuck(*args):
    started.set()
    never_release.wait()

layer = sys.argv[1]
if layer == "pool":
    pool = BoundedLookupPool(workers=1, name="stuck-dns")
    future = pool.submit(stuck)
    assert started.wait(2)
    assert not future.cancel()  # Running work still owns its slot.
elif layer == "website":
    with patch.object(website, "RESOLVE_WAIT_SECONDS", 0.1), \
            patch.object(website.socket, "getaddrinfo", stuck):
        with website.lookup_session() as session:
            assert website.resolve_domains(domains=("stuck.invalid",)) == ()
        assert started.is_set()
        assert session.timed_out == ["stuck.invalid"]
else:
    class Helper:
        def service_addresses(self, answers):
            raise AssertionError("No answer should be sent after shutdown")

    kwargs = {"resolver": stuck} if layer == "service" else {}
    refresher = service_resolver.ServiceAddressRefresher(
        applied_plan=lambda: {"service_list_rules": [
            {"list_id": "stuck", "domains": ["stuck.invalid"]}
        ]},
        helper_client=Helper(), poll_seconds=0.001, **kwargs,
    )
    with patch.object(service_resolver, "RESOLVE_WAIT_SECONDS", 0.1), \
            patch.object(website, "RESOLVE_WAIT_SECONDS", 0.1), \
            patch.object(website.socket, "getaddrinfo", stuck):
        refresher.start()
        assert started.wait(2)
        refresher.stop(timeout=2)
        assert not refresher.running
print("logical shutdown complete", flush=True)
"""
    try:
        child = subprocess.run(
            [sys.executable, "-c", program, layer],
            capture_output=True, text=True, timeout=5,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")},
        )
    except subprocess.TimeoutExpired as error:
        pytest.fail(f"{layer} blocked interpreter exit after: {error.stdout!r}")
    assert child.returncode == 0, child.stderr
    assert child.stdout.strip() == "logical shutdown complete"
