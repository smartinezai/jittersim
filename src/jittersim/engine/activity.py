"""Describe the network events the activity log records."""

from dataclasses import dataclass
from typing import List, Mapping, Tuple

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
    """What gives a device or everyone else its profile."""

    kind: str
    label: str = ""
    step: str = ""
    profile: str = ""


@dataclass(frozen=True)
class TargetState:
    """What the activity log compares for one device or for everyone else."""

    name: str
    source: Source
    outage: bool = False
    limits: Tuple[str, ...] = ()


def _started(name: str, source: Source) -> Tuple[str, str]:
    if source.kind == "schedule":
        return "schedule", f"{name}: schedule {source.label} started, now {source.profile}"
    if source.kind == "timer":
        return "timer", f"{name}: timer started, now {source.profile}"
    if source.kind == "scenario":
        return "scenario", f"{name}: scenario {source.label}, {source.step}, now {source.profile}"
    if source.kind == "progression":
        return "progression", f"{name}: progression started, {source.label}"
    if source.kind == "data-limit":
        return "limit", f"{name} reached its data limit, now {source.profile}"
    return (
        "limit",
        f"{name}: group {source.label} reached its shared data limit, now {source.profile}",
    )


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


def _ended(name: str, before: Source, after: Source) -> Tuple[str, str]:
    kind, ended = {
        "schedule": ("schedule", f"schedule {before.label} ended"),
        "timer": ("timer", "timer ended"),
        "scenario": ("scenario", f"scenario {before.label} ended"),
        "progression": ("progression", "progression ended"),
        "data-limit": ("limit", "data limit reset"),
        "group-data-limit": ("limit", "shared data limit reset"),
    }[before.kind]
    return kind, f"{name}: {ended}, back to {_describe(after)}"


def activity_events(
    old: Mapping[str, TargetState], new: Mapping[str, TargetState]
) -> List[Tuple[str, str]]:
    """Return (kind, message) for each change between two applied states.

    Rerolls are left out: a profile's values change, but its source doesn't.
    """

    events = []
    for key, state in new.items():
        before = old.get(key)
        if before is None:
            continue
        was, now = before.source, state.source
        if (was.kind, was.label, was.step) != (now.kind, now.label, now.step):
            if now.kind in _USUAL and was.kind in _USUAL:
                pass
            elif _RANK[now.kind] >= _RANK[was.kind]:
                events.append(_started(state.name, now))
            else:
                events.append(_ended(state.name, was, now))
        if state.outage != before.outage:
            events.append(
                ("outage", f"{state.name}: outage {'started' if state.outage else 'ended'}")
            )
        for limit in state.limits:
            if limit not in before.limits:
                events.append(("limit", f"{state.name} reached its service limit {limit}"))
        for limit in before.limits:
            if limit not in state.limits:
                events.append(("limit", f"{state.name}: service limit {limit} reset"))
    return events
