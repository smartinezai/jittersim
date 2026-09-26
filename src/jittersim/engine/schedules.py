"""Schedule matching helpers shared by the dashboard and runtime scheduler."""

import heapq
from datetime import datetime, timedelta
from typing import Iterable, Optional

from jittersim.config.models import Schedule


def is_schedule_active(*, schedule: Schedule, moment: datetime) -> bool:
    """Return whether a schedule covers the local minute represented by a datetime."""

    minute = moment.hour * 60 + moment.minute
    weekday = moment.weekday()
    if schedule.start_minute < schedule.end_minute:
        return (
            weekday in schedule.days_of_week
            and schedule.start_minute <= minute < schedule.end_minute
        )

    if minute >= schedule.start_minute:
        return weekday in schedule.days_of_week
    previous_weekday = (weekday - 1) % 7
    return minute < schedule.end_minute and previous_weekday in schedule.days_of_week


def active_schedule(
    *,
    schedules: Iterable[Schedule],
    moment: datetime,
    device_id: Optional[str] = None,
    group_id: Optional[str] = None,
    everyone_else: bool = False,
) -> Optional[Schedule]:
    """Choose one target's first active schedule in stable identifier order."""

    active = [
        schedule
        for schedule in schedules
        if schedule.enabled
        and schedule.device_id == device_id
        and schedule.group_id == group_id
        and schedule.everyone_else == everyone_else
        and is_schedule_active(schedule=schedule, moment=moment)
    ]
    return min(active, key=lambda schedule: schedule.id, default=None)


def next_schedule_change(
    *, schedules: Iterable[Schedule], now: float,
    device_id: Optional[str] = None, group_id: Optional[str] = None,
    everyone_else: bool = False,
) -> Optional[float]:
    """Find the next winning schedule change within one weekly cycle.

    Sweep boundaries rather than every minute. Overlaps use the same target
    precedence and identifier ordering as the runtime. Clock changes also
    create boundaries so repeated or missing local hours are handled.
    """

    relevant = {}
    for schedule in schedules:
        if not schedule.enabled:
            continue
        own = schedule.device_id == device_id and device_id is not None
        shared = schedule.group_id == group_id and group_id is not None
        others = everyone_else and schedule.everyone_else
        if own or shared or others:
            relevant[schedule.id] = (0 if own or others else 1, schedule)
    if not relevant:
        return None
    events = {}
    today = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    horizon = now + 9 * 86400
    for identifier, (_, schedule) in relevant.items():
        for offset in range(-1, 9):
            day = today + timedelta(days=offset)
            if day.weekday() not in schedule.days_of_week:
                continue
            start = day + timedelta(minutes=schedule.start_minute)
            end = day + timedelta(minutes=schedule.end_minute)
            if schedule.end_minute <= schedule.start_minute:
                end += timedelta(days=1)
            for boundary in (start, end):
                for fold in (0, 1):
                    stamp = boundary.replace(fold=fold).timestamp()
                    if now < stamp <= horizon:
                        events.setdefault(stamp, set()).add(identifier)
    # Include changes to the local UTC offset. These can start or end a window
    # even when the wall clock skips over its boundary.
    def utc_offset(stamp):
        return datetime.fromtimestamp(stamp).astimezone().utcoffset()

    previous = now
    previous_offset = utc_offset(now)
    while previous < horizon:
        following = min(previous + 6 * 3600, horizon)
        following_offset = utc_offset(following)
        if following_offset != previous_offset:
            low, high = int(previous), int(following) + 1
            while high - low > 1:
                middle = (low + high) // 2
                if utc_offset(middle) == previous_offset:
                    low = middle
                else:
                    high = middle
            events.setdefault(float(high), set()).update(relevant)
        previous, previous_offset = following, following_offset

    active = {}
    heap = []

    def update(identifier, moment):
        rank, schedule = relevant[identifier]
        enabled = is_schedule_active(schedule=schedule, moment=moment)
        if enabled and not active.get(identifier):
            heapq.heappush(heap, (rank, identifier))
        active[identifier] = enabled

    def winner():
        while heap and not active[heap[0][1]]:
            heapq.heappop(heap)
        return heap[0] if heap else None

    for identifier in relevant:
        update(identifier, datetime.fromtimestamp(now))
    current = winner()
    for stamp, identifiers in sorted(events.items()):
        moment = datetime.fromtimestamp(stamp)
        for identifier in identifiers:
            update(identifier, moment)
        if winner() != current:
            return stamp
    return None
