"""A weekly window in which the router's Wi-Fi is off and the Pi's test Wi-Fi takes its place."""

import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Optional

MINUTES_PER_DAY = 24 * 60
MINUTES_PER_WEEK = 7 * MINUTES_PER_DAY
WINDOW_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")

# The steps of a window. A window that is not running is idle.
IDLE = "idle"
COPYING = "copying"
COPIED = "copied"
ACTIVE = "active"
RESTORING = "restoring"
PHASES = (IDLE, COPYING, COPIED, ACTIVE, RESTORING)


@dataclass(frozen=True)
class ForcedWindow:
    """From a weekday and a minute to a weekday and a minute. Monday is 0.

    The end may come earlier in the week than the start. Then the window runs over Sunday into
    Monday. An end minute of 1440 is the end of that day so Friday to Sunday is Friday 0 to
    Sunday 1440.
    """

    id: str
    start_day: int
    start_minute: int
    end_day: int
    end_minute: int
    enabled: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or WINDOW_ID.fullmatch(self.id) is None:
            raise ValueError("window id must use lowercase letters and digits and hyphens and underscores")
        for day in (self.start_day, self.end_day):
            if type(day) is not int or not 0 <= day <= 6:
                raise ValueError("window days run from 0 (Monday) to 6 (Sunday)")
        if type(self.start_minute) is not int or not 0 <= self.start_minute < MINUTES_PER_DAY:
            raise ValueError("window start minute must be from 0 to 1439")
        if type(self.end_minute) is not int or not 1 <= self.end_minute <= MINUTES_PER_DAY:
            raise ValueError("window end minute must be from 1 to 1440")
        if type(self.enabled) is not bool:
            raise ValueError("window enabled must be true or false")
        if self._start() == self._end() % MINUTES_PER_WEEK:
            raise ValueError("a window needs a start that differs from its end")

    def _start(self) -> int:
        return self.start_day * MINUTES_PER_DAY + self.start_minute

    def _end(self) -> int:
        return self.end_day * MINUTES_PER_DAY + self.end_minute

    @staticmethod
    def _position(moment: datetime) -> int:
        return moment.weekday() * MINUTES_PER_DAY + moment.hour * 60 + moment.minute

    def contains(self, moment: datetime) -> bool:
        position = self._position(moment)
        start, end = self._start(), self._end()
        if start < end:
            return start <= position < end
        return position >= start or position < end

    def ends_at(self, moment: datetime) -> datetime:
        """When the running window ends. Only meaningful while the window contains the moment."""

        position = self._position(moment)
        minutes = (self._end() - position) % MINUTES_PER_WEEK
        floor = moment.replace(second=0, microsecond=0)
        return floor + timedelta(minutes=minutes)


@dataclass(frozen=True)
class WindowState:
    """Where the one running window is. It is stored so a restart carries on or restores."""

    phase: str = IDLE
    window_id: Optional[str] = None
    ends_at: Optional[float] = None
    error: Optional[str] = None
    # Why the last window did not run. It stays until the next window starts.
    problem: Optional[str] = None
    attempts: int = 0
    next_attempt_at: float = 0.0
    stop_requested: bool = False
    pi_reset_done: bool = False
    router_on_done: bool = False
    skipped_until: float = 0.0

    def __post_init__(self) -> None:
        if self.phase not in PHASES:
            raise ValueError("unknown window phase")

    def but(self, **changes) -> "WindowState":
        return replace(self, **changes)
