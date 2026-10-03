import threading
import time
from concurrent.futures import wait

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
        pool._executor.shutdown(wait=True)
    assert calls == []


def test_completed_and_failed_work_release_capacity():
    pool = BoundedLookupPool(workers=1, name="bounded-pool-result-test")

    def fails():
        raise OSError("lookup failed")

    try:
        assert pool.submit(lambda: "answer").result(timeout=2) == "answer"
        error = pool.submit(fails)
        assert isinstance(error.exception(timeout=2), OSError)
        assert pool.submit(lambda: "next").result(timeout=2) == "next"
    finally:
        pool._executor.shutdown(wait=True)
