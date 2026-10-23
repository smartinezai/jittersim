"""Keep DNS work bounded across calls that have already timed out."""

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable, Optional, Set

MAX_UNFINISHED_LOOKUPS = 32


class BoundedLookupPool:
    """Share fixed workers and refuse work when the bounded queue is full."""

    def __init__(self, *, workers: int, name: str) -> None:
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix=name)
        # A condition, so a caller can wait for a free place (wait_for_room).
        self._lock = threading.Condition()
        self._unfinished: Set[Future] = set()

    def submit(self, function: Callable, *arguments) -> Optional[Future]:
        with self._lock:
            if len(self._unfinished) >= MAX_UNFINISHED_LOOKUPS:
                return None
            result = Future()
            future = self._executor.submit(self._run, result, function, arguments)
            self._unfinished.add(future)
        # A finished future invokes its callback immediately.
        future.add_done_callback(self._finished)
        return result

    @staticmethod
    def _run(result: Future, function: Callable, arguments: tuple) -> None:
        # Cancelled queue items still reserve capacity until a worker consumes them.
        if not result.set_running_or_notify_cancel():
            return
        try:
            value = function(*arguments)
        except BaseException as error:
            result.set_exception(error)
        else:
            result.set_result(value)

    def _finished(self, future: Future) -> None:
        with self._lock:
            self._unfinished.discard(future)
            self._lock.notify_all()

    def unfinished(self) -> int:
        with self._lock:
            return len(self._unfinished)

    def wait_for_room(self, timeout: float) -> bool:
        """Wait up to `timeout` seconds until submit can take more work."""
        with self._lock:
            return self._lock.wait_for(
                lambda: len(self._unfinished) < MAX_UNFINISHED_LOOKUPS, timeout
            )
