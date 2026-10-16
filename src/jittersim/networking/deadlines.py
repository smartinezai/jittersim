"""One time budget shared by every step of a request (review G4).

Panic passes its deadline explicitly to each lock wait. The commands and helper
socket calls underneath read it from a context variable, so one deadline bounds
them too without every caller passing it on. Each thread starts without a deadline.
See docs/architecture.md, "Panic time budget".
"""

import contextvars
import time
from contextlib import contextmanager
from typing import Iterator, Optional

_DEADLINE: contextvars.ContextVar = contextvars.ContextVar("jittersim_deadline", default=None)
_FLOOR: contextvars.ContextVar = contextvars.ContextVar("jittersim_deadline_floor", default=0.0)


@contextmanager
def deadline_scope(deadline: float, *, floor: float = 0.0) -> Iterator[float]:
    """Bound the commands and helper calls of this thread by `deadline` (monotonic).

    A command that must still run after the deadline gets `floor` seconds. An
    enclosing scope with an earlier deadline keeps it.
    """

    outer = _DEADLINE.get()
    if outer is not None:
        deadline = min(deadline, outer)
    token, floor_token = _DEADLINE.set(deadline), _FLOOR.set(floor)
    try:
        yield deadline
    finally:
        _FLOOR.reset(floor_token)
        _DEADLINE.reset(token)


def scope_remaining() -> Optional[float]:
    """Seconds left in the current scope (can be negative), None without one."""

    deadline = _DEADLINE.get()
    return None if deadline is None else deadline - time.monotonic()


def bounded_wait(cap: float, deadline: Optional[float], *, reserve: float = 0.0,
                 floor: Optional[float] = None) -> float:
    """At most `cap` seconds, and no more than is left of `deadline` minus `reserve`.

    `floor` (by default the scope's) is what a step that must still be tried gets
    once the deadline has passed. Without a deadline the cap applies unchanged.
    """

    if deadline is None:
        return cap
    if floor is None:
        floor = _FLOOR.get()
    return max(0.0, min(cap, max(floor, deadline - time.monotonic() - reserve)))


def command_timeout(seconds: float) -> float:
    """A command's timeout, shortened to what is left of the current scope."""

    return bounded_wait(seconds, _DEADLINE.get())
