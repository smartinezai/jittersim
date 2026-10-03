"""Retain request ownership until the actual worker has stopped."""

import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional


class WorkersStillRunning(RuntimeError):
    """Shared state must remain owned when shutdown cannot finish."""


@dataclass(eq=False)
class _Owner:
    abandon: Callable
    release: Callable
    thread: Optional[threading.Thread] = None
    entered: bool = False
    completed: bool = False
    cancelled: bool = False


class RequestWorkers:
    """Track physical workers, including an interrupted startup."""

    def __init__(self, name: str, uncertain: Callable = lambda: None) -> None:
        self._name = name
        self._uncertain = uncertain
        self._condition = threading.Condition()
        self._owners = set()
        self._closing = False

    def reap(self) -> None:
        """Return capacity only after a completed thread has actually exited."""

        with self._condition:
            stopped = [owner for owner in self._owners
                       if owner.completed and (owner.thread is None or not owner.thread.is_alive())]
            for owner in stopped:
                self._owners.remove(owner)
        for owner in stopped:
            owner.release()

    def start(self, work: Callable, *, abandon: Callable, release: Callable) -> None:
        owner = _Owner(abandon, release)
        with self._condition:
            if self._closing:
                owner.cancelled = True
            self._owners.add(owner)
        if owner.cancelled:
            self._never_started(owner)
            return
        try:
            owner.thread = threading.Thread(target=self._run, args=(owner, work),
                                            name=self._name, daemon=True)
        except BaseException:
            # No start was attempted, so no native worker can own this request.
            self._never_started(owner)
            raise
        try:
            owner.thread.start()
        except BaseException:
            with self._condition:
                uncertain = not owner.entered and owner.thread.ident is None and not owner.completed
                if uncertain:
                    # The exception does not prove whether a native thread was created.
                    # Retain its socket and capacity until the child finishes or the
                    # process stops. Never infer ownership from exception text.
                    owner.cancelled = True
                    self._closing = True
            if uncertain:
                self._uncertain()
            self.reap()
            raise

    def _never_started(self, owner: _Owner) -> None:
        try:
            owner.abandon()
        finally:
            with self._condition:
                owner.completed = True
                self._condition.notify_all()
            self.reap()

    def _run(self, owner: _Owner, work: Callable) -> None:
        with self._condition:
            owner.entered = True
            cancelled = owner.cancelled or self._closing
        try:
            if cancelled:
                owner.abandon()
            else:
                work()
        finally:
            with self._condition:
                owner.completed = True
                self._condition.notify_all()

    def stop_admission(self) -> None:
        with self._condition:
            self._closing = True

    def close(self, timeout: float = 5.0) -> None:
        self.stop_admission()
        deadline = time.monotonic() + timeout
        while True:
            self.reap()
            with self._condition:
                if not self._owners:
                    return
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise WorkersStillRunning("Request workers did not stop. Shared state remains owned.")
                self._condition.wait(min(remaining, 0.05))
