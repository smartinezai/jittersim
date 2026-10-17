"""Describe the network events the activity log records."""

from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Tuple

# A target's usual state: changes between these are settings edits, not events.
_USUAL = frozenset(("profile", "group-profile", "none"))
# Which source wins when several could apply, so an ending can say what applies again.
_RANK = {
    "none": 0,
    "profile": 0,
    "group-profile": 1,
    "schedule": 2,
    "scenario": 3,
    "timer": 4,
    "progression": 5,
    "data-limit": 6,
    "group-data-limit": 6,
}


@dataclass(frozen=True)
class Source:
    """What gives a device or everyone else its profile.

    The identity tells sources apart, for example two timers in a row, so a
    rename is not an event. The other fields only word the message.
    """

    kind: str
    identity: str
    label: str = ""
    step: str = ""
    profile: str = ""
    # The group's name when the source belongs to the device's group.
    group: str = ""


@dataclass(frozen=True)
class TargetState:
    """What the activity log compares for one device or for everyone else."""

    name: str
    source: Source
    outage: bool = False
    # Reached service and time limits as (identity, label) pairs.
    limits: Tuple[Tuple[str, str], ...] = ()
    # An estimate from the same settings that produced this applied state.
    next_change_at: Optional[float] = None


def _describe(source: Source) -> str:
    if source.kind == "none":
        return "a normal connection"
    if source.kind == "schedule":
        return f"schedule {source.label}, {source.profile}"
    if source.kind == "scenario":
        return f"scenario {source.label}, {source.step}, {source.profile}"
    if source.kind == "timer":
        return f"its timer, {source.profile}"
    if source.kind == "progression":
        return f"its progression, {source.label}"
    return source.profile


def _started(source: Source) -> Tuple[str, str, str]:
    """Return (kind, separator, text) for a source that takes over."""

    if source.kind == "schedule":
        return "schedule", ": ", f"schedule {source.label} started, now {source.profile}"
    if source.kind == "timer":
        return "timer", ": ", f"timer started, now {source.profile}"
    if source.kind == "scenario":
        return "scenario", ": ", f"scenario {source.label}, {source.step}, now {source.profile}"
    if source.kind == "progression":
        return "progression", ": ", f"progression started, {source.label}"
    if source.kind == "data-limit":
        return "limit", " ", f"reached its data limit, now {source.profile}"
    return "limit", " ", f"reached its shared data limit, now {source.profile}"


def _ended(source: Source) -> Tuple[str, str, str]:
    """Return (kind, separator, text) for a source that stops applying."""

    kind, text = {
        "schedule": ("schedule", f"schedule {source.label} ended"),
        "timer": ("timer", "timer ended"),
        "scenario": ("scenario", f"scenario {source.label} ended"),
        "progression": ("progression", "progression ended"),
        "data-limit": ("limit", "data limit lifted"),
        "group-data-limit": ("limit", "shared data limit lifted"),
    }[source.kind]
    return kind, ": ", text


def activity_records(
    old: Mapping[str, TargetState], new: Mapping[str, TargetState]
) -> List[Tuple[str, str, Tuple[str, ...]]]:
    """Return (kind, message) for each change between two applied states.

    Rerolls are left out: a profile's values change, but its source doesn't.
    A change that comes from a group is one event for the whole group.
    """

    # (subject, kind, separator, text) -> what each target went back to, if anything.
    entries: Dict[Tuple[str, str, str, str], List[Optional[str]]] = {}
    targets: Dict[Tuple[str, str, str, str], List[str]] = {}

    def add(subject: str, event: Tuple[str, str, str], back: Optional[str] = None) -> None:
        kind, separator, text = event
        entries.setdefault((subject, kind, separator, text), []).append(back)
        targets.setdefault((subject, kind, separator, text), []).append(key)

    for key, state in new.items():
        before = old.get(key)
        if before is None:
            continue
        was, now = before.source, state.source
        if (was.kind, was.identity) != (now.kind, now.identity):
            if now.kind in _USUAL and was.kind in _USUAL:
                pass
            elif _RANK[now.kind] >= _RANK[was.kind]:
                add(f"Group {now.group}" if now.group else state.name, _started(now))
            else:
                subject = f"Group {was.group}" if was.group else state.name
                add(subject, _ended(was), _describe(now))
        if state.outage != before.outage:
            add(state.name, ("outage", ": ", f"outage {'started' if state.outage else 'ended'}"))
        was_reached, now_reached = dict(before.limits), dict(state.limits)
        for identity, label in state.limits:
            if identity not in was_reached:
                add(state.name, ("limit", " ", f"reached its {label}"))
        for identity, label in before.limits:
            if identity not in now_reached:
                add(state.name, ("limit", ": ", f"{label} lifted"))

    events = []
    for (subject, kind, separator, text), backs in entries.items():
        message = subject + separator + text
        if backs[0] is not None:
            # A group's members can go back to different settings.
            shared = backs[0] if len(set(backs)) == 1 else "each device's own settings"
            message += f", back to {shared}"
        events.append(
            (kind, message, tuple(dict.fromkeys(targets[(subject, kind, separator, text)])))
        )
    return events
