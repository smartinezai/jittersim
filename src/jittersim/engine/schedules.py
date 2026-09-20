"""Schedule matching helpers shared by the dashboard and runtime scheduler."""

from datetime import datetime
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
    device_id: str,
    moment: datetime,
) -> Optional[Schedule]:
    """Choose the first active schedule in stable identifier order."""

    active = [
        schedule
        for schedule in schedules
        if schedule.enabled
        and schedule.device_id == device_id
        and is_schedule_active(schedule=schedule, moment=moment)
    ]
    return min(active, key=lambda schedule: schedule.id, default=None)
