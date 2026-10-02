"""Shared precedence for device automations and schedule previews."""

from .schedules import active_schedule


def choose_automation(
    device, group, moment, progressions, timers, device_runs, group_runs, schedules
):
    progression = progressions.get(device.id)
    timer = None if progression is not None else timers.get(device.id)
    running = None
    if progression is None and timer is None:
        running = device_runs.get(device.id) or (group_runs.get(group.id) if group else None)
    schedule = None
    if progression is None and timer is None and running is None:
        schedule = active_schedule(schedules=schedules, device_id=device.id, moment=moment)
        if schedule is None and group is not None:
            schedule = active_schedule(schedules=schedules, group_id=group.id, moment=moment)
    return progression, timer, running, schedule


def reached_limit(limit):
    return limit is not None and limit.enabled and limit.used_bytes >= limit.limit_bytes
