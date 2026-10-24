"""Keep DNS work bounded across calls that have already timed out."""

import threading
from concurrent.futures import Future
from queue import Queue
from typing import Callable, Optional, Set

MAX_UNFINISHED_LOOKUPS = 32


class BoundedLookupPool:
    """Share process-lifetime daemon workers with bounded unfinished work.

    DNS cannot reliably be interrupted in a thread. A stuck lookup keeps its
    slot, but must not hold interpreter exit after the caller's deadline. Using
    ThreadPoolExecutor here would join even daemon workers at interpreter exit.
    Only best-effort lookups belong in this pool, never shutdown cleanup.
    """

    def __init__(self, *, workers: int, name: str) -> None:
        if workers <= 0:
            raise ValueError("workers must be greater than 0")
        self._workers = workers
        self._name = name
        self._threads = []
        self._jobs = Queue(maxsize=MAX_UNFINISHED_LOOKUPS)
        # A condition, so a caller can wait for a free place (wait_for_room).
        self._lock = threading.Condition()
        self._unfinished: Set[Future] = set()

    def submit(
        self, function: Callable, *arguments, unfinished_limit: int = MAX_UNFINISHED_LOOKUPS
    ) -> Optional[Future]:
        """Admit work below both the caller's limit and the pool's fixed capacity."""
        if unfinished_limit <= 0:
            raise ValueError("unfinished_limit must be greater than 0")
        with self._lock:
            if len(self._unfinished) >= min(unfinished_limit, MAX_UNFINISHED_LOOKUPS):
                return None
            if len(self._threads) < self._workers:
                thread = threading.Thread(
                    target=self._work, name=f"{self._name}-{len(self._threads)}", daemon=True
                )
                # Reserve before start: an interrupted start may still create a
                # physical worker, so its slot must not be used twice.
                self._threads.append(thread)
                thread.start()
            result = Future()
            self._unfinished.add(result)
            self._jobs.put_nowait((result, function, arguments))
        return result

    def _work(self) -> None:
        while True:
            result, function, arguments = self._jobs.get()
            try:
                self._run(result, function, arguments)
            finally:
                self._finished(result)
                self._jobs.task_done()
                # Idle workers must not retain a completed lookup's arguments.
                del result, function, arguments

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
