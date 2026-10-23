"""One slow read of the helper, shared by every request that asks within a short time."""

import threading
import time
from typing import Any, Callable, Optional


class _Flight:
    """A read in progress. Callers that arrive meanwhile wait for it."""

    def __init__(self, era: object) -> None:
        self.era = era
        self.done = threading.Event()
        self.value: Any = None
        self.error: Optional[BaseException] = None


class SharedReading:
    """Keep the last result of a slow read so that open dashboard tabs do not each repeat it.

    The first caller reads. A caller that arrives while it reads waits and takes the same
    result, or the same error. A caller that arrives within `max_age` seconds after a good
    read takes that result. An error is never kept.

    `drop` makes every kept result and every read in progress too old to use. It takes no
    lock and never waits, so Panic and Reset can call it at once.
    """

    def __init__(self) -> None:
        # Held for a moment to look at or change the fields below, never while reading.
        self._lock = threading.Lock()
        self._era = object()
        self._kept: Optional[tuple] = None  # (era, when the read began, value)
        self._flight: Optional[_Flight] = None

    def drop(self) -> None:
        self._era = object()

    def get(self, read: Callable[[], Any], max_age: float) -> Any:
        with self._lock:
            era = self._era
            kept = self._kept
            if kept is not None and kept[0] is era and time.monotonic() - kept[1] < max_age:
                return kept[2]
            flight = self._flight
            leader = flight is None or flight.era is not era
            if leader:
                flight = self._flight = _Flight(era)
        if not leader:
            flight.done.wait()
        else:
            began = time.monotonic()
            try:
                flight.value = read()
            except BaseException as error:
                flight.error = error
            finally:
                with self._lock:
                    if flight.error is None and era is self._era:
                        self._kept = (era, began, flight.value)
                    if self._flight is flight:
                        self._flight = None
                flight.done.set()
        if flight.error is not None:
            raise flight.error
        return flight.value
