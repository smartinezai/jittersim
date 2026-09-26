"""A bounded preview of known rule changes without rolling profiles or touching the network."""

from datetime import datetime

from .scenarios import scenario_position
from .schedules import is_schedule_active, next_schedule_change
from .selection import choose_automation, reached_limit


def device_timeline(repository, device, *, now):
    groups = repository.list_device_groups()
    group = next((item for item in groups if device.id in item.device_ids), None)
    schedules = tuple(
        item
        for item in repository.list_schedules()
        if item.enabled
        and (item.device_id == device.id or group is not None and item.group_id == group.id)
    )
    profiles = {item.id: item.name for item in repository.list_profiles()}
    timers = repository.list_active_timers(now=int(now))
    progressions = repository.list_active_progressions(now=int(now))
    runs = repository.list_scenario_runs()
    limit = next(
        (item for item in repository.list_data_limits() if item.device_id == device.id), None
    )
    shared = next(
        (
            item
            for item in repository.list_group_data_limits()
            if group is not None and item.group_id == group.id
        ),
        None,
    )
    horizon = now + 86400
    cursor = now
    changes = []
    uncertain = "Random outages, traffic thresholds and future edits can change this preview."
    iterations = 0
    while cursor < horizon and len(changes) < 32 and iterations < 64:
        iterations += 1
        device_runs, group_runs = {}, {}
        for run, scenario in runs:
            position = scenario_position(scenario, started_at=run.started_at, now=cursor)
            if position is not None:
                target = device_runs if run.device_id else group_runs
                target.setdefault(run.device_id or run.group_id, (run, scenario, position))
        active_timers, active_progressions = {}, {}
        for timer in timers:
            if timer.expires_at > cursor:
                active_timers.setdefault(timer.device_id, timer)
        for progression in progressions:
            if progression.started_at + progression.duration_seconds > cursor:
                active_progressions.setdefault(progression.device_id, progression)
        moment = datetime.fromtimestamp(cursor)
        progression, timer, running, schedule = choose_automation(
            device,
            group,
            moment,
            active_progressions,
            active_timers,
            device_runs,
            group_runs,
            schedules,
        )
        next_at = None
        profile = group.profile_id if group and group.profile_id else device.profile_id
        reason = "Group profile" if group and group.profile_id else "Device profile"
        if progression:
            profile = None
            reason = (
                "Progression from "
                + profiles[progression.start_profile_id]
                + " to "
                + profiles[progression.end_profile_id]
            )
            next_at = progression.started_at + progression.duration_seconds
        elif timer:
            profile, reason, next_at = timer.profile_id, "Device timer", timer.expires_at
        elif running:
            run, scenario, position = running
            profile = position.profile_id
            reason = scenario.name + " · step " + str(position.step_index + 1)
            next_at = int(cursor) + position.step_remaining_seconds
        else:
            if schedule:
                profile, reason = schedule.profile_id, "Schedule " + schedule.id
            next_at = next_schedule_change(
                schedules=schedules,
                now=cursor,
                device_id=device.id,
                group_id=group.id if group else None,
            )
        blocked = limit if reached_limit(limit) else shared if reached_limit(shared) else None
        if blocked:
            profile = blocked.profile_id
            reason = "Data limit reached. It takes priority over other rules."
            # A reset depends on usage accounting. Do not claim an exact future rule.
            next_at = None
        overridden = [
            item.id
            for item in schedules
            if is_schedule_active(schedule=item, moment=moment)
            and (blocked or progression or timer or running or schedule.id != item.id)
        ]
        entry = {
            "at": cursor,
            "profile": profiles.get(profile, "Changing gradually"),
            "reason": reason,
            "overridden_schedules": overridden,
        }
        if not changes or any(
            entry[key] != changes[-1][key] for key in ("profile", "reason", "overridden_schedules")
        ):
            changes.append(entry)
        if next_at is None or next_at <= cursor:
            break
        cursor = next_at
    return {
        "changes": changes,
        "horizon": horizon,
        "truncated": len(changes) == 32,
        "note": uncertain,
        "clock": datetime.fromtimestamp(now).astimezone().isoformat(),
    }


def overlapping_schedules(candidate, schedules):
    """Find actual weekly overlap, including windows crossing midnight."""

    def windows(schedule):
        for day in schedule.days_of_week:
            start = day * 1440 + schedule.start_minute
            end = day * 1440 + schedule.end_minute
            if end <= start:
                end += 1440
            yield start, end

    found = []
    if candidate.enabled:
        for other in schedules:
            if other.id == candidate.id or not other.enabled:
                continue
            if (other.device_id, other.group_id, other.everyone_else) != (
                candidate.device_id,
                candidate.group_id,
                candidate.everyone_else,
            ):
                continue
            if any(
                max(a, c + offset) < min(b, d + offset)
                for a, b in windows(candidate)
                for c, d in windows(other)
                for offset in (-10080, 0, 10080)
            ):
                found.append(other.id)
    return sorted(found)
